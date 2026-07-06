"""Dashboard mínimo (sección 4/19, fase 3): una única página HTML
autocontenida — sin plantillas ni dependencias nuevas (Chart.js se carga
por CDN), "página simple" tal como pide el spec. Los datos se calculan en
`services/reporting/dashboard_data.py` (una sola fuente de verdad,
reutilizada también por `scripts/estado.py`); este módulo solo renderiza.
"""

import html
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from services.reporting.dashboard_data import (
    count_open_positions,
    compute_closed_trades_summary,
    get_decisions_by_mode,
    get_equity_curve,
    get_recent_decisions,
)


def _json_for_script(data: object) -> str:
    """`</script>` dentro de un string embebido cerraría el tag antes de
    tiempo — mitigación estándar (OWASP) para JSON incrustado en HTML."""
    return json.dumps(data, default=str).replace("</", "<\\/")


def _fmt_pct(value: Decimal | None) -> str:
    return f"{value:.2%}" if value is not None else "n/a"


def _fmt_decimal(value: Decimal | None, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if value is not None else "n/a"


async def render_dashboard(session: AsyncSession) -> str:
    equity_curve = await get_equity_curve(session)
    summary = await compute_closed_trades_summary(session)
    open_positions = await count_open_positions(session)
    by_mode = await get_decisions_by_mode(session)
    recent = await get_recent_decisions(session)

    latest_equity = equity_curve[-1] if equity_curve else None

    chart_labels = _json_for_script([p.ts.isoformat() for p in equity_curve])
    chart_values = _json_for_script([str(p.equity_quote) for p in equity_curve])

    mode_rows = "\n".join(
        f"<tr><td>{html.escape(row.mode)}</td><td>{html.escape(row.final_action)}</td>"
        f"<td>{row.count}</td></tr>"
        for row in by_mode
    ) or "<tr><td colspan='3'>(sin decisiones todavía)</td></tr>"

    decision_rows = "\n".join(
        f"<tr><td>{html.escape(row.ts.isoformat())}</td><td>{html.escape(row.asset)}</td>"
        f"<td>{html.escape(row.mode)}</td><td>{html.escape(row.final_action)}</td>"
        f"<td>{html.escape(', '.join(row.rejection_reasons))}</td></tr>"
        for row in recent
    ) or "<tr><td colspan='5'>(sin decisiones todavía)</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>trading-lab — dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; background: #0f1115; color: #e6e6e6; }}
  h1 {{ font-size: 1.4rem; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }}
  .card {{ background: #1a1d24; border-radius: 8px; padding: 1rem 1.5rem; min-width: 160px; }}
  .card .label {{ font-size: 0.8rem; color: #9aa0a6; }}
  .card .value {{ font-size: 1.4rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.8rem; border-bottom: 1px solid #2a2d34; font-size: 0.85rem; }}
  th {{ color: #9aa0a6; font-weight: 500; }}
  canvas {{ max-height: 320px; }}
</style>
</head>
<body>
<h1>trading-lab — dashboard (fase 3)</h1>

<div class="cards">
  <div class="card"><div class="label">Equity</div><div class="value">{_fmt_decimal(latest_equity.equity_quote if latest_equity else None)} USDT</div></div>
  <div class="card"><div class="label">Drawdown</div><div class="value">{_fmt_pct(latest_equity.drawdown_pct if latest_equity else None)}</div></div>
  <div class="card"><div class="label">Posiciones abiertas</div><div class="value">{open_positions}</div></div>
  <div class="card"><div class="label">Trades cerrados</div><div class="value">{summary.n_trades}</div></div>
  <div class="card"><div class="label">Win rate</div><div class="value">{_fmt_pct(summary.win_rate)}</div></div>
  <div class="card"><div class="label">Profit factor</div><div class="value">{_fmt_decimal(summary.profit_factor)}</div></div>
</div>

<h2>Curva de equity</h2>
<canvas id="equityChart"></canvas>

<h2>Decisiones por modo de ablación (sección 13)</h2>
<table>
  <thead><tr><th>Modo</th><th>Acción final</th><th>Nº</th></tr></thead>
  <tbody>{mode_rows}</tbody>
</table>

<h2>Últimas decisiones</h2>
<table>
  <thead><tr><th>Fecha</th><th>Activo</th><th>Modo</th><th>Acción</th><th>Rechazos</th></tr></thead>
  <tbody>{decision_rows}</tbody>
</table>

<script>
new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      label: 'Equity (USDT)',
      data: {chart_values},
      borderColor: '#4f8cff',
      backgroundColor: 'rgba(79,140,255,0.1)',
      tension: 0.1,
      pointRadius: 0,
    }}],
  }},
  options: {{
    scales: {{ x: {{ display: false }} }},
    plugins: {{ legend: {{ display: false }} }},
  }},
}});
</script>
</body>
</html>
"""
