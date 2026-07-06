# Fase 3 — Meta-decider + dashboard + memo LLM (en progreso)

Estado: **en progreso, no cerrada**. Se implementó todo lo que la
sección 13/4/19 pide para fase 3 salvo probar el memo LLM contra la API
real de Anthropic (sin credenciales todavía) y confirmar en producción
un `size_multiplier` aplicado de verdad al abrir una posición (no ha
coincidido todavía una señal real con `MODE != technical_only`).

**Decisión del usuario** que motiva esta sesión: seguir con fase 3 tras
cerrar (casi del todo, solo falta Reddit) fase 2 — decisión ya tomada
explícitamente en la sesión anterior de no adelantar fases.

## Qué se hizo

### Meta-decider — tabla de política (sección 13)

- `services/decision/policy.py` (paquete nuevo): `POLICY_TABLE`, literal
  de la tabla de ejemplo de la sección 13, en un único archivo legible
  (criterio de aceptación explícito de la sección 19). `evaluate_policy
  (conviction, stance)` recorre la tabla de arriba a abajo y devuelve la
  primera fila que coincide.
- **`# DECISION`**: la fila "strong, bearish_strong **o veto** -> reject"
  de la tabla del spec se implementa aquí SOLO para `bearish_strong` — el
  caso "veto" ya está cubierto aguas arriba por el risk engine desde fase
  2 (`checks["fundamental_veto"]`, sección 12.4): si hay un veto activo,
  `risk_verdict.approved` ya es `False` y `apply_fundamental_policy` ni
  llega a consultar la tabla. Repetirlo aquí sería una segunda
  implementación del mismo check (prohibido por la regla crítica de la
  sección 6).
