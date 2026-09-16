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

**Bug de fallback silencioso en `_confirmed_fill` -- primera ejecución real del
nuevo flujo de sizing (2026-09-11, "Will Kashiwa Reysol win?")**: el sizing
en sí funcionó bien (YES real=6.578948 shares, NO dimensionado correctamente
por esa cantidad contra el book en vivo), pero `_confirmed_fill` tenía un
`fallback_shares`/`fallback_price` que se usaba cuando `get_trades` no
encontraba el trade -- y ese fallback **fabricaba silenciosamente** un
resultado usando la estimación pre-trade, como si fuera el fill real
confirmado. En producción, `get_trades` no encontró el trade de la pata NO en
el primer intento (lag de indexación transitorio del exchange -- confirmado:
la misma consulta, repetida minutos después, sí lo encontró con el tamaño y
precio reales exactos), así que el fallback activó y registró `no_shares` =
`yes_shares_real` (dando un falso 0.00% de desbalance) y `no_price_avg` = la
estimación pre-trade (0.72, no el 0.85 real) -- subestimando el costo real en
$0.82 (13% del trade) e **invirtiendo el signo del P&L** (+$0.475 registrado
vs. -$0.344 real). La reconciliación detectó la divergencia igual y activó el
kill-switch correctamente, pero por una causa distinta a la que su propio
mensaje sugiere.

Fix: `_confirmed_fill` reintenta `get_trades` con backoff corto (cubre el lag
transitorio, que es la causa confirmada) y, si tras los reintentos sigue sin
encontrar el trade, **ya no fabrica nada** -- devuelve `None`, y el caller
marca la posición `status="sin_confirmar"` (no "abierta" ni "pendiente" con
un desbalance inventado) y dispara el kill-switch con la misma severidad que
un leg imbalance: no se puede operar con confianza si ni siquiera se puede
verificar el resultado de la posición anterior.

**Prevención de leg imbalance por presupuesto infeasible (2026-09-13,
incidente Getafe CF vs. RC Deportivo)**: pata YES llenó real ($4.29, mercado
muy sesgado hacia YES), pero al enviar NO el exchange rechazó la orden de
plano -- `"invalid amount for a marketable BUY order ($0.52), min size: 1"`.
El presupuesto de NO (dimensionado por las shares reales de YES contra un
book de NO muy barato, ~$0.11) cayó bajo el mínimo de orden del exchange.
Este es **determinístico**: va a volver a pasar cada vez que el mercado esté
lo bastante sesgado como para que el lado barato, al tamaño que le toca
cubrir, no llegue al mínimo (a diferencia de Al Ittihad/Boca Juniors,
categorizados originalmente como "fallas de red aleatorias" -- corregido el
2026-09-16, ver CLAUDE.md: esa categorización nunca tuvo traceback real que
la respaldara, y ambos presupuestos estaban en el mismo rango bajo $1 que
este incidente). Fix: `_execute_fill` valida esto **antes de tocar
YES** -- camina el book de NO en vivo con el tamaño ESTIMADO de YES
(`fill.shares`, la única referencia disponible en este punto) y, si el
presupuesto resultante cae bajo `REAL_MIN_ORDER_VALUE_USD`, aborta el trade
completo sin gastar nada (mismo nivel que "no hay fill rentable"). No
reemplaza `_handle_leg_imbalance` -- sigue siendo la red de seguridad para
los casos que pasan esta validación previa pero fallan por otra razón en el
envío real (ver docstring de esa función).

