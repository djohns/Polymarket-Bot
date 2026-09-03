from __future__ import annotations

import datetime as dt

from polybot.dashboard.render import render_html
from polybot.dashboard.snapshot import Snapshot


def _empty_snapshot() -> Snapshot:
    return Snapshot(
        generated_at=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
        status_counts={},
        equity_curve=[],
        unrealized_pnl=0.0,
        hit_rate=None,
        resolved_count=0,
        avg_net_margin_pct=None,
        exposure_by_market=[],
        exposure_by_cluster=[],
        arb_signals_detected=0,
        arb_positions_simulated=0,
        longshot_signals_detected=0,
    )


def test_renders_without_crashing_on_empty_data():
    html = render_html(_empty_snapshot())
    assert "<html" in html
    assert "Sin posiciones resueltas todavía" in html
    assert "Sin muestras todavía" in html


def test_renders_real_numbers_not_placeholders():
    snap = _empty_snapshot()
    snap.status_counts = {"abierta": 3, "cerrada": 5, "pendiente": 1}
    snap.equity_curve = [
        (dt.datetime(2026, 9, 1, tzinfo=dt.UTC), 1.5),
        (dt.datetime(2026, 9, 2, tzinfo=dt.UTC), 3.25),
    ]
    snap.hit_rate = 1.0
    snap.resolved_count = 5
    snap.avg_net_margin_pct = 0.047
    snap.exposure_by_market = [("0xabc", "¿Ganará X?", 42.5)]
    snap.exposure_by_cluster = [("event-1", 42.5)]
    snap.arb_signals_detected = 71
    snap.arb_positions_simulated = 30
    snap.longshot_signals_detected = 500

    html = render_html(snap)
    assert "$3.25" in html  # último punto de la curva de equity
    assert "100.00%" in html  # hit rate
    assert "4.70%" in html  # margen neto promedio
    assert "71" in html and "30" in html
    assert "¿Ganará X?" in html
    assert "event-1" in html


def test_mentions_brier_arb_limitation_in_the_page_itself():
    html = render_html(_empty_snapshot())
    assert "No incluye arb" in html
