"""Helpers visuales del dashboard — checklist de riesgo en verde/rojo y
badges de estado, con el mismo estilo que ya se usaba en el dashboard de
Streamlit del proyecto hermano `cripto` (adaptados aquí, sin lógica
propia: solo formatean lo que ya calculó el risk engine/scanner real)."""

import streamlit as st

_FINAL_ACTION_COLOR = {"enter": "success", "reject": "error", "watchlist": "warning"}
_FINAL_ACTION_LABEL = {"enter": "ENTRA", "reject": "RECHAZADA", "watchlist": "WATCHLIST"}


def action_badge(final_action: str) -> None:
    msg_fn = getattr(st, _FINAL_ACTION_COLOR.get(final_action, "info"))
    msg_fn(f"### {_FINAL_ACTION_LABEL.get(final_action, final_action.upper())}")


def checklist_row(ok: bool, label: str) -> None:
    icon = "✅" if ok else "❌"
    bg = "#1b5e20" if ok else "#3a1414"
    border = "#4caf50" if ok else "#c62828"
    st.markdown(
        f"""<div style="background:{bg}; border-left:4px solid {border};
                       border-radius:6px; padding:8px 14px; margin:4px 0;
                       font-family:monospace; font-size:0.88rem;">
            <strong>{icon}</strong>&nbsp; {label}
        </div>""",
        unsafe_allow_html=True,
    )


def render_checklist(checks: dict[str, bool]) -> None:
    for name, passed in checks.items():
        checklist_row(passed, name)


def system_state_badge(state: str, halted_reason: str | None) -> None:
    if state == "running":
        st.success("🟢 Sistema en marcha (running)")
    else:
        st.error(f"🛑 Sistema en HALT — {halted_reason or 'sin motivo registrado'}")


def veto_badge(assets: list[str]) -> None:
    if assets:
        st.error(f"⚠️ Veto fundamental activo: {', '.join(assets)} — bloquea entradas nuevas en esos activos.")
    else:
        st.success("✅ Sin vetos fundamentales activos.")
