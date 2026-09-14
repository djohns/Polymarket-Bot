from __future__ import annotations

import datetime as dt

from polybot.dashboard.render import render_html
from polybot.dashboard.snapshot import RealTradingStatus, Snapshot


def _active_real_trading_status() -> RealTradingStatus:
    return RealTradingStatus(enabled=False, halted=False, halt_reason=None, halted_since=None, state="activo")


def _empty_snapshot() -> Snapshot:
    return Snapshot(
        generated_at=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
        real_trading=_active_real_trading_status(),
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


# ============================================================================
# Panel de estado de Fase 3 (2026-09-14) -- ver CLAUDE.md, sección Fase 3.
# ============================================================================


def test_active_state_shows_operable_and_no_halt_reason():
    snap = _empty_snapshot()
    snap.real_trading = RealTradingStatus(
        enabled=True, halted=False, halt_reason=None, halted_since=None, state="activo"
    )

    html = render_html(snap)

    assert 'class="real-trading-panel rt-activo"' in html
    assert "Activo y operable" in html
    assert "REAL_TRADING_ENABLED" in html
    assert "Sin activar" in html


def test_manual_state_shows_exact_halt_reason_and_duration():
    snap = _empty_snapshot()
    snap.generated_at = dt.datetime(2026, 9, 14, 4, 0, 0, tzinfo=dt.UTC)
    snap.real_trading = RealTradingStatus(
        enabled=True,
        halted=True,
        halt_reason="desbalance residual de shares en mercado 0xabc (3.9% > umbral 2.0%)",
        halted_since=dt.datetime(2026, 9, 14, 1, 39, 50, tzinfo=dt.UTC),
        state="manual",
    )

    html = render_html(snap)

    assert 'class="real-trading-panel rt-manual"' in html
    assert "esperando aprobación manual" in html
    # el motivo persistido por kill_switch.halt() aparece tal cual, sin reescribirlo
    # (el ">" queda html-escapado como "&gt;", comportamiento correcto de _esc)
    assert "desbalance residual de shares en mercado 0xabc (3.9% &gt; umbral 2.0%)" in html
    assert "2h 20m" in html  # duración desde halted_since hasta generated_at


def test_auto_recuperando_state_is_visually_distinct_from_manual():
    snap = _empty_snapshot()
    snap.real_trading = RealTradingStatus(
        enabled=True,
        halted=True,
        halt_reason="fill sin confirmar en mercado 0xabc (pata YES) -- no se puede garantizar el estado real",
        halted_since=dt.datetime(2026, 9, 14, 22, 19, 4, tzinfo=dt.UTC),
        state="auto_recuperando",
    )

    html = render_html(snap)

    assert 'class="real-trading-panel rt-auto"' in html
    assert "auto-recuperándose solo" in html
    assert 'class="real-trading-panel rt-manual"' not in html
    assert 'class="real-trading-panel rt-activo"' not in html


def test_malformed_flag_reason_falls_back_to_a_visible_placeholder():
    snap = _empty_snapshot()
    snap.real_trading = RealTradingStatus(
        enabled=True, halted=True, halt_reason=None, halted_since=None, state="manual"
    )

    html = render_html(snap)

    assert "motivo no disponible" in html
