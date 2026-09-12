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

Tolerancia asimétrica agregada el 2026-09-12 (ver
`check_balance_reconciliation_with_grace`): una divergencia POSITIVA (sobra
plata) con una posición "pendiente" en curso puede ser sólo el lag benigno
entre una resolución on-chain y que `real_resolution_job` la marque
"cerrada" (confirmado 2 veces: Santa Fe, Al Ittihad) -- se le da un margen
corto antes de decidir. Una divergencia NEGATIVA (falta plata) NUNCA recibe
ese margen: podría ser una pérdida real o un bug nuevo (como el incidente
original del 2026-09-08, que fue de signo negativo), así que dispara el
kill-switch de inmediato siempre, sin excepción. La asimetría es deliberada,
no un descuido -- no darle el mismo trato a ambos signos.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.event_log import log_event
from polybot.persistence.models import RealPosition


def expected_balance_usd(session: Session) -> float:
    """Balance que debería haber en la wallet según lo que `real_positions`
    ya sabe.

    Con `REAL_BALANCE_CHECKPOINT_USD` + `REAL_BALANCE_CHECKPOINT_AT` seteados
    (el balance real confirmado la última vez que se supo que
    `real_positions` estaba al día -- ej. justo después de un backfill): esa
    referencia, ajustada sólo por posiciones abiertas DESDE ese momento
    (`opened_at >= checkpoint_at`) -- las posiciones anteriores al checkpoint
    ya están reflejadas en el balance observado ese día, sumarlas de nuevo
    las contaría dos veces.

    Sin el par de checkpoint (caso simple, ej. una wallet recién fondeada):
    usa `REAL_CAPITAL_BASE_USD` como aproximación nominal contra TODAS las
    posiciones -- válido sólo si el balance real de arranque coincidía con
    ese número exacto.
    """
    if settings.real_balance_checkpoint_usd is not None and settings.real_balance_checkpoint_at:
        reference = settings.real_balance_checkpoint_usd
        since = dt.datetime.fromisoformat(settings.real_balance_checkpoint_at)
        committed_stmt = select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(
            RealPosition.status.in_(("enviada", "abierta", "pendiente", "sin_confirmar")),
            RealPosition.opened_at >= since,
        )
        realized_stmt = select(func.coalesce(func.sum(RealPosition.realized_pnl), 0.0)).where(
            RealPosition.status == "cerrada",
            RealPosition.opened_at >= since,
        )
    else:
        reference = settings.real_capital_base_usd
        committed_stmt = select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(
            RealPosition.status.in_(("enviada", "abierta", "pendiente", "sin_confirmar"))
        )
        realized_stmt = select(func.coalesce(func.sum(RealPosition.realized_pnl), 0.0)).where(
            RealPosition.status == "cerrada"
        )

    committed = session.execute(committed_stmt).scalar_one()
    realized = session.execute(realized_stmt).scalar_one()
    return reference - committed + realized


def _has_pending_position(session: Session) -> bool:
    """Una posición "pendiente" es candidata a estar resolviendo justo ahora
    (shares reales confirmadas, ya redimida on-chain, sólo falta que
    `real_resolution_job` la marque "cerrada") -- ver CLAUDE.md, casos Santa
    Fe/HJK/Al Ittihad. Es la condición que hace plausible que una divergencia
    POSITIVA sea ese lag benigno y no un bug nuevo."""
    count = session.execute(
        select(func.count()).select_from(RealPosition).where(RealPosition.status == "pendiente")
    ).scalar_one()
    return count > 0


def _halt_for_divergence(session: Session, actual_balance_usd: float, expected: float, divergence: float) -> None:
    threshold = settings.real_reconciliation_threshold_usd
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


def check_balance_reconciliation(session: Session, actual_balance_usd: float) -> bool:
    """Compara el balance real contra `expected_balance_usd`. Si divergen más
    que `REAL_RECONCILIATION_THRESHOLD_USD` (default $0.50, para tolerar
    fees/redondeo), loguea CRITICAL (vía `event_log`, persistido -- no sólo
    journald) y activa el kill-switch preventivamente: una divergencia así es
    exactamente la señal que faltó para detectar el incidente del 2026-09-08
    antes de que hiciera falta una auditoría manual. Devuelve True si detectó
    (y actuó sobre) una divergencia.

    Sin ventana de gracia -- dispara de inmediato ante cualquier divergencia,
    positiva o negativa. Es la usada para decidir directamente cuando no
    aplica (o ya se agotó) la tolerancia asimétrica de
    `check_balance_reconciliation_with_grace`."""
    threshold = settings.real_reconciliation_threshold_usd
    expected = expected_balance_usd(session)
    divergence = actual_balance_usd - expected

    if abs(divergence) <= threshold:
        return False

    _halt_for_divergence(session, actual_balance_usd, expected, divergence)
    return True


async def check_balance_reconciliation_with_grace(
    session: Session,
    actual_balance_usd: float,
    *,
    recheck_balance: Callable[[], Awaitable[float]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Igual que `check_balance_reconciliation`, pero con una tolerancia
    ASIMÉTRICA agregada el 2026-09-12 tras confirmar 2 casos (Santa Fe,
    Al Ittihad) del mismo patrón benigno: una posición real resuelve y se
    redime on-chain, pero `real_resolution_job` todavía no alcanzó a marcarla
    "cerrada" -- el balance real ya subió, `real_positions` todavía la cuenta
    como comprometida, y la reconciliación ve un excedente que en realidad es
    plata que ya volvió, no plata que falta.

    La tolerancia SÓLO aplica si se cumplen las dos condiciones a la vez:
    divergencia POSITIVA (`actual > expected`, sobra plata) Y al menos una
    posición en status="pendiente" (candidata a estar resolviendo ahora
    mismo). En ese caso, en vez de activar el kill-switch de inmediato, se
    espera `REAL_RECONCILIATION_GRACE_PERIOD_SECONDS` y se vuelve a chequear
    una sola vez con un balance fresco (`recheck_balance`) y el estado
    actualizado de `real_positions` -- si para entonces el job ya cerró la
    posición y la divergencia desapareció, no se hace nada.

    CRÍTICO -- esta tolerancia NUNCA aplica a divergencia NEGATIVA (falta
    plata respecto a lo esperado): ese es exactamente el escenario que
    podría ser una pérdida real o un bug nuevo (el incidente del 2026-09-08
    fue de signo negativo), y tratarlo con el mismo margen de espera dejaría
    correr un problema real más tiempo sin detenerlo. Divergencia negativa
    siempre dispara el kill-switch sin esperar, sin excepción, sin importar
    si hay posiciones pendientes."""
    threshold = settings.real_reconciliation_threshold_usd
    expected = expected_balance_usd(session)
    divergence = actual_balance_usd - expected

    if abs(divergence) <= threshold:
        return False

    if divergence > 0 and recheck_balance is not None and _has_pending_position(session):
        await sleep(settings.real_reconciliation_grace_period_seconds)
        actual_balance_usd = await recheck_balance()
        expected = expected_balance_usd(session)
        divergence = actual_balance_usd - expected
        if abs(divergence) <= threshold:
            return False

    _halt_for_divergence(session, actual_balance_usd, expected, divergence)
    return True
