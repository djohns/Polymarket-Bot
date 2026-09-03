from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.execution.resolution import ResolutionResult
from polybot.execution.resolution_job import resolve_open_positions
from polybot.persistence.models import Base, Opportunity, SignalResolution, SimulatedPosition


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _open_position(session, **overrides) -> SimulatedPosition:
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
    pos = SimulatedPosition(**defaults)
    session.add(pos)
    session.commit()
    return pos


def test_resolved_market_closes_position_with_realized_pnl():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(
        resolve_open_positions(
            session, stale_after=dt.timedelta(days=7), warned_stale=set(), fetch=fake_fetch
        )
    )

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "YES"
    assert pos.resolved_at is not None
    # realized = shares - cost - fee = 10 - 9 - 0.2 = 0.8, coincide con net_pnl del fill
    assert round(pos.realized_pnl, 6) == 0.8


def test_unresolved_market_leaves_position_open():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=False, resolved=False, winning_outcome=None)

    asyncio.run(
        resolve_open_positions(
            session, stale_after=dt.timedelta(days=7), warned_stale=set(), fetch=fake_fetch
        )
    )

    session.refresh(pos)
    assert pos.status == "abierta"
    assert pos.realized_pnl is None


def test_stale_closed_position_gets_flagged_pendiente():
    session = _session()
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
    pos = _open_position(session, opened_at=old)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=False, winning_outcome=None)

    warned: set[int] = set()
    asyncio.run(
        resolve_open_positions(session, stale_after=dt.timedelta(days=7), warned_stale=warned, fetch=fake_fetch)
    )

    session.refresh(pos)
    assert pos.status == "pendiente"
    assert pos.id in warned


def test_network_error_does_not_crash_and_leaves_position_untouched():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return None

    asyncio.run(
        resolve_open_positions(
            session, stale_after=dt.timedelta(days=7), warned_stale=set(), fetch=fake_fetch
        )
    )

    session.refresh(pos)
    assert pos.status == "abierta"


def test_resolution_backfills_longshot_signal_resolution():
    session = _session()
    _open_position(session, market_id="0xshared")
    session.add(
        Opportunity(
            market_id="0xshared",
            question="¿Test?",
            signal_type="longshot_bias",
            outcome="NO",
            outcome_price=0.05,
            corrected_price=0.10,
            trade_direction="YES",
        )
    )
    session.commit()

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(
        resolve_open_positions(
            session, stale_after=dt.timedelta(days=7), warned_stale=set(), fetch=fake_fetch
        )
    )

    resolution = session.execute(
        select(SignalResolution).where(SignalResolution.market_id == "0xshared")
    ).scalar_one()
    assert resolution.resolved_outcome == "YES"


def test_no_open_positions_is_a_noop():
    session = _session()

    async def fake_fetch(market_id):
        raise AssertionError("no debería llamarse sin posiciones abiertas")

    asyncio.run(
        resolve_open_positions(
            session, stale_after=dt.timedelta(days=7), warned_stale=set(), fetch=fake_fetch
        )
    )
