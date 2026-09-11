"""Capa de ejecución REAL de arbitraje intra-mercado (Fase 3).

Alcance deliberadamente acotado -- ver CLAUDE.md, sección "Fase 3":
- Sólo mercados que pasan `eligibility.is_fast_resolution_market` (deportes).
- Sólo si `settings.real_trading_enabled` es True (flag explícito que el
  usuario prende a mano después de validar el setup completo -- nunca se
  activa solo).
- El kill-switch (manual o automático) se verifica ANTES de cualquier intento
  de enviar una orden real, con prioridad sobre cualquier otra lógica.

Ejecución taker en ambas patas (decisión explícita del usuario, no maker):
un basket de arb necesita que YES y NO llenen esencialmente al mismo tiempo --
resting post-only en ambas patas dejaría leg risk real (una pata llena, la
otra no, antes de que la oportunidad desaparezca). Cruzar el spread de
inmediato en las dos, replicando la misma lógica de profundidad del
simulador de Fase 2 (que ya descuenta el fee taker del edge antes de aceptar
el trade), prioriza fill simultáneo garantizado sobre el ahorro de fee --con
$5 de tope por mercado el fee taker es marginal comparado con el riesgo de
quedar con una sola pata abierta.

Leg risk residual (no eliminado, sólo acotado): incluso enviando ambas patas
como taker, son dos llamadas HTTP separadas -- no hay forma de que el exchange
las ejecute atómicamente. Si la pata YES llena y la pata NO falla (o llena
parcial), queda una posición direccional no intencional con capital real. Se
trata como evento de emergencia: dispara el kill-switch automáticamente (no
se reintenta solo) y registra la posición desbalanceada para revisión manual
-- ver `_handle_leg_imbalance`.

**Incidente del 2026-09-08** (ver CLAUDE.md, sección Fase 3, para el post-mortem
completo): 4 órdenes YES reales llenaron de verdad on-chain, pero la detección
de fill de esa versión confiaba únicamente en `transactionsHashes` -- ese
campo se resuelve "best-effort" (el propio SDK lo documenta) y llegó vacío en
los 4 casos aunque el fill sí había ocurrido. El código concluyó "no llenó",
abandonó sin comprar la pata NO, y -- porque el registro en `real_positions`
sólo se escribía recién al final del flujo -- no dejó ningún rastro en la
base. Resultado: $10.87 en apuestas direccionales reales, sin cobertura,
invisibles al sistema, descubiertas recién por reconstrucción manual on-chain.
Dos correcciones estructurales resultantes, ambas en este módulo:
1. `_is_order_filled` ya no confía en un único campo (ver su docstring).
2. Cada pata se persiste en `real_positions` ANTES de saber si llenó (estado
   "enviada"), no después -- para que una orden real nunca vuelva a quedar
   invisible, sea cual sea la ambigüedad de la respuesta del exchange.

**Bug de sizing descubierto en la auditoría de la 4ta activación (2026-09-10/11)**
-- ver CLAUDE.md, sección Fase 3, para el detalle completo con los 4 casos
reconstruidos on-chain: las dos patas se dimensionaban de forma INDEPENDIENTE,
cada una con su propio presupuesto en dólares derivado de un único
`fill.shares` estimado ANTES de mandar ninguna orden (`simulate_arbitrage_fill`,
caminando el book local del WS). `MarketOrderArgsV2.amount` para una orden BUY
es un monto en dólares, no una cantidad de shares (el SDK no ofrece "comprar
exactamente N shares" para el lado BUY) -- así que la cantidad REAL de shares
que entrega cada pata es `presupuesto / precio_real_de_ejecución`, y ese precio
real puede diferir del estimado, sobre todo en la pata NO (enviada segunda,
después de confirmar el fill de YES, con el book real ya movido). Resultado
verificado en las 4 posiciones que llegaron a ejecutar ambas patas desde el
incidente: **las 4 terminaron con cantidades de shares YES y NO distintas**
(10.6% a 24.2% de diferencia) -- el sistema creía tener una canasta de arb sin
riesgo cuando en realidad quedaba una porción sin cobertura, expuesta
direccionalmente con capital real, sin ninguna alerta en el momento (recién se
detectó por reconstrucción manual de flujo de caja on-chain).

Fix aplicado: la pata NO ya no se dimensiona con un presupuesto independiente
-- se dimensiona por la cantidad REAL de shares que confirmó la pata YES
(`_confirmed_fill` primero, para saber cuánto llenó YES de verdad;
`_budget_for_no_leg` después, caminando el book de NO EN VIVO -- no el book
local del WS, potencialmente desatrasado -- para estimar cuántos dólares hacen
falta para esa cantidad exacta de shares). Esto reduce el desbalance esperado
pero no lo elimina: el book de NO puede seguir moviéndose entre esa consulta y
el envío real de la orden, y el SDK sigue sin aceptar una cantidad de shares
exacta para BUY. Por eso hay una red de seguridad adicional después de
confirmar el fill real de ambas patas (`_leg_imbalance_pct`): si el desbalance
residual supera `REAL_LEG_IMBALANCE_THRESHOLD_PCT`, se trata con la misma
severidad que un leg imbalance total (kill-switch + `status="pendiente"`) --
es el mismo riesgo de fondo (exposición direccional real sin cobertura), sólo
que llega por un camino distinto (ambas patas "llenaron" según `_is_order_filled`,
pero con tamaños que no calzan) en vez de que una pata falle del todo.
"""
from __future__ import annotations

