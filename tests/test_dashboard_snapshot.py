from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from polybot.config import settings
from polybot.dashboard.snapshot import _build_real_trading_status, build_snapshot
from polybot.execution.event_log import log_event
from polybot.persistence.models import (
    Base,
    Opportunity,
    RealPosition,
    SignalResolution,
    SimulatedPosition,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


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


# ============================================================================
# Estado de Fase 3 en el dashboard (2026-09-14) -- ver CLAUDE.md, sección
# Fase 3, y el docstring de `RealTradingStatus`/`_build_real_trading_status`.
# ============================================================================


def _unconfirmed_position(session, **overrides) -> RealPosition:
    defaults = {
        "market_id": "0xmarket",
        "cluster_id": "event-1",
        "question": "¿Test sin confirmar?",
        "status": "sin_confirmar",
        "yes_shares": 0.0,
        "no_shares": 0.0,
        "yes_price_avg": 0.0,
        "no_price_avg": 0.0,
        "cost_usd": 0.0,
        "fee_paid": 0.0,
        "net_pnl_expected": 0.0,
    }
    defaults.update(overrides)
    pos = RealPosition(**defaults)
    session.add(pos)
    session.commit()
    return pos


def test_real_trading_status_active_when_not_halted(tmp_path):
    flag = tmp_path / "HALT"
    session = _session()
    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.enabled is True
    assert status.halted is False
    assert status.halt_reason is None
    assert status.halted_since is None
    assert status.state == "activo"


def test_real_trading_status_lists_locked_markets_even_when_active(tmp_path):
    """Bloqueo per-mercado (2026-09-14): un `sin_confirmar` normalmente ya NO
    causa un halt GLOBAL (ver `execution.real_executor`), así que `state`
    puede seguir "activo" mientras 1+ mercados están bloqueados en paralelo
    -- `locked_markets` es independiente de `state`/`halted`."""
    flag = tmp_path / "HALT"
    session = _session()
    _unconfirmed_position(session, market_id="0xabc", question="¿Gana A?")
    _unconfirmed_position(session, market_id="0xdef", question="¿Gana B?")

    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.halted is False
    assert status.state == "activo"
    assert sorted(status.locked_markets) == [("0xabc", "¿Gana A?"), ("0xdef", "¿Gana B?")]


def test_real_trading_status_no_locked_markets_when_none_sin_confirmar(tmp_path):
    flag = tmp_path / "HALT"
    session = _session()
    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.locked_markets == []


def test_real_trading_status_manual_for_non_sin_confirmar_halt(tmp_path):
    """Drawdown, leg imbalance de red, leg_size_mismatch, reconciliación --
    cualquier causa que no sea "fill sin confirmar" es manual de entrada."""
    flag = tmp_path / "HALT"
    flag.write_text("2026-09-14T01:39:50+00:00 -- desbalance residual de shares en mercado 0xabc (3.9% > umbral 2.0%)\n")
    session = _session()
    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.halted is True
    assert status.state == "manual"
    assert "desbalance residual" in status.halt_reason
    assert status.halted_since == dt.datetime(2026, 9, 14, 1, 39, 50, tzinfo=dt.UTC)


def test_real_trading_status_auto_recuperando_for_unresolved_sin_confirmar(tmp_path):
    """Halt por "fill sin confirmar" con una posición sin_confirmar todavía
    sin un `auto_recovery_capped` -- sigue en proceso, se puede resolver sola."""
    flag = tmp_path / "HALT"
    flag.write_text(
        "2026-09-14T22:19:04+00:00 -- fill sin confirmar en mercado 0xabc (pata YES) "
        "-- no se puede garantizar el estado real de la posición\n"
    )
    session = _session()
    _unconfirmed_position(session, market_id="0xabc")

    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.halted is True
    assert status.state == "auto_recuperando"


def test_real_trading_status_manual_once_auto_recovery_cap_reached(tmp_path):
    """Mismo halt por "fill sin confirmar", pero la posición YA tiene un
    `auto_recovery_capped` -- pasa a ser manual, igual que cualquier otro halt,
    aunque la causa original haya sido sin_confirmar."""
    flag = tmp_path / "HALT"
    flag.write_text(
        "2026-09-14T22:19:04+00:00 -- fill sin confirmar en mercado 0xabc (pata YES) "
        "-- no se puede garantizar el estado real de la posición\n"
    )
    session = _session()
    pos = _unconfirmed_position(session, market_id="0xabc")
    log_event(session, "auto_recovery_capped", "critical", "tope alcanzado", real_position_id=pos.id)

    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.halted is True
    assert status.state == "manual"


def test_real_trading_status_malformed_flag_does_not_crash(tmp_path):
    """Un flag sin el formato esperado (ej. escrito a mano, sin el separador
    ' -- ') no debe romper el dashboard -- se degrada a "manual" sin motivo."""
    flag = tmp_path / "HALT"
    flag.write_text("algo sin el formato esperado\n")
    session = _session()
    with _override(real_trading_enabled=True, real_kill_switch_flag_path=str(flag)):
        status = _build_real_trading_status(session)

    assert status.halted is True
    assert status.state == "manual"
    assert status.halted_since is None


def test_build_snapshot_includes_real_trading_status(tmp_path):
    """`build_snapshot` expone el estado de Fase 3 sin romper el resto del
    snapshot -- integración mínima end-to-end."""
    flag = tmp_path / "HALT"
    session = _session()
    with _override(real_trading_enabled=False, real_kill_switch_flag_path=str(flag)):
        snap = build_snapshot(session)

    assert snap.real_trading.state == "activo"
    assert snap.real_trading.enabled is False
