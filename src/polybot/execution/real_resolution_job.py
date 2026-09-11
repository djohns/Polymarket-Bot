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

Payout: `winning_shares - cost_usd`, donde `winning_shares` es la cantidad
REAL confirmada del lado que ganó (`yes_shares` o `no_shares`, ver
`persistence.models.RealPosition` y `execution.real_executor._confirmed_fill`)
y `cost_usd` es el gasto real total de ambas patas. Una única fórmula sirve
tanto para una canasta bien calzada (payout = $1 × shares del lado ganador,
igual que antes) como para una posición con leg imbalance total (`no_shares=0`,
el payout es simplemente 0 si ganó el lado sin capital) o con desbalance
residual (`status="pendiente"` con ambas patas > 0 pero desiguales) -- no hace
falta distinguir el caso por `status`, sólo usar las shares reales de cada
lado (ver CLAUDE.md, sección Fase 3, "Bug de sizing descubierto en la
auditoría de la 4ta activación", para el porqué de este cambio respecto a la
fórmula anterior, que asumía una única `shares` compartida por ambas patas).
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
    winning_shares = pos.yes_shares if winning_outcome == "YES" else pos.no_shares
    realized = winning_shares - pos.cost_usd

    pos.status = "cerrada"
    pos.resolved_outcome = winning_outcome
    pos.resolved_at = now
    pos.realized_pnl = realized

    log_event(
        session,
        "position_resolved",
        "info",
        f"POSICIÓN REAL RESUELTA {pos.question[:60]} | outcome={winning_outcome} "
        f"realized_pnl={realized:.4f}{' (leg imbalance/desbalance)' if was_leg_imbalance else ''}",
        market_id=pos.market_id,
        real_position_id=pos.id,
    )
