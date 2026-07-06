"""Meta-decider (sección 13, fase 3): fusión determinista técnico ×
fundamental por **tabla de política explícita, no pesos**. Un solo
archivo legible — criterio de aceptación de la sección 19.

# DECISION (2026-07-06): la fila "strong, bearish_strong o veto -> reject"
# de la tabla de la sección 13 se implementa aquí SOLO para
# `bearish_strong` — el caso "veto" ya está cubierto aguas arriba, en el
# risk engine (`services/risk/engine.py::checks["fundamental_veto"]`,
# sección 12.4, fase 2): si hay un veto activo, `risk_verdict.approved`
# ya es `False` y `apply_fundamental_policy` ni se llega a consultar (ver
# más abajo). Repetirlo aquí sería una segunda implementación del mismo
# check (prohibido por la regla crítica de la sección 6).
#
# La fila "weak, cualquiera -> reject" no es alcanzable hoy: el único
# `setup_type` implementado (`range_breakout`) nunca produce
# `conviction=weak` (ver `services/technical/signal_builder.py`). Se deja
# en la tabla para cuando exista un setup que sí la produzca."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from core.enums import FundamentalStance, TechnicalConviction

Action = Literal["enter", "reject", "watchlist"]


@dataclass(frozen=True)
class PolicyOutcome:
    action: Action
    size_multiplier: Decimal  # 1.0 = tamaño estándar; irrelevante si action != "enter"


_ENTER_FULL = PolicyOutcome("enter", Decimal("1.0"))
_ENTER_HALF = PolicyOutcome("enter", Decimal("0.5"))
_REJECT = PolicyOutcome("reject", Decimal("0"))
_WATCHLIST = PolicyOutcome("watchlist", Decimal("0"))

# Tabla de política — sección 13. Se evalúa de arriba a abajo; gana la
# PRIMERA fila cuyo (conviction, stance) coincide. `None` en la columna
# de stances = "cualquiera".
POLICY_TABLE: list[tuple[TechnicalConviction, frozenset[FundamentalStance] | None, PolicyOutcome]] = [
    (
        TechnicalConviction.strong,
        frozenset(
            {
                FundamentalStance.bullish_strong,
                FundamentalStance.bullish_weak,
                FundamentalStance.neutral,
                FundamentalStance.unknown,
            }
        ),
        _ENTER_FULL,
    ),
    (TechnicalConviction.strong, frozenset({FundamentalStance.bearish_weak}), _ENTER_HALF),
    (TechnicalConviction.strong, frozenset({FundamentalStance.bearish_strong}), _REJECT),
    (TechnicalConviction.moderate, frozenset({FundamentalStance.bullish_strong}), _ENTER_FULL),
    (
        TechnicalConviction.moderate,
        frozenset(
            {
                FundamentalStance.bullish_weak,
                FundamentalStance.neutral,
                FundamentalStance.unknown,
                FundamentalStance.bearish_weak,
                FundamentalStance.bearish_strong,
            }
        ),
        _WATCHLIST,
    ),
    (TechnicalConviction.weak, None, _REJECT),
]


def evaluate_policy(conviction: TechnicalConviction, stance: FundamentalStance) -> PolicyOutcome:
    """Primera fila de `POLICY_TABLE` que coincide. Fail-closed: si
    ninguna coincide (no debería pasar — la tabla cubre los 18 pares
    conviction×stance posibles, ver nota abajo), `reject`.

    # DECISION: la sección 13 no cubre explícitamente `moderate` +
    # `bullish_weak` en su tabla de ejemplo ("los valores exactos se
    # calibran con datos de fase 1-2"). Fail-closed conservador: cae en
    # el mismo bucket que el resto de `moderate` no-`bullish_strong`
    # (`watchlist`), añadido explícitamente a la fila de arriba en vez
    # de dejarlo caer al fallback silencioso de esta función."""
    for row_conviction, stances, outcome in POLICY_TABLE:
        if row_conviction != conviction:
            continue
        if stances is None or stance in stances:
            return outcome
    return _REJECT


def apply_fundamental_policy(
    ablation_mode: str,
    conviction: TechnicalConviction,
    risk_approved: bool,
    stance: FundamentalStance,
) -> PolicyOutcome | None:
    """`None` si no aplica: modo `technical_only` (la tabla ni se
    consulta — comportamiento idéntico al que había antes de fase 3) o
    `risk_approved=False` (el risk engine/veto ya rechazó la entrada
    aguas arriba, sección 9/12.4 — no tiene sentido fusionar sobre algo
    ya descartado)."""
    if ablation_mode == "technical_only" or not risk_approved:
        return None
    return evaluate_policy(conviction, stance)
