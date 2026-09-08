"""Bitácora persistida de eventos críticos de Fase 3 (Fase 3, corrección post-incidente).

`journald` en la VPS retiene apenas ~8.8MB -- rotó por completo el tramo de
~19h que cubría el incidente del 2026-09-08 antes de que se pudiera auditar,
dejando el diagnóstico ciego justo cuando más hacía falta (ver CLAUDE.md,
sección Fase 3). `log_event` es el único punto de entrada para loguear un
evento crítico de ejecución real desde ahora: siempre hace las dos cosas --
loguea vía el logger de Python de siempre (journald sigue sirviendo para
tail en vivo/depuración) y persiste una fila en `real_execution_events`
(fuente de verdad durable, sobrevive rotación y reinicios).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from polybot.persistence.models import RealExecutionEvent

logger = logging.getLogger("polybot.execution.real")

_LEVELS = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "critical": logging.CRITICAL,
}


def log_event(
    session: Session,
    event_type: str,
    severity: str,
    message: str,
    *,
    market_id: str | None = None,
    real_position_id: int | None = None,
    detail: dict | None = None,
    exc_info: bool = False,
) -> None:
    """`exc_info=True` desde dentro de un bloque `except` adjunta la traza al
    log (igual que `logger.exception`) -- no se pierde información de
    diagnóstico sólo por loguear también a la base."""
    logger.log(_LEVELS[severity], message, exc_info=exc_info)
    session.add(
        RealExecutionEvent(
            event_type=event_type,
            severity=severity,
            message=message,
            market_id=market_id,
            real_position_id=real_position_id,
            detail=detail,
        )
    )
    session.commit()
