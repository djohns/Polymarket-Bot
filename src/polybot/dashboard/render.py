"""Fase 2, parte 3: renderiza el `Snapshot` como un reporte HTML autocontenido.

Sin dependencias externas (ni CDN ni librería de gráficos): los charts son SVG
generado a mano. Así el archivo se puede abrir localmente sin conexión, y el
mismo HTML sirve también para publicarlo como Artifact en el chat sin tocar
nada -- ver `scripts/publish_dashboard.py`.
"""
from __future__ import annotations

import datetime as dt
import html

from polybot.dashboard.snapshot import Snapshot

_STATUS_LABELS = {"abierta": "Abiertas", "cerrada": "Cerradas / resueltas", "pendiente": "Pendientes"}


def _fmt_money(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}"


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.2f}%"


def _fmt_dt(x: dt.datetime | None) -> str:
    if x is None:
        return "—"
    return x.strftime("%Y-%m-%d %H:%M UTC")


def _esc(x: object) -> str:
    return html.escape(str(x))


def _svg_line_chart(points: list[tuple[dt.datetime, float]], *, width: int = 720, height: int = 220) -> str:
    if not points:
        return '<p class="empty">Sin posiciones resueltas todavía — la curva de equity aparece acá cuando haya al menos una.</p>'

    pad = 40
    xs = [p[0].timestamp() for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys + [0.0]), max(ys + [0.0])
    if x_max == x_min:
        x_max = x_min + 1
    if y_max == y_min:
        y_max = y_min + 1

    def sx(x: float) -> float:
        return pad + (x - x_min) / (x_max - x_min) * (width - 2 * pad)

    def sy(y: float) -> float:
        return height - pad - (y - y_min) / (y_max - y_min) * (height - 2 * pad)

    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    zero_y = sy(0.0)
    last_x, last_y = sx(xs[-1]), sy(ys[-1])

    return f"""
<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Curva de equity">
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" class="zero-line" />
  <path d="{path}" class="equity-line" fill="none" />
  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" class="equity-dot" />
  <text x="{pad}" y="{height - 10}" class="axis-label">{_esc(points[0][0].date())}</text>
  <text x="{width - pad}" y="{height - 10}" class="axis-label" text-anchor="end">{_esc(points[-1][0].date())}</text>
  <text x="{pad}" y="{sy(y_max) + 12:.1f}" class="axis-label">{_fmt_money(y_max)}</text>
  <text x="{pad}" y="{sy(y_min) - 4:.1f}" class="axis-label">{_fmt_money(y_min)}</text>
</svg>
"""


def _svg_bar_chart(labels_values: list[tuple[str, float | None, int]], *, width: int = 720, height: int = 200) -> str:
    scored = [(label, score, n) for label, score, n in labels_values if score is not None]
    if not scored:
        return '<p class="empty">Sin muestras todavía (cobertura parcial — ver nota abajo).</p>'

    pad_l, pad_r, pad_t, pad_b = 40, 10, 10, 30
    bar_gap = 6
    n_bars = len(scored)
    bar_w = max(4.0, (width - pad_l - pad_r) / n_bars - bar_gap)
    max_score = max(0.25, max(s for _, s, _ in scored))

    bars = []
    for i, (label, score, n) in enumerate(scored):
        x = pad_l + i * (bar_w + bar_gap)
        bar_h = (score / max_score) * (height - pad_t - pad_b)
        y = height - pad_b - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" class="brier-bar">'
            f"<title>{_esc(label)}: {score:.4f} (n={n})</title></rect>"
        )
    first_label = scored[0][0]
    last_label = scored[-1][0]
    return f"""
<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Brier score por ventana">
  {''.join(bars)}
  <text x="{pad_l}" y="{height - 8}" class="axis-label">{_esc(first_label)}</text>
  <text x="{width - pad_r}" y="{height - 8}" class="axis-label" text-anchor="end">{_esc(last_label)}</text>
  <text x="{pad_l}" y="{pad_t + 10}" class="axis-label">máx {max_score:.2f}</text>
</svg>
"""


def _positions_table(positions: list[dict]) -> str:
    if not positions:
        return '<p class="empty">Sin posiciones simuladas todavía.</p>'
    rows = []
    for p in positions:
        pnl = p["realized_pnl"] if p["status"] == "cerrada" else p["net_pnl"]
        pnl_label = "realized_pnl" if p["status"] == "cerrada" else "net_pnl (esperado)"
        rows.append(
            "<tr>"
            f'<td>{_esc(_fmt_dt(p["opened_at"]))}</td>'
            f'<td class="q">{_esc(p["question"])[:70]}</td>'
            f'<td><span class="badge badge-{_esc(p["status"])}">{_esc(_STATUS_LABELS.get(p["status"], p["status"]))}</span></td>'
            f'<td>{_esc(_fmt_money(p["cost_usd"]))}</td>'
            f'<td title="{_esc(pnl_label)}">{_esc(_fmt_money(pnl))}</td>'
            "</tr>"
        )
    return f"""
<table class="positions">
  <thead><tr><th>Abierta</th><th>Mercado</th><th>Estado</th><th>Costo</th><th>P&amp;L</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
"""


