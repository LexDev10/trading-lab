# Especificación: Sistema multiagente de trading (crypto spot, swing corto)

**Documento de trabajo para Claude Code.** Contiene todo lo necesario para implementar el sistema fase a fase. Léelo completo antes de escribir código. Las secciones "Criterios de aceptación" definen cuándo una fase está terminada.

---

## 1. Contexto y objetivo

Sistema de trading semiautomático sobre **crypto spot** (Binance) que:

1. Escanea un universo pequeño de pares líquidos buscando setups de **swing corto** (holding esperado: 4 horas a 5 días, velas de 1h y 4h).
2. Genera evidencia técnica con **indicadores calculados en Python** (nunca por un LLM).
3. (Fase 2+) Añade una capa de contexto de noticias/social con salidas **categóricas**, no scores numéricos inventados.
4. Pasa toda decisión por un **risk engine determinista** con controles a nivel de cartera.
5. Ejecuta en **Binance Spot Testnet** primero; capital real solo tras superar gates cuantitativos explícitos (sección 15).
6. Registra **todas** las decisiones (aceptadas y rechazadas) para análisis posterior y ablación.

**Prioridad del proyecto:** aprendizaje + trazabilidad + honestidad estadística > agresividad en capturar movimientos. Ante la duda, el sistema NO opera.

**Perfil de latencia:** swing corto → tolerante a latencia. Scanner cada 15 min por cron/scheduler es suficiente. NO se necesita hot path por WebSocket en el MVP. No sobre-ingenierizar.

---

## 2. Principios de diseño NO negociables

Estos principios prevalecen sobre cualquier otra decisión de implementación:

1. **El LLM nunca calcula ni decide la ejecución.** Los indicadores, niveles de entrada/SL/TP y sizing son Python determinista. Los LLM (si se usan) solo clasifican texto, resumen y explican. Un LLM jamás está en el camino entre "señal válida" y "orden enviada".
2. **Salidas categóricas, no pseudo-precisión.** Prohibido que un LLM emita scores tipo `0.42`. Toda clasificación LLM usa enums (`bullish_strong | bullish_weak | neutral | bearish_weak | bearish_strong`, etc.) cuya tasa de acierto se mide históricamente después.
3. **Contratos Pydantic estrictos entre etapas.** Cada módulo produce un modelo Pydantic validado. Si la validación falla, el pipeline aborta ese setup y lo registra como rechazado con motivo `contract_violation`.
4. **Datos point-in-time desde el día uno.** Todo dato externo (noticia, post, snapshot) se guarda con `published_at` y `fetched_at` inmutables. Nunca se sobrescribe. Esto es lo que permite evaluación honesta de la capa fundamental (que NO es backtesteable con LLMs por contaminación del entrenamiento; solo forward-testeable).
5. **Todo se registra, incluidas las no-operaciones.** Cada setup evaluado genera una fila en `decision_logs` con payloads completos, entre o no entre.
6. **Fail-closed.** Cualquier error, timeout, dato faltante o incertidumbre → no operar. Nunca "asumir valores razonables".
7. **Empezar mínimo.** Fase 1 es SOLO técnica (backtesteable). La capa fundamental, el meta-decider y cualquier LLM llegan después y se evalúan como ablación contra la baseline técnica.

---

## 3. Hipótesis de edge y métricas de éxito

### 3.1 Hipótesis (explícita y falsable)

> "En majors de crypto spot, las rupturas de rango con confirmación de volumen en velas 1h/4h, filtradas por régimen de tendencia de BTC y por liquidez mínima, tienen continuación suficiente para producir expectativa positiva neta de comisiones con R:R ≥ 1.8 y gestión por ATR."

Esta hipótesis puede resultar falsa. El objetivo de las fases 0-3 es medirla honestamente, no confirmarla.

### 3.2 Baseline obligatoria

Todo resultado se compara contra **buy & hold de BTC** en el mismo periodo y contra **no operar** (0%). Un sistema que gana un 5% mientras BTC sube un 40% no está funcionando.

### 3.3 Métricas primarias (calculadas siempre netas de comisiones)

- **Expectancy por trade** = (win_rate × avg_win) − (loss_rate × avg_loss), neto de fees (taker 0.1% × 2 en Binance sin descuentos; parametrizable).
- **Max drawdown** de la equity curve.
- **Profit factor** y **Sharpe simple** (informativo, con pocos trades tiene mucho ruido).
- **Hit rate de las clasificaciones LLM** (fase 2+): % de veces que `bullish_strong` fue seguido de retorno positivo a horizonte fijo (4h, 24h, 72h).
- **Métricas de ejecución** (fase 4+): slippage real vs esperado, % órdenes rechazadas, diferencia TP/SL teórico vs ejecutado.

### 3.4 Plan de ablación

El sistema debe poder correr en 3 modos configurables, registrando el modo en cada `decision_log`:

- `MODE=technical_only` — baseline.
- `MODE=technical_plus_fundamental` — la capa fundamental puede vetar o reforzar.
- `MODE=full` — con meta-decider.

Si tras N semanas de paper el modo con fundamental no supera a `technical_only` de forma clara, la capa fundamental se considera ruido y se documenta como hallazgo (esto es un resultado válido del proyecto, no un fracaso).

---

## 4. Fases del proyecto

### Fase 0 — Infraestructura mínima (sin trading)
- Repo, docker-compose, PostgreSQL, migraciones, config, logging estructurado.
- Ingesta de velas 1h/4h y ticker 24h de Binance para el universo, persistidas.
- Scheduler (APScheduler dentro del servicio Python; NO Celery, NO n8n en esta fase).

### Fase 1 — Pipeline técnico + paper trading (backtesteable)
- Scanner con filtros duros + filtro de régimen BTC.
- Agente técnico determinista (indicadores + clasificación de régimen + niveles por ATR).
- Risk engine completo (incluidos controles de cartera).
- Executor contra **Binance Spot Testnet** con órdenes OCO.
- Monitor de posiciones + reconciliación de estado.
- Backtesting con vectorbt de las reglas técnicas (walk-forward).
- Journal completo en `decision_logs`.

