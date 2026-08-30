"""Estimación de comisiones taker por mercado.

Usa el `feeSchedule` que Gamma devuelve por mercado (rate, exponent) en vez de la
tabla estática de tasas por categoría del informe técnico: es la misma fórmula
documentada ahí (fee = shares × rate × (price × (1−price))^exponent, verificada
con el ejemplo de crypto: rate=0.07 → $1.75 por 100 shares a 50¢), pero leída en
vivo por mercado. Ver CLAUDE.md, sección "NO volver a explorar esto".
"""
from __future__ import annotations

from polybot.ingestion.gamma_discovery import MarketInfo


def taker_fee(shares: float, price: float, market: MarketInfo) -> float:
    """Fee estimada (en USDC) por comprar `shares` como taker a `price`.

    Los makers pagan 0% (y reciben rebate), por eso el arbitraje intra-mercado
    -que requiere cruzar el spread en ambas patas- se estima siempre a tasa taker.
    """
    if not market.fees_enabled or market.fee_rate <= 0:
        return 0.0
    return shares * market.fee_rate * (price * (1 - price)) ** market.fee_exponent
