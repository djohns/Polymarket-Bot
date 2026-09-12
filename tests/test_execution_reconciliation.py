from __future__ import annotations

import asyncio
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
    """Fuerza checkpoint=None explícitamente -- sin esto, un `.env` real con
    REAL_BALANCE_CHECKPOINT_USD ya seteado (como el de producción en la VPS)
    se filtra al singleton `settings` y hace que este test tome la rama de
    checkpoint en vez de la nominal que pretende ejercitar."""
    session_factory = _session_factory()
    with (
        _override(real_capital_base_usd=20.0, real_balance_checkpoint_usd=None, real_balance_checkpoint_at=None),
        session_factory() as session,
    ):
        assert reconciliation.expected_balance_usd(session) == 20.0


def test_expected_balance_subtracts_committed_capital():
    session_factory = _session_factory()
    with (
        _override(real_capital_base_usd=20.0, real_balance_checkpoint_usd=None, real_balance_checkpoint_at=None),
        session_factory() as session,
    ):
        session.add(
            RealPosition(
                market_id="0xa", cluster_id="c1", question="q", status="abierta",
                yes_shares=10, no_shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
                fee_paid=0.1, net_pnl_expected=0.2,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 15.0


def test_expected_balance_adds_back_realized_pnl_of_closed_positions():
    session_factory = _session_factory()
    with (
        _override(real_capital_base_usd=20.0, real_balance_checkpoint_usd=None, real_balance_checkpoint_at=None),
        session_factory() as session,
    ):
        session.add(
            RealPosition(
                market_id="0xa", cluster_id="c1", question="q", status="cerrada",
                yes_shares=10, no_shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
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
                yes_shares=5, no_shares=5, yes_price_avg=0.4, no_price_avg=0.0, cost_usd=2.0,
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
                yes_shares=5, no_shares=5, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=4.0,
                fee_paid=0.0, net_pnl_expected=0.1,
            )
        )
        session.commit()
        assert reconciliation.expected_balance_usd(session) == 22.727494 - 4.0


def test_no_divergence_within_threshold_is_a_noop(tmp_path):
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            triggered = reconciliation.check_balance_reconciliation(session, 20.30)
        assert triggered is False
        assert not flag.exists()


def test_divergence_beyond_threshold_halts_and_logs(tmp_path):
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
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
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            session.add(
                RealPosition(
                    market_id="0xa", cluster_id="c1", question="q", status="cerrada",
                    yes_shares=10, no_shares=10, yes_price_avg=0.4, no_price_avg=0.5, cost_usd=5.0,
                    fee_paid=0.1, net_pnl_expected=0.2, realized_pnl=0.06,
                    resolved_outcome="NO", resolved_at=None,
                )
            )
            session.commit()
            # capital base 20 - 0 comprometido (ya cerrada) + 0.06 realizado = 20.06
            triggered = reconciliation.check_balance_reconciliation(session, 20.06)
        assert triggered is False
        assert not flag.exists()


def _pending_position(**overrides) -> RealPosition:
    defaults = {
        "market_id": "0xpending", "cluster_id": "c1", "question": "q", "status": "pendiente",
        "yes_shares": 5, "no_shares": 0, "yes_price_avg": 0.5, "no_price_avg": 0.0, "cost_usd": 5.0,
        "fee_paid": 0.1, "net_pnl_expected": 0.0,
    }
    defaults.update(overrides)
    return RealPosition(**defaults)


def test_positive_divergence_with_pending_position_self_heals_within_grace_window(tmp_path):
    """Escenario Santa Fe/Al Ittihad: la posición "pendiente" resuelve y el job
    la cierra durante la ventana de gracia -- para el re-chequeo, la
    divergencia ya desapareció y no debe activarse el kill-switch."""
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_reconciliation_grace_period_seconds=90.0,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            pos = _pending_position()
            session.add(pos)
            session.commit()

            sleep_calls: list[float] = []

            async def fake_sleep(seconds: float) -> None:
                sleep_calls.append(seconds)
                # Simula que el job de resolución cerró la posición durante la espera.
                pos.status = "cerrada"
                pos.realized_pnl = 0.15
                pos.cost_usd = 5.0
                session.commit()

            async def recheck_balance() -> float:
                return 20.15  # balance ya reflejaba la redención

            # expected antes: 20 - 5 (comprometido) = 15; actual=20 -> diff=+5, dispara la gracia
            triggered = asyncio.run(
                reconciliation.check_balance_reconciliation_with_grace(
                    session, 20.0, recheck_balance=recheck_balance, sleep=fake_sleep
                )
            )
        assert triggered is False
        assert not flag.exists()
        assert sleep_calls == [90.0]


def test_positive_divergence_with_pending_position_halts_if_it_persists(tmp_path):
    """Si la posición sigue "pendiente" tras la ventana de gracia (no fue el
    lag benigno, o el job todavía no corrió), el kill-switch se activa igual
    que antes -- la tolerancia no es un pase libre indefinido."""
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_reconciliation_grace_period_seconds=90.0,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            session.add(_pending_position())
            session.commit()

            sleep_calls: list[float] = []

            async def fake_sleep(seconds: float) -> None:
                sleep_calls.append(seconds)  # nada cambia -- la posición sigue "pendiente"

            async def recheck_balance() -> float:
                return 20.0  # divergencia idéntica en el re-chequeo

            triggered = asyncio.run(
                reconciliation.check_balance_reconciliation_with_grace(
                    session, 20.0, recheck_balance=recheck_balance, sleep=fake_sleep
                )
            )
        assert triggered is True
        assert kill_switch.is_halted(str(flag)) is True
        assert sleep_calls == [90.0]


def test_negative_divergence_halts_immediately_even_with_pending_position(tmp_path):
    """CRÍTICO: la asimetría es a propósito -- una divergencia NEGATIVA (falta
    plata) nunca recibe el margen de espera, sin importar si hay una posición
    "pendiente" en curso. Podría ser una pérdida real o un bug nuevo (mismo
    signo que el incidente del 2026-09-08)."""
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_reconciliation_grace_period_seconds=90.0,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            session.add(_pending_position())
            session.commit()

            sleep_calls: list[float] = []

            async def fake_sleep(seconds: float) -> None:
                sleep_calls.append(seconds)

            async def recheck_balance() -> float:
                raise AssertionError("no debería recheckear una divergencia negativa")

            # expected = 20 - 5 = 15; actual=11 -> diff=-4, negativa
            triggered = asyncio.run(
                reconciliation.check_balance_reconciliation_with_grace(
                    session, 11.0, recheck_balance=recheck_balance, sleep=fake_sleep
                )
            )
        assert triggered is True
        assert kill_switch.is_halted(str(flag)) is True
        assert sleep_calls == []  # nunca esperó


def test_positive_divergence_without_pending_position_halts_immediately(tmp_path):
    """Sin ninguna posición "pendiente", no hay candidata a estar resolviendo
    ahora mismo -- la tolerancia no aplica aunque la divergencia sea positiva."""
    session_factory = _session_factory()
    flag = tmp_path / "HALT"
    with _override(
        real_capital_base_usd=20.0,
        real_reconciliation_threshold_usd=0.50,
        real_reconciliation_grace_period_seconds=90.0,
        real_kill_switch_flag_path=str(flag),
        real_balance_checkpoint_usd=None,
        real_balance_checkpoint_at=None,
    ):
        with session_factory() as session:
            sleep_calls: list[float] = []

            async def fake_sleep(seconds: float) -> None:
                sleep_calls.append(seconds)

            async def recheck_balance() -> float:
                raise AssertionError("no debería recheckear sin posiciones pendientes")

            triggered = asyncio.run(
                reconciliation.check_balance_reconciliation_with_grace(
                    session, 25.0, recheck_balance=recheck_balance, sleep=fake_sleep
                )
            )
        assert triggered is True
        assert sleep_calls == []
