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

    book_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
