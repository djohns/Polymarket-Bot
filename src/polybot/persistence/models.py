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

    book_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
