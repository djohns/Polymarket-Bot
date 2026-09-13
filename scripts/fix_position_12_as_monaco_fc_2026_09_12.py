"""Backfill puntual, ya ejecutado en la VPS -- se deja como referencia/auditoría.

Corrige `real_positions.id=12` ("Will AS Monaco FC win on 2026-09-12?"), la
primera posición real de la 8va activación (2026-09-12 17:03:46 GMT). La pata
YES llenó real (confirmado vía `get_trades`), pero la pata NO nunca fue
matched por el exchange -- no un lag de indexación transitorio (a diferencia
de Kashiwa Reysol): confirmado con `get_trades` consultado horas después
(cero trades en absoluto para ese token) y con el flujo de caja on-chain real
(`data-api.polymarket.com/activity`, un único trade en todo el mercado, la
compra YES). `_confirmed_fill` agotó los reintentos correctamente sin
fabricar ningún resultado (fix del incidente del 2026-09-11) y marcó la
posición `"sin_confirmar"` -- ver CLAUDE.md, sección Fase 3, para el
post-mortem completo.

El mercado ya resolvió (`closed=True`, ganó "No") antes de este backfill, así
que el resultado real se puede calcular con certeza: sólo la pata YES tiene
capital real confirmado, y esa pata perdió -- pérdida total del costo de YES,
no hace falta ninguna reconstrucción adicional (a diferencia de Kashiwa
Reysol, que necesitó reconstruir precio/shares reales de la pata NO que sí
había llenado).

Valores:
- YES: 6.659575 shares @ $0.47, cost_usd real=$3.212945 (fee incluido, ya
  persistido correctamente desde el momento del fill -- no cambia).
- NO: nunca llenó, no_shares=0 (ya así, no cambia).
- Resultado: mercado resolvió "No" -- la pata YES (la única con capital real)
  perdió. realized_pnl = -cost_usd = -3.212945.

No es idempotente a propósito -- backfill de un evento puntual, no pensado
para volver a correrse.
"""
from __future__ import annotations

import datetime as dt

from polybot.persistence.db import get_session
from polybot.persistence.models import RealPosition

POSITION_ID = 12

REALIZED_PNL = -3.212945  # pérdida total: sólo YES tiene capital real, y perdió

NOTE = (
    "Corregido el 2026-09-12: la pata NO nunca fue matched por el exchange "
    "(confirmado on-chain vía data-api.polymarket.com/activity -- único "
    "trade real del mercado es la compra YES -- y vía get_trades consultado "
    "horas después, cero trades para el token NO). No fue un lag de "
    "indexación transitorio como Kashiwa Reysol. El mercado ya resolvió "
    "(ganó 'No'), así que la pata YES -- la única con capital real -- "
    "perdió por completo. status=cerrada, realized_pnl=-3.212945. "
    "Ver CLAUDE.md, sección Fase 3."
)


def main() -> None:
    with get_session() as session:
        pos = session.get(RealPosition, POSITION_ID)
        if pos is None:
            raise RuntimeError(f"real_positions.id={POSITION_ID} no existe")
        if pos.question != "Will AS Monaco FC win on 2026-09-12?":
            raise RuntimeError(f"real_positions.id={POSITION_ID} no es la posición esperada: {pos.question!r}")
        if pos.status != "sin_confirmar":
            raise RuntimeError(f"real_positions.id={POSITION_ID} status inesperado: {pos.status!r}")

        pos.status = "cerrada"
        pos.resolved_outcome = "NO"
        pos.resolved_at = dt.datetime.now(dt.UTC)
        pos.realized_pnl = REALIZED_PNL
        pos.notes = (pos.notes or "") + " | " + NOTE
        session.commit()

    print(f"real_positions.id={POSITION_ID} corregido: status=cerrada realized_pnl={REALIZED_PNL:.6f}")


if __name__ == "__main__":
    main()