import logging

from py_clob_client_v2.clob_types import MarketOrderArgsV2, OrderType, TradeParams
from py_clob_client_v2.constants import FAILED_TRADE_STATUS
from py_clob_client_v2.order_utils.model.side import SideString
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.eligibility import is_fast_resolution_market
from polybot.execution.event_log import log_event
from polybot.execution.simulator import simulate_arbitrage_fill
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.persistence.models import RealPosition
from polybot.risk.sizing import max_capital_for_real_trade
from polybot.signals.fees import taker_fee

logger = logging.getLogger(__name__)


def _real_exposure(session: Session, *, market_id: str | None = None, cluster_id: str | None = None) -> float:
    """Suma `cost_usd` de posiciones REALES abiertas o en curso, vía SQL agregado
    (nunca .all()) -- mismo patrón que `_open_exposure` en main.py para las
    simuladas. Incluye "enviada"/"pendiente" además de "abierta": una orden
    recién enviada (o desbalanceada) ya compromete capital real aunque su
    resultado final todavía no esté confirmado."""
    stmt = select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(
        RealPosition.status.in_(("abierta", "enviada", "pendiente"))
    )
    if market_id is not None:
        stmt = stmt.where(RealPosition.market_id == market_id)
    if cluster_id is not None:
        stmt = stmt.where(RealPosition.cluster_id == cluster_id)
    return session.execute(stmt).scalar_one()


def _place_market_buy(client, token_id: str, budget_usd: float) -> dict:
    """Orden de mercado FOK (fill-or-kill): o se llena por completo de inmediato
    contra la profundidad disponible, o no toca capital -- nunca queda una
    orden resting esperando matchear (eso sería volver a exponerse a leg risk
    en el tiempo, justo lo que la ejecución taker busca evitar)."""
    return client.create_and_post_market_order(
        MarketOrderArgsV2(token_id=token_id, amount=budget_usd, side=SideString.BUY),
        order_type=OrderType.FOK,
    )


def _order_matched(response: dict) -> bool:
    """Señal primaria: `transactionsHashes` (el hash de asentamiento ya
    resuelto) o `tradeIDs` (un trade fue creado -- matcheó -- aunque el hash
    todavía no se haya resuelto; el propio docstring de `post_order` en el
    SDK documenta esto como la vía alternativa). Cualquiera de las dos es
    evidencia de que la orden matcheó."""
    hashes = response.get("transactionsHashes") or response.get("transactionHashes")
    trade_ids = response.get("tradeIDs") or response.get("tradeIds")
    return bool(hashes) or bool(trade_ids)