**Gap en esa prevención, encontrado en la posición 23 "Deportivo Toluca FC"
(2026-09-16)**: la validación de arriba corre UNA sola vez, antes de tocar
YES, contra el tamaño ESTIMADO (`fill.shares`). Si el fill REAL de YES
difiere mucho de esa estimación -- acá, 3.641 shares reales contra 7.6923
estimadas, porque el precio de YES saltó de ~0.37 implícito a 0.78 real en
vivo (un mercado deportivo moviéndose durante el partido, ej. un gol) -- el
presupuesto de NO recalculado para el tamaño REAL puede caer bajo el mínimo
aunque la validación pre-YES haya pasado para la estimación. Sin una segunda
validación, el único aviso era el mismo 400 determinístico del exchange en
el envío real, indistinguible en el log de una falla de red genuina (mismo
`event_type="order_send_failed"` que Al Ittihad/Boca Juniors). Fix:
`_execute_fill` revalida el presupuesto recalculado de NO contra
`REAL_MIN_ORDER_VALUE_USD` justo antes de intentar el envío HTTP -- si cae
bajo el mínimo, aborta por `_handle_leg_imbalance` con un evento y motivo
explícitos (`no_leg_infeasible_post_fill`), distintos de una falla de envío
HTTP genérica, para no perder la distinción que este incidente reveló.

**Bloqueo per-mercado de "sin_confirmar" (2026-09-14, ver CLAUDE.md sección
Fase 3, análisis de viabilidad previo a este cambio)**: un `sin_confirmar`
ya no detiene TODO el sistema -- el riesgo real está acotado a esa
posición/mercado específico (no hay razón estructural para que bloquee
mercados no relacionados). `_handle_unconfirmed_fill` ya NO llama a
`kill_switch.halt()` global por defecto; en cambio, mientras la fila siga en
`status="sin_confirmar"`, `maybe_execute` la detecta vía
`_market_locked_by_unconfirmed` (un segundo gate, independiente del global
`is_halted()`) y omite silenciosamente nuevas órdenes en ESE
`market_id` puntual -- el resto del sistema sigue operando con normalidad.
Los otros 3 tipos de incidente (drawdown por equity, leg imbalance por falla
de red, `leg_size_mismatch`) siguen bloqueando todo el sistema globalmente
sin excepción -- eso no cambió, son señales de posible problema sistémico,
no acotadas a un mercado.

Salvaguarda de concurrencia: varios mercados bloqueados en paralelo dejan de
ser "un caso puntual" -- si la cantidad de posiciones `sin_confirmar`
simultáneas alcanza `REAL_MAX_CONCURRENT_SIN_CONFIRMAR` (default 2), la
posición que cruza ese tope SÍ dispara el kill-switch GLOBAL (mismo
criterio que `auto_recovery_capped`: el mensaje deja explícito que es por el
tope, no porque ese caso puntual sea distinto). Sin esto, el bloqueo
per-mercado por sí solo no tendría ninguna señal de que el patrón "conocido"
empezó a repetirse con una frecuencia anormal.

