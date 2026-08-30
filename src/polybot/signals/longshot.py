"""Motor de señales v2: sesgo favorito-longshot. Sólo señal informativa, no ejecutable (Fase 1).

El informe técnico da la regla de cálculo literal ("corrección de 3-8% hacia 0,50
para contratos <$0,15 o >$0,85") pero mezcla dos efectos académicos con
implicaciones direccionales distintas: el sesgo clásico favorito-longshot dice
que los longshots están sobrevalorados (precio de mercado > prob. real, es decir
la corrección debería alejarse de 0,50), mientras la corrección por aversión al
riesgo (CRRA, Wolfers/Zitzewitz/Manski) dice que el precio de mercado ya está
sesgado hacia los extremos y hay que corregir hacia 0,50. Esta ambigüedad queda
sin resolver en el informe; se implementa literalmente la instrucción de cálculo
("hacia 0,50") y se loguean ambos valores (precio de mercado y corregido) para
que se revise la dirección de trading en el chat del proyecto antes de Fase 2.
"""
from __future__ import annotations

from dataclasses import dataclass

from polybot.ingestion.gamma_discovery import MarketInfo


@dataclass(frozen=True)
class LongshotSignal:
    market: MarketInfo
    outcome: str  # "YES" | "NO"
    outcome_price: float
    corrected_price: float


def corrected_probability(price: float, correction: float) -> float:
    if price < 0.5:
        return min(price + correction, 0.5)
    return max(price - correction, 0.5)


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
                )
            )
    return signals