### Fase 2 — Capa fundamental point-in-time
- Ingesta: RSS de anuncios de Binance, RSS de medios crypto, Reddit (OAuth Data API). NewsAPI opcional. X/Twitter: fuera del alcance.
- Almacén point-in-time inmutable.
- Clasificación con Ollama (local) a categorías + tipos de evento. Solo forward-test.
- La capa fundamental en este punto solo puede **vetar** (risk_flag) o **etiquetar contexto**; no genera entradas por sí sola.

### Fase 3 — Meta-decider + ablación
- Fusión por **tabla de política determinista** (reglas explícitas técnico × fundamental), NO por pesos mágicos.
- LLM externo opcional solo para redactar el "memo" explicativo del trade, nunca para decidir.
- Dashboard mínimo (FastAPI + página simple) con equity, decisiones y ablación por modo.

### Fase 4 — Capital real mínimo
- Solo si se cumplen los gates de la sección 15.
- Mismo código, `ENVIRONMENT=live`, tamaño por trade mínimo permitido por el exchange.

### Explícitamente FUERA de alcance (no implementar aunque parezca fácil)
- n8n, Celery/Redis, MLflow, pgvector/RAG, CCXT multi-exchange, WebSockets de baja latencia, acciones, forex, derivados/futuros, shorts. Se podrán añadir después; el código debe dejar interfaces limpias pero sin implementarlos.

---

## 5. Stack técnico

| Capa | Elección | Justificación |
|---|---|---|
| Lenguaje | Python 3.12 | — |
| API/paneles | FastAPI (solo health + dashboard en fase 3) | ligero |
| Scheduling | APScheduler in-process | swing corto no necesita cola distribuida |
| DB | PostgreSQL 16 + `jsonb` | payloads semiestructurados; pgvector se pospone |
| Validación | Pydantic v2 | contratos + JSON Schema |
| Datos/indicadores | pandas + numpy + `ta` (o cálculo propio de EMA/ATR/RSI) | evitar dependencias pesadas |
| Backtesting | vectorbt | reglas técnicas vectorizadas |
| LLM local (fase 2) | Ollama con structured outputs (JSON Schema) | clasificación barata |
| Exchange | binance-connector oficial (spot) | testnet soportada |
| Alertas | Telegram Bot API (un canal) | simple |
| Infra | docker-compose (app + postgres + ollama opcional) | reproducible |
| Tests | pytest + fixtures grabadas de la API de Binance | — |

Gestión de dependencias: `uv` o `pip-tools` con lockfile. Tipado estricto (`mypy --strict` en `core/` y `services/risk/`).

---

## 6. Estructura del repositorio

```
trading-lab/
├─ pyproject.toml
├─ docker-compose.yml
├─ .env.example
├─ README.md
├─ app/
│  ├─ main.py                  # FastAPI: /health, /dashboard (fase 3)
│  ├─ scheduler.py             # APScheduler: jobs de scan, monitor, reconcile
│  └─ config.py                # Pydantic Settings; TODO desde .env
├─ core/
│  ├─ schemas/                 # contratos Pydantic (sección 7)
│  │  ├─ market.py
│  │  ├─ technical.py
│  │  ├─ fundamental.py
│  │  ├─ decision.py
│  │  ├─ risk.py
│  │  └─ orders.py
│  ├─ enums.py                 # todos los enums del sistema
│  └─ logging.py               # logging JSON estructurado
├─ services/
│  ├─ data/
│  │  ├─ binance_market_data.py   # velas, ticker 24h, exchangeInfo, book ticker
│  │  └─ persistence.py
│  ├─ scanner/
│  │  ├─ filters.py               # filtros duros (sección 8)
│  │  ├─ regime.py                # régimen BTC (sección 8.3)
│  │  └─ scanner.py
│  ├─ technical/
│  │  ├─ indicators.py            # EMA, ATR, RSI, volumen relativo, rangos
│  │  ├─ setups.py                # detección de ruptura de rango + confirmación
│  │  └─ signal_builder.py        # produce TechnicalSignal
│  ├─ fundamental/                # fase 2
│  │  ├─ ingest_rss.py
│  │  ├─ ingest_reddit.py
│  │  ├─ pit_store.py             # almacén point-in-time
│  │  └─ classifier_ollama.py
│  ├─ decision/
│  │  ├─ policy_table.py          # fusión determinista (fase 3)
│  │  └─ decider.py
│  ├─ risk/
│  │  ├─ engine.py                # checklist completo (sección 9)
│  │  ├─ sizing.py                # riesgo fijo fraccional
│  │  └─ portfolio_state.py       # exposición, correlación, drawdown
│  ├─ execution/
│  │  ├─ binance_executor.py      # OCO, filtros de exchange, idempotencia
│  │  ├─ reconciler.py            # estado local vs exchange
│  │  └─ paper_ledger.py          # equity y fills simulados sobre testnet
│  ├─ monitor/
│  │  ├─ position_monitor.py
│  │  └─ exit_rules.py            # salidas por tiempo, por invalidación, por veto
│  └─ journal/
│     └─ decision_logger.py
├─ backtests/
│  ├─ strategy_breakout.py        # misma lógica que services/technical (compartir código)
│  └─ walk_forward.py
├─ db/
│  ├─ migrations/                 # alembic
│  └─ repositories/
├─ notifications/
│  └─ telegram.py
└─ tests/
   ├─ unit/
   ├─ integration/                # contra testnet, marcados @pytest.mark.testnet
   └─ fixtures/                   # respuestas grabadas de Binance
```

**Regla crítica:** la lógica de señales en `services/technical/` y la usada en `backtests/` debe ser **el mismo código importado**, no dos implementaciones. Divergencia backtest/live es un bug de primera clase.

