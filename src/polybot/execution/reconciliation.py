"""Reconciliación automática de balance real (Fase 3, corrección post-incidente).

Red de seguridad agregada tras el incidente del 2026-09-08 (ver CLAUDE.md,
sección Fase 3): el bug de `_is_order_filled` dejó $10.87 de capital real
gastado sin ningún registro en `real_positions` -- nada en el sistema lo
habría detectado solo hasta esta auditoría manual. Esta reconciliación es
justamente la red que debería haber avisado antes: compara periódicamente el
balance real de la wallet contra lo que `real_positions` implica que debería
haber, y si divergen más de lo que fees/redondeo explican, asume que algo
quedó sin registrar y detiene el trading real preventivamente -- no espera a
que alguien audite a mano para notarlo.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.event_log import log_event
from polybot.persistence.models import RealPosition


def expected_balance_usd(session: Session) -> float:
    """Balance que debería haber en la wallet según lo que `real_positions`
    ya sabe: el capital base menos lo comprometido en posiciones que todavía
    no devolvieron el capital ("enviada"/"abierta"/"pendiente" -- cualquier
    estado donde el capital salió de la wallet y no hay certeza de que haya
    vuelto), más el P&L ya realizado de las posiciones cerradas."""
    committed = session.execute(
        select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(
            RealPosition.status.in_(("enviada", "abierta", "pendiente"))
        )
    ).scalar_one()
    realized = session.execute(
        select(func.coalesce(func.sum(RealPosition.realized_pnl), 0.0)).where(RealPosition.status == "cerrada")
    ).scalar_one()
    return settings.real_capital_base_usd - committed + realized


def check_balance_reconciliation(session: Session, actual_balance_usd: float) -> bool:
    """Compara el balance real contra `expected_balance_usd`. Si divergen más
    que `REAL_RECONCILIATION_THRESHOLD_USD` (default $0.50, para tolerar
    fees/redondeo), loguea CRITICAL (vía `event_log`, persistido -- no sólo
    journald) y activa el kill-switch preventivamente: una divergencia así es
    exactamente la señal que faltó para detectar el incidente del 2026-09-08
    antes de que hiciera falta una auditoría manual. Devuelve True si detectó
    (y actuó sobre) una divergencia."""
    threshold = settings.real_reconciliation_threshold_usd
    expected = expected_balance_usd(session)
    divergence = actual_balance_usd - expected

    if abs(divergence) <= threshold:
        return False

    log_event(
        session,
        "reconciliation_divergence",
        "critical",
        f"Divergencia de balance real: actual=${actual_balance_usd:.2f} vs. "
        f"esperado=${expected:.2f} (según real_positions) -- diferencia de "
        f"${divergence:+.2f}, supera el umbral de ${threshold:.2f}. Puede indicar "
        "una orden real ejecutada que no quedó registrada -- ver CLAUDE.md, "
        "incidente del 2026-09-08.",
        detail={"actual_balance_usd": actual_balance_usd, "expected_balance_usd": expected, "divergence_usd": divergence},
    )
    kill_switch.halt(
        f"divergencia de reconciliación: balance real ${actual_balance_usd:.2f} vs. "
        f"esperado ${expected:.2f} (diff ${divergence:+.2f})",
        session=session,
    )
    return True
