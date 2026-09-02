"""Fase 2, parte 1: simulador de ejecución de arbitraje intra-mercado.

Simula un fill hipotético contra el order book real en el momento de la detección —
ninguna orden se firma ni se envía. Camina los niveles reales de ask de ambos lados
(YES y NO) en paralelo (1 share de cada uno por unidad de arb) para estimar el
slippage cuando el tamaño de la posición consume más de un nivel: el precio marginal
de cada libro es no decreciente a medida que se avanza en profundidad, así que se
sigue acumulando tamaño mientras el borde neto (precio marginal + fee, contra el
payout de $1 garantizado) siga siendo positivo, o hasta agotar el tope de capital de
`risk.sizing.max_capital_for_arb_trade`.
"""
from __future__ import annotations

from dataclasses import dataclass

from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.risk.sizing import max_capital_for_arb_trade
from polybot.signals.fees import taker_fee


@dataclass(frozen=True)
class SimulatedFill:
    shares: float
    yes_price_avg: float
    no_price_avg: float
    yes_price_best: float
    no_price_best: float
    cost_usd: float
    fee_estimate: float
    gross_pnl: float
    slippage_estimate: float
    net_pnl: float


def simulate_arbitrage_fill(
    market: MarketInfo,
    yes_book: OrderBook,
    no_book: OrderBook,
    market_exposure_usd: float,
    cluster_exposure_usd: float,
) -> SimulatedFill | None:
    yes_levels = sorted(yes_book.asks.items())
    no_levels = sorted(no_book.asks.items())
    if not yes_levels or not no_levels:
        return None

    yes_best, no_best = yes_levels[0][0], no_levels[0][0]

    max_cost = max_capital_for_arb_trade(market_exposure_usd, cluster_exposure_usd)
    if max_cost <= 0:
        return None

    yi = ni = 0
    yes_remaining = yes_levels[0][1]
    no_remaining = no_levels[0][1]
    shares = yes_cost = no_cost = fee = 0.0

    while yi < len(yes_levels) and ni < len(no_levels):
        yp, np_ = yes_levels[yi][0], no_levels[ni][0]
        marginal_price = yp + np_
        marginal_fee = taker_fee(1, yp, market) + taker_fee(1, np_, market)
        if marginal_price + marginal_fee >= 1:
            break  # a esta profundidad el borde ya no es positivo (precios sólo suben)

        step = min(yes_remaining, no_remaining)
        remaining_budget = max_cost - (yes_cost + no_cost)
        affordable = remaining_budget / marginal_price if marginal_price > 0 else step
        take = min(step, affordable)
        if take <= 0:
            break

        shares += take
        yes_cost += take * yp
        no_cost += take * np_
        fee += take * marginal_fee
        yes_remaining -= take
        no_remaining -= take

        if take < step:
            break  # presupuesto agotado a mitad de nivel

        if yes_remaining <= 0:
            yi += 1
            yes_remaining = yes_levels[yi][1] if yi < len(yes_levels) else 0.0
        if no_remaining <= 0:
            ni += 1
            no_remaining = no_levels[ni][1] if ni < len(no_levels) else 0.0

    if shares <= 0:
        return None

    cost_usd = yes_cost + no_cost
    gross_pnl = shares - cost_usd  # payout garantizado: $1 por share (par YES+NO) al vencimiento
    slippage_estimate = cost_usd - shares * (yes_best + no_best)
    net_pnl = gross_pnl - fee

    return SimulatedFill(
        shares=shares,
        yes_price_avg=yes_cost / shares,
        no_price_avg=no_cost / shares,
        yes_price_best=yes_best,
        no_price_best=no_best,
        cost_usd=cost_usd,
        fee_estimate=fee,
        gross_pnl=gross_pnl,
        slippage_estimate=slippage_estimate,
        net_pnl=net_pnl,
    )
