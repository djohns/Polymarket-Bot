from __future__ import annotations

import datetime as dt

from polybot.signals.brier import brier_score, brier_score_by_window


def test_perfect_calibration_scores_zero():
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_worst_case_scores_one():
    assert brier_score([(1.0, False), (0.0, True)]) == 1.0


def test_coin_flip_scores_quarter():
    assert round(brier_score([(0.5, True), (0.5, False)]), 4) == 0.25


def test_empty_returns_none():
    assert brier_score([]) is None


def test_grouped_by_day():
    d1 = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
    d2 = dt.datetime(2026, 9, 2, 10, 0, tzinfo=dt.UTC)
    predictions = [
        (d1, 0.9, True),
        (d1, 0.1, False),
        (d2, 0.5, True),
    ]
    report = brier_score_by_window(predictions, window="day")
    assert list(report.keys()) == ["2026-09-01", "2026-09-02"]
    assert round(report["2026-09-01"][0], 4) == 0.01
    assert report["2026-09-01"][1] == 2
    assert report["2026-09-02"] == (0.25, 1)