def _confirm_via_order_status(client, order_id: str) -> bool | None:
    """Consulta el estado de la orden por su ID directamente (`GET /order/{id}`).

    **Validado contra el servidor real y resultó inútil para este caso de uso**
    (ver CLAUDE.md, sección Fase 3): para las 4 órdenes de mercado FOK del
    incidente del 2026-09-08, ya asentadas y confirmadas,
    `client.get_order(order_id)` devolvió `None` en las 4 -- no una excepción,
    no un dict con campos reconocibles, directamente `None`. La hipótesis
    (razonable a priori) de que traería `size_matched`/`status` no se sostuvo
    en la práctica: `get_order` parece servir sólo órdenes resting/abiertas,
    no el registro post-hoc de una orden de mercado ya ejecutada y liquidada.
    Se deja esta función como intento adicional de bajo costo (si alguna vez
    sí devuelve algo reconocible, se usa), pero la señal real que sí funciona
    para este escenario es `_confirm_via_trades` -- ver ahí. Devuelve
    True/False si la respuesta trae una señal reconocible, o None si la
    consulta falla, devuelve `None`/algo no interpretable, o no aporta nada."""
    try:
        order = client.get_order(order_id)
    except Exception:
        logger.warning("No se pudo confirmar el estado de la orden %s vía get_order", order_id, exc_info=True)
        return None
    if not isinstance(order, dict):
        return None

    size_matched = order.get("size_matched", order.get("sizeMatched"))
    if size_matched is not None:
        try:
            return float(size_matched) > 0
        except (TypeError, ValueError):
            pass

    status = str(order.get("status", "")).upper()
    if status in {"MATCHED", "FILLED"}:
        return True
    if status in {"UNMATCHED", "LIVE", "CANCELED", "CANCELLED"}:
        return False
    return None


def _confirm_via_trades(client, token_id: str, order_id: str) -> bool | None:
    """Busca, vía `get_trades(asset_id=token_id)`, el trade cuyo
    `taker_order_id` coincide con `order_id` -- ésta es la señal que SÍ
    funciona contra el servidor real para una orden de mercado FOK ya
    ejecutada (validado con las 4 órdenes reales del incidente del
    2026-09-08: las 4 aparecieron acá con `status="CONFIRMED"`, mientras que
    `get_order` devolvía `None` para las mismas). `FAILED` (la única
    constante de estado fallido que expone el propio SDK,
    `constants.FAILED_TRADE_STATUS`) es la única lectura negativa; cualquier
    otro estado (`CONFIRMED`, `MATCHED`, `MINED`, `RETRYING`, ...) implica que
    el trade existe -- matcheó -- aunque su liquidación on-chain todavía esté
    en curso. Devuelve None si la consulta falla o no aparece ningún trade
    con ese `taker_order_id` (inconcluso, no es lo mismo que "no llenó")."""
    try:
        trades = client.get_trades(TradeParams(asset_id=token_id), only_first_page=True)
    except Exception:
        logger.warning("No se pudo confirmar el estado de la orden %s vía get_trades", order_id, exc_info=True)
        return None
    for trade in trades or []:
        if trade.get("taker_order_id") == order_id or trade.get("takerOrderId") == order_id:
            return str(trade.get("status", "")).upper() != FAILED_TRADE_STATUS
    return None


