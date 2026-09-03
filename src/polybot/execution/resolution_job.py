"""Fase 2, parte 2: orquesta el cierre de posiciones simuladas al resolver su mercado.

Corre como job periódico independiente del loop de ingesta/detección (ver
`main.py::resolution_loop`), consultando sólo los mercados con posiciones de
arb abiertas/pendientes -- un set chico y acotado por los límites de exposición
de `risk/sizing.py`, no toda la población de mercados vistos.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from polybot.execution.resolution import ResolutionResult, fetch_market_resolution
from polybot.persistence.models import Opportunity, SignalResolution, SimulatedPosition

logger = logging.getLogger(__name__)


async def resolve_open_positions(
    session: Session,
    *,
    stale_after: dt.timedelta,
    warned_stale: set[int],
    fetch=fetch_market_resolution,
) -> None:
    open_positions = (
        session.execute(
            select(SimulatedPosition).where(SimulatedPosition.status.in_(("abierta", "pendiente")))
        )
        .scalars()
        .all()
    )
    if not open_positions:
        return

    by_market: dict[str, list[SimulatedPosition]] = {}
    for pos in open_positions:
        by_market.setdefault(pos.market_id, []).append(pos)

    now = dt.datetime.now(dt.UTC)
    for market_id, positions in by_market.items():
        result = await fetch(market_id)
        if result is None:
            continue  # error de red -- se reintenta en el próximo ciclo

        if not result.found:
            logger.warning(
                "Mercado %s no encontrado en CLOB al chequear resolución (¿purgado/archivado?)", market_id
            )
            continue

        if result.resolved:
            for pos in positions:
                _close_position(pos, result.winning_outcome, now)
            _backfill_longshot_resolution(session, market_id, result.winning_outcome, now)
            continue

        if not result.closed:
            continue  # aún en curso, nada que hacer

        _flag_stale_if_needed(positions, result, now, stale_after, warned_stale)

    session.commit()


def _close_position(pos: SimulatedPosition, winning_outcome: str, now: dt.datetime) -> None:
    realized = pos.shares - pos.cost_usd - pos.fee_estimate
    if abs(realized - pos.net_pnl) > 1e-6:
        logger.warning(
            "Posición %d (mercado %s): P&L realizado (%.6f) difiere del esperado al fill (%.6f) -- revisar",
            pos.id,
            pos.market_id,
            realized,
            pos.net_pnl,
        )
    pos.status = "cerrada"
    pos.resolved_outcome = winning_outcome
    pos.resolved_at = now
    pos.realized_pnl = realized
    logger.info(
        "POSICIÓN RESUELTA %s | outcome=%s realized_pnl=%.4f",
        pos.question[:60],
        winning_outcome,
        realized,
    )


def _flag_stale_if_needed(
    positions: list[SimulatedPosition],
    result: ResolutionResult,
    now: dt.datetime,
    stale_after: dt.timedelta,
    warned_stale: set[int],
) -> None:
    for pos in positions:
        opened_at = pos.opened_at
        if opened_at.tzinfo is None:
            # SQLite no conserva tzinfo en round-trip vía SQLAlchemy (DateTime(timezone=True)
            # se guarda pero vuelve naive al releer tras expirar la sesión) -- siempre se
            # escribe en UTC (ver default de `opened_at` en el modelo), así que reetiquetar
            # alcanza; no es una conversión real de huso horario.
            opened_at = opened_at.replace(tzinfo=dt.UTC)
        age = now - opened_at
        if age < stale_after:
            continue
        pos.status = "pendiente"
        if pos.id in warned_stale:
            continue
        warned_stale.add(pos.id)
        logger.warning(
            "Posición %d (mercado %s) sigue sin resolver %s después de abierta (cerrado=%s) "
            "-- posible disputa de oráculo UMA u oráculo pendiente; se sigue reintentando.",
            pos.id,
            pos.market_id,
            age,
            result.closed,
        )


def _backfill_longshot_resolution(
    session: Session, market_id: str, winning_outcome: str, now: dt.datetime
) -> None:
    """Aprovecha esta misma resolución (ya consultada para el arb) para alimentar
    Brier score si el mercado también tuvo señales longshot. Cobertura parcial
    por diseño -- ver CLAUDE.md."""
    already = session.execute(
        select(SignalResolution.id).where(SignalResolution.market_id == market_id)
    ).first()
    if already:
        return

    has_longshot = session.execute(
        select(Opportunity.id)
        .where(Opportunity.market_id == market_id, Opportunity.signal_type == "longshot_bias")
        .limit(1)
    ).first()
    if not has_longshot:
        return

    session.add(SignalResolution(market_id=market_id, resolved_outcome=winning_outcome, resolved_at=now))
