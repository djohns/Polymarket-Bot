
from polybot.execution import kill_switch


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
