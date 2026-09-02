"""Sizing de arbitraje intra-mercado — "fórmula de ganancia garantizada" (no Kelly).

El informe técnico distingue baskets bloqueados de arb puro (comprar YES+NO<$1 paga
$1 garantizado al vencimiento sin importar el resultado) de señales con edge incierto
(cuarto de Kelly). Como el arb no tiene incertidumbre probabilística que ponderar, el
tamaño no se calcula con Kelly: se maximiza el tamaño rentable disponible en el book
(ver `execution.simulator`), acotado por límites de exposición — no por probabilidad —
porque igual hay riesgos reales (disputa de oráculo, lock-up de capital hasta
resolución, ejecución no atómica) que justifican no apostar el máximo teórico.
"""
from __future__ import annotations

from polybot.config import settings


def max_capital_for_arb_trade(market_exposure_usd: float, cluster_exposure_usd: float) -> float:
    """Tope de capital (USD) disponible para una nueva posición de arb.

    El menor entre: fracción máxima por trade, y el espacio restante hasta el límite
    de exposición por mercado y por cluster de eventos correlacionados (ver
    CLAUDE.md para el criterio de cluster). Nunca negativo.
    """
    capital_base = settings.arb_capital_base
    caps = [
        capital_base * settings.arb_max_fraction_per_trade,
        capital_base * settings.arb_max_exposure_per_market - market_exposure_usd,
        capital_base * settings.arb_max_exposure_per_cluster - cluster_exposure_usd,
    ]
    return max(0.0, min(caps))