**Advertencia de profundidad insuficiente persistida en la DB (2026-09-14)**:
la advertencia de "el book de NO no alcanza para cubrir el tamaño real de
YES" existía desde el rediseño de sizing, pero sólo iba al logger de Python
-- confirmado en producción que se pierde sin remedio con la rotación de
journald (el incidente de CA Huracán, `leg_size_mismatch` del 3.9%, ya no
tenía rastro en journald apenas ~2h después). Ahora también se persiste como
evento `insufficient_book_depth` (severidad "warning", no crítico -- todavía
no se sabe si esto va a explicar el desbalance final, sólo es un dato
adicional) con el book completo, la profundidad cubierta vs. requerida y el
presupuesto resultante -- puramente aditivo, no cambia qué presupuesto se
manda ni ninguna decisión de ejecución.
"""
from __future__ import annotations

import logging
import time

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
        RealPosition.status.in_(("abierta", "enviada", "pendiente", "sin_confirmar"))
    )
    if market_id is not None:
        stmt = stmt.where(RealPosition.market_id == market_id)
    if cluster_id is not None:
        stmt = stmt.where(RealPosition.cluster_id == cluster_id)
    return session.execute(stmt).scalar_one()


def _market_locked_by_unconfirmed(session: Session, market_id: str) -> bool:
    """True si ESE mercado puntual tiene una posición `sin_confirmar` en
    curso -- el bloqueo per-mercado del 2026-09-14 (ver docstring del módulo):
    mientras la fila siga en ese status, no se manda una orden real nueva en
    este mercado, pero el resto del sistema no se ve afectado. Se limpia solo
    en cuanto la posición deja de estar `sin_confirmar` (auto-recuperada,
    backfilleada a mano, o resuelta), sin ningún estado adicional que
    mantener."""
    return (
        session.execute(
            select(func.count())
            .select_from(RealPosition)
            .where(RealPosition.status == "sin_confirmar", RealPosition.market_id == market_id)
        ).scalar_one()
        > 0
    )


def _concurrent_sin_confirmar_count(session: Session) -> int:
    """Cantidad total de posiciones `sin_confirmar` abiertas en este momento,
    sin importar el mercado -- usada para decidir si se cruzó el tope de
    concurrencia (`REAL_MAX_CONCURRENT_SIN_CONFIRMAR`), la única señal
    sistémica que le queda al bloqueo per-mercado (ver docstring del módulo)."""
    return session.execute(
        select(func.count()).select_from(RealPosition).where(RealPosition.status == "sin_confirmar")
    ).scalar_one()


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


def _fetch_trade_totals(client, token_id: str, order_id: str) -> tuple[float, float] | None:
    """Un intento de `get_trades(asset_id=token_id)`, filtrado por
    `taker_order_id`. Devuelve `(total_size, total_raw_cost)` si encuentra al
    menos un trade que matchee, o `None` si la consulta falla o no aparece
    nada -- sin decidir qué hacer con eso, eso es responsabilidad de
    `_confirmed_fill`."""
    try:
        trades = client.get_trades(TradeParams(asset_id=token_id), only_first_page=True)
    except Exception:
        logger.warning("Fallo consultando get_trades para la orden %s", order_id, exc_info=True)
        return None

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

    return (total_size, total_raw_cost) if total_size > 0 else None


def _confirmed_fill(
    client,
    token_id: str,
    order_id: str,
    market: MarketInfo,
    *,
    retries: int = 3,
    retry_delay_seconds: float = 2.0,
) -> tuple[float, float, float] | None:
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

    **No fabrica un resultado si no puede confirmar** (corrección del
    incidente del 2026-09-11, ver docstring del módulo): reintenta
    `get_trades` hasta `retries` veces con `retry_delay_seconds` entre
    intentos -- cubre el lag de indexación transitorio del exchange, que es
    la causa confirmada del incidente (el mismo trade, consultado minutos
    después, sí aparecía). Si después de todos los intentos sigue sin
    aparecer nada, devuelve `None` -- es responsabilidad explícita del
    caller tratar eso como "no se pudo confirmar" (`status="sin_confirmar"`
    + kill-switch), nunca como "confirmado y calzado" con datos inventados.
    Si hay más de un trade para la misma orden (varios maker matcheados), se
    promedia por tamaño."""
    for attempt in range(retries):
        totals = _fetch_trade_totals(client, token_id, order_id)
        if totals is not None:
            total_size, total_raw_cost = totals
            price_avg = total_raw_cost / total_size
            cost_usd = total_raw_cost + taker_fee(total_size, price_avg, market)
            return total_size, price_avg, cost_usd

        if attempt < retries - 1:
            time.sleep(retry_delay_seconds)

    return None


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
            if _market_locked_by_unconfirmed(session, market.condition_id):
                logger.debug(
                    "Mercado %s bloqueado por un sin_confirmar en curso, se omite ejecución real "
                    "(el resto del sistema sigue operando con normalidad)",
                    market.question[:60],
                )
                return

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

        # Validación de feasibility ANTES de gastar capital en YES (2026-09-13,
        # incidente Getafe/Deportivo -- ver docstring del módulo y CLAUDE.md):
        # en un mercado muy sesgado (una pata carísima, la otra muy barata),
        # el presupuesto que le tocaría a la pata NO puede caer bajo el
        # mínimo de orden que exige el exchange -- la orden se rechaza de
        # plano y YES queda comprado sin cobertura posible, un leg imbalance
        # evitable. Se estima acá con el book de NO EN VIVO y el tamaño
        # ESTIMADO de YES (`fill.shares`, la única referencia disponible
        # antes de enviar nada real) -- no reemplaza la red de seguridad de
        # `_handle_leg_imbalance`, que sigue cubriendo los casos que pasan
        # esta validación pero fallan por otra razón en el envío real (ej.
        # las fallas de red de Al Ittihad/Boca Juniors). Sin margen de
        # tolerancia agregado a propósito: es sólo una estimación pre-trade
        # (el book puede moverse para cuando YES confirme), así que sumar un
        # colchón arbitrario sería otra suposición sin verificar -- el riesgo
        # residual de un caso límite que pase esta validación y aun así falle
        # en el envío real queda cubierto por el leg imbalance existente.
        estimated_no_asks = _fresh_asks(self._client, market.no_token_id)
        estimated_no_budget, _ = _cost_for_target_shares(estimated_no_asks, fill.shares)
        if estimated_no_budget < settings.real_min_order_value_usd:
            logger.info(
                "Arb infeasible en %s: presupuesto estimado de NO ($%.4f para %.4f shares) cae bajo "
                "el mínimo de orden del exchange ($%.2f) -- mercado demasiado sesgado, se aborta antes "
                "de tocar YES",
                market.question[:60],
                estimated_no_budget,
                fill.shares,
                settings.real_min_order_value_usd,
            )
            return

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
        confirmed_yes = _confirmed_fill(
            self._client,
            market.yes_token_id,
            position.yes_order_id,
            market,
            retries=settings.real_fill_confirm_retries,
            retry_delay_seconds=settings.real_fill_confirm_retry_delay_seconds,
        )
        if confirmed_yes is None:
            self._handle_unconfirmed_fill(session, position, leg="YES", raw_response=yes_resp)
            return
        yes_shares_real, yes_price_real, yes_cost_real = confirmed_yes
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
            # Persistido en la DB (2026-09-14, ver CLAUDE.md, sección Fase 3):
            # antes esto sólo iba al logger de Python -- se perdía sin remedio
            # con la rotación de journald (confirmado en vivo: ni el incidente
            # de CA Huracán, de apenas ~2h antes, sobrevivió). Puramente
            # aditivo, no cambia qué presupuesto se manda ni ninguna decisión
            # de ejecución -- sólo evita descartar un dato que ya se calculaba.
            log_event(
                session,
                "insufficient_book_depth",
                "warning",
                f"Profundidad insuficiente en el book de NO para {market.question[:60]}: "
                f"se necesitaban {yes_shares_real:.4f} shares, el book en vivo sólo cubre "
                f"{no_covered:.4f} -- se manda el presupuesto para lo que alcanza",
                market_id=market.condition_id,
                real_position_id=position.id,
                detail={
                    "required_shares": yes_shares_real,
                    "covered_shares": no_covered,
                    "no_budget_usd": no_budget,
                    "best_ask_price": min(no_asks) if no_asks else None,
                    "no_book_asks": no_asks,
                },
            )

        # Revalidación de feasibility POST-fill real de YES (2026-09-16,
        # incidente posición 23 "Deportivo Toluca FC" -- ver docstring del
        # módulo y CLAUDE.md): la validación de la Décima activación (arriba,
        # `estimated_no_budget`) sólo corre UNA vez, antes de tocar YES,
        # contra el tamaño ESTIMADO (`fill.shares`). Si el fill real de YES
        # difiere mucho de esa estimación -- acá, 3.641 shares reales contra
        # 7.6923 estimadas, porque el precio de YES saltó de ~0.37 implícito
        # a 0.78 real en vivo -- el presupuesto de NO recalculado para el
        # tamaño REAL puede caer bajo el mínimo aunque la validación pre-YES
        # haya pasado para el tamaño estimado. Sin este chequeo, el único
        # aviso era el 400 determinístico del exchange en el envío real
        # (`"invalid amount... min size: 1"`), indistinguible en el log de
        # una falla de red genuina -- se aborta acá, ANTES del intento HTTP,
        # con el motivo explícito para no perder esa distinción.
        if no_budget < settings.real_min_order_value_usd:
            logger.warning(
                "Arb infeasible post-fill en %s: presupuesto recalculado de NO ($%.4f para %.4f shares "
                "reales, estimado pre-trade %.4f) cae bajo el mínimo de orden del exchange ($%.2f) -- "
                "el precio se movió entre la estimación y el fill real de YES, se aborta antes de "
                "enviar la orden NO",
                market.question[:60],
                no_budget,
                yes_shares_real,
                fill.shares,
                settings.real_min_order_value_usd,
            )
            log_event(
                session,
                "no_leg_infeasible_post_fill",
                "critical",
                f"Presupuesto de NO recalculado tras el fill real de YES (${no_budget:.4f} para "
                f"{yes_shares_real:.4f} shares reales, estimado {fill.shares:.4f}) cae bajo el mínimo "
                f"de orden del exchange (${settings.real_min_order_value_usd:.2f}) -- infeasible "
                "post-fill por movimiento de precio, no se intenta enviar la orden NO",
                market_id=market.condition_id,
                real_position_id=position.id,
                detail={
                    "no_budget_usd": no_budget,
                    "yes_shares_real": yes_shares_real,
                    "estimated_yes_shares": fill.shares,
                    "min_order_value_usd": settings.real_min_order_value_usd,
                },
            )
            self._handle_leg_imbalance(
                session,
                position,
                no_resp=None,
                reason=(
                    "Infeasible post-fill por movimiento de precio: el presupuesto de NO recalculado "
                    "tras el fill real de YES cayó bajo el mínimo de orden del exchange antes de "
                    "intentar el envío (no es una falla de envío HTTP)."
                ),
            )
            return

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
        confirmed_no = _confirmed_fill(
            self._client,
            market.no_token_id,
            position.no_order_id,
            market,
            retries=settings.real_fill_confirm_retries,
            retry_delay_seconds=settings.real_fill_confirm_retry_delay_seconds,
        )
        if confirmed_no is None:
            self._handle_unconfirmed_fill(session, position, leg="NO", raw_response=no_resp)
            return
        no_shares_real, no_price_real, no_cost_real = confirmed_no
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

    def _handle_leg_imbalance(
        self, session: Session, position: RealPosition, no_resp: dict | None, *, reason: str | None = None
    ) -> None:
        """Marca la posición desbalanceada y detiene todo trading real de inmediato --
        no se intenta deshacer ni recuperar automáticamente (ver CLAUDE.md).

        `reason` (2026-09-16, incidente posición 23 "Deportivo Toluca FC"):
        motivo explícito opcional para distinguir POR QUÉ la pata NO nunca
        se confirmó -- por defecto (sin `reason`) sigue siendo el caso
        genérico de falla de envío HTTP (Al Ittihad/Boca Juniors); pasar un
        `reason` explícito cuando el llamador ya sabe la causa (ej.
        infeasible post-fill por movimiento de precio, ver
        `no_leg_infeasible_post_fill`) para no perder esa distinción en
        `notes`/el motivo del kill-switch."""
        position.status = "pendiente"
        position.no_shares = 0.0  # la pata NO nunca se confirmó -- no hay capital real ahí
        # cost_usd ya quedó en el costo real de sólo YES (fee incluido) desde que se
        # confirmó esa pata -- no hace falta recalcularlo acá.
        position.fee_paid = 0.0
        position.notes = reason or "Leg imbalance: pata YES llenó, pata NO no confirmó."
        session.commit()

        kill_switch.halt(
            f"leg imbalance real en mercado {position.market_id} -- "
            f"{reason or 'pata YES llenó, pata NO no confirmó'}",
            session=session,
        )

    def _handle_unconfirmed_fill(
        self, session: Session, position: RealPosition, *, leg: str, raw_response: dict | None = None
    ) -> None:
        """`_confirmed_fill` agotó los reintentos sin encontrar el trade real de
        la pata `leg` -- no se puede saber con certeza cuánto llenó ni a qué
        precio. Se trata con la misma severidad que un leg imbalance (ver
        docstring del módulo, incidente del 2026-09-11): no se asume nada
        (ni que calzó, ni que no), se detiene el trading real para revisión
        manual. `yes_shares`/`no_shares`/`cost_usd` quedan en lo último que sí
        se confirmó (la estimación pre-trade si es la pata YES la que no se
        pudo confirmar; el costo real de YES solo si fue la pata NO).

        `raw_response` (2026-09-13, incidente AS Monaco FC): la respuesta cruda
        de `create_and_post_market_order` para la pata `leg`, persistida en
        `detail` -- puramente diagnóstico, no cambia ninguna decisión. Antes de
        esto no quedaba ningún rastro de qué devolvió el exchange en el
        momento del envío para un caso "sin_confirmar" (a diferencia de
        `order_send_failed`, que sí captura la excepción vía `exc_info`, este
        camino no lanza ninguna -- la orden se envió "bien", sólo que
        `get_trades` nunca encontró el trade real). Sirve para verificar en el
        próximo caso si la respuesta trae alguna señal temprana de "no
        matcheó" (`status`/`errorMsg`) que permita saltar los reintentos --
        ver CLAUDE.md, sección Fase 3, investigación pendiente (Paso 2).

        **Bloqueo per-mercado, no global (2026-09-14)**: a diferencia de un
        leg imbalance total o un `leg_size_mismatch` (ver docstring del
        módulo), este incidente ya NO dispara el kill-switch GLOBAL por
        defecto -- el riesgo real está acotado a este `market_id`, y
        `RealExecutionEngine.maybe_execute` lo bloquea vía
        `_market_locked_by_unconfirmed` mientras la fila siga en este status.
        Sólo si la cantidad de posiciones `sin_confirmar` concurrentes cruza
        `REAL_MAX_CONCURRENT_SIN_CONFIRMAR` se trata como señal sistémica y
        SÍ se dispara el halt global (mismo criterio que `auto_recovery_capped`:
        el mensaje deja explícito que es por el tope de concurrencia, no
        porque este caso puntual sea distinto de los demás)."""
        position.status = "sin_confirmar"
        position.notes = (
            f"No se pudo confirmar el fill real de la pata {leg} tras reintentar "
            "get_trades (posible lag de indexación del exchange) -- el sistema NO "
            "puede garantizar que la canasta esté calzada ni el costo real. "
            "Bloquea sólo este mercado (no todo el sistema) mientras se revisa/"
            "auto-recupera."
        )
        session.commit()

        log_event(
            session,
            "fill_not_confirmed",
            "critical",
            f"No se pudo confirmar el fill real de la pata {leg} en {position.question[:60]} "
            "tras reintentar -- posición marcada sin_confirmar, bloqueando sólo este mercado",
            market_id=position.market_id,
            real_position_id=position.id,
            detail={"raw_response": raw_response} if raw_response is not None else None,
        )

        concurrent = _concurrent_sin_confirmar_count(session)
        if concurrent >= settings.real_max_concurrent_sin_confirmar:
            log_event(
                session,
                "sin_confirmar_concurrency_capped",
                "critical",
                f"Tope de concurrencia de sin_confirmar alcanzado ({concurrent}/"
                f"{settings.real_max_concurrent_sin_confirmar}) -- se activa el kill-switch "
                f"GLOBAL como señal sistémica, no porque el mercado {position.market_id} en "
                "particular sea distinto de los demás mercados ya bloqueados",
                market_id=position.market_id,
                real_position_id=position.id,
                detail={
                    "concurrent_sin_confirmar": concurrent,
                    "max_concurrent": settings.real_max_concurrent_sin_confirmar,
                },
            )
            kill_switch.halt(
                f"tope de concurrencia de sin_confirmar alcanzado ({concurrent}/"
                f"{settings.real_max_concurrent_sin_confirmar}) -- posible problema "
                "sistémico, no acotado a un mercado",
                session=session,
            )
        else:
            logger.info(
                "Fill sin confirmar en mercado %s -- se bloquea sólo ESE mercado "
                "(%d/%d sin_confirmar concurrentes), el resto del sistema sigue operando",
                position.market_id,
                concurrent,
                settings.real_max_concurrent_sin_confirmar,
            )