---

## 7. Contratos de datos (Pydantic)

Definir exactamente estos modelos en `core/schemas/`. Campos adicionales permitidos solo si se documentan aquí primero (actualizar este doc).

### 7.1 Enums (`core/enums.py`)

```python
class Regime(str, Enum): trend_up, trend_down, range, chop_high_vol
class HorizonClass(str, Enum): hours, days          # swing corto: 4h-5d
class SignalDirection(str, Enum): long, none        # spot: SOLO largos
class TechnicalConviction(str, Enum): strong, moderate, weak
class FundamentalStance(str, Enum): bullish_strong, bullish_weak, neutral, bearish_weak, bearish_strong, unknown
class EventType(str, Enum): exchange_listing, delisting, regulatory, hack_exploit, etf_flows, unlock, partnership, macro, other
class FinalAction(str, Enum): enter, reject, watchlist
class RejectionReason(str, Enum):
    regime_filter, liquidity, spread, rr_too_low, max_positions,
    correlated_exposure, daily_loss_limit, drawdown_killswitch,
    cooldown, contract_violation, fundamental_veto, exchange_filter,
    stale_data, execution_error, no_setup, sl_distance_invalid
    # DECISION (fase 1, implementación): añadido `no_setup` — el filtro duro
    # de "movimiento" (sección 8.2.4) no tenía motivo de rechazo asociado en
    # la lista original. Se usa cuando el activo pasa liquidez/spread/frescura
    # pero no hay ni cambio 24h suficiente ni ruptura de rango detectada, para
    # cumplir el principio 5 ("todo se registra, incluidas las no-operaciones").
    # DECISION (fase 1, implementación): añadido `sl_distance_invalid` — los
    # checks `sl_distance_min`/`sl_distance_max` (sección 9.1) no tenían
    # motivo de rechazo propio en la lista original. El check `notional_min`
    # reutiliza `exchange_filter` (mismo motivo que usa la sección 10.1 para
    # rechazos por filtros del exchange tras redondeo).
class TradeStatus(str, Enum): pending, open, closed_tp, closed_sl, closed_manual, closed_invalidated, closed_time, error
```

### 7.2 `TechnicalSignal`

```python
class TechnicalSignal(BaseModel):
    asset: str                      # "SOLUSDT"
    generated_at: AwareDatetime
    candle_close_time: AwareDatetime   # vela sobre la que se calcula (anti look-ahead)
    timeframe: Literal["1h", "4h"]
    regime: Regime
    direction: SignalDirection
    conviction: TechnicalConviction
    setup_type: Literal["range_breakout"]   # extensible en el futuro
    entry_zone: tuple[Decimal, Decimal]
    stop_loss: Decimal                       # basado en ATR, bajo el rango roto
    take_profit: Decimal
    atr_14: Decimal
    rel_volume: Decimal                      # volumen vela / media 20
    horizon_class: HorizonClass
    invalidation_rule: str                   # legible, ej. "close_1h_below_stop_level"
    evidence: dict                           # valores de indicadores usados
```

Reglas de construcción: `stop_loss` = mínimo del rango − 0.5×ATR14. `take_profit` tal que R:R bruto ≥ 2.0 (el neto lo verifica el risk engine). Todo `Decimal`, nunca float, en precios y cantidades.

### 7.3 `FundamentalContext` (fase 2)

```python
class FundamentalContext(BaseModel):
    asset: str
    generated_at: AwareDatetime
    lookback_hours: int                      # ventana de items considerados
    stance: FundamentalStance                # categórico, del clasificador
    event_types: list[EventType]
    veto: bool                               # True si evento invalidante (hack, delisting, regulatory grave)
    veto_reason: str | None
    item_ids: list[int]                      # FK a news/social items usados (trazabilidad)
    summary: str                             # 2-3 frases, generado por Ollama
```

Prohibido: cualquier campo numérico de "sentimiento" o "confianza" generado por LLM.

### 7.4 `RiskVerdict`

```python
class RiskVerdict(BaseModel):
    approved: bool
    rejection_reasons: list[RejectionReason]
    checks: dict[str, bool]                  # resultado de CADA check, siempre todos
    size_quote: Decimal | None               # en USDT si aprobado
    rr_net_of_fees: Decimal | None
    portfolio_snapshot: dict                 # exposición actual en el momento del veredicto
```

### 7.5 `DecisionRecord`

Agrega todos los payloads anteriores + `final_action`, `mode` (ablación), `expected_tp/sl`, versiones de código y modelos (`git_sha`, `ollama_model`), y se persiste en `decision_logs` SIEMPRE, incluso en rechazos del scanner.

---

## 8. Scanner y filtro de régimen

### 8.1 Universo (config, no hardcode)

MVP: `BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, DOTUSDT` (10 pares; ampliable a 20 por config). BTC además actúa como referencia de régimen.

### 8.2 Filtros duros (todos deben pasar; orden barato→caro)

1. **Datos frescos:** última vela cerrada hace < 2× timeframe. Si no → `stale_data`.
2. **Liquidez:** volumen 24h en quote ≥ `MIN_QUOTE_VOL_24H` (default 50M USDT).
3. **Spread:** (ask−bid)/mid del book ticker ≤ `MAX_SPREAD_BPS` (default 5 bps).
4. **Movimiento:** |cambio 24h| ≥ `MIN_ABS_CHANGE_PCT` (default 3%) O ruptura de rango detectada en 1h/4h. El scanner busca *setups*, no solo "cosas que suben".

### 8.3 Filtro de régimen de cartera (crítico — spot es solo largo)

Régimen BTC calculado en 4h: `trend_up` si precio > EMA50 > EMA200 y EMA50 con pendiente positiva; `trend_down` si lo contrario; resto `range`/`chop`.

