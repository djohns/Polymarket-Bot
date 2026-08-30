"""Motor de señales v1: arbitraje intra-mercado (YES_ask + NO_ask < $1)."""
from __future__ import annotations

from dataclasses import dataclass

from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.signals.fees import taker_fee


@dataclass(frozen=True)
class ArbitrageOpportunity:
    market: MarketInfo
    yes_price: float
    no_price: float
    gross_spread: float
    fee_estimate: float
    net_spread: float


def detect_arbitrage(
    market: MarketInfo,
    yes_book: OrderBook,
    no_book: OrderBook,
    threshold: float,
) -> ArbitrageOpportunity | None:
    """Compra simultánea de 1 YES + 1 NO al mejor ask. Ambas patas son taker (cruzan el spread)."""
    yes_ask = yes_book.best_ask()
    no_ask = no_book.best_ask()
    if yes_ask is None or no_ask is None:
        return None

    gross_spread = 1 - (yes_ask + no_ask)
    if gross_spread <= threshold:
        return None

    fee_estimate = taker_fee(1, yes_ask, market) + taker_fee(1, no_ask, market)
    net_spread = gross_spread - fee_estimate

    return ArbitrageOpportunity(
        market=market,
        yes_price=yes_ask,
        no_price=no_ask,
        gross_spread=gross_spread,
        fee_estimate=fee_estimate,
        net_spread=net_spread,
    )