def _confirmed_fill(
    client, token_id: str, order_id: str, market: MarketInfo, *, fallback_shares: float, fallback_price: float
) -> tuple[float, float, float]:
    """Recupera el tamaño, precio promedio y COSTO REAL (fee incluido) de una
    orden ya confirmada como llenada, vía `get_trades(asset_id=token_id)`
    filtrando por `taker_order_id` -- misma fuente que `_confirm_via_trades`
    (validada contra el servidor real en el incidente del 2026-09-08), pero
    acá se lee el `size`/`price` de cada trade en vez de sólo su `status`.

    El `price` que devuelve `get_trades` es el precio de ejecución SIN fee --
    verificado en la auditoría de la 4ta activación comparando contra
    `usdcSize` de `data-api.polymarket.com/activity` (el gasto real en la
    wallet): la diferencia coincide con `taker_fee(size, price, market)`, no
    con cero. Sin sumar el fee acá, `cost_usd` quedaría subestimado en el
    orden del fee taker real (~2-5% observado), reintroduciendo el mismo tipo
    de error de registro que este módulo corrige -- sólo que en el costo en
    vez de en las shares.

    Si hay más de un trade para la misma orden (varios maker matcheados), se
    promedia por tamaño. Si la consulta falla o no aparece ningún trade con
    ese order_id, cae de vuelta a la estimación pre-trade (`fallback_*`) --
    degradado, pero es el comportamiento anterior a este fix, no una
    regresión nueva; el chequeo de desbalance residual post-fill
    (`_leg_imbalance_pct`) sigue funcionando igual sobre lo que sea que
    termine guardado."""
    try:
        trades = client.get_trades(TradeParams(asset_id=token_id), only_first_page=True)
    except Exception:
        logger.warning("No se pudo confirmar tamaño/precio real de la orden %s vía get_trades", order_id, exc_info=True)
        trades = None

    total_size = 0.0
    total_raw_cost = 0.0
    for trade in trades or []:
        if trade.get("taker_order_id") != order_id and trade.get("takerOrderId") != order_id:
            continue
        try:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0))
        except (TypeError, ValueError):
            continue
        total_size += size
        total_raw_cost += size * price

    if total_size <= 0:
        total_size = fallback_shares
        total_raw_cost = fallback_shares * fallback_price

    price_avg = total_raw_cost / total_size if total_size > 0 else 0.0
    cost_usd = total_raw_cost + taker_fee(total_size, price_avg, market)
    return total_size, price_avg, cost_usd


def _fresh_asks(client, token_id: str) -> dict[float, float]:
    """Book de asks de `token_id` consultado EN VIVO (`get_order_book`, REST),
    no el book local mantenido por el WS -- para cuando se llega a dimensionar
    la pata NO ya pasó tiempo real (confirmar el fill de YES, alguna consulta
    extra de `get_trades`), y el book local puede estar desatrasado respecto
    al libro real justo cuando más importa la precisión."""
    raw = client.get_order_book(token_id)
    return {float(lvl["price"]): float(lvl["size"]) for lvl in (raw.get("asks") or [])}


def _cost_for_target_shares(asks: dict[float, float], target_shares: float) -> tuple[float, float]:
    """Camina los niveles ask (precio ascendente) acumulando hasta cubrir
    `target_shares` (o lo que la profundidad disponible permita, si es menos).
    Devuelve `(costo_usd, shares_cubiertas)`."""
    remaining = target_shares
    cost = 0.0
    covered = 0.0
    for price, size in sorted(asks.items()):
        if remaining <= 0:
            break
        take = min(size, remaining)
        cost += take * price
        covered += take
        remaining -= take
    return cost, covered


def _leg_imbalance_pct(yes_shares: float, no_shares: float) -> float:
    """Diferencia relativa entre las dos patas, contra la más grande de las
    dos -- 0.0 si están perfectamente calzadas, 1.0 si una de las dos es cero."""
    larger = max(yes_shares, no_shares)
    if larger <= 0:
        return 0.0
    return abs(yes_shares - no_shares) / larger


