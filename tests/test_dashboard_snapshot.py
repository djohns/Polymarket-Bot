from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from polybot.dashboard.snapshot import build_snapshot
from polybot.persistence.models import Base, Opportunity, SignalResolution, SimulatedPosition


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _position(**overrides) -> SimulatedPosition:
    defaults = {
        "market_id": "0xmarket",
        "cluster_id": "event-1",
        "question": "¿Test?",
        "shares": 10.0,
        "yes_price_avg": 0.4,
        "no_price_avg": 0.5,
        "yes_price_best": 0.4,
        "no_price_best": 0.5,
        "cost_usd": 9.0,
        "fee_estimate": 0.2,
        "gross_pnl": 1.0,
        "slippage_estimate": 0.0,
        "net_pnl": 0.8,
    }
    defaults.update(overrides)
    return SimulatedPosition(**defaults)


def test_empty_db_does_not_crash():
    session = _session()
    snap = build_snapshot(session)
    assert snap.status_counts == {}
    assert snap.equity_curve == []
    assert snap.hit_rate is None
    assert snap.resolved_count == 0
    assert snap.avg_net_margin_pct is None
    assert snap.unrealized_pnl == 0.0


def test_status_counts_and_hit_rate():
    session = _session()
    session.add(_position(status="abierta", net_pnl=0.5))
    session.add(
        _position(
            status="cerrada",
            resolved_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            realized_pnl=0.7,
        )
    )
    session.add(
        _position(
            status="cerrada",
            resolved_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            realized_pnl=-0.1,
        )
    )
    session.commit()

    snap = build_snapshot(session)
    assert snap.status_counts["abierta"] == 1
    assert snap.status_counts["cerrada"] == 2
    assert snap.resolved_count == 2
    assert round(snap.hit_rate, 4) == 0.5
    assert snap.unrealized_pnl == 0.5


def test_equity_curve_is_cumulative_and_ordered():
    session = _session()
    session.add(
        _position(resolved_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC), status="cerrada", realized_pnl=1.0)
    )
    session.add(
        _position(resolved_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC), status="cerrada", realized_pnl=2.0)
    )
    session.commit()

    snap = build_snapshot(session)
    assert [round(v, 4) for _, v in snap.equity_curve] == [2.0, 3.0]
    assert snap.equity_curve[0][0] < snap.equity_curve[1][0]


def test_exposure_grouped_by_market_and_cluster_only_open():
    session = _session()
    session.add(_position(status="abierta", market_id="0xA", cluster_id="ev-1", cost_usd=10.0))
    session.add(_position(status="abierta", market_id="0xA", cluster_id="ev-1", cost_usd=5.0))
    session.add(_position(status="cerrada", market_id="0xB", cluster_id="ev-2", cost_usd=999.0))
    session.commit()

    snap = build_snapshot(session)
    assert snap.exposure_by_market == [("0xA", "¿Test?", 15.0)]
    assert snap.exposure_by_cluster == [("ev-1", 15.0)]


def test_signal_counts_and_brier_partial_coverage():
    session = _session()
    session.add(
        Opportunity(
            market_id="0xshared",
            question="¿Test?",
            signal_type="longshot_bias",
            outcome="YES",
            outcome_price=0.9,
            corrected_price=0.85,
            trade_direction="YES",
            detected_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        )
    )
    session.add(Opportunity(market_id="0xshared", question="¿Test?", signal_type="arbitrage"))
    session.add(
        SignalResolution(
            market_id="0xshared", resolved_outcome="YES", resolved_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
        )
    )
    session.commit()

    snap = build_snapshot(session)
    assert snap.arb_signals_detected == 1
    assert snap.longshot_signals_detected == 1
    assert "2026-09-01" in snap.brier_by_day
    score, n = snap.brier_by_day["2026-09-01"]
    assert n == 1
    assert round(score, 4) == round((0.85 - 1.0) ** 2, 4)
