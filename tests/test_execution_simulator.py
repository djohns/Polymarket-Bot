from __future__ import annotations

from polybot.execution.simulator import simulate_arbitrage_fill
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook

MARKET = MarketInfo(
    condition_id="0xabc",
    question="¿Test?",
    yes_token_id="yes",
    no_token_id="no",
    fee_rate=0.0,
    fee_exponent=1.0,
    fees_enabled=False,
    cluster_id="event-1",
)


def _book(asset_id: str, levels: dict[float, float]) -> OrderBook:
    return OrderBook(asset_id=asset_id, asks=dict(levels))


def test_fills_only_best_level_when_budget_and_depth_allow_it():
    yes_book = _book("yes", {0.40: 1000.0})
    no_book = _book("no", {0.50: 1000.0})

    fill = simulate_arbitrage_fill(MARKET, yes_book, no_book, 0.0, 0.0)

    assert fill is not None
    # max_capital_for_arb_trade con defaults: 1000 * 0.05 = 50 USD de tope por trade
    assert fill.cost_usd <= 50.0 + 1e-9
    assert fill.yes_price_avg == 0.40
    assert fill.no_price_avg == 0.50
    assert fill.slippage_estimate == 0.0


def test_walks_multiple_levels_and_reports_slippage():
    yes_book = _book("yes", {0.40: 10.0, 0.42: 1000.0})
    no_book = _book("no", {0.50: 1000.0})

    fill = simulate_arbitrage_fill(MARKET, yes_book, no_book, 0.0, 0.0)

    assert fill is not None
    assert fill.shares > 10.0  # tuvo que cruzar al segundo nivel de YES
    assert fill.yes_price_avg > 0.40  # promedio ponderado, no el mejor precio
    assert fill.slippage_estimate > 0.0


def test_stops_when_marginal_edge_turns_negative():
    yes_book = _book("yes", {0.40: 1.0, 0.65: 1000.0})
    no_book = _book("no", {0.50: 1000.0})

    fill = simulate_arbitrage_fill(MARKET, yes_book, no_book, 0.0, 0.0)

    assert fill is not None
    # 0.65 + 0.50 = 1.15 > 1: no debe cruzar al segundo nivel de YES
    assert fill.shares <= 1.0 + 1e-9
    assert fill.yes_price_avg == 0.40


def test_returns_none_when_market_exposure_cap_exhausted():
    yes_book = _book("yes", {0.40: 1000.0})
    no_book = _book("no", {0.50: 1000.0})

    # límite por mercado (10% de 1000 = 100 USD) ya consumido por completo
    fill = simulate_arbitrage_fill(MARKET, yes_book, no_book, 100.0, 0.0)

    assert fill is None


def test_returns_none_without_depth_on_either_side():
    yes_book = _book("yes", {})
    no_book = _book("no", {0.50: 10.0})

    assert simulate_arbitrage_fill(MARKET, yes_book, no_book, 0.0, 0.0) is None
