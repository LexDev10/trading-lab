# Pantalla /focus — análisis interactivo por moneda (especificación de implementación)

Fecha: 2026-07-07. Estado: **propuesta aprobada, pendiente de implementar**.

Pantalla nueva dentro de ESTA aplicación (no una app aparte) para analizar
en detalle 3-4 monedas de gran volumen (BTC, ETH, BNB, SOL): precio y
valores clave siempre visibles, y tres acciones bajo demanda — análisis
técnico, análisis fundamental y monitor de arbitraje — mostrando
**gráficamente los argumentos a favor y en contra** de cada una.

# DECISION: la sección 4/19 del spec original pide "dashboard simple";
# esta pantalla va más allá. Se documenta aquí como extensión explícita.
# Regla innegociable heredada de la sección 6: TODO el análisis reutiliza
# el código existente (`evaluate_asset`, `compute_btc_regime`,
# `item_classifications`...) — esta pantalla NO implementa lógica de
# señales propia, solo la presenta.

---

## 1. Por qué dentro de esta app y no una aparte

- La regla crítica del spec (sección 6) prohíbe una segunda
  implementación de la lógica de señales. Una app aparte tendría que
  importar este código igualmente — sería este proyecto con otro deploy.
- Todo lo necesario ya existe: FastAPI corriendo (`app/main.py`), velas y
  tickers en Postgres refrescados cada 15 min, pipeline técnico
  (`services/scanner/scanner.py::evaluate_asset`), capa fundamental
  (`item_classifications`, `veto.py`), y el patrón de página HTML
  autocontenida (`app/dashboard.py`).
- Se haría una app aparte SOLO si esto creciera a producto multiusuario
  (React, websockets, auth). No es el caso.

## 2. Guardarraíles (leer antes de escribir código)

1. **Solo lectura hacia el trading.** Ningún endpoint de /focus escribe
   en `trade_entries`/`trade_exits`/`equity_snapshots` ni altera el
   ciclo del scheduler. Los botones NUNCA abren posiciones.
2. El análisis técnico manual se ejecuta como `/analiza` en modo
   **informe** (`trigger=manual`, `final_action=watchlist`), registrando
   su `decision_log` y respetando el rate limit `manual_max_per_hour`
   ya existente en `app/config.py`.
