"""Migración + backfill puntual, corre una sola vez -- ver CLAUDE.md, sección
Fase 3, "Bug de sizing descubierto en la auditoría de la 4ta activación"
(2026-09-10/11) para el post-mortem completo.

Dos cosas en un mismo script, porque la segunda depende de que la primera ya
haya corrido:

1. **Migración de esquema**: `real_positions` tenía un único campo `shares`
   compartido por las patas YES y NO, asumiendo que una canasta de arb
   siempre queda calzada en cantidad de shares -- resultó ser falso (ver
   `execution.real_executor`, docstring del módulo). Se reemplaza por
   `yes_shares`/`no_shares` (reales, independientes) + `leg_imbalance_pct`.
   SQLite en la VPS es 3.34.1 (anterior a 3.35, que agregó `ALTER TABLE ...
   DROP COLUMN`) -- por eso la migración reconstruye la tabla entera
   (crear nueva con el esquema del ORM actual, copiar filas, reemplazar) en
   vez de un `ALTER TABLE` directo. Se hace con un backup previo del archivo
   de la base completo, fuera de cualquier transacción SQL (no depende de
   que el rollback de SQLite alcance para deshacer un swap de tablas).

2. **Backfill de las 8 posiciones reales existentes** con los valores REALES
   reconstruidos a partir del flujo de caja on-chain exacto
   (`data-api.polymarket.com/activity`, `type=TRADE,REDEEM`, filtrado por la
   proxy wallet y cada `conditionId`) -- no la estimación pre-trade que
   `real_executor.py` guardaba antes de este fix. El P&L real total de las 8
   pasa de +$0.5959 (el que mostraba `real_positions` antes de este backfill)
   a +$0.2532 -- verificado contra `get_balance_allowance()` en vivo el
   2026-09-11 (coincide al centésimo de centavo: checkpoint $22.727494 + P&L
   real total = balance real observado $22.585493).

No es idempotente a propósito -- es un backfill de un evento puntual (la
migración de esquema es la parte que si se corriera dos veces fallaría al
intentar crear una tabla que ya existe con el nombre nuevo, lo cual actúa
como salvaguarda natural contra una doble ejecución accidental).
"""
from __future__ import annotations

import shutil
import sys

sys.path.insert(0, "src")

from sqlalchemy import text

from polybot.config import settings
from polybot.persistence.db import engine
from polybot.persistence.models import Base

DB_PATH = settings.database_url.removeprefix("sqlite:///")

# Valores reales reconstruidos, por id de `real_positions` -- `yes_cost_usd`/
# `no_cost_usd` son `usdcSize` de `data-api.polymarket.com/activity` (type=TRADE),
# el gasto REAL fee-inclusive de cada pata (no `price × size`: el `price` que
# devuelve el exchange es el precio de ejecución SIN fee -- verificado
# comparando contra `usdcSize`, la diferencia coincide con
# `signals.fees.taker_fee(size, price, market)` al ~5% de tasa observado en
# estos mercados). Ver CLAUDE.md para el detalle de cada posición.
_TRADES = {
    # id: (yes_shares, yes_cost_usd, no_shares, no_cost_usd)
    1: (4.625, 1.9055, 0.0, 0.0),  # FC Seoul -- nunca se compró NO (incidente 2026-09-08)
    2: (5.2, 1.34875, 0.0, 0.0),  # Club Brugge KV -- idem
    3: (6.125, 3.01644, 0.0, 0.0),  # Aston Villa FC -- idem
    4: (5.146063, 4.605176, 0.0, 0.0),  # AEK -- idem
    5: (4.538462, 1.22366, 5.078945, 3.857588),  # Independiente Santa Fe
    6: (5.512143, 1.57504, 4.180723, 3.49949),  # HJK Helsinki
    7: (5.25, 2.79552, 4.185186, 2.31198),  # Barcelona/Feyenoord
    8: (5.137255, 2.68419, 4.388889, 2.42451),  # San Diego FC
}
_REDEEMED = {
    1: 0.0,  # nunca redimido -- perdió, tokens sin valor
    2: 0.0,
    3: 6.125,
    4: 5.146063,
    5: 5.078945,
    6: 5.512143,
    7: 5.25,
    8: 4.388889,
}
_RESOLVED_OUTCOME = {1: "NO", 2: "NO", 3: "YES", 4: "YES", 5: "NO", 6: "YES", 7: "YES", 8: "NO"}


def _leg_imbalance_pct(yes_shares: float, no_shares: float) -> float:
    larger = max(yes_shares, no_shares)
    if larger <= 0:
        return 0.0
    return abs(yes_shares - no_shares) / larger


