"""Fase 2, parte 2: Brier score de calibración.

Lectura sobre si aplica a arb puro (pedida explícitamente, ver CLAUDE.md,
sección "Fase 2, parte 2"): NO. El arb "long" (comprar YES+NO<$1) no tiene una
predicción direccional -- paga $1 al vencimiento sin importar qué outcome gane,
así que no existe una "probabilidad implícita" que evaluar contra el resultado.
Puntuar eso con Brier sería calificar una predicción que nunca se hizo. La
única señal que sí produce una probabilidad implícita real es el sesgo
favorito-longshot (`Opportunity.corrected_price`), así que el Brier score se
calcula ahí, cruzando contra `SignalResolution` (ver `execution/resolution_job`
para cómo se completa esa tabla, y su cobertura parcial por diseño).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from polybot.persistence.models import Opportunity, SignalResolution


def brier_score(predictions: list[tuple[float, bool]]) -> float | None:
    """`predictions`: lista de (probabilidad predicha de que el outcome ocurra, ocurrió).

    None si la lista está vacía (no hay nada que puntuar todavía).
    """
    if not predictions:
        return None
    total = sum((p - (1.0 if happened else 0.0)) ** 2 for p, happened in predictions)
    return total / len(predictions)


def brier_score_by_window(
    predictions: list[tuple[dt.datetime, float, bool]], window: str = "day"
) -> dict[str, tuple[float | None, int]]:
    """Agrupa por fecha (`window="day"`) o semana ISO (`window="week"`).

    Devuelve {ventana: (brier_score, n_muestras)}, ordenado por ventana.
    """
    buckets: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for ts, p, happened in predictions:
        if window == "week":
            iso = ts.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = ts.date().isoformat()
        buckets[key].append((p, happened))
    return {key: (brier_score(vals), len(vals)) for key, vals in sorted(buckets.items())}


def longshot_brier_report(session: Session, window: str = "day") -> dict[str, tuple[float | None, int]]:
    """Brier score de las señales longshot cuyo mercado ya resolvió.

    Cobertura parcial por diseño (Fase 2, parte 2): sólo cubre mercados que
    también tuvieron una posición de arb resuelta (ver `SignalResolution`), no
    la población completa de mercados con señal longshot. Ampliar la cobertura
    a todos los mercados longshot es una fase futura si se necesita
    calibración representativa antes de que la señal sea ejecutable.
    """
    rows = session.execute(
        select(
            Opportunity.detected_at,
            Opportunity.corrected_price,
            Opportunity.outcome,
            SignalResolution.resolved_outcome,
        )
        .join(SignalResolution, SignalResolution.market_id == Opportunity.market_id)
        .where(Opportunity.signal_type == "longshot_bias", Opportunity.outcome.is_not(None))
    ).all()

    predictions = [
        (detected_at, corrected_price, outcome == resolved_outcome)
        for detected_at, corrected_price, outcome, resolved_outcome in rows
    ]
    return brier_score_by_window(predictions, window=window)
