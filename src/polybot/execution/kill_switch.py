"""Kill-switch de trading real (Fase 3) -- manual y automático por drawdown.

Mecanismo elegido: un archivo flag en disco (`settings.real_kill_switch_flag_path`,
default `data/REAL_TRADING_HALTED`). Su sola presencia detiene el envío de
órdenes reales; su ausencia lo permite. Se prefirió esto sobre una variable de
entorno porque el proceso corre 24/7 bajo systemd y una env var requeriría
reiniciar el servicio para cambiarla -- el usuario necesita poder detener o
reanudar el trading real sin matar el proceso (que seguiría haciendo paper
trading normal para todo lo demás).

El kill-switch automático (caída de balance real bajo el piso configurado)
usa el mismo archivo: al activarse escribe el flag con un motivo y un
timestamp, exactamente igual que si el usuario lo hubiera creado a mano. Esto
es intencional -- una vez que el kill-switch automático dispara, el sistema
NO debe reintentar operar solo; requiere que el usuario borre el archivo a
mano para reactivar, tal como se le pidió explícitamente. El archivo persiste
entre reinicios del proceso (vive en disco, no en memoria), así que un
reinicio del servicio no reactiva el trading real por accidente.

**Piso de drawdown vs. reconciliación -- dos mecanismos distintos, no
confundirlos** (aclarado tras el falso positivo del 2026-09-09, ver
CLAUDE.md, sección Fase 3):
- `check_balance_kill_switch` (acá abajo) evalúa **equity** (balance líquido
  + capital comprometido en posiciones reales en curso) contra un piso fijo
  -- responde "¿el bot perdió plata de verdad?". Antes de esta corrección
  comparaba el balance líquido crudo, que baja simplemente por tener
  posiciones reales abiertas (dinero temporalmente fuera de la wallet, no
  perdido) -- con 2+ posiciones de $5 abiertas a la vez sobre $20-22 de
  capital, esto bastaba para disparar el piso de $15 sin ninguna pérdida
  real (7.5h de trading real detenido sin causa genuina el 2026-09-09).
- `execution.reconciliation.check_balance_reconciliation` evalúa si el
  balance líquido real coincide con lo que `real_positions` dice que
  debería haber -- responde "¿hay una orden real que se ejecutó y no quedó
  registrada?". No mira ningún piso; cualquier divergencia (positiva o
  negativa) más allá del umbral de tolerancia es sospechosa.
Ambos pueden activar el mismo archivo flag (mismo mecanismo de parada), pero
preguntan cosas distintas y no deben fusionarse en una sola función.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.persistence.models import RealPosition

logger = logging.getLogger(__name__)


def is_halted(flag_path: str | None = None) -> bool:
    return os.path.exists(flag_path or settings.real_kill_switch_flag_path)


def halt(reason: str, flag_path: str | None = None, session: Session | None = None) -> None:
    """Escribe el flag de parada si todavía no existe (idempotente -- no pisa el
    motivo/timestamp de una parada previa si ya estaba activa). Si se pasa
    `session`, además persiste el evento en `real_execution_events` (ver
    `execution.event_log`) -- el archivo flag por sí solo ya detiene el
    trading real (eso no depende de la DB), pero sin esto el motivo de la
    parada sólo vivía en journald, que ya demostró rotar en horas."""
    path = flag_path or settings.real_kill_switch_flag_path
    already_halted = os.path.exists(path)
    if not already_halted:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(f"{dt.datetime.now(dt.UTC).isoformat()} -- {reason}\n")
        logger.critical("KILL-SWITCH ACTIVADO: %s (flag: %s)", reason, path)

    if session is not None and not already_halted:
        from polybot.execution.event_log import log_event

        log_event(session, "kill_switch_triggered", "critical", f"KILL-SWITCH ACTIVADO: {reason}")


def committed_capital_usd(session: Session) -> float:
    """Capital real comprometido en posiciones que todavía no cerraron
    (`"enviada"`/`"abierta"`/`"pendiente"`) -- plata que salió de la wallet
    pero no está perdida, sólo temporalmente fuera del balance líquido hasta
    que la posición resuelva."""
    stmt = select(func.coalesce(func.sum(RealPosition.cost_usd), 0.0)).where(
        RealPosition.status.in_(("enviada", "abierta", "pendiente"))
    )
    return session.execute(stmt).scalar_one()


def check_balance_kill_switch(
    current_balance_usd: float, session: Session, flag_path: str | None = None
) -> bool:
    """Activa el kill-switch automático si el EQUITY real (balance líquido +
    capital comprometido en posiciones reales en curso) cae bajo el piso
    configurado (`REAL_KILL_SWITCH_BALANCE_FLOOR_USD`, default $15 = 25% de
    drawdown sobre $20 base). Devuelve True si el trading real está detenido
    (ya sea por esto o porque ya lo estaba).

    Usa equity y no el balance líquido crudo a propósito: el balance líquido
    baja por sí solo cada vez que hay una posición real abierta (el capital
    de esa posición sigue existiendo, sólo que no está en la wallet todavía)
    -- comparar eso contra un piso fijo confunde "hay posiciones en curso"
    con "se perdió plata de verdad" (ver docstring del módulo)."""
    floor = settings.real_kill_switch_balance_floor_usd
    equity = current_balance_usd + committed_capital_usd(session)
    if equity < floor:
        halt(
            f"equity real ${equity:.2f} (balance líquido ${current_balance_usd:.2f} + "
            f"comprometido ${equity - current_balance_usd:.2f}) por debajo del piso ${floor:.2f}",
            flag_path=flag_path,
            session=session,
        )
    return is_halted(flag_path)
