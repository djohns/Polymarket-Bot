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
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from polybot.config import settings

logger = logging.getLogger(__name__)


def is_halted(flag_path: str | None = None) -> bool:
    return os.path.exists(flag_path or settings.real_kill_switch_flag_path)


def halt(reason: str, flag_path: str | None = None) -> None:
    """Escribe el flag de parada si todavía no existe (idempotente -- no pisa el
    motivo/timestamp de una parada previa si ya estaba activa)."""
    path = flag_path or settings.real_kill_switch_flag_path
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{dt.datetime.now(dt.UTC).isoformat()} -- {reason}\n")
    logger.critical("KILL-SWITCH ACTIVADO: %s (flag: %s)", reason, path)


def check_balance_kill_switch(current_balance_usd: float, flag_path: str | None = None) -> bool:
    """Activa el kill-switch automático si el balance real cae bajo el piso
    configurado (`REAL_KILL_SWITCH_BALANCE_FLOOR_USD`, default $15 = 25% de
    drawdown sobre $20 base). Devuelve True si el trading real está detenido
    (ya sea por esto o porque ya lo estaba)."""
    floor = settings.real_kill_switch_balance_floor_usd
    if current_balance_usd < floor:
        halt(
            f"balance real ${current_balance_usd:.2f} por debajo del piso ${floor:.2f}",
            flag_path=flag_path,
        )
    return is_halted(flag_path)
