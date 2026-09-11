from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.execution.real_resolution_job import resolve_open_real_positions
from polybot.execution.resolution import ResolutionResult
from polybot.persistence.models import Base, RealExecutionEvent, RealPosition


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _open_position(session, **overrides) -> RealPosition:
    defaults = {
        "market_id": "0xmarket",
        "cluster_id": "event-1",
        "question": "¿Test?",
        "status": "abierta",
        "yes_shares": 10.0,
        "no_shares": 10.0,
        "yes_price_avg": 0.4,
        "no_price_avg": 0.5,
        "cost_usd": 9.0,
        "fee_paid": 0.2,
        "net_pnl_expected": 0.8,
    }
    defaults.update(overrides)
    pos = RealPosition(**defaults)
    session.add(pos)
    session.commit()
    return pos


def test_resolved_basket_position_closes_with_guaranteed_payout_formula():
    """Ambas patas llenaron con el mismo tamaño (canasta calzada) -- payout
    garantizado de $1/share sin importar el resultado. `cost_usd` ya es el
    gasto real total (fee incluido, ver `real_executor._confirmed_fill`), así
    que la fórmula no resta `fee_paid` aparte."""
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "YES"
    assert pos.resolved_at is not None
    # realized = yes_shares - cost_usd = 10 - 9 = 1.0
    assert round(pos.realized_pnl, 6) == 1.0


def test_resolved_leg_imbalance_position_wins_pays_shares_minus_cost():
    """status="pendiente" (leg imbalance total) -- sólo la pata YES tiene
    capital real (`no_shares=0`). Si YES ganó, el payout es yes_shares - cost_usd."""
    session = _session()
    pos = _open_position(session, status="pendiente", no_shares=0.0, cost_usd=4.0, fee_paid=0.0)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "YES"
    assert round(pos.realized_pnl, 6) == 6.0  # 10 - 4.0


def test_resolved_leg_imbalance_position_loses_all_cost():
    session = _session()
    pos = _open_position(session, status="pendiente", no_shares=0.0, cost_usd=4.0, fee_paid=0.0)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "NO"
    # ganó el lado que nunca tuvo shares reales (no_shares=0) -- payout 0, pérdida total del costo
    assert round(pos.realized_pnl, 6) == -4.0


def test_resolved_residual_mismatch_position_pays_winning_side_real_shares():
    """status="pendiente" por desbalance residual (no por leg imbalance total)
    -- ambas patas tienen shares reales pero distintas (ver
    `real_executor._leg_imbalance_pct`). El payout usa las shares REALES del
    lado ganador, no un promedio ni la estimación pre-trade."""
    session = _session()
    pos = _open_position(
        session,
        status="pendiente",
        yes_shares=5.0,
        no_shares=4.0,
        cost_usd=4.5,
        fee_paid=0.0,
        leg_imbalance_pct=0.20,
    )

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    # ganó NO -- payout con las shares reales de NO (4.0), no las de YES (5.0)
    assert round(pos.realized_pnl, 6) == -0.5  # 4.0 - 4.5


def test_unresolved_market_leaves_position_open():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=False, resolved=False, winning_outcome=None)

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "abierta"
    assert pos.realized_pnl is None


def test_network_error_does_not_crash_and_leaves_position_untouched():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return None

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "abierta"


def test_no_open_positions_is_a_noop():
    session = _session()

    async def fake_fetch(market_id):
        raise AssertionError("no debería llamarse sin posiciones abiertas")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))


def test_resolution_persists_event_to_db():
    session = _session()
    _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    assert any(e.event_type == "position_resolved" for e in events)