- `trend_down` en BTC → **el sistema entero no abre posiciones nuevas** en ningún par (los majors correlacionan ~1 con BTC en caídas). Se registran los setups como `rejected: regime_filter`.
- `chop_high_vol` (ATR% de BTC > percentil 90 histórico) → tampoco se opera.
- Esto convierte "estar fuera del mercado" en una decisión activa y registrada.

---

## 9. Risk engine determinista (checklist completo)

`services/risk/engine.py` ejecuta TODOS los checks siempre (no cortocircuitar: queremos el resultado de cada uno en `RiskVerdict.checks`). Orden de evaluación indiferente; aprobación solo si todos pasan.

### 9.1 Checks por operación
| Check | Regla (defaults en config) |
|---|---|
| `rr_net` | R:R **neto de comisiones** ≥ 1.8. Fees = 2 × taker_fee × nominal; restar del beneficio esperado |
| `sl_distance_min` | distancia entrada→SL ≥ 1.0 × ATR14 (evitar stops dentro del ruido) |
| `sl_distance_max` | distancia entrada→SL ≤ 4.0 × ATR14 |
| `spread` | spread actual ≤ 5 bps re-verificado en el momento de ejecutar |
| `notional_min` | nominal ≥ minNotional del exchange × 1.2 |

### 9.2 Checks de cartera (los que faltaban en el diseño original)
| Check | Regla |
|---|---|
| `max_positions` | ≤ 3 posiciones abiertas |
| `max_exposure_total` | exposición total ≤ 30% del equity |
| `correlated_exposure` | TODOS los majors cuentan como un único bucket "beta-BTC": exposición del bucket ≤ 20% del equity. (Implementación simple y honesta: no fingir matrices de correlación en el MVP) |
| `daily_loss_limit` | pérdida realizada+no realizada del día ≥ 2% del equity → no nuevas entradas hasta 00:00 UTC |
| `drawdown_killswitch` | drawdown desde máximo de equity ≥ 10% → sistema en modo `halt`: cierra gestión normal de posiciones abiertas pero NO abre nada; requiere rearme **manual** por CLI/endpoint |
| `cooldown_asset` | tras cerrar posición en un activo (TP o SL), 12h sin reentrar en él |
| `cooldown_losses` | 2 SL consecutivos en el sistema → pausa de 24h |

### 9.3 Sizing
Riesgo fijo fraccional: arriesgar `RISK_PER_TRADE` (default **0.5%** del equity) entre entrada y SL. `size_quote = equity × 0.005 / distancia_relativa_al_SL`, redondeado a los filtros del exchange. Nunca sizing por convicción en el MVP.

---

## 10. Ejecución (Binance Spot) — detalles obligatorios

`binance_executor.py` debe implementar explícitamente:

1. **Validación previa con `exchangeInfo`:** redondear precio a `PRICE_FILTER.tickSize`, cantidad a `LOT_SIZE.stepSize`, verificar `NOTIONAL.minNotional`. Cachear exchangeInfo 24h. Si tras redondeo el R:R neto baja del umbral → rechazar (`exchange_filter`).
2. **Entrada:** orden LIMIT en la zona de entrada con `timeInForce=GTC` y expiración lógica propia: si no llena en `ENTRY_TTL_MINUTES` (default 45), cancelar y registrar `watchlist`. Nada de market orders persiguiendo precio.
3. **OCO tras el fill:** al confirmarse el fill de entrada, colocar OCO (TP limit + SL stop-limit con `stopLimitPrice` ligeramente peor que `stopPrice`). Guardar ambos `orderId` y `listClientOrderId`.
4. **Idempotencia:** todo envío usa `newClientOrderId` determinista (`{decision_log_id}-entry`, `{decision_log_id}-oco`). Ante timeout de red: consultar por clientOrderId antes de reintentar. Jamás reenviar a ciegas.
5. **Condición de carrera monitor↔OCO:** si el monitor decide cerrar por invalidación/veto: (a) cancelar OCO, (b) confirmar cancelación, (c) verificar si mientras tanto se ejecutó parcial/totalmente, (d) solo entonces vender a mercado el remanente. Documentar esta secuencia en el código.
6. **Fills parciales:** posición = cantidad realmente ejecutada. OCO sobre la cantidad real. Si fill parcial < minNotional al cerrar → gestionarlo (vender todo el remanente junto).
7. **Rate limits y errores:** respetar cabeceras `X-MBX-USED-WEIGHT`, backoff exponencial, y ante error persistente → `halt` + alerta Telegram. Fail-closed.
8. **Reconciliación:** job cada 5 min compara estado local (posiciones/órdenes en DB) contra el exchange (`GET /api/v3/openOrders`, balances). Cualquier discrepancia → alerta + marcar posición `error` + no abrir nada nuevo hasta resolver.

### 10.1 Paper trading
Se usa **Binance Spot Testnet** con el mismo código (`ENVIRONMENT=testnet` cambia base URL y keys). El `paper_ledger` calcula equity con fills reales de testnet + fee simulada de 0.1% por lado. Aviso conocido: la liquidez de testnet no es realista; los datos de mercado para señales se toman SIEMPRE del API de producción (solo lectura), y solo la ejecución va a testnet.

> # DECISION (fase 1, implementación, 2026-07-03): mientras no existan
> credenciales `BINANCE_API_KEY`/`SECRET` de testnet, `paper_ledger.py`
> (`services/execution/paper_ledger.py`) sustituye el fill real de testnet
> por una **simulación pura sobre velas ya ingeridas** (sin llamar a
> ningún exchange): al aprobar el risk engine, abre la posición al
> `entry_ref` de la señal y la sigue vela a vela hasta SL, TP, invalidación
> técnica (`invalidation_rule`, ahora también expuesta como
> `invalidation_level: Decimal` estructurado en `TechnicalSignal`) o
> expiración por horizonte (`MAX_HOLD_HOURS_INTRADAY`/
> `MAX_HOLD_DAYS_SWING`). Fee 0.1%/lado igual que aquí. Escribe
> `trade_entries`/`trade_exits`/`equity_snapshots` con la misma forma que
> usaría un executor real, así que los checks de cartera de la sección 9.2
> se activan sin cambios. Se abandona en cuanto exista el executor OCO
> real contra testnet — ver `services/execution/paper_ledger.py` para el
> detalle completo y las simplificaciones explícitas (sin redondeo a
> filtros de exchange, sin veto fundamental, monitor a la cadencia de 15
> min del ciclo existente en vez de los 5 min de la sección 11).

