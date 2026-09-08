from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from polybot.config import settings
from polybot.execution import kill_switch, reconciliation
from polybot.persistence.models import Base, RealPosition


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def _override(**kwargs):
    old = {k: getattr(settings, k) for k in kwargs}
    for k, v in kwargs.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


def test_expected_balance_with_no_positions_equals_capital_base():
    session_factory = _session_factory()
    with _override(real_capital_base_usd=20.0), session_factory() as session:
        assert reconciliation.expected_balance_usd(session) == 20.0


def test_expected_balance_subtracts_committed_capital():
    session_factory = _session_factory()
    with _override(real_capital_base_usd=20.0), session_factory() as session:
        session.add(
            RealPosition(
                market_id="0xa", cluster_id="c1", question="q", status="abierta",
                shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
                fee_paid=0.1, net_pnl_expected=0.2,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 15.0


def test_expected_balance_adds_back_realized_pnl_of_closed_positions():
    session_factory = _session_factory()
    with _override(real_capital_base_usd=20.0), session_factory() as session:
        session.add(
            RealPosition(
                market_id="0xa", cluster_id="c1", question="q", status="cerrada",
                shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
                fee_paid=0.1, net_pnl_expected=0.2, realized_pnl=1.5,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 21.5


def test_no_divergence_within_threshold_is_a_noop(tmp_path):
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(real_capital_base_usd=20.0, real_reconciliation_threshold_usd=0.50, real_kill_switch_flag_path=str(flag)):
        with session_factory() as session:
            triggered = reconciliation.check_balance_reconciliation(session, 20.30)
        assert triggered is False
        assert not flag.exists()


def test_divergence_beyond_threshold_halts_and_logs(tmp_path):
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(real_capital_base_usd=20.0, real_reconciliation_threshold_usd=0.50, real_kill_switch_flag_path=str(flag)):
        with session_factory() as session:
            triggered = reconciliation.check_balance_reconciliation(session, 11.46)
        assert triggered is True
        assert kill_switch.is_halted(str(flag)) is True
