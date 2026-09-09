from __future__ import annotations

import datetime as dt
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


def test_checkpoint_ignores_positions_opened_before_it():
    """Sin esto, una posición ya reflejada en el balance del checkpoint (por
    estar cerrada antes de que se tomara) se restaría dos veces -- ver
    CLAUDE.md, sección Fase 3, incidente del 2026-09-08."""
    session_factory = _session_factory()
    with _override(
        real_balance_checkpoint_usd=22.727494,
        real_balance_checkpoint_at="2026-09-08T22:10:00+00:00",
    ), session_factory() as session:
        session.add(
            RealPosition(
                market_id="0xold", cluster_id="c1", question="q", status="cerrada",
                opened_at=dt.datetime(2026, 9, 8, 18, 0, tzinfo=dt.UTC),
                shares=5, yes_price_avg=0.4, no_price_avg=0.0, cost_usd=2.0,
                fee_paid=0.0, net_pnl_expected=0.0, realized_pnl=-0.45,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 22.727494


def test_checkpoint_counts_positions_opened_after_it():
    session_factory = _session_factory()
    with _override(
        real_balance_checkpoint_usd=22.727494,
        real_balance_checkpoint_at="2026-09-08T22:10:00+00:00",
    ), session_factory() as session:
        session.add(
            RealPosition(
                market_id="0xnew", cluster_id="c1", question="q", status="abierta",
                opened_at=dt.datetime(2026, 9, 8, 23, 0, tzinfo=dt.UTC),
                shares=5, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=4.0,
                fee_paid=0.0, net_pnl_expected=0.1,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 22.727494 - 4.0


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


def test_resolved_position_via_job_does_not_cause_false_divergence(tmp_path):
    """Reproduce el escenario del 2026-09-09: una posición real que resuelve y
    se redime on-chain no debe seguir contando como "comprometida" una vez
    que el job de resolución la marca "cerrada" -- si el job hizo su trabajo,
    la reconciliación no debe dispararse."""
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(real_capital_base_usd=20.0, real_reconciliation_threshold_usd=0.50, real_kill_switch_flag_path=str(flag)):
        with session_factory() as session:
            session.add(
                RealPosition(
                    market_id="0xa", cluster_id="c1", question="q", status="cerrada",
                    shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
                    fee_paid=0.1, net_pnl_expected=0.2, realized_pnl=0.06,
                    resolved_outcome="NO", resolved_at=None,
                )
            )
            session.commit()
            # capital base 20 - 0 comprometido (ya cerrada) + 0.06 realizado = 20.06
            triggered = reconciliation.check_balance_reconciliation(session, 20.06)
        assert triggered is False
        assert not flag.exists()