---

## 11. Monitor de posiciones

Job cada 5 min sobre posiciones abiertas:

1. **Estado de órdenes:** ¿saltó TP o SL? → cerrar ciclo, registrar `trade_exits`, activar cooldowns.
2. **Invalidación técnica:** evaluar `invalidation_rule` sobre la última vela cerrada (ej. cierre 1h bajo el nivel del rango). Si se cumple antes del SL → cierre anticipado (secuencia de la sección 10.5).
3. **Salida por tiempo:** si `horizon_class=hours` y la posición lleva > 48h sin tocar TP → cerrar (`closed_time`). Si `days`, límite 7 días.
4. **Veto fundamental (fase 2+):** si llega item clasificado con `veto=true` para el activo (hack, delisting, regulación grave) → cierre anticipado + alerta.
5. Cada evento → fila en `position_events` + mensaje Telegram.

---

## 12. Capa fundamental point-in-time (fase 2)

### 12.1 Almacén PIT (inmutable, append-only)

```sql
news_items(id, source, source_url, title, body_text, asset_tags text[],
           published_at timestamptz, fetched_at timestamptz,
           content_hash unique, raw_jsonb)
social_items(id, platform, subreddit, post_id unique, title, body_text,
             score_at_fetch, num_comments_at_fetch,
             published_at, fetched_at, raw_jsonb)
item_classifications(id, item_id, item_kind, model_name, model_version,
                     classified_at, stance, event_types text[],
                     veto bool, output_jsonb)
```

Reglas: nunca UPDATE sobre items; reclasificar = nueva fila en `item_classifications`. Toda consulta para decisiones usa `fetched_at <= momento_de_decisión` (garantía anti look-ahead por construcción).

### 12.2 Fuentes MVP
- RSS de anuncios de Binance (listados/delistados) — señal de mayor calidad.
- RSS de 2-3 medios (CoinDesk, The Block).
- Reddit OAuth Data API: r/CryptoCurrency + subreddit del activo; top/new con score y comentarios en el momento del fetch.
- Fetch cada 15 min. NewsAPI opcional detrás de flag. X/Twitter: NO.

> # DECISION (fase 2, 2026-07-03): ingesta de Reddit implementada en
> `services/fundamental/ingest_reddit.py` — grant OAuth
> `client_credentials` ("app-only", sin usuario/contraseña de Reddit):
> solo se necesita lectura pública de listados, ninguna acción en nombre
> de una cuenta, así que no hace falta guardar credenciales personales
> (`REDDIT_CLIENT_ID`/`SECRET`/`USER_AGENT` en Apéndice A). El documento
> no nombra qué subreddit corresponde a "el activo" para cada par del
> universo — mapeo best-effort en `ASSET_SUBREDDITS` (ej. BTC→r/Bitcoin,
> ETH→r/ethereum); si algún nombre queda desactualizado, esa fuente
> simplemente no aporta items (`ingest_all` es fail-closed por
> subreddit, igual que por fuente RSS — sección 12.2). Sin credenciales
> configuradas, `ingest_all` no intenta nada por red (no es un error, es
> un requisito externo pendiente, mismo criterio que
> `notifications/telegram.py`).

### 12.3 Clasificador (Ollama, structured outputs)
Modelo local (ej. `llama3.1:8b` o `qwen2.5:7b`, configurable) con JSON Schema estricto → `stance` (enum), `event_types` (enums), `veto` (bool), `summary` (str). Temperatura 0. Registrar `model_name+version` en cada clasificación. **Evaluación:** job semanal calcula hit-rate de cada stance contra retornos realizados a 4h/24h/72h → tabla `classifier_scorecard`. Esto es lo que decide si la capa aporta señal.

### 12.4 Rol en la decisión (fase 2)
Solo dos efectos posibles: `veto=true` → rechazo (`fundamental_veto`) o cierre anticipado; y etiquetado de contexto en el journal. NO puede generar entradas ni "reforzar convicción" todavía (eso llega en fase 3 con la tabla de política, y solo si el scorecard lo justifica).

> # DECISION (fase 2, arranque, 2026-07-03): este round implementa
> **solo** el almacén `news_items` + ingesta RSS/JSON (Binance, CoinDesk,
> The Block) — ver `services/fundamental/ingest_rss.py`. `social_items`,
> `item_classifications` y `classifier_scorecard` NO se crean todavía
> (esquema especulativo sin consumidor); se añaden en su propia migración
> cuando se construyan Reddit (necesita registrar una app OAuth,
> credenciales que el proyecto aún no tiene) y el clasificador Ollama
> (necesita decidir conectividad: ¿servicio en docker-compose o
> `host.docker.internal` contra el Ollama del host?, sin resolver
> todavía). Binance no publica RSS público estable para anuncios: se usa
> el endpoint JSON no documentado que consume su propia web
> (`bapi/composite/v1/public/cms/article/catalog/list/query`, verificado
> manualmente) — riesgo explícito de que cambie de forma sin aviso;
> `ingest_all` es fail-closed por fuente (un fallo en una no tumba las
> otras). `asset_tags` es una heurística determinista de palabra completa
> contra alias conocidos (no NLP) — filtra relevancia por activo, no
> reemplaza la clasificación real del punto 12.3, todavía sin construir.

---

## 13. Meta-decider (fase 3) — tabla de política, no pesos

Fusión determinista y legible. Ejemplo del formato (los valores exactos se calibran con datos de fase 1-2):