- **`# DECISION`**: la sección 13 no cubre explícitamente `moderate` +
  `bullish_weak` en su tabla de ejemplo ("los valores exactos se calibran
  con datos de fase 1-2"). Se añadió al mismo bucket que el resto de
  `moderate` no-`bullish_strong` (`watchlist`) — la tabla cubre así los
  18 pares conviction×stance posibles sin depender de un fallback
  silencioso.
- La fila "weak, cualquiera -> reject" no es alcanzable hoy: el único
  `setup_type` implementado (`range_breakout`) nunca produce
  `conviction=weak` (`services/technical/signal_builder.py`). Se deja en
  la tabla para cuando exista un setup que sí la produzca.

### `# DECISION` central de esta ronda: veto (fase 2) vs. tabla de política (fase 3)

Sección 12.4 ya decía que el veto es una capacidad de fase 2 ("veto=true
-> rechazo... NO puede generar entradas ni reforzar convicción todavía,
**eso llega en fase 3** con la tabla de política"). Esto separa dos
cosas:
- El **veto** es una salvaguarda de riesgo (hackeos, delistings) — sigue
  aplicando **siempre**, independientemente de `MODE`. No tendría
  sentido que el modo "técnico puro" de una ablación ignorase un hackeo
  en curso solo por motivos de comparación estadística.
- La **tabla de política** (fusión stance→acción/tamaño) es la pieza
  NUEVA de fase 3, y esa sí se gatea: si `MODE=technical_only` no se
  consulta en absoluto (comportamiento idéntico al de antes de fase 3);
  si `technical_plus_fundamental`/`full`, se aplica.

**Decisión de ablación confirmada con el usuario**: "solo-modo-activo"
por ahora — el sistema opera siempre bajo el `MODE` configurado;
`decision_logs.mode` ya se graba por decisión y permite comparar
periodos históricos con `MODE` distinto. Se descartó explícitamente
"shadow-mode" (evaluar los 3 modos en paralelo sobre la misma señal,
abriendo solo una posición real) por complejidad — queda como posible
mejora futura si se necesita una comparación apples-to-apples sin
esperar a rotar `MODE` manualmente.

### Bug encontrado y corregido: falta de límite superior anti look-ahead

`services/fundamental/veto.py::asset_has_active_veto` y
`get_latest_stance` (fase 2) solo acotaban `classified_at > cutoff`
(límite inferior de la ventana de frescura) — **sin límite superior**
`classified_at <= now`. Detectado al escribir
`tests/integration/test_veto.py::test_get_latest_stance_returns_most_recent_within_window`:
con un `now` de test en el pasado, una clasificación REAL de producción
(fechada más tarde) ganaba el `ORDER BY classified_at DESC` por encima
de los datos sembrados por el test. Más allá del test, es un bug real:
viola el mismo principio anti look-ahead que ya aplica en todo el resto
del sistema (sección 12.1, "toda consulta usa `fetched_at <=
momento_de_decisión`") — sin el límite superior, una clasificación
fechada después de `now` contaría como ya conocida en el momento de la
decisión. Corregido en ambas funciones.

### Enganche en el pipeline de decisión

- `services/scanner/scanner.py::evaluate_asset`: tras un `risk_verdict`
  aprobado, si `settings.mode != "technical_only"` resuelve el
  `fundamental_stance` (`get_latest_stance`) y calcula el
  `PolicyOutcome` (`apply_fundamental_policy`). Ambos se guardan en
  `AssetEvaluation` (campos nuevos).
- `decide_final_action` (compartida con `scripts/analiza.py`, regla de
  no duplicar lógica de la sección 6) gana un parámetro opcional
  `policy_outcome: PolicyOutcome | None = None` — el default preserva
  exactamente el comportamiento anterior a fase 3 cuando no se pasa
  (verificado con `tests/unit/test_scanner.py`). `reject`→rechaza;
  `watchlist`→no abre posición aunque el risk engine haya aprobado;
  `enter`→comportamiento normal.
- Un `size_multiplier != 1` se aplica copiando el `RiskVerdict`
  (`.model_copy(update={"size_quote": ...})`) justo antes de
  `paper_ledger.open_position` — no se toca la firma del paper ledger.
- El resultado de la fusión (`stance`, `policy_action`,
  `size_multiplier`) se guarda en `DecisionRecord.decision` — campo del
  schema que existía desde fase 1 (`core/schemas/decision.py`) sin
  usarse nunca hasta ahora.
- `scripts/analiza.py` actualizado igual (misma función `evaluate_asset`
  + `decide_final_action`, sin divergencia).

### Memo LLM opcional (sección 13)

- `services/reporting/llm_memo.py`: `generate_trade_memo(settings,
  decision_payload)` llama a la API de mensajes de Anthropic
  (`api.anthropic.com/v1/messages`) vía `httpx` directamente — sin SDK
  oficial nuevo, mismo criterio que `services/fundamental/classify.py`
  con Ollama. Redacta un resumen de 3-5 frases a partir de los payloads
  YA decididos (técnico + fundamental + risk + política); nunca influye
  la decisión.
- `USE_REMOTE_LLM=false` y `REMOTE_LLM_API_KEY=""` por defecto — no toca
  la red sin ambos configurados (mismo patrón fail-safe que
  Reddit/Telegram). Fail-open ante error de red.
- Solo se dispara en `MODE=full`, al abrir una posición de papel; el
  texto se envía como mensaje de Telegram aparte del de apertura.
- Modelo por defecto: `claude-haiku-4-5-20251001` (barato/rápido — la
  tarea es redactar un resumen corto, no razonar, mismo criterio que la
  elección de `qwen3.5:9b` para el clasificador local).

### Dashboard mínimo (sección 4/19)

- `services/reporting/dashboard_data.py`: 5 funciones de solo lectura
  (`get_equity_curve`, `compute_closed_trades_summary`,
  `count_open_positions`, `get_decisions_by_mode`,
  `get_recent_decisions`) sobre tablas ya existentes.
- **Refactor sin cambio de comportamiento**: la fórmula de win_rate/
  pnl/profit_factor de `scripts/estado.py::_print_closed_summary` se
  extrajo a `compute_closed_trades_summary` — ahora `estado.py` solo
  imprime, y el dashboard usa la misma fuente (nunca dos cálculos del
  mismo número).
- `app/dashboard.py` + `GET /dashboard` en `app/main.py`: página HTML
  autocontenida — sin plantillas ni dependencias Python nuevas. Chart.js
  se carga por CDN para la curva de equity; los datos se inyectan como
  JSON en un `<script>` embebido (con la mitigación estándar de escapar
  `</script>` dentro del JSON). Tarjetas de resumen, tabla de decisiones
  por modo de ablación, tabla de las últimas 30 decisiones.

## Verificado en producción, no solo en tests

Con el stack corriendo en background durante la sesión:
- `get_latest_stance`/`asset_has_active_veto` contra una clasificación
  real (noticia sobre el desplome del 73% de una empresa con reservas en
  AVAX, `bearish_strong`/`veto=true`, ver `CHANGELOG.md` de fase 2):
  simulando `MODE=technical_plus_fundamental` y `conviction=strong`
  hipotético (si el veto no existiera), la tabla de política devuelve
  `reject` — exactamente la fila de la sección 13.
  `apply_fundamental_policy` con el `risk_verdict.approved=False` real
  (veto activo) devuelve `None` correctamente, sin duplicar el rechazo.
- `GET /dashboard` renderiza con datos reales: 690+ decisiones ya
  registradas hoy por el scheduler en vivo, agrupadas correctamente por
  `mode` (`technical_only`, el único usado hasta ahora — el usuario no
  ha cambiado `MODE` en `.env` todavía).

## Qué quedó fuera (y por qué)

- **Memo LLM contra la API real de Anthropic**: sin `REMOTE_LLM_API_KEY`
  configurada, solo verificado con `httpx` mockeado
  (`tests/unit/test_llm_memo.py`). Se activa rellenando la key y
  `USE_REMOTE_LLM=true` + `MODE=full` — mismo patrón que Reddit.
- **`size_multiplier` aplicado de verdad al abrir una posición real**:
  no ha coincidido todavía una señal técnica real con
  `MODE != technical_only` (el usuario no ha cambiado el modo por
  defecto). Verificado end-to-end con tests de integración/unitarios y
  con una simulación manual contra datos reales de clasificación (ver
  arriba), pero no con una apertura de posición real bajo ese modo.
- **Shadow-mode** (evaluar los 3 modos en paralelo): descartado
  explícitamente para esta ronda, ver `# DECISION` arriba.
- **Dashboard sin autenticación**: aceptable para un dev tool local
  (puerto 8000 solo expuesto en el host del usuario), no se ha
  planteado necesario todavía.

## Tests

**19 tests unitarios nuevos** (sin DB): `test_policy.py` (14 casos
parametrizados, uno por cada par conviction×stance relevante + 4 casos
de `apply_fundamental_policy`), `test_scanner.py` (7 casos de
`decide_final_action` con/sin `policy_outcome`), `test_llm_memo.py` (4
casos, mismo patrón de `httpx` mockeado que `test_telegram.py`).

**8 tests de integración nuevos** (Postgres real): `test_dashboard_data.py`
(5 funciones, criterio de assert-por-delta — mismo Postgres que la app
real en background, ver docstring del archivo), 3 casos nuevos en
`test_veto.py` para `get_latest_stance`.

Total: **122 unitarios + 21 integración** en verde. `mypy` sin
categorías de error nuevas frente al baseline de fase 2 (solo deuda
preexistente del mismo tipo — anotaciones de retorno faltantes,
`_env_file` — repetida en los archivos de test nuevos).

## Cómo reproducir la verificación

```bash
docker compose up -d --build
docker compose run --rm --no-deps app uv run pytest -v
docker compose exec app uv run pytest tests/integration -v
docker compose exec app uv run mypy .
curl http://localhost:8000/dashboard
docker compose exec -e MODE=technical_plus_fundamental app uv run python -m scripts.analiza BTCUSDT
```
