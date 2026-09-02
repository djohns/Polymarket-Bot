"""Kelly fraccionado — primitiva reusable de sizing para señales con edge incierto.

No se usa hoy: el informe técnico distingue baskets bloqueados de arbitraje puro
(pagan $1 garantizado al vencimiento, sin incertidumbre probabilística que ponderar)
de "todo lo demás" (edge incierto, se dimensiona con cuarto de Kelly). La única
señal ejecutada por el simulador de Fase 2 parte 1 es el arb, que usa en cambio
`risk.sizing.max_capital_for_arb_trade` ("fórmula de ganancia garantizada"). Esta
función queda lista para cuando se ejecute una señal de edge incierto (ej.
favorito-longshot) en una fase futura.
"""
from __future__ import annotations

from polybot.config import settings


def fractional_kelly(true_probability: float, price: float, fraction: float | None = None) -> float:
    """Kelly fraccionado para una apuesta binaria: f* = (p - price) / (1 - price) × fracción.

    `true_probability` es la probabilidad real estimada del outcome; `price` es su
    precio de mercado (0 < price < 1). Es la fórmula estándar de Kelly para una
    apuesta binaria con pago 1:(1-price)/price, referenciada en la literatura que
    cita el informe (Thorp; Matej et al. 2021). Devuelve la fracción del capital a
    arriesgar, acotada a [0, 1] — 0 si el precio no deja edge positivo.
    """
    if fraction is None:
        fraction = settings.kelly_fraction
    if price <= 0.0 or price >= 1.0:
        return 0.0
    edge = (true_probability - price) / (1 - price)
    return max(0.0, min(1.0, edge * fraction))