def main() -> None:
    backup_path = f"{DB_PATH}.bak-2026-09-11-leg-sizing-fix"
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup del archivo de la base en {backup_path}")

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE real_positions RENAME TO real_positions_old"))
        # SQLite mueve los índices junto con la tabla renombrada, conservando sus
        # nombres originales (`ix_real_positions_*`) -- si no se los dropea acá,
        # `create_all` de abajo choca al intentar crear índices con esos mismos
        # nombres para la tabla `real_positions` nueva (los nombres de índice son
        # globales en SQLite, no por-tabla). Verificado contra la VPS: sin este
        # drop, el primer intento de esta migración falló a mitad de camino con
        # "index ix_real_positions_market_id already exists" -- se dejó la base
        # en un estado intermedio (`real_positions` nueva vacía +
        # `real_positions_old` con los datos intactos) que se restauró a mano
        # antes de agregar este fix.
        for column in ("opened_at", "market_id", "cluster_id", "status"):
            conn.execute(text(f"DROP INDEX IF EXISTS ix_real_positions_{column}"))

    # Crea `real_positions` con el esquema actual del ORM (yes_shares/no_shares/
    # leg_imbalance_pct, sin `shares`) -- no toca ninguna otra tabla existente.
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["real_positions"]])

    with engine.begin() as conn:
        old_rows = conn.execute(text("SELECT * FROM real_positions_old ORDER BY id")).mappings().all()
        unknown_ids = [row["id"] for row in old_rows if row["id"] not in _TRADES]
        if unknown_ids:
            raise RuntimeError(
                f"real_positions tiene ids no contemplados en este backfill puntual: {unknown_ids} -- "
                "este script sólo conoce el flujo de caja real de las 8 posiciones de las primeras "
                "4 activaciones (ids 1-8); no correrlo contra una base con posiciones nuevas sin "
                "antes extender _TRADES/_REDEEMED/_RESOLVED_OUTCOME con sus valores reales."
            )

        for row in old_rows:
            row = dict(row)
            pos_id = row["id"]
            yes_shares, yes_cost_usd, no_shares, no_cost_usd = _TRADES[pos_id]
            cost_usd = yes_cost_usd + no_cost_usd
            realized_pnl = _REDEEMED[pos_id] - cost_usd
            leg_imbalance_pct = _leg_imbalance_pct(yes_shares, no_shares)
            # yes_price_avg/no_price_avg quedan informativos (precio efectivo pagado,
            # fee incluido) -- lo que importa para el payout es cost_usd, ya correcto arriba.
            row["yes_price_avg"] = yes_cost_usd / yes_shares if yes_shares > 0 else 0.0
            row["no_price_avg"] = no_cost_usd / no_shares if no_shares > 0 else 0.0
            row["cost_usd"] = cost_usd
            row["realized_pnl"] = realized_pnl
            row["resolved_outcome"] = _RESOLVED_OUTCOME[pos_id]

            conn.execute(
                text(
                    """
                    INSERT INTO real_positions (
                        id, opened_at, market_id, cluster_id, question, status,
                        yes_shares, no_shares, leg_imbalance_pct, yes_price_avg, no_price_avg,
                        cost_usd, fee_paid, net_pnl_expected, realized_pnl,
                        resolved_outcome, resolved_at, yes_order_id, no_order_id,
                        yes_tx_hash, no_tx_hash, notes
                    ) VALUES (
                        :id, :opened_at, :market_id, :cluster_id, :question, :status,
                        :yes_shares, :no_shares, :leg_imbalance_pct, :yes_price_avg, :no_price_avg,
                        :cost_usd, :fee_paid, :net_pnl_expected, :realized_pnl,
                        :resolved_outcome, :resolved_at, :yes_order_id, :no_order_id,
                        :yes_tx_hash, :no_tx_hash, :notes
                    )
                    """
                ),
                {
                    "id": pos_id,
                    "opened_at": row["opened_at"],
                    "market_id": row["market_id"],
                    "cluster_id": row["cluster_id"],
                    "question": row["question"],
                    "status": row["status"],
                    "yes_shares": yes_shares,
                    "no_shares": no_shares,
                    "leg_imbalance_pct": leg_imbalance_pct,
                    "yes_price_avg": row["yes_price_avg"],
                    "no_price_avg": row["no_price_avg"],
                    "cost_usd": row["cost_usd"],
                    "fee_paid": row["fee_paid"],
                    "net_pnl_expected": row["net_pnl_expected"],
                    "realized_pnl": row["realized_pnl"],
                    "resolved_outcome": row["resolved_outcome"],
                    "resolved_at": row["resolved_at"],
                    "yes_order_id": row["yes_order_id"],
                    "no_order_id": row["no_order_id"],
                    "yes_tx_hash": row["yes_tx_hash"],
                    "no_tx_hash": row["no_tx_hash"],
                    "notes": (row["notes"] or "")
                    + (
                        " | Corregido el 2026-09-11 con shares/precios/cost_usd/realized_pnl "
                        "reales reconstruidos de flujo de caja on-chain (ver CLAUDE.md, Fase 3, "
                        "bug de sizing de la 4ta activación) -- no hace falta re-hacerlo."
                    ),
                },
            )

        conn.execute(text("DROP TABLE real_positions_old"))

    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COALESCE(SUM(realized_pnl), 0) FROM real_positions WHERE status='cerrada'")
        ).scalar_one()
        count = conn.execute(text("SELECT COUNT(*) FROM real_positions")).scalar_one()

    print(f"Migración completa: {count} posiciones, P&L real total = {total:.6f}")
    assert abs(total - 0.253196) < 1e-3, f"P&L total inesperado: {total} (se esperaba ~0.2532)"


if __name__ == "__main__":
    main()
