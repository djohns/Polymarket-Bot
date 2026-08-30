"""Motor de señales v2: sesgo favorito-longshot. Sólo señal informativa, no ejecutable (Fase 1).

Dirección de trading confirmada (chat del proyecto): se apuesta contra el sesgo
en cada extremo, no se corrige "hacia 0,50" en términos de acción.
- Longshot caro/sobrevalorado (precio < `price_low`) -> comprar el outcome
  contrario (el mercado paga de más por el longshot; se apuesta a que NO gana).
- Favorito barato/infravalorado (precio > `price_high`) -> comprar ese mismo
  outcome (el mercado paga de menos por el favorito).

El cálculo de magnitud (`corrected_probability`, corrección hacia 0,50) se
mantiene igual a como estaba: sólo estima cuánto se desvía el precio de una
probabilidad "corregida", no determina la dirección de trading.
"""
from __future__ import annotations

from dataclasses import dataclass

from polybot.ingestion.gamma_discovery import MarketInfo

OTHER_OUTCOME = {"YES": "NO", "NO": "YES"}


@dataclass(frozen=True)
class LongshotSignal:
    market: MarketInfo
    outcome: str  # "YES" | "NO" -- outcome cuyo precio disparó la señal
    outcome_price: float
    corrected_price: float
    trade_direction: str  # "YES" | "NO" -- outcome que la señal sugiere comprar


def corrected_probability(price: float, correction: float) -> float:
    if price < 0.5:
        return min(price + correction, 0.5)
    return max(price - correction, 0.5)


def _trade_direction(outcome: str, price: float, price_low: float) -> str:
    is_longshot = price < price_low
    return OTHER_OUTCOME[outcome] if is_longshot else outcome


def detect_longshot_bias(
    market: MarketInfo,
    yes_price: float | None,
    no_price: float | None,
    price_low: float,
    price_high: float,
    correction: float,
) -> list[LongshotSignal]:
    signals: list[LongshotSignal] = []
    for outcome, price in (("YES", yes_price), ("NO", no_price)):
        if price is None:
            continue
        if price < price_low or price > price_high:
            signals.append(
                LongshotSignal(
                    market=market,
                    outcome=outcome,
                    outcome_price=price,
                    corrected_price=corrected_probability(price, correction),
                    trade_direction=_trade_direction(outcome, price, price_low),
                )
            )
    return signals
