"""Backfill puntual, ya ejecutado en la VPS -- se deja como referencia/auditoría.

Corrige `real_positions.id=14` ("Will Manchester United FC win on
2026-09-13?"), la segunda posición real de la 9na activación
(2026-09-13 15:52:49 GMT). La pata YES nunca fue matched por el exchange
-- ver CLAUDE.md, sección Fase 3, "Paso 2 -- descartado", para el
post-mortem completo: la respuesta cruda de `create_and_post_market_order`
traía `status="delayed"` con `success=true` (sugiriendo que iba a matchear
en breve), pero `get_trades` nunca encontró el trade, ni en el momento ni
consultado horas después. Confirmado con certeza antes de este backfill:
`get_trades` para ambos tokens (YES y NO) del mercado devuelve 0 trades en
total, y `data-api.polymarket.com/activity` confirma 0 trades on-chain --
nunca se gastó ningún capital real en esta posición.

El mercado ya resolvió (`closed=True`, ganó "No" -- Manchester United no
ganó). Como la pata YES nunca tuvo capital real y nunca se llegó a enviar
la pata NO (la posición quedó "sin_confirmar" antes de eso), el resultado
real es sin ambigüedad: no se compró nada, no se perdió nada.
`realized_pnl=$0.00`. A diferencia de AS Monaco FC (que sí tenía la pata
YES real y perdió su costo completo), acá no hay ningún costo real que
contabilizar -- `cost_usd` en la fila original era sólo la estimación
pre-trade ($5.00), nunca el gasto real.

No es idempotente a propósito -- backfill de un evento puntual, no pensado
para volver a correrse.
"""
from __future__ import annotations

import datetime as dt

from polybot.persistence.db import get_session
from polybot.persistence.models import RealPosition

POSITION_ID = 14

REALIZED_PNL = 0.0  # nunca se gastó capital real -- no se compró nada, no se perdió nada

NOTE = (
    "Corregido el 2026-09-13: la pata YES nunca fue matched por el exchange "
    "(confirmado con get_trades para ambos tokens del mercado -- 0 trades en "
    "total -- y con data-api.polymarket.com/activity -- 0 trades on-chain). "
    "La respuesta cruda decía status='delayed' success=true, pero nunca "
    "llegó a matchear ni horas después -- ver CLAUDE.md, 'Paso 2 -- "
    "descartado'. El mercado ya resolvió (ganó 'No'), pero como nunca se "
    "gastó capital real, el resultado es realized_pnl=$0.00 (no se compró "
    "nada, no se perdió nada), no una pérdida del costo estimado. "
    "status=cerrada. Ver CLAUDE.md, sección Fase 3."
)


def main() -> None:
    with get_session() as session:
        pos = session.get(RealPosition, POSITION_ID)
        if pos is None:
            raise RuntimeError(f"real_positions.id={POSITION_ID} no existe")
        if pos.question != "Will Manchester United FC win on 2026-09-13?":
            raise RuntimeError(f"real_positions.id={POSITION_ID} no es la posición esperada: {pos.question!r}")
        if pos.status != "sin_confirmar":
            raise RuntimeError(f"real_positions.id={POSITION_ID} status inesperado: {pos.status!r}")

        pos.status = "cerrada"
        pos.resolved_outcome = "NO"
        pos.resolved_at = dt.datetime.now(dt.UTC)
        pos.realized_pnl = REALIZED_PNL
        pos.cost_usd = 0.0  # el $5.00 original era sólo la estimación pre-trade, nunca se gastó
        pos.fee_paid = 0.0
        pos.notes = (pos.notes or "") + " | " + NOTE
        session.commit()

    print(f"real_positions.id={POSITION_ID} corregido: status=cerrada realized_pnl={REALIZED_PNL:.6f}")


if __name__ == "__main__":
    main()
