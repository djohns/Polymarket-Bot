"""Reporte de Brier score de la señal favorito-longshot (Fase 2, parte 2).

No calcula Brier para arb: el arb puro no tiene una predicción direccional que
puntuar (ver `polybot.signals.brier` y CLAUDE.md). La cobertura es parcial por
diseño -- sólo mercados que también tuvieron una posición de arb resuelta (ver
`SignalResolution`).

Uso: PYTHONPATH=src python scripts/brier_report.py [day|week]
"""
from __future__ import annotations

import sys

from polybot.persistence.db import get_session
from polybot.signals.brier import longshot_brier_report

if __name__ == "__main__":
    window = sys.argv[1] if len(sys.argv) > 1 else "day"
    with get_session() as session:
        report = longshot_brier_report(session, window=window)

    if not report:
        print("Sin muestras todavía (ningún mercado con señal longshot resolvió aún vía el tracking parcial).")
        sys.exit(0)

    print(f"Brier score de favorito-longshot por {window}:")
    for key, (score, n) in report.items():
        print(f"  {key}: {score:.4f} (n={n})" if score is not None else f"  {key}: sin muestras")
