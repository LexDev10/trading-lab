"""dashboard/main.py — Dashboard Streamlit de solo lectura para trading-lab.

Lanzar (servicio propio en docker-compose, puerto 8501):
    docker compose up -d dashboard

No escribe nada en la base de datos ni toma decisiones: todo lo que
muestra ya lo calculó el pipeline real (scanner, risk engine,
paper_ledger, clasificador fundamental) — este módulo solo lee y pinta,
vía `services/reporting/dashboard_data.py` (una sola fuente de verdad
por pregunta, regla crítica sección 6 del spec).

# DECISION (2026-07-07): este archivo NO puede llamarse `app.py`. El
# proyecto ya tiene un paquete top-level `app/` (`app.config`, `app.main`,
# `app.scheduler`) — `streamlit run` antepone el directorio del script a
# `sys.path`, así que un `dashboard/app.py` sombrea ese paquete real: todo
# `from app.config import ...` dentro de `dashboard/` resolvía "app" al
# propio script en vez del paquete, con un error de importación circular
# (visto en vivo la primera vez que se abrió en el navegador). Cualquier
# módulo nuevo en `dashboard/` debe evitar nombres que coincidan con
# paquetes top-level del proyecto (`app`, `core`, `services`, `db`, etc.).
"""

import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data as dd
from dashboard.components import action_badge, render_checklist, system_state_badge, veto_badge

st.set_page_config(
    page_title="Trading Lab · Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

with st.sidebar:
    st.title("⚙️ Trading Lab")
    st.caption("Dashboard de solo lectura — no ejecuta ni decide nada.")
    if st.button("🔄 Actualizar ahora", type="primary", width="stretch"):
        st.rerun()
    st.divider()
    st.caption(f"Última carga: {datetime.now().strftime('%H:%M:%S')}")

st.title("📊 Trading Lab — Estado del sistema")

# ── Datos base (una sola vez por render) ───────────────────────────
system_state = dd.get_system_state_info()
active_vetoes = dd.get_active_vetoes()
open_positions_count = dd.count_open_positions()
closed_summary = dd.compute_closed_trades_summary()
equity_curve = dd.get_equity_curve()

# ── Estado del sistema ──────────────────────────────────────────────
col_state, col_veto = st.columns(2)
with col_state:
    system_state_badge(system_state.state, system_state.halted_reason)
with col_veto:
    veto_badge(active_vetoes)

st.divider()

# ── Fila de KPIs ─────────────────────────────────────────────────────
latest = equity_curve[-1] if equity_curve else None
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
kpi1.metric("Equity actual", f"${float(latest.equity_quote):,.2f}" if latest else "—")
kpi2.metric("Drawdown actual", f"{float(latest.drawdown_pct) * 100:.2f}%" if latest else "—")
kpi3.metric("Posiciones abiertas", open_positions_count)
kpi4.metric("Trades cerrados", closed_summary.n_trades)
win_rate = f"{float(closed_summary.win_rate) * 100:.1f}%" if closed_summary.win_rate is not None else "—"
kpi5.metric("Win rate", win_rate)

st.divider()

tab_resumen, tab_analizar, tab_posiciones, tab_decisiones, tab_fundamental, tab_trades = st.tabs(
    ["📈 Resumen", "🔍 Analizar", "💼 Posiciones", "🧭 Decisiones", "📰 Fundamental", "📒 Trades"]
)

# ── Tab: Resumen ─────────────────────────────────────────────────────
with tab_resumen:
    st.subheader("Curva de equity")
    if equity_curve:
        df_eq = pd.DataFrame(
            [{"ts": p.ts, "equity": float(p.equity_quote), "drawdown_pct": float(p.drawdown_pct) * 100} for p in equity_curve]
        )
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(x=df_eq["ts"], y=df_eq["equity"], mode="lines", name="Equity", line={"color": "#3B82F6"}))
        fig_eq.update_layout(height=320, margin={"l": 10, "r": 10, "t": 20, "b": 10}, template="plotly_dark")
        st.plotly_chart(fig_eq, width="stretch")

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(x=df_eq["ts"], y=df_eq["drawdown_pct"], mode="lines", name="Drawdown %", line={"color": "#EF4444"}))
        fig_dd.update_layout(
            height=180, margin={"l": 10, "r": 10, "t": 20, "b": 10}, template="plotly_dark", yaxis_title="Drawdown %"
        )
        st.plotly_chart(fig_dd, width="stretch")
    else:
        st.info("Todavía no hay puntos de equity registrados (`equity_snapshots` vacío).")

    st.subheader("Trades cerrados — agregado")
    c1, c2, c3 = st.columns(3)
    c1.metric("PnL total", f"${float(closed_summary.total_pnl_quote):,.2f}")
    avg_pct = f"{float(closed_summary.avg_pnl_pct) * 100:.2f}%" if closed_summary.avg_pnl_pct is not None else "—"
    c2.metric("PnL medio %", avg_pct)
    pf = f"{float(closed_summary.profit_factor):.2f}" if closed_summary.profit_factor is not None else "—"
    c3.metric("Profit factor", pf)

    st.subheader("Decisiones por modo (histórico)")
    mode_counts = dd.get_decisions_by_mode()
    if mode_counts:
        df_modes = pd.DataFrame([{"Modo": m.mode, "Acción": m.final_action, "Conteo": m.count} for m in mode_counts])
        st.dataframe(df_modes, width="stretch", hide_index=True)
    else:
        st.info("Sin decisiones registradas todavía.")

