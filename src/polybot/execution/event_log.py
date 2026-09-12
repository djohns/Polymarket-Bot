"""Bitácora persistida de eventos críticos de Fase 3 (Fase 3, corrección post-incidente).

`journald` en la VPS retiene apenas ~8.8MB -- rotó por completo el tramo de
~19h que cubría el incidente del 2026-09-08 antes de que se pudiera auditar,
dejando el diagnóstico ciego justo cuando más hacía falta (ver CLAUDE.md,
sección Fase 3). `log_event` es el único punto de entrada para loguear un
evento crítico de ejecución real desde ahora: siempre hace las dos cosas --
loguea vía el logger de Python de siempre (journald sigue sirviendo para
tail en vivo/depuración) y persiste una fila en `real_execution_events`
(fuente de verdad durable, sobrevive rotación y reinicios).

Gap cerrado el 2026-09-12 (ver CLAUDE.md, casos Al Ittihad y Boca Juniors --
2 leg imbalance por `order_send_failed` sin ningún detalle de la excepción
sobrevivido más allá de journald, ya rotado para cuando se pudo auditar):
antes, `exc_info=True` sólo adjuntaba la traza al log de Python (perdida con
la rotación de journald) pero nunca al `detail` JSON persistido en la fila --
exactamente la misma clase de dato efímero que motivó crear esta tabla en
primer lugar. Ahora, `exc_info=True` además captura la excepción en curso
(`sys.exc_info()`) y la mergea dentro de `detail` (tipo, mensaje, traceback
completo) -- sobrevive tanto a la rotación de journald como a un reinicio.
"""
from __future__ import annotations

import logging
import sys
import traceback

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
    log (igual que `logger.exception`) Y al `detail` persistido -- no se
    pierde información de diagnóstico sólo por journald rotar antes de que
    alguien la mire (ver docstring del módulo)."""
    logger.log(_LEVELS[severity], message, exc_info=exc_info)

    persisted_detail = dict(detail) if detail else None
    if exc_info:
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is not None:
            persisted_detail = {
                **(persisted_detail or {}),
                "exception_type": exc_type.__name__,
                "exception_message": str(exc_value),
                "traceback": traceback.format_exc(),
            }

    session.add(
        RealExecutionEvent(
            event_type=event_type,
            severity=severity,
            message=message,
            market_id=market_id,
            real_position_id=real_position_id,
            detail=persisted_detail,
        )
    )
    session.commit()
