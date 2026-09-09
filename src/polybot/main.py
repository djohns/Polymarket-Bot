"""Fase 1: ingesta + detección de señales, sólo logging. Sin ejecución de órdenes."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from py_clob_client_v2 import AssetType, BalanceAllowanceParams, ClobClient
from sqlalchemy import func, select

from polybot.config import CLOB_API_URL, POLYGON_CHAIN_ID, settings
from polybot.execution import kill_switch, reconciliation
from polybot.execution.allowances import ensure_collateral_allowance
from polybot.execution.key_management import load_private_key
from polybot.execution.real_executor import RealExecutionEngine
from polybot.execution.real_resolution_job import resolve_open_real_positions
from polybot.execution.resolution_job import resolve_open_positions
from polybot.execution.simulator import simulate_arbitrage_fill
from polybot.ingestion.gamma_discovery import MarketInfo, fetch_active_markets
from polybot.ingestion.orderbook import OrderBookStore
from polybot.ingestion.ws_client import MarketWebSocketClient
from polybot.persistence.db import get_session, init_db
from polybot.persistence.models import Opportunity, SimulatedPosition
from polybot.signals.arbitrage import detect_arbitrage
from polybot.signals.longshot import detect_longshot_bias

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class SignalEngine:
    def __init__(
        self,
        markets: list[MarketInfo],
        store: OrderBookStore,
        real_engine: RealExecutionEngine | None = None,
    ) -> None:
        self._store = store
        self._real_engine = real_engine
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

            market_exposure = _open_exposure(session, market_id=market.condition_id)
            cluster_exposure = _open_exposure(session, cluster_id=market.cluster_id)
            fill = simulate_arbitrage_fill(market, yes_book, no_book, market_exposure, cluster_exposure)
            if fill is not None:
                logger.info(
                    "FILL SIMULADO %s | shares=%.2f costo=%.2f fee=%.4f slippage=%.4f net_pnl=%.4f",
                    market.question[:60],
                    fill.shares,
                    fill.cost_usd,
                    fill.fee_estimate,
                    fill.slippage_estimate,
                    fill.net_pnl,
                )
                session.add(
                    SimulatedPosition(
                        market_id=market.condition_id,
                        cluster_id=market.cluster_id,
                        question=market.question,
                        shares=fill.shares,
                        yes_price_avg=fill.yes_price_avg,
                        no_price_avg=fill.no_price_avg,
                        yes_price_best=fill.yes_price_best,
                        no_price_best=fill.no_price_best,
                        cost_usd=fill.cost_usd,
                        fee_estimate=fill.fee_estimate,
                        gross_pnl=fill.gross_pnl,
                        slippage_estimate=fill.slippage_estimate,
                        net_pnl=fill.net_pnl,
                        book_snapshot={
                            "yes": yes_book.top_levels(),
                            "no": no_book.top_levels(),
                        },
                    )
                )

            session.commit()

        if self._real_engine is not None:
            try:
                self._real_engine.maybe_execute(market, yes_book, no_book)
            except Exception:
                logger.exception(
                    "Fallo inesperado en ejecución real para %s -- no se reintenta este ciclo",
                    market.question[:60],
                )

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
                    "LONGSHOT_BIAS %s | %s price=%.4f corrected=%.4f -> comprar %s",
                    market.question[:60],
                    sig.outcome,
                    sig.outcome_price,
                    sig.corrected_price,
                    sig.trade_direction,
                )
                session.add(
                    Opportunity(
                        market_id=market.condition_id,
                        question=market.question,
                        signal_type="longshot_bias",
                        outcome=sig.outcome,
                        outcome_price=sig.outcome_price,
                        corrected_price=sig.corrected_price,
                        trade_direction=sig.trade_direction,
                        book_snapshot={
                            "yes": yes_book.top_levels(),
                            "no": no_book.top_levels(),
                        },
                    )
                )
            session.commit()


def _open_exposure(session, *, market_id: str | None = None, cluster_id: str | None = None) -> float:
    """Suma `cost_usd` de posiciones simuladas abiertas, vía SQL agregado (nunca .all())."""
    stmt = select(func.coalesce(func.sum(SimulatedPosition.cost_usd), 0.0)).where(
        SimulatedPosition.status == "abierta"
    )
    if market_id is not None:
        stmt = stmt.where(SimulatedPosition.market_id == market_id)
    if cluster_id is not None:
        stmt = stmt.where(SimulatedPosition.cluster_id == cluster_id)
    return session.execute(stmt).scalar_one()


def _midpoint(book) -> float | None:
    bid, ask = book.best_bid(), book.best_ask()
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


async def _run_ws_session(
    markets: list[MarketInfo], store: OrderBookStore, real_engine: RealExecutionEngine | None = None
) -> asyncio.Task:
    engine = SignalEngine(markets, store, real_engine)
    asset_ids = [tid for m in markets for tid in (m.yes_token_id, m.no_token_id)]
    ws_client = MarketWebSocketClient(asset_ids, store, engine.on_update)
    logger.info("Sesión WS: %d mercados, %d assets suscritos", len(markets), len(asset_ids))
    return asyncio.create_task(ws_client.run())


async def resolution_loop() -> None:
    """Job periódico e independiente del loop de ingesta/detección (Fase 2, parte 2):
    consulta la resolución real de los mercados con posiciones de arb abiertas o
    pendientes, cierra las que ya resolvieron con su P&L realizado, y aprovecha esa
    misma consulta para alimentar Brier score si el mercado también tuvo señales
    longshot. Corre en el mismo event loop que la ingesta WS, pero sin bloquearla:
    la consulta HTTP es async (`httpx.AsyncClient`), así que cede el control en cada
    `await` en vez de trabar el heartbeat/reconexión del WebSocket.

    También resuelve `RealPosition` (Fase 3, agregado tras el incidente del
    2026-09-09: sin esto, una posición real que resolvía y se redimía on-chain
    quedaba `"abierta"` para siempre, y la reconciliación automática disparaba
    el kill-switch comparando contra ese estado ya desactualizado). Mismo ciclo,
    misma sesión -- no hace falta un loop aparte.
    """
    stale_after = dt.timedelta(days=settings.resolution_stale_after_days)
    warned_stale: set[int] = set()
    while True:
        try:
            with get_session() as session:
                await resolve_open_positions(session, stale_after=stale_after, warned_stale=warned_stale)
                await resolve_open_real_positions(session)
        except Exception:
            logger.exception("Fallo en el ciclo de resolución de mercados, se reintenta en el próximo ciclo")
        await asyncio.sleep(settings.resolution_check_interval_seconds)


def _build_real_execution_engine() -> RealExecutionEngine | None:
    """Construye el cliente CLOB autenticado L2 y el motor de ejecución real
    (Fase 3). Devuelve None si `REAL_TRADING_ENABLED` no está en true -- el
    resto del sistema sigue funcionando en modo Fase 1/2 sin cambios.

    La private key sólo existe en memoria de este proceso a partir de acá
    (ver `execution.key_management`); nunca se loguea ni se vuelve a escribir
    a disco.
    """
    if not settings.real_trading_enabled:
        logger.info("REAL_TRADING_ENABLED=false -- Fase 3 desactivada, sólo paper trading")
        return None

    if kill_switch.is_halted():
        logger.critical(
            "Kill-switch ya está activo al arrancar (%s) -- no se construye el motor de ejecución real",
            settings.real_kill_switch_flag_path,
        )
        return None

    if not settings.real_funder_address:
        logger.critical(
            "REAL_FUNDER_ADDRESS no está configurado -- sin la proxy wallet (Safe Wallet) "
            "correcta el cliente operaría contra la EOA, que no tiene el pUSD real. "
            "Fase 3 no arranca."
        )
        return None

    private_key = load_private_key(settings.real_encrypted_key_path, settings.real_key_passphrase_env_var)
    creds_ok = all([settings.clob_api_key, settings.clob_api_secret, settings.clob_api_passphrase])
    from py_clob_client_v2 import ApiCreds

    creds = (
        ApiCreds(
            api_key=settings.clob_api_key,
            api_secret=settings.clob_api_secret,
            api_passphrase=settings.clob_api_passphrase,
        )
        if creds_ok
        else None
    )
    # signature_type/funder: la cuenta se conectó con una wallet externa (MetaMask), a la
    # que Polymarket le asigna una proxy wallet -- ahí vive el pUSD real, no en la EOA
    # firmante. El valor de signature_type se verificó empíricamente contra el balance
    # real (no se asumió de la documentación general) -- ver CLAUDE.md, sección Fase 3,
    # para el detalle completo de por qué es POLY_1271=3 y no POLY_GNOSIS_SAFE=2.
    client = ClobClient(
        host=CLOB_API_URL,
        chain_id=POLYGON_CHAIN_ID,
        key=private_key,
        creds=creds,
        signature_type=settings.real_signature_type,
        funder=settings.real_funder_address,
    )
    del private_key  # no queda ninguna otra referencia en este scope

    if creds is None:
        creds = client.create_or_derive_api_key()
        client.set_api_creds(creds)

    if not ensure_collateral_allowance(client):
        logger.critical("Allowance de COLLATERAL no operable -- Fase 3 no arranca")
        return None

    logger.warning(
        "FASE 3 ACTIVA: ejecución real habilitada (capital base $%.2f, tope $%.2f/mercado, $%.2f/cluster)",
        settings.real_capital_base_usd,
        settings.real_max_exposure_per_market_usd,
        settings.real_max_exposure_per_cluster_usd,
    )
    return RealExecutionEngine(client, get_session)


async def real_balance_kill_switch_loop(client) -> None:
    """Chequeo periódico e independiente del balance real de USDC -- si cae bajo
    `REAL_KILL_SWITCH_BALANCE_FLOOR_USD`, activa el kill-switch automático (ver
    `execution.kill_switch`). Corre en el mismo event loop que todo lo demás,
    pero la llamada HTTP del cliente CLOB no bloquea porque se ejecuta en un
    executor aparte (`asyncio.to_thread`) -- el SDK es síncrono.

    También corre acá la reconciliación de balance (`execution.reconciliation`,
    agregada tras el incidente del 2026-09-08): mismo balance ya consultado,
    se reusa para comparar contra lo que `real_positions` implica que debería
    haber, sin pagar una consulta HTTP extra.
    """
    while True:
        try:
            balance = await asyncio.to_thread(
                client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            current_balance = float(balance.get("balance", 0) or 0) / 1_000_000  # USDC, 6 decimales
            with get_session() as session:
                reconciliation.check_balance_reconciliation(session, current_balance)
                kill_switch.check_balance_kill_switch(current_balance, session=session)
        except Exception:
            logger.exception("Fallo consultando balance real, se reintenta en el próximo ciclo")
        await asyncio.sleep(settings.real_balance_check_interval_seconds)


async def run() -> None:
    init_db()
    store = OrderBookStore()

    markets = fetch_active_markets(settings.discovery_market_limit)
    if not markets:
        logger.error("No se encontraron mercados binarios activos, abortando.")
        return

    real_engine = _build_real_execution_engine()
    if real_engine is not None:
        asyncio.create_task(real_balance_kill_switch_loop(real_engine._client))

    logger.info("Arrancando ingesta, arb_threshold=%.3f", settings.arb_threshold)
    current_ids = {m.condition_id for m in markets}
    ws_task = await _run_ws_session(markets, store, real_engine)
    asyncio.create_task(resolution_loop())

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
            ws_task = await _run_ws_session(new_markets, store, real_engine)

            active_asset_ids = {
                tid for m in new_markets for tid in (m.yes_token_id, m.no_token_id)
            }
            purged = store.keep_only(active_asset_ids)
            if purged:
                logger.info("OrderBookStore: purgados %d books de assets fuera del set activo", purged)


if __name__ == "__main__":
    asyncio.run(run())