| Técnico | Fundamental | Acción | Ajuste |
|---|---|---|---|
| strong | bullish_* / neutral / unknown | enter | tamaño estándar |
| strong | bearish_weak | enter | tamaño × 0.5 |
| strong | bearish_strong o veto | reject | — |
| moderate | bullish_strong | enter | tamaño estándar |
| moderate | neutral/unknown/bearish_* | watchlist | — |
| weak | cualquiera | reject | — |

Un LLM externo (API, opcional, flag `USE_REMOTE_LLM`) solo redacta el memo explicativo del trade a partir de los payloads ya decididos. Nunca altera la decisión.

---

## 14. Backtesting y evaluación honesta

- **Solo la capa técnica es backtesteable.** vectorbt sobre 2+ años de velas 1h/4h descargadas de Binance (persistir en `candles`).
- **Walk-forward obligatorio:** optimizar parámetros (longitud de rango, umbral de volumen, múltiplos ATR) en ventana in-sample de 6 meses, validar en 2 meses out-of-sample, rodar. Reportar SOLO métricas out-of-sample concatenadas.
- Incluir fees (0.1% por lado) y slippage pesimista (1 tick + 2 bps) en toda simulación.
- Comparar contra buy&hold BTC y contra 0%.
- **La capa fundamental NO se backtesta con LLM** (contaminación de entrenamiento): solo forward-test con el PIT store + scorecard.
- Resultado del backtest documentado en `backtests/RESULTS.md` con configuración exacta y git SHA. Si la expectancy out-of-sample neta es ≤ 0: **no pasar a fase 2**; iterar la hipótesis o pararse ahí y documentarlo (resultado válido del proyecto).

---

## 15. Gates para capital real (cuantitativos, no negociables)

`ENVIRONMENT=live` solo puede activarse si TODO esto se cumple, verificado por un script `scripts/check_live_gates.py` que lee la DB y emite informe:

1. ≥ **60 días** de paper trading continuo en testnet con el código congelado (sin cambios de estrategia; bugfixes permitidos y registrados).
2. ≥ **30 trades** cerrados en paper.
3. Expectancy neta de fees > 0 con intervalo razonable (bootstrap simple del expectancy: percentil 25 > 0 es el listón).
4. Max drawdown en paper ≤ 10%.
5. % de órdenes con error/rechazo de exchange < 2%.
6. Reconciliación sin discrepancias no explicadas en los últimos 30 días.
7. Killswitch y daily-loss-limit probados con tests de integración que simulan las condiciones.

Al pasar a live: capital inicial máximo definido por el usuario en config (`LIVE_MAX_CAPITAL`, sugerido: una cantidad cuya pérdida total sea emocionalmente irrelevante), sizing mínimo del exchange, y revisión manual semanal. Cualquier violación de gate en live (drawdown 10%) → `halt` automático.

### Seguridad operativa (obligatoria antes de live)
- API keys de Binance **sin permiso de retirada** (solo spot trading + lectura). Verificarlo programáticamente al arrancar.
- Restricción de IP en las keys.
- Keys en `.env` fuera de git; `.env.example` sin valores.
- Endpoint/CLI de `halt` manual inmediato.

---

## 16. Esquema de base de datos (resumen)

```
assets(symbol pk, base, quote, active)
candles(asset, timeframe, open_time pk compuesto, o,h,l,c,v, quote_volume)
market_snapshots(id, asset, ts, bid, ask, spread_bps, quote_vol_24h, change_24h_pct, raw_jsonb)
regime_log(id, ts, btc_regime, atr_pct, details_jsonb)
decision_logs(id, ts, mode, trigger, asset, git_sha,
              scanner_jsonb, technical_jsonb, fundamental_jsonb,
              decision_jsonb, risk_verdict_jsonb,
              final_action, rejection_reasons text[],
              expected_tp, expected_sl, horizon_class)
trade_entries(id, decision_log_id fk, environment, client_order_id unique,
              exchange_order_id, entry_time, entry_price, qty,
              tp, sl, oco_list_id, status)
trade_exits(id, trade_entry_id fk, exit_time, exit_price, exit_qty,
            exit_type, fees_paid, pnl_quote, pnl_pct_net)
position_events(id, trade_entry_id fk, ts, event_type, payload_jsonb)
equity_snapshots(id, ts, environment, equity_quote, open_positions, drawdown_pct)
news_items / social_items / item_classifications  (sección 12.1)
classifier_scorecard(id, week, stance, horizon, n, hit_rate, avg_fwd_return)
```

Migraciones con Alembic desde el día uno. `rejected_setups` es una vista sobre `decision_logs WHERE final_action='reject'`.

---

## 17. Observabilidad y alertas

- Logging JSON a stdout (`structlog`): cada job loguea inicio/fin/duración/resultado con `decision_log_id` como correlación.
- Telegram: nueva posición, cierre (con PnL), veto fundamental, halt/killswitch, error de reconciliación, resumen diario 22:00 UTC (equity, trades del día, setups rechazados por motivo).
- `/health` responde estado de: DB, API Binance, frescura de datos, modo (`running/halt`), y `git_sha`.

---

## 18. Testing (mínimos por fase)

- **Unit:** indicadores contra valores conocidos; risk engine con casos límite de CADA check (tests parametrizados: uno que pasa y uno que falla por check); redondeos a filtros de exchange; sizing.
- **Integration (testnet):** ciclo completo entrada→OCO→cancelación; idempotencia con timeout simulado; reconciliación con discrepancia inyectada; secuencia de carrera monitor↔OCO.
- **Backtest regression:** el backtest sobre un fixture fijo de velas debe producir métricas exactas conocidas (protege contra divergencia señales live/backtest).
- **Anti look-ahead:** test que verifica que ninguna señal usa la vela en curso ni items con `fetched_at` posterior al momento de decisión.

---

## 19. Criterios de aceptación por fase (checklist para Claude Code)