# ── Tab: Analizar ahora ──────────────────────────────────────────────
with tab_analizar:
    st.subheader("🔍 Analizar un activo ahora")
    st.caption(
        "Ejecuta bajo demanda el mismo pipeline que el escáner automático "
        "(régimen BTC, filtros duros, señal técnica, risk engine) — "
        "`services/scanner/scanner.py::evaluate_asset`, la MISMA función, "
        "sin una segunda implementación. **Solo modo informe**: registra la "
        "decisión pero nunca abre una posición desde aquí."
    )

    universe = dd.get_universe()
    col_sel, col_free = st.columns(2)
    with col_sel:
        selected = st.selectbox("Elige del universo", options=["(ninguno)"] + universe)
    with col_free:
        free_symbol = st.text_input("…o escribe cualquier par USDT", placeholder="ej. PEPEUSDT").strip().upper()

    asset_to_analyze = free_symbol or (selected if selected != "(ninguno)" else "")

    if st.button("🔍 Analizar (informe)", type="primary", disabled=not asset_to_analyze):
        st.session_state.pop("manual_analysis_error", None)
        with st.spinner(f"Analizando {asset_to_analyze}…"):
            try:
                st.session_state["manual_analysis_result"] = dd.run_manual_analysis(asset_to_analyze, operar=False)
            except Exception as exc:
                st.session_state["manual_analysis_result"] = None
                st.session_state["manual_analysis_error"] = str(exc)

    manual_error = st.session_state.get("manual_analysis_error")
    manual_result = st.session_state.get("manual_analysis_result")

    if manual_error:
        st.error(f"Error al analizar: {manual_error}")
    elif manual_result is not None:
        st.divider()
        st.markdown(f"### Resultado — {manual_result.asset}")

        if manual_result.early_rejection:
            st.warning(manual_result.early_rejection)
        else:
            for w in manual_result.warnings:
                st.info(w)

            regime_label = manual_result.regime.value if manual_result.regime else "—"
            blocks_note = "  🚫 bloquea entradas" if manual_result.regime_blocks else ""
            st.markdown(f"**Régimen BTC (4h):** `{regime_label}`{blocks_note}")
            if manual_result.regime_details:
                with st.expander("Ver detalle del régimen"):
                    st.json(manual_result.regime_details)

            if manual_result.scanner_checks:
                st.markdown("**Filtros duros del scanner:**")
                render_checklist(manual_result.scanner_checks)

            ts = manual_result.technical_signal
            if ts is None:
                st.info(
                    "No se detectó ningún setup de ruptura de rango confirmado por "
                    "volumen (o los filtros duros no pasaron)."
                )
            else:
                st.markdown(f"**Setup técnico:** {ts.setup_type} ({ts.timeframe}, conviction={ts.conviction.value})")
                c1, c2, c3 = st.columns(3)
                c1.metric("Zona de entrada", f"{float(ts.entry_zone[0]):.4f} – {float(ts.entry_zone[1]):.4f}")
                c2.metric("Stop loss", f"{float(ts.stop_loss):.4f}")
                c3.metric("Take profit", f"{float(ts.take_profit):.4f}")

                rv = manual_result.risk_verdict
                if rv is not None:
                    st.markdown("**Risk engine — checklist completo:**")
                    render_checklist(rv.checks)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Aprobado", "✅ Sí" if rv.approved else "❌ No")
                    rr_label = f"{float(rv.rr_net_of_fees):.2f}" if rv.rr_net_of_fees is not None else "—"
                    c2.metric("R/R neto de fees", rr_label)
                    size_label = f"${float(rv.size_quote):,.2f}" if rv.size_quote is not None else "—"
                    c3.metric("Tamaño (quote)", size_label)

                if manual_result.policy_action is not None:
                    stance_label = manual_result.fundamental_stance.value if manual_result.fundamental_stance else "?"
                    st.info(
                        f"Meta-decider (MODE={manual_result.system_mode}): stance={stance_label} "
                        f"→ {manual_result.policy_action} (tamaño ×{manual_result.policy_size_multiplier})"
                    )

            st.divider()
            if manual_result.final_action is not None:
                action_badge(manual_result.final_action.value)
            if manual_result.rejection_reasons:
                st.warning("Motivos: " + ", ".join(manual_result.rejection_reasons))
            st.caption(f"decision_log_id: {manual_result.decision_log_id}")
    else:
        st.info('Elige un activo y pulsa "Analizar" para ver el resultado aquí.')

