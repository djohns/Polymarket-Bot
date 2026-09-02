from __future__ import annotations

from polybot.risk.kelly import fractional_kelly


def test_positive_edge_scaled_by_fraction():
    # p=0.60, price=0.50 -> Kelly completo f* = (0.60-0.50)/(1-0.50) = 0.20
    assert round(fractional_kelly(0.60, 0.50, fraction=1.0), 4) == 0.20
    assert round(fractional_kelly(0.60, 0.50, fraction=0.25), 4) == 0.05


def test_no_edge_returns_zero():
    assert fractional_kelly(0.50, 0.50, fraction=0.25) == 0.0
    assert fractional_kelly(0.40, 0.50, fraction=0.25) == 0.0


def test_clamped_to_one():
    # edge teórico > 1 con fracción agresiva; el resultado no debe superar el capital total
    assert fractional_kelly(0.90, 0.10, fraction=2.0) == 1.0


def test_invalid_price_returns_zero():
    assert fractional_kelly(0.6, 0.0, fraction=0.25) == 0.0
    assert fractional_kelly(0.6, 1.0, fraction=0.25) == 0.0
