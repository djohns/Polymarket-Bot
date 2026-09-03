"""Fase 2, parte 3: genera el reporte HTML del dashboard y lo escribe a disco.

Pensado para correr como job puntual (systemd timer, `OnUnitActiveSec`), no
como proceso vivo -- ver CLAUDE.md para la justificación de por qué no se usa
Streamlit/Dash en la VPS de 498MB. Corre, consulta agregados SQL, escribe el
HTML, termina -- sin footprint de RAM persistente.
"""
from __future__ import annotations

import logging

from polybot.config import settings
from polybot.dashboard.render import render_html
from polybot.dashboard.snapshot import build_snapshot
from polybot.persistence.db import get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def generate(output_path: str) -> None:
    init_db()
    with get_session() as session:
        snap = build_snapshot(session)
    html = render_html(snap)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(
        "Dashboard regenerado en %s (%d posiciones, %d señales arb detectadas)",
        output_path,
        sum(snap.status_counts.values()),
        snap.arb_signals_detected,
    )


if __name__ == "__main__":
    generate(settings.dashboard_output_path)
