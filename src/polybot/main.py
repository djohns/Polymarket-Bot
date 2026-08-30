"""Fase 1: ingesta + detección de señales, sólo logging. Sin ejecución de órdenes."""
from __future__ import annotations

import asyncio
import logging
import time

from polybot.config import settings
from polybot.ingestion.gamma_discovery import MarketInfo, fetch_active_markets
from polybot.ingestion.orderbook import OrderBookStore
from polybot.ingestion.ws_client import MarketWebSocketClient
from polybot.persistence.db import get_session, init_db
from polybot.persistence.models import Opportunity
from polybot.signals.arbitrage import detect_arbitrage
from polybot.signals.longshot import detect_longshot_bias

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, markets: list[MarketInfo], store: OrderBookStore) -> None:
        self._store = store
        self._by_token: dict[str, MarketInfo] = {}
        for m in markets:
            self._by_token[m.yes_token_id] = m
            self._by_token[m.no_token_id] = m
        self._last_logged: dict[tuple[str, str], float] = {}

    def _cooldown_ok(self, market_id: str, signal_type: str) -> bool:
        key = (market_id, signal_type)
        now = time.monotonic()
        last = self._last_logged.get(key, 0.0)
        if now - last < settings.opportunity_log_cooldown_seconds:
            return False
        self._last_logged[key] = now
        return True

    async def on_update(self, affected_asset_ids: set[str]) -> None:
        affected_markets = {
            self._by_token[a] for a in affected_asset_ids if a in self._by_token
        }
        for market in affected_markets:
            yes_book = self._store.get(market.yes_token_id)
            no_book = self._store.get(market.no_token_id)
            if yes_book is None or no_book is None:
                continue

            self._check_arbitrage(market, yes_book, no_book)
            self._check_longshot(market, yes_book, no_book)

    def _check_arbitrage(self, market, yes_book, no_book) -> None:
        opp = detect_arbitrage(market, yes_book, no_book, settings.arb_threshold)
        if opp is None or not self._cooldown_ok(market.condition_id, "arbitrage"):
            return

        logger.info(
            "ARBITRAJE %s | yes=%.4f no=%.4f gross=%.4f fee=%.4f net=%.4f",
            market.question[:60],
            opp.yes_price,
            opp.no_price,
            opp.gross_spread,
            opp.fee_estimate,
            opp.net_spread,
        )
        with get_session() as session:
            session.add(
                Opportunity(
                    market_id=market.condition_id,
                    question=market.question,
                    signal_type="arbitrage",
                    yes_price=opp.yes_price,
                    no_price=opp.no_price,
                    gross_spread=opp.gross_spread,
                    fee_estimate=opp.fee_estimate,
                    net_spread=opp.net_spread,
                    book_snapshot={
                        "yes": yes_book.top_levels(),
                        "no": no_book.top_levels(),
                    },
                )
            )
            session.commit()

    def _check_longshot(self, market, yes_book, no_book) -> None:
        yes_mid = _midpoint(yes_book)
        no_mid = _midpoint(no_book)
        signals = detect_longshot_bias(
            market,
            yes_mid,
            no_mid,
            settings.longshot_price_low,
            settings.longshot_price_high,
            settings.longshot_correction,
        )
        if not signals or not self._cooldown_ok(market.condition_id, "longshot_bias"):
            return

        with get_session() as session:
            for sig in signals:
                logger.info(
                    "LONGSHOT_BIAS %s | %s price=%.4f corrected=%.4f",
                    market.question[:60],
                    sig.outcome,
                    sig.outcome_price,
                    sig.corrected_price,
                )
                session.add(
                    Opportunity(
                        market_id=market.condition_id,
                        question=market.question,
                        signal_type="longshot_bias",
                        outcome_price=sig.outcome_price,
                        corrected_price=sig.corrected_price,
                        book_snapshot={
                            "yes": yes_book.top_levels(),
                            "no": no_book.top_levels(),
                        },
                    )
                )
            session.commit()


def _midpoint(book) -> float | None:
    bid, ask = book.best_bid(), book.best_ask()
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


async def _run_ws_session(
    markets: list[MarketInfo], store: OrderBookStore
) -> asyncio.Task:
    engine = SignalEngine(markets, store)
    asset_ids = [tid for m in markets for tid in (m.yes_token_id, m.no_token_id)]
    ws_client = MarketWebSocketClient(asset_ids, store, engine.on_update)
    logger.info("Sesión WS: %d mercados, %d assets suscritos", len(markets), len(asset_ids))
    return asyncio.create_task(ws_client.run())


async def run() -> None:
    init_db()
    store = OrderBookStore()

    markets = fetch_active_markets(settings.discovery_market_limit)
    if not markets:
        logger.error("No se encontraron mercados binarios activos, abortando.")
        return

    logger.info("Arrancando ingesta, arb_threshold=%.3f", settings.arb_threshold)
    current_ids = {m.condition_id for m in markets}
    ws_task = await _run_ws_session(markets, store)

    while True:
        await asyncio.sleep(settings.discovery_interval_seconds)
        try:
            new_markets = fetch_active_markets(settings.discovery_market_limit)
        except Exception:
            logger.exception("Fallo en re-descubrimiento de Gamma, se mantiene el set actual")
            continue

        new_ids = {m.condition_id for m in new_markets}
        if new_ids != current_ids:
            logger.info(
                "Set de mercados cambió (%d -> %d), reiniciando sesión WS",
                len(current_ids),
                len(new_ids),
            )
            ws_task.cancel()
            current_ids = new_ids
            ws_task = await _run_ws_session(new_markets, store)


if __name__ == "__main__":
    asyncio.run(run())