def _is_order_filled(client, response: dict, token_id: str) -> bool:
    """Determina si una orden real llenó. Ya NO confía únicamente en
    `transactionsHashes` (ver el incidente documentado en el docstring del
    módulo): ese campo se resuelve "best-effort" y puede llegar vacío en una
    orden que sí llenó. Orden de señales, de más a menos directa:
    1. `transactionsHashes`/`tradeIDs` en la respuesta inicial (`_order_matched`).
    2. Si la respuesta no trae ninguna de las dos pero sí un `orderID`, se
       busca el trade asociado vía `get_trades` (`_confirm_via_trades`) --
       ésta es la señal validada contra el servidor real, ver su docstring.
    3. `_confirm_via_order_status` (`get_order`) como intento adicional de
       bajo costo -- en la práctica no aportó nada contra el servidor real
       (ver su docstring), se mantiene por si acaso.
    4. Si TODO lo anterior es inconcluso, se asume LLENADA, no al revés --
       la lección del incidente fue exactamente el error opuesto: tratar una
       ambigüedad como "no llenó" dejó una orden real con capital gastado sin
       registrar en ningún lado. Sin `orderID` en absoluto (nunca se registró
       intento de orden) sí se concluye con confianza que no llenó -- ahí no
       hay ambigüedad que resolver a favor de la cautela.
    """
    if _order_matched(response):
        return True

    order_id = response.get("orderID") or response.get("orderId")
    if not order_id:
        return False

    confirmed = _confirm_via_trades(client, token_id, order_id)
    if confirmed is not None:
        return confirmed

    confirmed = _confirm_via_order_status(client, order_id)
    return confirmed if confirmed is not None else True