# ── Tab: Posiciones ──────────────────────────────────────────────────
with tab_posiciones:
    st.subheader("Posiciones de papel — pendientes / abiertas")
    positions = dd.get_open_positions_detail()
    if positions:
        df_pos = pd.DataFrame(
            [
                {
                    "Activo": p.asset,
                    "Estado": p.status,
                    "Timeframe": p.timeframe or "—",
                    "Entrada (hora)": p.entry_time,
                    "Precio entrada": float(p.entry_price),
                    "Zona entrada": (
                        f"{float(p.entry_zone_low):.4f} – {float(p.entry_zone_high):.4f}"
                        if p.entry_zone_low is not None and p.entry_zone_high is not None
                        else "—"
                    ),
                    "Cantidad": float(p.qty),
                    "SL": float(p.sl),
                    "TP": float(p.tp),
                }
                for p in positions
            ]
        )
        st.dataframe(df_pos, width="stretch", hide_index=True)
    else:
        st.info("No hay posiciones de papel abiertas ni pendientes ahora mismo.")

# ── Tab: Decisiones ──────────────────────────────────────────────────
with tab_decisiones:
    st.subheader("Últimas decisiones del escáner")
    decisions = dd.get_recent_decisions(limit=50)
    if not decisions:
        st.info("Sin decisiones registradas todavía.")
    else:
        df_dec = pd.DataFrame(
            [
                {
                    "ID": d.id,
                    "Hora": d.ts,
                    "Activo": d.asset,
                    "Modo": d.mode,
                    "Acción final": d.final_action,
                    "Motivos de rechazo": ", ".join(d.rejection_reasons) or "—",
                }
                for d in decisions
            ]
        )
        st.dataframe(df_dec, width="stretch", hide_index=True)

        st.divider()
        st.markdown("#### Inspeccionar una decisión (checklist completo del risk engine)")
        selected_id = st.selectbox(
            "Elige un ID de la tabla de arriba",
            options=[d.id for d in decisions],
            format_func=lambda i: f"#{i} — {next(d.asset for d in decisions if d.id == i)}",
        )
        detail = dd.get_decision_detail(selected_id)
        if detail is None:
            st.warning("No se encontró el detalle de esa decisión.")
        else:
            action_badge(detail.final_action)
            if detail.rejection_reasons:
                st.warning("Motivos de rechazo: " + ", ".join(detail.rejection_reasons))

            if detail.risk_verdict and isinstance(detail.risk_verdict.get("checks"), dict):
                st.markdown("**Checklist del risk engine:**")
                render_checklist(detail.risk_verdict["checks"])
                rr = detail.risk_verdict.get("rr_net_of_fees")
                size = detail.risk_verdict.get("size_quote")
                if rr is not None or size is not None:
                    c1, c2 = st.columns(2)
                    c1.metric("R/R neto de fees", f"{float(rr):.2f}" if rr is not None else "—")
                    c2.metric("Tamaño (quote)", f"${float(size):,.2f}" if size is not None else "—")
            else:
                st.info("Esta decisión no llegó a generar un veredicto de riesgo (sin setup técnico, o filtros duros ya la descartaron antes).")

            if detail.technical:
                with st.expander("Ver señal técnica completa (JSON)"):
                    st.json(detail.technical)
            if detail.decision:
                with st.expander("Ver decisión del meta-decider (JSON)"):
                    st.json(detail.decision)

# ── Tab: Fundamental ─────────────────────────────────────────────────
with tab_fundamental:
    st.subheader("Vetos activos")
    veto_badge(active_vetoes)

    st.subheader("Últimas clasificaciones (noticias / Reddit)")
    classifications = dd.get_recent_classifications(limit=50)
    if classifications:
        df_cls = pd.DataFrame(
            [
                {
                    "Clasificado": c.classified_at,
                    "Publicado": c.published_at,
                    "Tipo": c.item_kind,
                    "Fuente": c.source or "—",
                    "Postura": c.stance,
                    "Veto": "🚫" if c.veto else "",
                    "Activos": ", ".join(c.asset_tags) or "—",
                    "Resumen": c.summary,
                }
                for c in classifications
            ]
        )
        st.dataframe(df_cls, width="stretch", hide_index=True)
    else:
        st.info("Sin clasificaciones todavía (el clasificador Ollama corre cada ciclo del scheduler).")

# ── Tab: Trades ──────────────────────────────────────────────────────
with tab_trades:
    st.subheader("Historial de trades cerrados")
    history = dd.get_closed_trades_history(limit=100)
    if history:
        df_hist = pd.DataFrame(
            [
                {
                    "Activo": h.asset,
                    "Entrada": h.entry_time,
                    "Salida": h.exit_time,
                    "Precio entrada": float(h.entry_price),
                    "Precio salida": float(h.exit_price),
                    "Tipo de salida": h.exit_type,
                    "PnL": float(h.pnl_quote),
                    "PnL %": float(h.pnl_pct_net) * 100,
                }
                for h in history
            ]
        )
        st.dataframe(
            df_hist.style.map(lambda v: "color: #4caf50" if isinstance(v, float) and v > 0 else ("color: #ef5350" if isinstance(v, float) and v < 0 else ""), subset=["PnL", "PnL %"]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Todavía no hay trades cerrados.")

st.divider()
st.caption(
    "⚠️ Paper trading (simulación sobre velas reales, sin exchange) — sistema educativo/de investigación, "
    "no constituye asesoramiento financiero."
)
