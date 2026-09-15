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
corto (`REAL_RECONCILIATION_GRACE_PERIOD_SECONDS`, 240s) antes de decidir.
Una divergencia NEGATIVA (falta plata) NUNCA recibe ese margen: podría ser
una pérdida real o un bug nuevo (como el incidente original del 2026-09-08,
que fue de signo negativo), así que dispara el kill-switch de inmediato
siempre, sin excepción. La asimetría es deliberada, no un descuido -- no
darle el mismo trato a ambos signos.

**"sin_confirmar" NO usa una ventana de gracia -- usa exclusión aritmética
(2026-09-15, reemplaza el intento anterior del 2026-09-14 de agrandar la
ventana)**: el 2026-09-14 se le dio a "sin_confirmar" su propia ventana de
gracia extendida (`REAL_RECONCILIATION_GRACE_PERIOD_SIN_CONFIRMAR_SECONDS`,
540s), calibrada para cubrir el overhead del propio job de auto-recuperación
(300s de espera + 180s de ciclo + 60s de margen). Un caso real (posición 21,
"Daejeon Citizen FC", 2026-09-15) mostró que esa calibración resolvía el
problema equivocado: el partido en sí tardó 2.55h en resolver, no unos
minutos -- ninguna ventana de gracia razonable puede cubrir la duración de
un partido en vivo, y el sistema quedó detenido globalmente ~7.5h después de
que el incidente puntual ya estaba resuelto de fondo (ver CLAUDE.md, sección
Fase 3, para el post-mortem completo). El error de diseño: tratar
"sin_confirmar" como si fuera el mismo patrón que "pendiente" (un lag corto
de *procesamiento* entre una resolución ya ocurrida y que el job la marque)
cuando en realidad es un lag de *espera al resultado del partido en sí* --
un problema de escala de tiempo completamente distinta, sin ventana fija que
lo resuelva.

La corrección no es una ventana más larga -- es no depender del tiempo en
absoluto. Mientras la única causa de la divergencia sea capital comprometido
en posiciones `sin_confirmar` **actualmente conocidas** (su `cost_usd` ya
está en `real_positions`, ya contenido al mercado puntual por el bloqueo
per-mercado, ya contado en los topes de exposición, y con el tope de
concurrencia de `sin_confirmar` como única red de seguridad sistémica
restante -- ver `execution.real_executor`), esa divergencia no es una señal
nueva de nada: ya se sabe exactamente qué la explica. `check_balance_
reconciliation_with_grace` resta ese monto conocido (`_sin_confirmar_
committed_cost_usd`) de la divergencia observada, sin esperar nada, y sólo
activa el kill-switch si sobra algo sin explicar. Esto reemplaza por
completo el rol que tenía la ventana de gracia de "sin_confirmar" -- la
variable `REAL_RECONCILIATION_GRACE_PERIOD_SIN_CONFIRMAR_SECONDS` se
elimina, no se deja como alternativa sin usar. La ventana de gracia
genérica de 240s para "pendiente" (arriba) no cambia -- ese patrón sí es
un lag corto de procesamiento, donde esperar un rato tiene sentido.
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
    -- shares reales confirmadas, ya redimida on-chain, sólo falta que
    `real_resolution_job` la marque "cerrada" (ver CLAUDE.md, casos Santa
    Fe/HJK/Al Ittihad). Es la condición que hace plausible que una
    divergencia POSITIVA sea ese lag corto de procesamiento y no un bug
    nuevo -- la ventana de gracia genérica de 240s sigue aplicando sólo a
    este patrón (ver docstring del módulo, "sin_confirmar" ya no usa
    ventana de gracia, usa `_sin_confirmar_committed_cost_usd` en su
    lugar)."""
    count = session.execute(
        select(func.count()).select_from(RealPosition).where(RealPosition.status == "pendiente")
    ).scalar_one()
    return count > 0


def _sin_confirmar_committed_cost_usd(session: Session) -> float:
    """Suma de `cost_usd` de las posiciones actualmente `sin_confirmar` --
    el monto exacto que la reconciliación ya sabe que está contado como
    "comprometido" (`expected_balance_usd`) sin que todavía se sepa si se
    gastó capital real de verdad (ver `real_executor._handle_unconfirmed_
    fill`). Una divergencia POSITIVA de exactamente esa magnitud (o menos)
    no es una señal nueva de nada -- ya se sabe qué la explica, y el
    bloqueo per-mercado + la auto-recuperación + el tope de concurrencia de
    `sin_confirmar` (ver `execution.real_executor`) ya la están manejando.
    Ver docstring del módulo para por qué esto reemplaza a una ventana de
    gracia."""
    return session.execute(
        select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(RealPosition.status == "sin_confirmar")
    ).scalar_one()


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
    """Igual que `check_balance_reconciliation`, pero con dos mecanismos de
    tolerancia ASIMÉTRICOS -- ninguno aplica a divergencia NEGATIVA, ver
    abajo -- que resuelven dos patrones benignos distintos, cada uno con su
    propia lógica (no una sola ventana genérica para ambos, ver docstring
    del módulo):

    1. **"pendiente"** (agregado 2026-09-12, confirmado con Santa Fe/Al
       Ittihad): un lag corto de *procesamiento* -- la posición ya resolvió
       y se redimió on-chain, sólo falta que `real_resolution_job` la marque
       "cerrada". Se resuelve esperando `REAL_RECONCILIATION_GRACE_PERIOD_
       SECONDS` (240s) y rechequeando una vez.
    2. **"sin_confirmar"** (rediseñado 2026-09-15, ver docstring del
       módulo): NO es un lag de procesamiento -- es la incertidumbre de una
       pata que todavía no se puede confirmar, que puede coincidir con un
       partido en curso durante horas. En vez de esperar (ninguna ventana
       fija alcanza), se resta de la divergencia el `cost_usd` total de las
       posiciones `sin_confirmar` conocidas (`_sin_confirmar_committed_
       cost_usd`) -- si eso explica toda la divergencia, no hay nada nuevo
       que señalar, sin esperar nada.

    Si la divergencia (positiva) excede lo que el punto 2 explica, se cae al
    punto 1 (si hay una posición "pendiente" que lo justifique) antes de
    activar el kill-switch -- cubre el caso de que ambos patrones coexistan.

    CRÍTICO -- ninguna de las dos tolerancias aplica a divergencia NEGATIVA
    (falta plata respecto a lo esperado): ese es exactamente el escenario
    que podría ser una pérdida real o un bug nuevo (el incidente del
    2026-09-08 fue de signo negativo), y tratarlo con cualquier margen
    dejaría correr un problema real más tiempo sin detenerlo. Divergencia
    negativa siempre dispara el kill-switch de inmediato, sin excepción,
    sin importar qué posiciones estén en curso."""
    threshold = settings.real_reconciliation_threshold_usd
    expected = expected_balance_usd(session)
    divergence = actual_balance_usd - expected

    if abs(divergence) <= threshold:
        return False

    if divergence > 0:
        if divergence <= _sin_confirmar_committed_cost_usd(session) + threshold:
            return False

        if recheck_balance is not None and _has_pending_position(session):
            await sleep(settings.real_reconciliation_grace_period_seconds)
            actual_balance_usd = await recheck_balance()
            expected = expected_balance_usd(session)
            divergence = actual_balance_usd - expected
            if abs(divergence) <= threshold:
                return False
            if divergence > 0 and divergence <= _sin_confirmar_committed_cost_usd(session) + threshold:
                return False

    _halt_for_divergence(session, actual_balance_usd, expected, divergence)
    return True