class RealExecutionEngine:
    def __init__(self, client, session_factory) -> None:
        self._client = client
        self._session_factory = session_factory

    def maybe_execute(self, market: MarketInfo, yes_book: OrderBook, no_book: OrderBook) -> None:
        if not settings.real_trading_enabled:
            return
        if not is_fast_resolution_market(market):
            return
        if kill_switch.is_halted():
            logger.debug("Kill-switch activo, se omite ejecución real en %s", market.question[:60])
            return

        with self._session_factory() as session:
            market_exposure = _real_exposure(session, market_id=market.condition_id)
            cluster_exposure = _real_exposure(session, cluster_id=market.cluster_id)
            total_exposure = _real_exposure(session)

            max_cost = max_capital_for_real_trade(market_exposure, cluster_exposure, total_exposure)
            if max_cost <= 0:
                return

            fill = simulate_arbitrage_fill(market, yes_book, no_book, 0.0, 0.0, max_cost=max_cost)
            if fill is None or fill.net_pnl <= 0:
                return

            self._execute_fill(session, market, fill)

    def _execute_fill(self, session: Session, market: MarketInfo, fill) -> None:
        yes_budget = fill.shares * fill.yes_price_avg

        position = RealPosition(
            market_id=market.condition_id,
            cluster_id=market.cluster_id,
            question=market.question,
            status="enviada",
            # Valores pre-trade -- se sobreescriben con el fill real confirmado
            # más abajo (`_confirmed_fill`). Quedan acá sólo como mejor estimado
            # disponible mientras la orden YES todavía no se mandó.
            yes_shares=fill.shares,
            no_shares=fill.shares,
            yes_price_avg=fill.yes_price_avg,
            no_price_avg=fill.no_price_avg,
            cost_usd=fill.cost_usd,
            fee_paid=fill.fee_estimate,
            net_pnl_expected=fill.net_pnl,
        )
        session.add(position)
        session.commit()  # persistida ANTES de saber si la pata YES llena -- ver docstring del módulo

        log_event(
            session,
            "order_sent",
            "info",
            f"EJECUCIÓN REAL {market.question[:60]} | shares_estimadas={fill.shares:.2f} "
            f"yes_budget={yes_budget:.2f} net_pnl_esperado={fill.net_pnl:.4f}",
            market_id=market.condition_id,
            real_position_id=position.id,
        )

        try:
            yes_resp = _place_market_buy(self._client, market.yes_token_id, yes_budget)
        except Exception:  # noqa: BLE001 -- capa de I/O con un SDK externo, se maneja explícitamente abajo
            # Falla al ENVIAR la orden YES (red/timeout/excepción del cliente). No se puede
            # confirmar con certeza que no se gastó capital real (podría haber llegado al
            # exchange y perderse la respuesta) -- se deja "pendiente" para revisión manual
            # en vez de asumir que no pasó nada, en vez de descartar el registro.
            position.status = "pendiente"
            position.notes = "Excepción al enviar la orden YES -- resultado real no confirmado."
            session.commit()
            log_event(
                session,
                "order_send_failed",
                "critical",
                f"Fallo al ENVIAR la orden YES real en {market.question[:60]} (budget={yes_budget:.2f}) "
                "-- resultado no confirmado, requiere revisión manual",
                market_id=market.condition_id,
                real_position_id=position.id,
                exc_info=True,
            )
            return

        position.yes_order_id = yes_resp.get("orderID") or yes_resp.get("orderId")
        position.yes_tx_hash = str((yes_resp.get("transactionsHashes") or [None])[0]) or None
        session.commit()

        if not _is_order_filled(self._client, yes_resp, market.yes_token_id):
            position.status = "cancelada"
            session.commit()
            log_event(
                session,
                "order_not_filled",
                "warning",
                f"Orden YES real no llenó en {market.question[:60]}, se aborta sin tocar la pata NO",
                market_id=market.condition_id,
                real_position_id=position.id,
            )
            return

        # Tamaño y costo REALES de la pata YES (no la estimación pre-trade) -- ver
        # docstring del módulo, "Bug de sizing descubierto en la auditoría de la
        # 4ta activación".
        yes_shares_real, yes_price_real, yes_cost_real = _confirmed_fill(
            self._client,
            market.yes_token_id,
            position.yes_order_id,
            market,
            fallback_shares=fill.shares,
            fallback_price=fill.yes_price_avg,
        )
        position.yes_shares = yes_shares_real
        position.no_shares = 0.0  # todavía no se compró nada de NO
        position.yes_price_avg = yes_price_real
        position.cost_usd = yes_cost_real  # interino -- si algo falla antes de NO, ya queda correcto
        session.commit()

        log_event(
            session,
            "order_filled",
            "info",
            f"Orden YES real llenó en {market.question[:60]} | shares_reales={yes_shares_real:.4f} "
            f"(estimadas={fill.shares:.4f}), enviando pata NO dimensionada por ese tamaño real",
            market_id=market.condition_id,
            real_position_id=position.id,
        )

        # La pata NO se dimensiona por la cantidad REAL de shares de YES, no por
        # un presupuesto independiente -- caminando el book de NO EN VIVO (no el
        # snapshot local del WS, que puede estar desatrasado a esta altura).
        no_asks = _fresh_asks(self._client, market.no_token_id)
        no_budget, no_covered = _cost_for_target_shares(no_asks, yes_shares_real)
        if no_covered < yes_shares_real:
            logger.warning(
                "Profundidad insuficiente en el book de NO para %s: se necesitaban %.4f shares, "
                "el book en vivo sólo cubre %.4f -- se manda el presupuesto para lo que alcanza",
                market.question[:60],
                yes_shares_real,
                no_covered,
            )

        try:
            no_resp = _place_market_buy(self._client, market.no_token_id, no_budget)
        except Exception:  # noqa: BLE001 -- capa de I/O con un SDK externo, se maneja explícitamente abajo
            log_event(
                session,
                "order_send_failed",
                "critical",
                f"Fallo al ENVIAR la orden NO real en {market.question[:60]} (budget={no_budget:.2f}) "
                "tras llenar YES -- posición desbalanceada con capital real ya comprometido",
                market_id=market.condition_id,
                real_position_id=position.id,
                exc_info=True,
            )
            self._handle_leg_imbalance(session, position, no_resp=None)
            return

        position.no_order_id = no_resp.get("orderID") or no_resp.get("orderId")
        position.no_tx_hash = str((no_resp.get("transactionsHashes") or [None])[0]) or None
        session.commit()

        if not _is_order_filled(self._client, no_resp, market.no_token_id):
            log_event(
                session,
                "leg_imbalance",
                "critical",
                f"Orden NO real NO llenó en {market.question[:60]} tras llenar YES -- "
                "posición desbalanceada con capital real",
                market_id=market.condition_id,
                real_position_id=position.id,
            )
            self._handle_leg_imbalance(session, position, no_resp=no_resp)
            return

        # Tamaño y costo REALES de la pata NO -- las shares pueden diferir de
        # `yes_shares_real` (el objetivo) aunque el book en vivo se haya usado
        # para dimensionarla; el book pudo seguir moviéndose entre esa consulta
        # y el envío real.
        no_shares_real, no_price_real, no_cost_real = _confirmed_fill(
            self._client,
            market.no_token_id,
            position.no_order_id,
            market,
            fallback_shares=yes_shares_real,
            fallback_price=fill.no_price_avg,
        )
        position.no_shares = no_shares_real
        position.no_price_avg = no_price_real
        position.cost_usd = yes_cost_real + no_cost_real
        session.commit()

        imbalance_pct = _leg_imbalance_pct(yes_shares_real, no_shares_real)
        position.leg_imbalance_pct = imbalance_pct

        if imbalance_pct > settings.real_leg_imbalance_threshold_pct:
            # Ambas patas "llenaron" según `_is_order_filled`, pero con tamaños que no
            # calzan -- mismo riesgo de fondo que un leg imbalance total (exposición
            # direccional real sin cobertura completa), sólo que llega por un camino
            # distinto. Se trata igual: "pendiente" + kill-switch, no se asume canasta
            # calzada. Ver docstring del módulo.
            position.status = "pendiente"
            position.notes = (
                f"Desbalance residual de shares tras confirmar ambas patas: "
                f"YES={yes_shares_real:.4f} NO={no_shares_real:.4f} "
                f"({imbalance_pct:.1%} > umbral {settings.real_leg_imbalance_threshold_pct:.1%}) -- "
                "ambas patas llenaron pero la canasta no quedó calzada; hay exposición "
                "direccional real sin cobertura completa, requiere revisión manual."
            )
            session.commit()
            log_event(
                session,
                "leg_size_mismatch",
                "critical",
                f"Desbalance residual en {market.question[:60]}: YES={yes_shares_real:.4f} "
                f"NO={no_shares_real:.4f} ({imbalance_pct:.1%}) -- ambas patas llenaron pero "
                "la canasta no quedó calzada, hay exposición direccional real sin cobertura",
                market_id=market.condition_id,
                real_position_id=position.id,
            )
            kill_switch.halt(
                f"desbalance residual de shares en mercado {market.condition_id} "
                f"({imbalance_pct:.1%} > umbral {settings.real_leg_imbalance_threshold_pct:.1%}) -- "
                "ambas patas llenaron pero con tamaños distintos",
                session=session,
            )
            return

        position.status = "abierta"
        session.commit()
        log_event(
            session,
            "position_opened",
            "info",
            f"Posición real abierta en {market.question[:60]} | cost_usd={position.cost_usd:.2f} "
            f"yes_shares={yes_shares_real:.4f} no_shares={no_shares_real:.4f} "
            f"desbalance={imbalance_pct:.2%}",
            market_id=market.condition_id,
            real_position_id=position.id,
        )

    def _handle_leg_imbalance(self, session: Session, position: RealPosition, no_resp: dict | None) -> None:
        """Marca la posición desbalanceada y detiene todo trading real de inmediato --
        no se intenta deshacer ni recuperar automáticamente (ver CLAUDE.md)."""
        position.status = "pendiente"
        position.no_shares = 0.0  # la pata NO nunca se confirmó -- no hay capital real ahí
        # cost_usd ya quedó en el costo real de sólo YES (fee incluido) desde que se
        # confirmó esa pata -- no hace falta recalcularlo acá.
        position.fee_paid = 0.0
        position.notes = "Leg imbalance: pata YES llenó, pata NO no confirmó."
        session.commit()

        kill_switch.halt(
            f"leg imbalance real en mercado {position.market_id} -- pata YES llenó, pata NO no confirmó",
            session=session,
        )
