"""Fase 2, parte 3: cálculo del snapshot de datos para el dashboard.

Todas las consultas son agregados SQL (COUNT/SUM/AVG/GROUP BY) o selects
acotados con LIMIT -- nunca se carga la tabla completa a objetos Python (ver
CLAUDE.md, lección de la sesión de análisis de cierre de Fase 1, donde un
`.all()` sobre una tabla grande casi generó un OOM en la VPS de 498MB).

**Estado de Fase 3 (2026-09-14)** -- ver `RealTradingStatus`/`_build_real_trading_status`:
el dashboard hasta ahora era sólo de Fase 2 (paper trading); esto agrega un
indicador del estado de la ejecución real, separado del resto de las
métricas, para que el usuario sepa de un vistazo si el bot necesita su
atención sin tener que entrar a la VPS. El motivo exacto de un halt se lee
tal cual del mismo archivo/evento que ya persiste `kill_switch.halt()` --
nunca se inventa una descripción nueva.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.persistence.models import (
    Opportunity,
    RealExecutionEvent,
    RealPosition,
    SimulatedPosition,
)
from polybot.signals.brier import longshot_brier_report

_FLAG_LINE_RE = re.compile(r"^(?P<ts>\S+)\s+--\s+(?P<reason>.*)$")


@dataclass
class RealTradingStatus:
    """`state`: "activo" (operable, sin halt) | "manual" (detenido, requiere
    luz verde explícita del usuario -- drawdown, leg imbalance de red,
    leg_size_mismatch, sin_confirmar con huella no reconocida o con el tope
    de auto-recuperaciones ya alcanzado) | "auto_recuperando" (detenido por
    un `sin_confirmar` con la huella exacta ya caracterizada, todavía dentro
    del proceso de auto-recuperación -- ver `real_resolution_job.py` --, se
    puede resolver solo sin intervención)."""

    enabled: bool
    halted: bool
    halt_reason: str | None
    halted_since: dt.datetime | None
    state: str  # "activo" | "manual" | "auto_recuperando"


def _read_kill_switch_flag(path: str) -> tuple[dt.datetime | None, str | None]:
    """Lee tal cual el timestamp/motivo que `kill_switch.halt()` ya escribió
    en el flag -- nunca se redacta una descripción nueva, es la misma fuente
    de verdad que ya usa el resto del proyecto para diagnosticar un halt."""
    try:
        with open(path, encoding="utf-8") as f:
            line = f.readline().strip()
    except OSError:
        return None, None

    match = _FLAG_LINE_RE.match(line)
    if not match:
        return None, line or None

    try:
        when = dt.datetime.fromisoformat(match.group("ts"))
    except ValueError:
        when = None
    return when, match.group("reason")


def _build_real_trading_status(session: Session) -> RealTradingStatus:
    halted = kill_switch.is_halted()
    if not halted:
        return RealTradingStatus(enabled=settings.real_trading_enabled, halted=False, halt_reason=None, halted_since=None, state="activo")

    halted_since, halt_reason = _read_kill_switch_flag(settings.real_kill_switch_flag_path)

    # "auto_recuperando" sólo si el halt es por un `sin_confirmar` que todavía
    # no llegó a un desenlace final -- si ya se le agotó el tope de
    # auto-recuperaciones (`auto_recovery_capped`), pasa a ser manual igual
    # que cualquier otro halt, aunque la causa original haya sido sin_confirmar.
    state = "manual"
    if halt_reason and "fill sin confirmar" in halt_reason:
        unconfirmed_ids = (
            session.execute(select(RealPosition.id).where(RealPosition.status == "sin_confirmar")).scalars().all()
        )
        if unconfirmed_ids:
            capped_ids = set(
                session.execute(
                    select(RealExecutionEvent.real_position_id)
                    .where(
                        RealExecutionEvent.event_type == "auto_recovery_capped",
                        RealExecutionEvent.real_position_id.in_(unconfirmed_ids),
                    )
                    .distinct()
                ).scalars()
            )
            if any(pid not in capped_ids for pid in unconfirmed_ids):
                state = "auto_recuperando"

    return RealTradingStatus(
        enabled=settings.real_trading_enabled,
        halted=True,
        halt_reason=halt_reason,
        halted_since=halted_since,
        state=state,
    )


@dataclass
class Snapshot:
    generated_at: dt.datetime
    real_trading: RealTradingStatus
    status_counts: dict[str, int]
    equity_curve: list[tuple[dt.datetime, float]]  # (resolved_at, cumsum realized_pnl)
    unrealized_pnl: float  # suma de net_pnl de posiciones abiertas/pendientes -- aproximación, ver nota
    hit_rate: float | None
    resolved_count: int
    avg_net_margin_pct: float | None  # promedio de (realized_pnl / cost_usd) por trade cerrado
    exposure_by_market: list[tuple[str, str, float]]  # (market_id, question, cost_usd abierto)
    exposure_by_cluster: list[tuple[str, float]]
    arb_signals_detected: int
    arb_positions_simulated: int
    longshot_signals_detected: int
    brier_by_day: dict[str, tuple[float | None, int]] = field(default_factory=dict)
    recent_positions: list[dict] = field(default_factory=list)


def build_snapshot(session: Session, *, recent_limit: int = 20, top_n: int = 10) -> Snapshot:
    status_counts = dict(
        session.execute(
            select(SimulatedPosition.status, func.count()).group_by(SimulatedPosition.status)
        ).all()
    )

    resolved_rows = session.execute(
        select(SimulatedPosition.resolved_at, SimulatedPosition.realized_pnl)
        .where(SimulatedPosition.status == "cerrada", SimulatedPosition.resolved_at.is_not(None))
        .order_by(SimulatedPosition.resolved_at)
    ).all()
    equity_curve: list[tuple[dt.datetime, float]] = []
    running = 0.0
    for resolved_at, realized_pnl in resolved_rows:
        running += realized_pnl or 0.0
        equity_curve.append((resolved_at, running))

    unrealized_pnl = session.execute(
        select(func.coalesce(func.sum(SimulatedPosition.net_pnl), 0.0)).where(
            SimulatedPosition.status.in_(("abierta", "pendiente"))
        )
    ).scalar_one()

    resolved_count, wins = session.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(case((SimulatedPosition.realized_pnl > 0, 1), else_=0)), 0
            ),
        ).where(SimulatedPosition.status == "cerrada")
    ).one()
    hit_rate = (wins / resolved_count) if resolved_count else None

    avg_net_margin_pct = session.execute(
        select(func.avg(SimulatedPosition.realized_pnl / func.nullif(SimulatedPosition.cost_usd, 0))).where(
            SimulatedPosition.status == "cerrada"
        )
    ).scalar_one()

    exposure_by_market = session.execute(
        select(SimulatedPosition.market_id, SimulatedPosition.question, func.sum(SimulatedPosition.cost_usd))
        .where(SimulatedPosition.status == "abierta")
        .group_by(SimulatedPosition.market_id)
        .order_by(func.sum(SimulatedPosition.cost_usd).desc())
        .limit(top_n)
    ).all()

    exposure_by_cluster = session.execute(
        select(SimulatedPosition.cluster_id, func.sum(SimulatedPosition.cost_usd))
        .where(SimulatedPosition.status == "abierta")
        .group_by(SimulatedPosition.cluster_id)
        .order_by(func.sum(SimulatedPosition.cost_usd).desc())
        .limit(top_n)
    ).all()

    arb_signals_detected = session.execute(
        select(func.count()).select_from(Opportunity).where(Opportunity.signal_type == "arbitrage")
    ).scalar_one()
    longshot_signals_detected = session.execute(
        select(func.count()).select_from(Opportunity).where(Opportunity.signal_type == "longshot_bias")
    ).scalar_one()
    arb_positions_simulated = session.execute(select(func.count()).select_from(SimulatedPosition)).scalar_one()

    recent_rows = session.execute(
        select(
            SimulatedPosition.opened_at,
            SimulatedPosition.question,
            SimulatedPosition.status,
            SimulatedPosition.cost_usd,
            SimulatedPosition.net_pnl,
            SimulatedPosition.realized_pnl,
        )
        .order_by(SimulatedPosition.opened_at.desc())
        .limit(recent_limit)
    ).all()
    recent_positions = [
        {
            "opened_at": opened_at,
            "question": question,
            "status": status,
            "cost_usd": cost_usd,
            "net_pnl": net_pnl,
            "realized_pnl": realized_pnl,
        }
        for opened_at, question, status, cost_usd, net_pnl, realized_pnl in recent_rows
    ]

    brier_by_day = longshot_brier_report(session, window="day")
    real_trading = _build_real_trading_status(session)

    return Snapshot(
        generated_at=dt.datetime.now(dt.UTC),
        real_trading=real_trading,
        status_counts=status_counts,
        equity_curve=equity_curve,
        unrealized_pnl=unrealized_pnl,
        hit_rate=hit_rate,
        resolved_count=resolved_count,
        avg_net_margin_pct=avg_net_margin_pct,
        exposure_by_market=list(exposure_by_market),
        exposure_by_cluster=list(exposure_by_cluster),
        arb_signals_detected=arb_signals_detected,
        arb_positions_simulated=arb_positions_simulated,
        longshot_signals_detected=longshot_signals_detected,
        brier_by_day=brier_by_day,
        recent_positions=recent_positions,
    )