**Fase 0:** `docker-compose up` levanta app+postgres; migraciones aplican; velas de 10 pares en DB actualizándose cada 15 min; logs JSON; tests unit verdes.

**Fase 1:** backtest walk-forward ejecutable con un comando y RESULTS.md generado; scanner+técnico+risk producen `decision_logs` cada ciclo (incluidos rechazos); una posición completa abre y cierra en testnet vía OCO; reconciliación detecta una orden cancelada a mano en la web de testnet; killswitch probado; alertas Telegram funcionan; `/analiza <PAR>` devuelve informe con veredicto desglosado (incluido un par fuera del universo) y `/analiza <PAR> operar` respeta el risk engine.

**Fase 2:** PIT store llenándose cada 15 min; clasificador Ollama con JSON Schema y temperatura 0; scorecard semanal calculándose; un veto simulado cierra una posición de testnet.

**Fase 3:** los 3 modos de ablación ejecutables por config; tabla de política en un solo archivo legible; dashboard con equity por modo; memo LLM opcional detrás de flag.

**Fase 4:** `check_live_gates.py` implementado; verificación de permisos de API key al arrancar; `LIVE_MAX_CAPITAL` respetado en sizing.

---

## 20. Instrucciones de trabajo para Claude Code

1. Implementar estrictamente por fases y en orden. No adelantar features de fases posteriores.
2. Ante ambigüedad: elegir la opción más conservadora (fail-closed) y dejar comentario `# DECISION:` explicando la elección.
3. Todos los parámetros mencionados en este doc van a `config.py` con los defaults indicados; nada hardcodeado.
4. Precios y cantidades: `Decimal` en todo el sistema. Tests que lo verifiquen.
5. Cada PR/commit de fase incluye actualización del README con cómo ejecutar esa fase.
6. No añadir dependencias fuera del stack de la sección 5 sin justificarlo en el README.
7. Los secretos nunca en el código; leer siempre de entorno.
8. Al terminar cada fase, generar un informe corto `docs/PHASE_N_REPORT.md`: qué se hizo, qué tests lo cubren, qué quedó fuera.

---

---

## 21. Análisis bajo demanda, vigilancia core y capital ocioso

Sección añadida tras decisión de diseño con el usuario. Complementa al scanner automático **reutilizando el pipeline completo**; prohibido duplicar lógica de análisis.

### 21.1 Dos disparadores, un solo pipeline

- `trigger=scheduled`: el scanner cada 15 min sobre el universo (comportamiento ya descrito).
- `trigger=manual`: petición explícita del usuario vía comando de Telegram, CLI o endpoint HTTP.

Añadir a `core/enums.py`: `class Trigger(str, Enum): scheduled, manual`. Añadir columna `trigger` a `decision_logs` (ya reflejado en sección 16).

### 21.2 Interfaz del modo manual

Comandos de Telegram (con equivalentes CLI y HTTP):

- `/analiza SOLUSDT` → **modo INFORME (default)**: corre el pipeline completo (técnico + fundamental si fase ≥ 2 + risk engine) y devuelve un informe con el veredicto desglosado check por check (qué pasó, qué falló y por qué). NUNCA ejecuta. Registra `decision_log` con `final_action=watchlist`, `trigger=manual`.
- `/analiza SOLUSDT operar` → **modo OPERAR**: igual, pero si el risk engine aprueba, ejecuta por el flujo normal (sección 10). La palabra clave `operar` es obligatoria y explícita en cada petición; no existe configuración que haga que el modo manual opere por defecto.
- `/estado` → posiciones abiertas, equity, régimen BTC, modo del sistema (`running/halt`), drawdown actual.

**Regla innegociable:** el modo manual jamás se salta el risk engine ni el filtro de régimen. Si BTC está en `trend_down`, `/analiza X operar` devuelve el análisis completo + rechazo motivado (`regime_filter`). **No existe ningún comando de override.** Si el usuario quiere operar contra el veredicto del sistema, deberá hacerlo a mano en el exchange y esa operación quedará fuera del journal — decisión consciente: elegir la moneda a mano no puede convertirse en una puerta trasera para operar por impulso.

### 21.3 Activos fuera del universo configurado

Permitidos en modo manual con estas condiciones:

1. Debe existir el par contra USDT en Binance spot (verificar en `exchangeInfo`).
2. Descarga on-demand del histórico: mínimo 500 velas de 1h y 200 de 4h antes de analizar (persistir en `candles` para reutilización).
3. Se aplican los **mismos** filtros duros de liquidez y spread (sección 8.2). Si no los pasa, el informe lo indica y el modo operar queda bloqueado para ese activo (`rejection: liquidity/spread`).
4. Los activos analizados a mano NO se incorporan al universo del scanner automático (eso solo se cambia por config, deliberadamente).
5. Límite anti-abuso: `MANUAL_MAX_PER_HOUR=10` análisis manuales por hora, para proteger rate limits del exchange y coste de cómputo.

### 21.4 Vigilancia core (BTC/ETH "siempre a la vista")

El universo ya incluye BTC y ETH, por lo que ya se analizan cada 15 minutos aunque no generen setup. Añadido: los activos en `CORE_ASSETS` (default `BTCUSDT,ETHUSDT`) aparecen SIEMPRE en el resumen diario de Telegram aunque no haya habido setup: régimen actual, ATR%, distancia a EMA50/EMA200 y al rango vigente. Objetivo: visibilidad continua sin forzar operaciones.

### 21.5 Capital ocioso y stablecoins (aclaración de diseño)

En este sistema, **el estado por defecto del capital ya ES estar en stablecoin**: todo cotiza contra USDT, y cuando no hay posiciones abiertas (o el filtro de régimen bloquea entradas por BTC bajista), el 100% del equity está aparcado en USDT. "Ponerse en stablecoin cuando el mercado cae" no es una función que haya que construir: es el comportamiento emergente de spot solo-largo + filtro de régimen.

