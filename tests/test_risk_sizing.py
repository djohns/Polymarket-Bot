from contextlib import contextmanager

from polybot.config import settings
from polybot.risk.sizing import max_capital_for_real_trade


@contextmanager
def _override(**kwargs):
    """`Settings` es un dataclass frozen -- no se puede usar monkeypatch.setattr
    directo sobre la instancia. Se muta con `object.__setattr__` (bypassea el
    frozen) y se restaura al salir del bloque."""
    old = {k: getattr(settings, k) for k in kwargs}
    for k, v in kwargs.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


def test_no_exposure_caps_at_min_of_per_market_and_per_cluster():
    with _override(real_max_exposure_per_market_usd=5.0, real_max_exposure_per_cluster_usd=5.0, real_capital_base_usd=20.0):
        assert max_capital_for_real_trade(0.0, 0.0, 0.0) == 5.0


def test_market_exposure_reduces_available_cap():
    with _override(real_max_exposure_per_market_usd=5.0, real_max_exposure_per_cluster_usd=5.0, real_capital_base_usd=20.0):
        assert max_capital_for_real_trade(3.0, 0.0, 3.0) == 2.0


def test_total_capital_base_is_a_hard_ceiling():
    with _override(real_max_exposure_per_market_usd=5.0, real_max_exposure_per_cluster_usd=5.0, real_capital_base_usd=20.0):
        # market/cluster nuevos (0 exposición ahí), pero ya hay $18 comprometidos en otro lado
        assert max_capital_for_real_trade(0.0, 0.0, 18.0) == 2.0


def test_never_negative():
    with _override(real_max_exposure_per_market_usd=5.0, real_max_exposure_per_cluster_usd=5.0, real_capital_base_usd=20.0):
        assert max_capital_for_real_trade(10.0, 0.0, 10.0) == 0.0
