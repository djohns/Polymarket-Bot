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
como taker, son dos órdenes HTTP separadas -- no hay forma de que el exchange
las ejecute atómicamente. Si la pata YES llena y la pata NO falla (o llena
parcial), queda una posición direccional no intencional con capital real. Se
trata como evento de emergencia: dispara el kill-switch automáticamente (no
se reintenta solo) y registra la posición desbalanceada para revisión manual
-- ver `_handle_leg_imbalance`.
"""
from __future__ import annotations

import logging

from py_clob_client_v2.clob_types import MarketOrderArgsV2, OrderType
from py_clob_client_v2.order_utils.model.side import SideString
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.eligibility import is_fast_resolution_market
from polybot.execution.simulator import simulate_arbitrage_fill
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.persistence.models import RealPosition
from polybot.risk.sizing import max_capital_for_real_trade

logger = logging.getLogger(__name__)


def _real_exposure(session: Session, *, market_id: str | None = None, cluster_id: str | None = None) -> float:
    """Suma `cost_usd` de posiciones REALES abiertas, vía SQL agregado (nunca .all()) --
    mismo patrón que `_open_exposure` en main.py para las simuladas."""
    stmt = select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(RealPosition.status == "abierta")
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


def _order_filled(response: dict) -> bool:
    """Heurística conservadora: sólo se considera llenada una orden si el
    exchange devolvió al menos un hash de transacción de asentamiento. Se
    verifica y ajusta contra la respuesta real de la API durante el setup
    supervisado (ver CLAUDE.md, "antes de la primera orden real")."""
    hashes = response.get("transactionsHashes") or response.get("transactionHashes")
    return bool(hashes)


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
        no_budget = fill.shares * fill.no_price_avg

        logger.info(
            "EJECUCIÓN REAL %s | shares=%.2f yes_budget=%.2f no_budget=%.2f net_pnl_esperado=%.4f",
            market.question[:60],
            fill.shares,
            yes_budget,
            no_budget,
            fill.net_pnl,
        )

        try:
            yes_resp = _place_market_buy(self._client, market.yes_token_id, yes_budget)
        except Exception:
            logger.exception("Fallo al enviar la orden YES real en %s, se aborta el trade", market.question[:60])
            return

        if not _order_filled(yes_resp):
            logger.warning("Orden YES real no llenó en %s, se aborta sin tocar la pata NO", market.question[:60])
            return

        try:
            no_resp = _place_market_buy(self._client, market.no_token_id, no_budget)
        except Exception:
            logger.exception("Fallo al enviar la orden NO real en %s tras llenar YES", market.question[:60])
            self._handle_leg_imbalance(session, market, fill, yes_resp, no_resp=None)
            return

        if not _order_filled(no_resp):
            logger.critical(
                "Orden NO real NO llenó en %s tras llenar YES -- posición desbalanceada con capital real",
                market.question[:60],
            )
            self._handle_leg_imbalance(session, market, fill, yes_resp, no_resp)
            return

        session.add(
            RealPosition(
                market_id=market.condition_id,
                cluster_id=market.cluster_id,
                question=market.question,
                shares=fill.shares,
                yes_price_avg=fill.yes_price_avg,
                no_price_avg=fill.no_price_avg,
                cost_usd=fill.cost_usd,
                fee_paid=fill.fee_estimate,
                net_pnl_expected=fill.net_pnl,
                yes_order_id=yes_resp.get("orderID") or yes_resp.get("orderId"),
                no_order_id=no_resp.get("orderID") or no_resp.get("orderId"),
                yes_tx_hash=str((yes_resp.get("transactionsHashes") or [None])[0]),
                no_tx_hash=str((no_resp.get("transactionsHashes") or [None])[0]),
            )
        )
        session.commit()

    def _handle_leg_imbalance(self, session: Session, market: MarketInfo, fill, yes_resp: dict, no_resp: dict | None) -> None:
        """Registra la pata desbalanceada y detiene todo trading real de inmediato --
        no se intenta deshacer ni recuperar automáticamente (ver CLAUDE.md)."""
        session.add(
            RealPosition(
                market_id=market.condition_id,
                cluster_id=market.cluster_id,
                question=market.question,
                status="pendiente",
                shares=fill.shares,
                yes_price_avg=fill.yes_price_avg,
                no_price_avg=fill.no_price_avg,
                cost_usd=fill.shares * fill.yes_price_avg,  # sólo la pata YES realmente costó capital
                fee_paid=0.0,
                net_pnl_expected=fill.net_pnl,
                yes_order_id=yes_resp.get("orderID") or yes_resp.get("orderId"),
                no_order_id=(no_resp or {}).get("orderID") or (no_resp or {}).get("orderId"),
                yes_tx_hash=str((yes_resp.get("transactionsHashes") or [None])[0]),
                no_tx_hash=str(((no_resp or {}).get("transactionsHashes") or [None])[0]),
            )
        )
        session.commit()
        kill_switch.halt(
            f"leg imbalance real en mercado {market.condition_id} -- pata YES llenó, pata NO no confirmó"
        )
