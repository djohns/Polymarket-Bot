"""Filtro de elegibilidad para ejecución real (Fase 3).

Alcance deliberadamente acotado: sólo arb intra-mercado en mercados de
"resolución rápida", definidos como deportes (la única categoría con evidencia
empírica de resolución <24h en los datos de Fase 2 -- ver auditorías de Fase 2
en el historial del proyecto). Gamma expone `sportsMarketType` (ej. "moneyline")
directamente en cada mercado deportivo y en ningún otro -- se usa ese campo
como señal directa en vez de inferir por categoría/tags/NLP, mismo criterio que
ya se usó para `feeSchedule` en Fase 1 (preferir el campo que la API ya da en
vivo sobre heurísticas propias). Todo lo que no califica sigue sólo en el
simulador de Fase 2, sin excepción -- este filtro es la única puerta de entrada
al motor de ejecución real.
"""
from __future__ import annotations

from polybot.ingestion.gamma_discovery import MarketInfo


def is_fast_resolution_market(market: MarketInfo) -> bool:
    return market.sports_market_type is not None
