"""Backfill puntual, ya ejecutado en la VPS -- se deja como referencia/auditoría.

Registra retroactivamente en `real_positions` las 4 posiciones reales que el
bot ejecutó el 2026-09-08 y que el bug de `_order_filled` dejó sin ningún
registro (ver CLAUDE.md, sección Fase 3, "Incidente del 2026-09-08" para el
post-mortem completo). Los datos vienen de reconstrucción manual on-chain
(Polygon, vía RPC público) cruzada contra los `simulated_positions` del
mismo instante (mismo book, mismo precio) para estimar `yes_price_avg` y
`shares` -- el costo real (`cost_usd`) sí es exacto, tomado directo de los
eventos `Transfer` de pUSD desde la proxy wallet. `shares`/`yes_price_avg`
son la mejor aproximación posible, no un valor confirmado por el exchange
-- por eso cada fila queda con una nota explícita en `notes`.

No se vuelve a ejecutar -- si se corre dos veces, duplica las filas (no es
idempotente a propósito: es un backfill de un incidente puntual, no una
migración recurrente).
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, "src")

from polybot.persistence.db import get_session
from polybot.persistence.models import RealPosition

NOTE = (
    "Backfill retroactivo del incidente del 2026-09-08 (ver CLAUDE.md, sección "
    "Fase 3): orden YES real llenó on-chain pero _order_filled() (bug, sólo "
    "miraba transactionsHashes) la clasificó como no llenada -- nunca se compró "
    "la pata NO ni se registró nada. cost_usd es exacto (evento Transfer de pUSD "
    "on-chain); yes_price_avg/shares son estimados a partir del "
    "simulated_position más cercano en el tiempo para el mismo mercado (mismo "
    "book, no un valor confirmado por el exchange)."
)

POSITIONS = [
    {
        "market_id": "0x982ed9da008de212dc7858788e4fd3856d44ea77eb403d8f6c6d18126f78571e",
        "cluster_id": "912304",
        "question": "Will FC Seoul win on 2026-09-08?",
        "opened_at": dt.datetime(2026, 9, 8, 11, 43, 4, tzinfo=dt.UTC),
        "shares": 1.9055 / 0.36,
        "yes_price_avg": 0.36,
        "cost_usd": 1.9055,
        "fee_paid": 0.0,
        "resolved_outcome": "NO",
        "realized_pnl": -1.9055,
        "yes_tx_hash": "0xe5a0454da71d35300d1fce8923e6a57bd52f74372552e91fde07850590fe885a",
    },
    {
        "market_id": "0x3dfa59b86395e68e3da94c9ca36246592c1434c0b3352aea159d31de7647856d",
        "cluster_id": "931707",
        "question": "Will Club Brugge KV win on 2026-09-08?",
        "opened_at": dt.datetime(2026, 9, 8, 16, 57, 28, tzinfo=dt.UTC),
        "shares": 1.3487 / 0.25,
        "yes_price_avg": 0.25,
        "cost_usd": 1.3487,
        "fee_paid": 0.0,
        "resolved_outcome": "NO",
        "realized_pnl": -1.3487,
        "yes_tx_hash": "0x308237d77c92468ecc136eef969486ab13c6f8e1a37f724a48c55cc4422f5aee",
    },
    {
        "market_id": "0x39280ab9591ddebda5a2564c9702c56f0793983c597b868502548cfb7c908045",
        "cluster_id": "931707",
        "question": "Will Aston Villa FC win on 2026-09-08?",
        "opened_at": dt.datetime(2026, 9, 8, 17, 5, 57, tzinfo=dt.UTC),
        "shares": 2.94 / 0.56,
        "yes_price_avg": 0.56,
        "cost_usd": 2.94,
        "fee_paid": 0.0764,
        "resolved_outcome": "YES",
        "realized_pnl": (2.94 / 0.56) - 2.94 - 0.0764,
        "yes_tx_hash": "0x784cba400290789150c1e979534e5e88427fb095f2d39aa58a8be0c96fb012e5",
    },
    {
        "market_id": "0x419a7251943ebb0c94d4fc116f34f6e0c6f7d89ba3f1476e0dbdb1a73ca42aa7",
        "cluster_id": "931709",
        "question": "Will AEK win on 2026-09-08?",
        "opened_at": dt.datetime(2026, 9, 8, 18, 7, 15, tzinfo=dt.UTC),
        "shares": 4.6052 / 0.89,
        "yes_price_avg": 0.89,
        "cost_usd": 4.6052,
        "fee_paid": 0.0,
        "resolved_outcome": "YES",
        "realized_pnl": (4.6052 / 0.89) - 4.6052,
        "yes_tx_hash": "0xd04376ffe706186587bfaf73194cb685285a4c099baae01f0ece99d1e403e3ca",
    },
]


def main() -> None:
    with get_session() as session:
        for p in POSITIONS:
            session.add(
                RealPosition(
                    market_id=p["market_id"],
                    cluster_id=p["cluster_id"],
                    question=p["question"],
                    status="cerrada",
                    opened_at=p["opened_at"],
                    shares=p["shares"],
                    yes_price_avg=p["yes_price_avg"],
                    no_price_avg=0.0,  # nunca se compró la pata NO
                    cost_usd=p["cost_usd"],
                    fee_paid=p["fee_paid"],
                    net_pnl_expected=0.0,  # no aplica -- nunca fue el arb completo intencional
                    realized_pnl=p["realized_pnl"],
                    resolved_outcome=p["resolved_outcome"],
                    resolved_at=None,  # timestamp exacto de redención no identificado con certeza
                    yes_tx_hash=p["yes_tx_hash"],
                    notes=NOTE,
                )
            )
        session.commit()
    print(f"Backfill completo: {len(POSITIONS)} posiciones insertadas.")


if __name__ == "__main__":
    main()
