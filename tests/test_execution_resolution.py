from __future__ import annotations

from polybot.execution.resolution import _parse_resolution


def test_open_market_is_unresolved():
    data = {"closed": False, "tokens": [{"outcome": "Yes", "winner": False}]}
    result = _parse_resolution(data)
    assert result.closed is False
    assert result.resolved is False
    assert result.winning_outcome is None


def test_resolved_yes_market():
    data = {
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": True, "price": 1},
            {"outcome": "No", "winner": False, "price": 0},
        ],
    }
    result = _parse_resolution(data)
    assert result.closed is True
    assert result.resolved is True
    assert result.winning_outcome == "YES"


def test_resolved_no_market():
    data = {
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": False, "price": 0},
            {"outcome": "No", "winner": True, "price": 1},
        ],
    }
    result = _parse_resolution(data)
    assert result.winning_outcome == "NO"


def test_closed_without_winner_is_not_resolved():
    """Cerrado (trading detenido) pero el oráculo todavía no determinó un ganador único
    -- posible disputa o propuesta pendiente. No debe romper, sólo marcar no-resuelto."""
    data = {
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": False},
            {"outcome": "No", "winner": False},
        ],
    }
    result = _parse_resolution(data)
    assert result.closed is True
    assert result.resolved is False
    assert result.winning_outcome is None


def test_closed_with_no_tokens_does_not_crash():
    result = _parse_resolution({"closed": True, "tokens": []})
    assert result.resolved is False