- Riesgo conocido y aceptado en el MVP: riesgo de emisor/depeg de USDT. Documentar como limitación en el README.
- Mitigación futura (FUERA de alcance del MVP): repartir el saldo ocioso entre USDT/USDC. Complica el enrutado (la mayoría de pares cotizan en USDT) y no aporta al aprendizaje inicial.
- Las stablecoins NUNCA son activos operables por el sistema: sin volatilidad direccional no hay setup posible. Excluidas del scanner; si se piden por `/analiza`, responder con mensaje explicativo, no con análisis.

### 21.6 Encaje en fases

- **Fase 1:** `/analiza` en modo informe (técnico + risk engine), `/estado`, columna `trigger`, y modo operar contra testnet.
- **Fase 2:** el informe manual incorpora el contexto fundamental y los items PIT usados.

## Apéndice A — Variables de configuración (defaults)

```
ENVIRONMENT=testnet            # testnet | live
MODE=technical_only            # technical_only | technical_plus_fundamental | full
UNIVERSE=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,DOTUSDT
CORE_ASSETS=BTCUSDT,ETHUSDT
MANUAL_MAX_PER_HOUR=10
MANUAL_MIN_CANDLES_1H=500
SCAN_INTERVAL_MINUTES=15
MIN_QUOTE_VOL_24H=50000000
MAX_SPREAD_BPS=5
MIN_ABS_CHANGE_PCT=3.0
RISK_PER_TRADE=0.005
MAX_POSITIONS=3
MAX_EXPOSURE_TOTAL=0.30
MAX_EXPOSURE_BTC_BETA=0.20
DAILY_LOSS_LIMIT=0.02
DRAWDOWN_KILLSWITCH=0.10
COOLDOWN_ASSET_HOURS=12
COOLDOWN_AFTER_2SL_HOURS=24
MIN_RR_NET=1.8
TAKER_FEE=0.001
ENTRY_TTL_MINUTES=45
MAX_HOLD_HOURS_INTRADAY=48
MAX_HOLD_DAYS_SWING=7
USE_REMOTE_LLM=false
OLLAMA_MODEL=qwen3.5:9b
LIVE_MAX_CAPITAL=0             # debe fijarlo el usuario explícitamente
TELEGRAM_BOT_TOKEN= / TELEGRAM_CHAT_ID=
BINANCE_API_KEY= / BINANCE_API_SECRET=   # testnet y live separadas

# DECISION (fase 1, implementación): parámetros no listados originalmente,
# necesarios para el detector de ruptura de rango (sección 3.1/8.2.4). Se
# calibran con datos reales de fase 1, como el resto de umbrales de la
# sección 13.
RANGE_LOOKBACK_CANDLES=20      # nº de velas previas que definen el rango roto
VOLUME_CONFIRM_MULT=1.5        # rel_volume mínimo para confirmar ruptura

# DECISION (fase 2, 2026-07-03): el documento nombra OLLAMA_MODEL pero no
# dice cómo conecta la app con el servidor Ollama, ni da un modelo por
# defecto obligatorio (solo ejemplos: "llama3.1:8b o qwen2.5:7b"). El
# usuario decidió: (a) usar el Ollama que ya corre en SU HOST (no un
# servicio nuevo en docker-compose) — evita duplicar/redescargar modelos
# dentro de Docker; (b) qwen3.5:9b como modelo (ya lo tenía descargado,
# generación más nueva que el qwen2.5:7b de ejemplo, y sin la sobrecarga
# de latencia de un modelo de razonamiento tipo deepseek-r1 para una tarea
# de clasificación simple y frecuente).
OLLAMA_HOST=http://host.docker.internal:11434

# DECISION (fase 2, 2026-07-03): variables no listadas originalmente para
# Reddit OAuth Data API (sección 12.2). Se crean en
# https://www.reddit.com/prefs/apps (tipo "script").
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=trading-lab/0.1 (fundamental ingest)

# DECISION (fase 1, implementación): equity de arranque para el risk engine
# mientras no exista ningún `equity_snapshots` real (antes del primer fill
# en testnet). Una vez el paper_ledger persista snapshots reales de la
# cuenta testnet (sección 10.1), estos los sustituyen por completo.
PAPER_STARTING_EQUITY_USDT=10000

# DECISION (fase 2, 2026-07-06): parámetros no listados originalmente,
# necesarios para el clasificador Ollama (sección 12.3/12.4).
FUNDAMENTAL_CLASSIFY_BATCH_SIZE=10   # items sin clasificar por corrida del job (~5-15s/item en local)
FUNDAMENTAL_VETO_HOURS=24            # cuánto tiempo permanece activo un veto=true tras classified_at

# DECISION (fase 3, 2026-07-06): el documento nombra USE_REMOTE_LLM pero
# no dice qué proveedor ni modelo usar para el memo (sección 13). Se
# llama a la API de mensajes de Anthropic directamente vía httpx (sin
# SDK nuevo). Sin REMOTE_LLM_API_KEY, generate_trade_memo no hace nada
# (no falla) aunque USE_REMOTE_LLM=true — mismo criterio que Reddit.
REMOTE_LLM_API_KEY=
REMOTE_LLM_MODEL=claude-haiku-4-5-20251001
```

## Apéndice B — Qué NO hacer (resumen de trampas conocidas)

- No usar la vela en curso para señales (look-ahead).
- No backtestear la capa LLM/fundamental (contaminación de entrenamiento).
- No usar floats para dinero.
- No reenviar órdenes tras timeout sin consultar por clientOrderId.
- No dejar que un LLM produzca números de confianza.
- No optimizar parámetros sobre todo el histórico (siempre walk-forward).
- No contar exposición por activo ignorando que todos los majors son beta-BTC.
- No pasar a live sin cumplir los gates de la sección 15.
- No añadir n8n/Celery/RAG/multi-exchange "porque queda bien en la arquitectura".
