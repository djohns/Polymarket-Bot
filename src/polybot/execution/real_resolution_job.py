"""Resolución real de mercados para `RealPosition` (Fase 3, corrección post-incidente).

Mismo patrón que `resolution_job.py` para `SimulatedPosition` (Fase 2, parte 2)
-- ver ahí para el detalle de por qué se usa `GET /markets/{condition_id}` de
CLOB en vez de Gamma. Se agregó recién ahora porque hasta este punto
`RealPosition` no tenía ningún job de resolución propio: una posición real que
resolvía y se redimía on-chain se quedaba marcada `"abierta"` para siempre,
haciendo que la reconciliación automática (`execution/reconciliation.py`)
comparara contra un estado desactualizado y disparara el kill-switch por una
causa en realidad benigna (ver CLAUDE.md, sección Fase 3, "divergencia de
reconciliación del 2026-09-09").

Dos tipos de posición real, con payout distinto al resolver:
- `status="abierta"`: ambas patas (YES y NO) llenaron -- basket de arb
  completo, payout garantizado de $1/share sin importar el resultado (mismo
  cálculo que `SimulatedPosition`).
- `status="pendiente"` (leg imbalance): sólo la pata YES tiene capital real
  comprometido -- el payout depende del resultado real (gana si YES resultó
  ganador, pierde todo si no).
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from polybot.execution.event_log import log_event
from polybot.execution.resolution import fetch_market_resolution
from polybot.persistence.models import RealPosition

logger = logging.getLogger(__name__)


async def resolve_open_real_positions(session: Session, *, fetch=fetch_market_resolution) -> None:
    open_positions = (
        session.execute(select(RealPosition).where(RealPosition.status.in_(("abierta", "pendiente"))))
        .scalars()
        .all()
    )
    if not open_positions:
        return

    by_market: dict[str, list[RealPosition]] = {}
    for pos in open_positions:
        by_market.setdefault(pos.market_id, []).append(pos)

    now = dt.datetime.now(dt.UTC)
    for market_id, positions in by_market.items():
        result = await fetch(market_id)
        if result is None:
            continue  # error de red -- se reintenta en el próximo ciclo

        if not result.found:
            logger.warning(
                "Mercado real %s no encontrado en CLOB al chequear resolución (¿purgado/archivado?)", market_id
            )
            continue

        if not result.resolved:
            continue  # aún en curso (o cerrado sin ganador único todavía) -- nada que hacer

        for pos in positions:
            _close_real_position(session, pos, result.winning_outcome, now)

    session.commit()


def _close_real_position(session: Session, pos: RealPosition, winning_outcome: str, now: dt.datetime) -> None:
    was_leg_imbalance = pos.status == "pendiente"
    if was_leg_imbalance:
        # Sólo la pata YES tiene capital real -- el payout depende del resultado.
        realized = (pos.shares - pos.cost_usd) if winning_outcome == "YES" else -pos.cost_usd
    else:
        # Basket completo (ambas patas llenaron) -- payout garantizado de $1/share,
        # mismo cálculo que SimulatedPosition._close_position.
        realized = pos.shares - pos.cost_usd - pos.fee_paid

    pos.status = "cerrada"
    pos.resolved_outcome = winning_outcome
    pos.resolved_at = now
    pos.realized_pnl = realized

    log_event(
        session,
        "position_resolved",
        "info",
        f"POSICIÓN REAL RESUELTA {pos.question[:60]} | outcome={winning_outcome} "
        f"realized_pnl={realized:.4f}{' (leg imbalance)' if was_leg_imbalance else ''}",
        market_id=pos.market_id,
        real_position_id=pos.id,
    )