3. Llamadas a APIs externas (tickers de otros exchanges, fase 4) con
   timeout corto (≤5 s) y **fail-open hacia la UI** (tarjeta "sin
   datos"), nunca bloqueando la página ni el ciclo técnico.
4. Todo texto de origen externo (summaries del clasificador, títulos de
   noticias) se renderiza con `html.escape` — mismo criterio que
   `app/dashboard.py` (y su mitigación `</script>` para JSON embebido).
5. Parámetros nuevos a `app/config.py` + `.env.example`, nada hardcodeado.

## 3. Configuración nueva

```python
# --- Pantalla /focus ---
focus_assets: str = "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT"  # subconjunto de UNIVERSE
focus_arbitrage_exchanges: str = "kraken,coinbase"      # además de binance (fase 4)
focus_arbitrage_taker_fee_other: Decimal = Decimal("0.0025")  # fee estimada exchange externo
```

`focus_assets_list` como property, igual que `universe_list`. Validar
(fail-closed, log warning) que cada asset esté en `UNIVERSE` — si no,
no habría velas ingeridas para él.

## 4. Endpoints

Router nuevo `app/routes_focus.py` (montado en `app/main.py` con
`app.include_router`), datos en `services/reporting/focus_data.py`
(mismo patrón dashboard: el route renderiza/serializa, el service
calcula, una sola fuente de verdad).

### 4.1 `GET /focus` → HTML
Página autocontenida (sin build de frontend): estilo oscuro del
dashboard actual + **lightweight-charts** de TradingView por CDN para el
gráfico de velas (Chart.js no hace candlestick bien; se mantiene Chart.js
solo en /dashboard).

### 4.2 `GET /api/focus/{asset}` → JSON (estado base)
Todo de tablas ya persistidas, cero red externa:
- De `market_snapshots` (último): precio mid, bid/ask, spread_bps,
  cambio 24h, volumen 24h.
- De `candles` 4h (últimas 500, vía `get_recent_candles` +
  `candles_to_frame` con `now` — anti look-ahead como siempre):
  régimen del activo (`compute_btc_regime`, agnóstica al activo),
  EMA50/200, ATR14 y ATR%, máximo/mínimo del rango de
  `range_lookback_candles` velas (`rolling_range`).
- Velas 4h (open/high/low/close/volume + open_time) para el gráfico,
  últimas ~200.
- Posición de papel abierta en el activo (si la hay): entrada, SL, TP,
  PnL latente al último close — solo informativo.

### 4.3 `POST /api/focus/{asset}/technical` → JSON (botón 1)
Ejecuta el pipeline REAL en modo informe:
1. Rate limit `manual_max_per_hour` (misma implementación que
   `scripts/analiza.py` — extraer a función compartida si hoy está
   inline ahí; no duplicar).
2. Carga velas 1h/4h + snapshot + régimen BTC y llama a
   `evaluate_asset(...)` — MISMA función del ciclo automático.
3. Registra `decision_log` con `trigger=manual`,
   `final_action=watchlist` (modo informe de `decide_final_action`).
4. Devuelve la estructura para el renderizado a favor/en contra:

```json
{
  "steps": [
    {"group": "filtros_duros", "check": "liquidity", "ok": true,
     "detail": "vol 24h 12.4B ≥ 50M"},
    {"group": "setup", "check": "range_breakout_4h", "ok": false,
     "detail": "close 43210 no supera range_high 44100"},
    {"group": "risk_engine", "check": "rr_net", "ok": true,
     "detail": "R:R neto 2.1 ≥ 1.8"}
  ],
  "signal": {"entry_zone": [..], "sl": .., "tp": .., "invalidation": ..} | null,
  "verdict": "sin_setup" | "rechazado" | "aprobaría_entrada"
}
```

Los `detail` se construyen en `focus_data.py` a partir de los dicts
`checks` + `scanner_payload` + `TechnicalSignal.evidence` que el
pipeline YA produce — no se recalcula nada.

La UI revela los `steps` secuencialmente (~300 ms entre pasos) para el
efecto "se ve cómo lo va haciendo". # DECISION: el backend devuelve el
JSON completo y el frontend anima la revelación — mismo resultado visual
que streaming/SSE con una fracción de la complejidad.

Si hay señal: dibujar entry zone / SL / TP / invalidación como líneas de
precio sobre el gráfico de velas.

### 4.4 `POST /api/focus/{asset}/fundamental` → JSON (botón 2)
Solo lectura de tablas existentes (sin llamar a Ollama desde la UI —
los items ya están clasificados por `fundamental_classify_job`):
- `get_latest_stance` + `asset_has_active_veto` (funciones existentes,
  única fuente de verdad).
- Lista de `item_classifications` del activo en la ventana
  `fundamental_veto_hours` (join con `news_items`/`social_items` por
  `(item_kind, item_id)` para título, fuente y `published_at`), cada
  item como tarjeta:
  - **a favor** (verde): stance bullish_*
  - **en contra** (rojo): stance bearish_* o `veto=true` (destacado)
  - neutral/unknown en gris
  con el `summary` de una frase que ya genera el clasificador.
- Si no hay items clasificados en la ventana: estado vacío honesto
  ("sin señal fundamental fresca"), NO disparar clasificación ad-hoc.

# NOTA: cuando se aborde el bug #18 de docs/CODE_REVIEW_2026-07-07.md
# (veto social vs news), esta pantalla debe reflejar la distinción
# (badge "veto solo-entradas" vs "veto con cierre").

### 4.5 `POST /api/focus/{asset}/arbitrage` → JSON (botón 3, fase 4)
Módulo nuevo `services/data/exchange_tickers.py`: ticker público (sin
API key) de Kraken y Coinbase para el par, con mapeo de símbolos
(`BTCUSDT` → `XBTUSD` Kraken / `BTC-USD` Coinbase; dict explícito por
asset de `focus_assets`, fail-closed si falta el mapeo).

Respuesta con desglose honesto a favor/en contra, en NETO:

```json
{
  "pairs": [
    {"venue_buy": "binance", "venue_sell": "kraken",
     "gross_spread_pct": 0.18,
     "costs": {"fee_buy": 0.10, "fee_sell": 0.25, "withdraw_transfer": "~20 min exposición"},
     "net_spread_pct": -0.17,
     "verdict": "no_rentable"}
  ]
}
```

# DECISION: es un MONITOR informativo, no un ejecutor. Para majors en
# venues grandes el neto será casi siempre negativo — mostrar ese
# resultado con su desglose ES el análisis. Sin persistencia en fase 4
# (si más adelante se quiere histórico de spreads, tabla nueva aparte).
# USD≠USDT: se compara contra pares USD de Kraken/Coinbase; añadir nota
# visible en la UI de que existe base risk USDT/USD (~bps).

## 5. Frontend (dentro del HTML autocontenido)

- Desplegable de moneda (`focus_assets`) → fetch de 4.2 y render.
- Fila de tarjetas: precio (auto-refresh cada 60 s re-fetching 4.2),
  cambio 24h, volumen, spread, régimen (color por regime), ATR%.
- Gráfico lightweight-charts: velas 4h + EMA50/200 + líneas de rango;
  al ejecutar el técnico, añade entry/SL/TP/invalidación.
- Tres botones. Cada uno: estado loading → render progresivo de
  steps/tarjetas (verde ✅ / rojo ❌ / gris ○) → veredicto final grande.
- Sin framework: JS vanilla en el propio HTML, como /dashboard.

## 6. Fases de implementación

| Fase | Contenido | Toca |
|------|-----------|------|
| F1 | Config + `focus_data.py` (estado base) + `GET /focus` + `GET /api/focus/{asset}` + gráfico | Solo lectura de DB |
| F2 | Botón técnico (4.3) + niveles sobre el gráfico + rate limit compartido con `/analiza` | `decision_logs` (trigger=manual) |
| F3 | Botón fundamental (4.4) | Solo lectura |
| F4 | `exchange_tickers.py` + botón arbitraje (4.5) | Red externa nueva |

**Prerequisito recomendado**: los bugs #10-#12 de
`docs/CODE_REVIEW_2026-07-07.md` van ANTES que esto — la pantalla es
observabilidad y no corrompe nada, pero el paper que muestra sí está
corrompido mientras esos bugs sigan activos.

## 7. Tests de aceptación

- **Unit** (`focus_data.py`): construcción de `steps` a partir de un
  `AssetEvaluation` fixture (con y sin señal, con y sin veto); mapeo de
  símbolos de exchanges; cálculo de spread neto con fees.
- **Integración**: `GET /api/focus/BTCUSDT` con velas/snapshot sembrados
  (environment de test — recordar el filtro `ENVIRONMENT` en cualquier
  query nueva de cartera, regla #3 del CLAUDE.md); `POST .../technical`
  crea exactamente 1 `decision_log` con `trigger=manual` y NO crea
  `trade_entries`; rate limit devuelve 429 al superar
  `manual_max_per_hour`.
- **Fail-open externo**: mock de Kraken caído → respuesta 200 con la
  tarjeta de ese venue en "sin datos".
- Verificación estándar tras cada fase (regla CLAUDE.md):
  `pytest -v` + `pytest tests/integration -v` + `mypy .` en Docker.

## 8. Expectativas (contexto de la decisión, 2026-07-07)

Registrado para futuras sesiones: esta pantalla **no añade edge** — es
observabilidad y disciplina de decisión. La rentabilidad del sistema
sigue dependiendo de la estrategia subyacente (expectancy OOS actual
+0.096%/trade, pendiente de recalcular tras el fix del bug #11, que
probablemente la reduzca). El monitor de arbitraje se espera
sistemáticamente negativo en neto para majors; su valor es mostrar el
porqué con números reales.
