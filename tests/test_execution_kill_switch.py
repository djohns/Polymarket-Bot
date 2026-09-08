from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.execution import kill_switch
from polybot.persistence.models import Base, RealExecutionEvent


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_is_halted_false_when_no_flag(tmp_path):
    flag = tmp_path / "HALT"
    assert kill_switch.is_halted(str(flag)) is False


def test_halt_creates_flag_file(tmp_path):
    flag = tmp_path / "sub" / "HALT"
    kill_switch.halt("motivo de prueba", str(flag))
    assert flag.exists()
    assert "motivo de prueba" in flag.read_text()


def test_halt_is_idempotent_does_not_overwrite(tmp_path):
    flag = tmp_path / "HALT"
    kill_switch.halt("primer motivo", str(flag))
    original = flag.read_text()
    kill_switch.halt("segundo motivo", str(flag))
    assert flag.read_text() == original


def test_check_balance_kill_switch_triggers_below_floor(tmp_path):
    from polybot.config import settings

    flag = tmp_path / "HALT"
    old = settings.real_kill_switch_balance_floor_usd
    object.__setattr__(settings, "real_kill_switch_balance_floor_usd", 15.0)
    try:
        assert kill_switch.check_balance_kill_switch(10.0, str(flag)) is True
        assert flag.exists()
    finally:
        object.__setattr__(settings, "real_kill_switch_balance_floor_usd", old)


def test_halt_with_session_persists_event(tmp_path):
    """Corrección post-incidente (2026-09-08): el motivo del kill-switch debe
    quedar en la DB, no sólo en journald (que ya demostró rotar en horas)."""
    flag = tmp_path / "HALT"
    session_factory = _session_factory()
    with session_factory() as session:
        kill_switch.halt("prueba con persistencia", str(flag), session=session)

    with session_factory() as session:
        events = session.execute(select(RealExecutionEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "kill_switch_triggered"
        assert events[0].severity == "critical"
        assert "prueba con persistencia" in events[0].message


def test_halt_with_session_does_not_duplicate_event_if_already_halted(tmp_path):
    flag = tmp_path / "HALT"
    session_factory = _session_factory()
    with session_factory() as session:
        kill_switch.halt("primer motivo", str(flag), session=session)
        kill_switch.halt("segundo motivo", str(flag), session=session)

    with session_factory() as session:
        events = session.execute(select(RealExecutionEvent)).scalars().all()
        assert len(events) == 1


def test_check_balance_kill_switch_ok_above_floor(tmp_path):
    from polybot.config import settings

    flag = tmp_path / "HALT"
    old = settings.real_kill_switch_balance_floor_usd
    object.__setattr__(settings, "real_kill_switch_balance_floor_usd", 15.0)
    try:
        assert kill_switch.check_balance_kill_switch(20.0, str(flag)) is False
        assert not flag.exists()
    finally:
        object.__setattr__(settings, "real_kill_switch_balance_floor_usd", old)
