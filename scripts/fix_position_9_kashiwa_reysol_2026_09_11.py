"""Backfill puntual, ya ejecutado en la VPS -- se deja como referencia/auditoría.

Corrige `real_positions.id=9` ("Will Kashiwa Reysol win on 2026-09-11?"), la
primera posición real ejecutada con el nuevo flujo de
sizing (2026-09-11 10:36 GMT). El sizing en sí funcionó bien (YES real
confirmado, NO dimensionado correctamente contra el book en vivo), pero
`_confirmed_fill` tenía un bug de fallback silencioso: cuando `get_trades` no
encontró el trade de la pata NO en el primer intento (lag de indexación
transitorio del exchange), fabricó un resultado con la estimación pre-trade
en vez de señalar que no se había confirmado nada -- ver CLAUDE.md, sección
Fase 3, y `execution/real_executor.py` (docstring del módulo) para el
post-mortem completo.

Valores reales reconstruidos de `data-api.polymarket.com/activity`
(type=TRADE,REDEEM, market=0x2a3fc55a45010413bc922ff22b2e00106b97e61a1bc35c0f85b7dfc347943092)
y confirmados independientemente contra `get_trades` en vivo (mismo
`taker_order_id`, mismo tamaño y precio exactos):
- YES: 6.578948 shares @ $0.1899999818, costo real (fee incluido) $1.30062.
- NO: 6.56776 shares @ $0.8496041268 (NO 6.578948 @ $0.72 como quedó
  registrado por el fallback), costo real $5.621956.
- Redimido: $6.578948 (sólo el lado YES, que ganó).

No es idempotente a propósito -- backfill de un evento puntual, no pensado
para volver a correrse.
"""
from __future__ import annotations

from polybot.persistence.db import get_session
from polybot.persistence.models import RealPosition

POSITION_ID = 9

YES_SHARES = 6.578948
YES_PRICE_AVG = 0.1899999818
YES_COST_USD = 1.30062

NO_SHARES = 6.56776
NO_PRICE_AVG = 0.8496041268
NO_COST_USD = 5.621956

COST_USD = YES_COST_USD + NO_COST_USD
REDEEMED_USD = 6.578948  # sólo el lado YES, que ganó
REALIZED_PNL = REDEEMED_USD - COST_USD
LEG_IMBALANCE_PCT = abs(YES_SHARES - NO_SHARES) / max(YES_SHARES, NO_SHARES)

NOTE = (
    "Corregido el 2026-09-11: el registro original (no_shares=6.578948 @ "
    "$0.72, cost_usd=$6.10, realized_pnl=+$0.475) venía del fallback "
    "silencioso de _confirmed_fill (get_trades no encontró el trade de NO en "
    "el primer intento por un lag de indexación transitorio) -- valores "
    "reales reconstruidos de data-api.polymarket.com/activity y confirmados "
    "contra get_trades en vivo: NO=6.56776 shares @ $0.8496041268, "
    "cost_usd real=$6.922576, realized_pnl real=-$0.343628 (pérdida, no "
    "ganancia). Ver CLAUDE.md, sección Fase 3."
)


def main() -> None:
    with get_session() as session:
        pos = session.get(RealPosition, POSITION_ID)
        if pos is None:
            raise RuntimeError(f"real_positions.id={POSITION_ID} no existe")
        if pos.question != "Will Kashiwa Reysol win on 2026-09-11?":
            raise RuntimeError(f"real_positions.id={POSITION_ID} no es la posición esperada: {pos.question!r}")

        pos.yes_shares = YES_SHARES
        pos.yes_price_avg = YES_PRICE_AVG
        pos.no_shares = NO_SHARES
        pos.no_price_avg = NO_PRICE_AVG
        pos.cost_usd = COST_USD
        pos.realized_pnl = REALIZED_PNL
        pos.leg_imbalance_pct = LEG_IMBALANCE_PCT
        pos.notes = (pos.notes or "") + " | " + NOTE
        session.commit()

    print(f"real_positions.id={POSITION_ID} corregido: cost_usd={COST_USD:.6f} realized_pnl={REALIZED_PNL:.6f}")


if __name__ == "__main__":
    main()
