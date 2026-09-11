from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Opportunity(Base):
    """Una señal u oportunidad detectada por el motor de señales (Fase 1: sólo logging, sin ejecución)."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
    market_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(String)
    signal_type: Mapped[str] = mapped_column(String, index=True)  # "arbitrage" | "longshot_bias"

    # Arbitraje intra-mercado
    yes_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_spread: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Sesgo favorito-longshot
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # "YES" | "NO" -- a qué outcome refieren outcome_price/corrected_price
    outcome_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    corrected_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_direction: Mapped[str | None] = mapped_column(String, nullable=True)  # "YES" | "NO"

    book_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SimulatedPosition(Base):
    """Posición simulada de arbitraje intra-mercado (Fase 2, parte 1: fill hipotético
    contra el order book real, sin firmar ni enviar nada).

    El arb "long" (comprar YES+NO<$1) paga $1 garantizado al vencimiento sin importar
    el resultado, pero el P&L se deja sin realizar (`realized_pnl` nulo, `status`
    "abierta") hasta que una fase futura confirme la resolución real del mercado —
    hay riesgo de disputa de oráculo y de fills que revierten on-chain (ver informe
    técnico, sección de riesgos). `net_pnl` es el resultado esperado/bloqueado al
    fill, no el resultado confirmado.
    """

    __tablename__ = "simulated_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )
    market_id: Mapped[str] = mapped_column(String, index=True)
    cluster_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(String)
    strategy: Mapped[str] = mapped_column(String, default="arbitrage_long")
    status: Mapped[str] = mapped_column(String, default="abierta", index=True)  # "abierta" | "cerrada" | "pendiente"

    shares: Mapped[float] = mapped_column(Float)
    yes_price_avg: Mapped[float] = mapped_column(Float)
    no_price_avg: Mapped[float] = mapped_column(Float)
    yes_price_best: Mapped[float] = mapped_column(Float)
    no_price_best: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    fee_estimate: Mapped[float] = mapped_column(Float)
    gross_pnl: Mapped[float] = mapped_column(Float)
    slippage_estimate: Mapped[float] = mapped_column(Float)
    net_pnl: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # "YES" | "NO"
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    book_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RealPosition(Base):
    """Posición REAL de arbitraje intra-mercado (Fase 3) -- firmada y enviada de
    verdad vía `execution.real_executor`, separada por completo de
    `SimulatedPosition` (que sigue corriendo sin cambios para todo lo demás:
    horizonte largo, longshot, y cualquier mercado que no califique como
    resolución rápida). Mismo nivel de detalle que la posición simulada más
    los campos que sólo existen para una orden real: fee efectivamente
    cobrado por el exchange y los hashes de transacción on-chain de cada pata.

    `yes_shares`/`no_shares` son la cantidad REAL confirmada de cada pata
    (vía `execution.real_executor._confirmed_fill`, contra `get_trades`), no
    una estimación pre-trade -- pueden diferir entre sí (`leg_imbalance_pct`
    registra esa diferencia relativa). Antes de la migración del 2026-09-11
    (ver `scripts/fix_real_position_leg_sizing_2026_09_11.py` y CLAUDE.md,
    sección Fase 3) había un único campo `shares` compartido por ambas patas,
    asumiendo una canasta siempre calzada -- resultó ser falso en las 4
    posiciones que llegaron a ejecutar ambas patas desde el incidente del
    2026-09-08 (10.6% a 24.2% de diferencia real entre YES y NO en las 4).
    """

    __tablename__ = "real_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )
    market_id: Mapped[str] = mapped_column(String, index=True)
    cluster_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(String)
    # "enviada" (orden YES recién enviada, resultado todavía no confirmado -- ver
    # incidente del 2026-09-08, sección Fase 3 de CLAUDE.md: antes de este estado
    # una orden real podía llenar de verdad y no quedar registrada en ningún lado
    # si la señal de "llenó" fallaba) | "cancelada" (YES confirmado sin llenar,
    # nada de capital tocado) | "abierta" | "cerrada" | "pendiente" (leg imbalance
    # total O desbalance residual de shares más allá del umbral -- ver
    # `real_executor._leg_imbalance_pct` -- requiere revisión manual en ambos casos)
    status: Mapped[str] = mapped_column(String, default="enviada", index=True)

    yes_shares: Mapped[float] = mapped_column(Float)
    no_shares: Mapped[float] = mapped_column(Float, default=0.0)
    # Diferencia relativa entre yes_shares/no_shares (0.0 = perfectamente calzada),
    # calculada una vez confirmado el fill real de ambas patas. `None` para
    # posiciones que nunca llegaron a confirmar la pata NO (leg imbalance total).
    leg_imbalance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    yes_price_avg: Mapped[float] = mapped_column(Float)
    no_price_avg: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    fee_paid: Mapped[float] = mapped_column(Float)
    net_pnl_expected: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    yes_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    no_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    yes_tx_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    no_tx_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class RealExecutionEvent(Base):
    """Bitácora persistida (no sólo journald) de eventos críticos de ejecución
    real -- envío de orden, confirmación de fill, leg imbalance, kill-switch,
    divergencia de reconciliación. Agregada tras el incidente del 2026-09-08:
    journald en la VPS retiene apenas ~8.8MB y rotó por completo el período del
    incidente en horas, dejando el diagnóstico ciego justo cuando más hacía
    falta. Esta tabla es la fuente de verdad durable para esos eventos -- ver
    CLAUDE.md, sección Fase 3, "logging resiliente".
    """

    __tablename__ = "real_execution_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )
    event_type: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default="info", index=True)  # "info" | "warning" | "critical"
    message: Mapped[str] = mapped_column(String)
    market_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    real_position_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SignalResolution(Base):
    """Resolución real de un mercado que tuvo al menos una señal favorito-longshot
    (`Opportunity.signal_type == "longshot_bias"`), para calcular Brier score
    (ver `signals/brier.py`).

    Se completa de forma oportunista: el job de resolución (Fase 2, parte 2) sólo
    consulta activamente los mercados con posiciones de arb abiertas/pendientes;
    cuando resuelve uno de esos mercados, aprovecha la misma consulta para
    completar esta tabla si ese mercado también tuvo señales longshot. Cobertura
    parcial por diseño — no representa la población completa de mercados con
    señal longshot. Ver CLAUDE.md, sección "Fase 2, parte 2" para la justificación.
    """

    __tablename__ = "signal_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    resolved_outcome: Mapped[str] = mapped_column(String)  # "YES" | "NO"
    resolved_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