def _exposure_table(rows: list[tuple[str, str | None, float]], *, label_col: str, detail_col: str | None) -> str:
    if not rows:
        return '<p class="empty">Sin exposición abierta actualmente.</p>'
    if detail_col is None:
        body = "".join(
            f"<tr><td>{_esc(str(key)[:16])}…</td><td>{_esc(_fmt_money(cost_usd))}</td></tr>"
            for key, _detail, cost_usd in rows
        )
        header = f"<tr><th>{_esc(label_col)}</th><th>Exposición abierta</th></tr>"
    else:
        body = "".join(
            f"<tr><td>{_esc(str(key)[:16])}…</td><td class='q'>{_esc(detail or '')[:50]}</td>"
            f"<td>{_esc(_fmt_money(cost_usd))}</td></tr>"
            for key, detail, cost_usd in rows
        )
        header = f"<tr><th>{_esc(label_col)}</th><th>{_esc(detail_col)}</th><th>Exposición abierta</th></tr>"
    return f"""
<table class="positions">
  <thead>{header}</thead>
  <tbody>{body}</tbody>
</table>
"""


def render_html(snap: Snapshot) -> str:
    total_positions = sum(snap.status_counts.values())
    final_equity = (snap.equity_curve[-1][1] if snap.equity_curve else 0.0) + snap.unrealized_pnl
    fill_rate = (snap.arb_positions_simulated / snap.arb_signals_detected) if snap.arb_signals_detected else None

    status_cards = "".join(
        f'<div class="card"><div class="card-value">{snap.status_counts.get(s, 0)}</div>'
        f'<div class="card-label">{label}</div></div>'
        for s, label in _STATUS_LABELS.items()
    )

    brier_rows = [(day, score, n) for day, (score, n) in snap.brier_by_day.items()]
    brier_total_n = sum(n for _, _, n in brier_rows)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ledger de arbitraje</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {{
    --bg: #f5f6f8;
    --surface: #ffffff;
    --border: #dde1e7;
    --text-primary: #17202c;
    --text-secondary: #566171;
    --text-muted: #8891a0;
    --accent: #16805f;
    --accent-soft: #e2f3ec;
    --info: #2f6fb0;
    --info-soft: #e5eefa;
    --warn: #a8690a;
    --warn-soft: #faeed9;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #11151d;
      --surface: #171d27;
      --border: #2a323e;
      --text-primary: #e7ebf1;
      --text-secondary: #9aa4b4;
      --text-muted: #6a7382;
      --accent: #3ecf9a;
      --accent-soft: #163227;
      --info: #6ba7e0;
      --info-soft: #172a3d;
      --warn: #e0a53f;
      --warn-soft: #34280f;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #11151d;
    --surface: #171d27;
    --border: #2a323e;
    --text-primary: #e7ebf1;
    --text-secondary: #9aa4b4;
    --text-muted: #6a7382;
    --accent: #3ecf9a;
    --accent-soft: #163227;
    --info: #6ba7e0;
    --info-soft: #172a3d;
    --warn: #e0a53f;
    --warn-soft: #34280f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "IBM Plex Sans", -apple-system, "Segoe UI", sans-serif;
    margin: 0; padding: 32px 28px 48px; background: var(--bg); color: var(--text-primary);
    max-width: 880px; margin-inline: auto;
  }}
  h1 {{ font-size: 21px; font-weight: 600; margin: 0 0 4px; letter-spacing: -.01em; }}
  h2 {{
    font-size: 13px; font-weight: 600; margin: 34px 0 12px; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: .07em;
  }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .card-value {{
    font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums;
    font-size: 21px; font-weight: 600;
  }}
  .card-label {{ font-size: 12px; color: var(--text-secondary); margin-top: 3px; }}
  .chart {{ width: 100%; height: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }}
  .equity-line {{ stroke: var(--accent); stroke-width: 2; }}
  .equity-dot {{ fill: var(--accent); }}
  .zero-line {{ stroke: var(--border); stroke-dasharray: 4 3; }}
  .brier-bar {{ fill: var(--info); }}
  .axis-label {{ fill: var(--text-muted); font-size: 10px; font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  .empty {{ color: var(--text-muted); font-size: 13px; }}
  table.positions {{
    width: 100%; border-collapse: collapse; font-size: 13px;
    font-variant-numeric: tabular-nums;
  }}
  table.positions th {{
    text-align: left; color: var(--text-muted); font-weight: 500; font-size: 11px;
    text-transform: uppercase; letter-spacing: .04em;
    padding: 7px 10px; border-bottom: 1px solid var(--border);
  }}
  table.positions td {{ padding: 7px 10px; border-bottom: 1px solid var(--border); }}
  table.positions tr:last-child td {{ border-bottom: none; }}
  td.q {{ color: var(--text-secondary); }}
  .badge {{ padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 500; }}
  .badge-abierta {{ background: var(--accent-soft); color: var(--accent); }}
  .badge-cerrada {{ background: var(--info-soft); color: var(--info); }}
  .badge-pendiente {{ background: var(--warn-soft); color: var(--warn); }}
  .note {{
    font-size: 12.5px; line-height: 1.55; color: var(--text-secondary); background: var(--surface);
    border: 1px solid var(--border); border-radius: 8px; padding: 11px 14px; margin-top: 10px;
  }}
  .note code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 11.5px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 760px) {{ .cols {{ grid-template-columns: 1fr; }} body {{ padding: 24px 18px 40px; }} }}
</style>
</head>
<body>
  <h1>Polymarket Bot — ledger de arbitraje</h1>
  <div class="subtitle">Fase 2 (paper trading) · generado {_esc(_fmt_dt(snap.generated_at))} · sin trading real, todo simulado</div>

  <h2>Posiciones simuladas ({total_positions} en total)</h2>
  <div class="grid">{status_cards}</div>

  <h2>P&amp;L acumulado</h2>
  {_svg_line_chart(snap.equity_curve)}
  <div class="note">
    Equity final aproximada (incluye no-realizado): <strong>{_fmt_money(final_equity)}</strong> —
    {_fmt_money(snap.equity_curve[-1][1] if snap.equity_curve else 0.0)} realizado de
    {snap.resolved_count} posiciones cerradas, + {_fmt_money(snap.unrealized_pnl)} no-realizado de las
    {snap.status_counts.get("abierta", 0) + snap.status_counts.get("pendiente", 0)} abiertas/pendientes.
    El no-realizado es el <code>net_pnl</code> calculado al momento del fill (Fase 2 parte 1) — no se
    re-marca contra el order book actual en vivo, así que es una aproximación, no un mark-to-market real.
  </div>

  <h2>Métricas clave</h2>
  <div class="grid">
    <div class="card"><div class="card-value">{_fmt_pct(snap.hit_rate)}</div><div class="card-label">Hit rate ({snap.resolved_count} resueltas)</div></div>
    <div class="card"><div class="card-value">{_fmt_pct(snap.avg_net_margin_pct)}</div><div class="card-label">Margen neto promedio / trade</div></div>
    <div class="card"><div class="card-value">{snap.arb_signals_detected}</div><div class="card-label">Señales arb detectadas</div></div>
    <div class="card"><div class="card-value">{snap.arb_positions_simulated}</div><div class="card-label">Posiciones simuladas</div></div>
    <div class="card"><div class="card-value">{_fmt_pct(fill_rate)}</div><div class="card-label">Tasa de conversión señal→fill</div></div>
    <div class="card"><div class="card-value">{snap.longshot_signals_detected}</div><div class="card-label">Señales longshot detectadas</div></div>
  </div>
  <div class="note">
    Hit rate cercano a 100% es lo esperado para arb puro: la posición paga $1/share sin importar el
    outcome, no es una apuesta direccional — un hit rate bajo indicaría un bug en el sizing o el cálculo
    de fees, no mala suerte. La tasa de conversión señal→fill será &lt;100% cuando el book no tenía
    profundidad suficiente o los límites de exposición (mercado/cluster) ya estaban al tope.
  </div>

  <h2>Brier score — favorito-longshot (por día)</h2>
  {_svg_bar_chart(brier_rows)}
  <div class="note">
    <strong>No incluye arb.</strong> El arb puro no tiene una predicción direccional que puntuar (paga $1
    sin importar el resultado) — ver CLAUDE.md, Fase 2 parte 2. Cobertura parcial por diseño: sólo
    mercados que también tuvieron una posición de arb resuelta ({brier_total_n} muestras en total hasta
    ahora), no la población completa de señales longshot.
  </div>

  <h2>Exposición actual abierta</h2>
  <div class="cols">
    <div>
      <div class="card-label" style="margin-bottom:6px">Por mercado</div>
      {_exposure_table(snap.exposure_by_market, label_col="Mercado (id)", detail_col="Pregunta")}
    </div>
    <div>
      <div class="card-label" style="margin-bottom:6px">Por cluster</div>
      {_exposure_table([(c, None, v) for c, v in snap.exposure_by_cluster], label_col="Cluster (id)", detail_col=None)}
    </div>
  </div>

  <h2>Posiciones recientes</h2>
  {_positions_table(snap.recent_positions)}
</body>
</html>
"""
