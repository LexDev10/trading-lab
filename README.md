# trading-lab

Un bot de trading de cripto que opera en papel: analiza el mercado cada 15
minutos, decide si abriría una posición y la sigue hasta el cierre, pero no
manda ninguna orden a ningún exchange. Spot, swing corto, velas de 1h y 4h.

Lo empecé porque quería saber si una estrategia de ruptura de rango aguanta
en real o solo en el backtest. La respuesta corta, de momento: aguanta, pero
mucho menos de lo que parecía al principio.

Python 3.12, PostgreSQL, FastAPI, Streamlit, todo en Docker.

## Por qué está montado así

Tres cosas rompen casi cualquier bot, y las tres tienen su contramedida
explícita aquí:

**Sobreajuste al backtest.** La validación es walk-forward, no un backtest de
toda la historia. Y hay tests de regresión que fallan si algún indicador mira
una vela que en ese momento todavía no había cerrado.

**Ejecución de fantasía.** Al principio yo también asumía que una señal se
llena al precio de cierre. No se llena. Ahora el sistema registra una orden
pendiente con una zona de entrada y un TTL de 45 minutos, y vela a vela
comprueba si el precio llegó de verdad a tocarla. Si no llegó, la orden
expira y no cuenta. Este cambio solo, aplicado al backtest, bajó la
expectancy de +0.096% a +0.044% por trade. Duele, pero el número de antes era
mentira.

**Riesgo sin freno.** Antes de mirar si la señal es buena, el risk engine
mira si puede permitirse la operación: exposición total, exposición a cosas
correlacionadas con BTC, pérdida acumulada del día, drawdown desde el pico,
cooldown si el activo acaba de dar un stop. Si algo falla, se rechaza y queda
registrado el motivo. Ante la duda, no se opera.

Hay una capa fundamental (noticias RSS y Reddit clasificadas con un LLM local
vía Ollama), pero **solo puede vetar**, nunca abrir. Un modelo leyendo Reddit
no debería tener permiso para meterte en una posición. Ni siquiera puede
cerrar una posición abierta por su cuenta: para eso exige al menos dos
fuentes de noticias independientes coincidiendo.

## Cómo funciona un ciclo

Cada 15 minutos:

1. Descarga velas 1h/4h y el ticker de 24h de todo el universo (~10 pares).
2. Actualiza lo que ya está abierto: ¿se llenó alguna orden pendiente?, ¿tocó
   stop loss o take profit?, ¿se invalidó el setup?, ¿se pasó de tiempo?
3. **Solo entonces** escanea buscando entradas nuevas.

El orden importa y me costó un bug entenderlo: escaneando primero, el risk
engine evaluaba el límite de pérdida diaria sin contar los stops que se
acababan de tocar en ese mismo ciclo, y dejaba abrir cuando debería haber
frenado.

Cada decisión, se opere o no, queda en `decision_logs` con el veredicto
completo del risk engine. Se puede reconstruir por qué el sistema hizo o no
hizo algo cualquier día.

```
Binance + RSS/Reddit
        │
        ├── pipeline técnico ──┐
        │   (régimen BTC,      │
        │    filtros, setups)  │
        │                      ├── meta-decider ── risk engine ── paper ledger
        └── capa fundamental ──┘   (tabla de        (puede decir   (fills, SL/TP,
            (clasificación,         política)        que no)        equity)
             veto)                                                    │
                                                     ┌────────────────┴──────────┐
                                                 dashboard              Telegram
                                              (Streamlit/API)      (alertas + informe)
```

## Ponerlo en marcha

Necesitas Docker y poco más. Ollama es opcional: sin él, el modo por defecto
(`technical_only`) funciona igual.

```bash
git clone https://github.com/LexDev10/trading-lab.git
cd trading-lab
cp .env.example .env
docker compose up -d --build
```

Con los valores por defecto ya opera en papel: no hacen falta claves de
Binance porque los datos de mercado son endpoints públicos y no se ejecuta
nada real. Si quieres las notificaciones, rellena `TELEGRAM_BOT_TOKEN` y
`TELEGRAM_CHAT_ID`.

- Dashboard: http://localhost:8501
- Healthcheck: http://localhost:8000/health

Para dejarlo corriendo 24/7 en un servidor está [docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md).
En un VPS hay que añadir el override de producción, no es opcional: el
compose base publica puertos pensando en una máquina local, y ni la API ni el
dashboard tienen login.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Uso diario

```bash
# Ver cómo va la cartera
docker compose exec app uv run python -m scripts.estado

# Analizar un activo a mano, esté o no en el universo
docker compose exec app uv run python -m scripts.analiza SOLUSDT
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar

# Congelar el sistema (rechaza toda entrada nueva hasta rearmar)
docker compose exec app uv run python -m scripts.halt "volatilidad rara"
docker compose exec app uv run python -m scripts.rearm

# Generar el informe diario sin esperar al cron
docker compose exec app uv run python -m scripts.informe
```

El resto está en [COMANDOS.md](COMANDOS.md).

## Informe diario

Todos los días a las 22:00 (hora de Madrid, configurable) llega un informe
por Telegram y se guarda una copia en `reports/`: equity y drawdown contra
sus límites, los trades cerrados del día uno a uno, cuántas órdenes
expiraron sin llenarse, las posiciones abiertas con su PnL no realizado, los
motivos por los que se rechazaron señales, vetos fundamentales activos y el
acumulado histórico.

Además avisa en el momento de cada apertura y cada cierre.

Detalle tonto pero que importa: la ventana que agrega el informe es el día
UTC aunque se envíe a las 22:00 de Madrid, porque tiene que contar el mismo
"hoy" que el límite de pérdida diaria del risk engine. Si no, el informe te
diría una cosa y el freno estaría mirando otra.

## Qué dice el backtest

Walk-forward sobre unos 800 días, resultados out-of-sample:

| | |
|---|---|
| Trades | 719 |
| Expectancy | +0.0438% por trade |
| Profit factor | 1.41 |
| Win rate | 27.4% |
| Max drawdown | −13.78% |

Es una estrategia de pocas ganadoras y grandes, que es exactamente lo que
uno espera de rupturas de rango. El drawdown de −13.78% está por encima del
killswitch del 10%, así que simulé qué habría pasado aplicando el freno sobre
esa misma lista de trades: todas las métricas mejoran, incluso en el
escenario más pesimista. Metodología y matices en
[backtests/RESULTS.md](backtests/RESULTS.md).

No extrapoles esto. Son 719 trades de una ventana histórica concreta.

## Lo que todavía no hace

Prefiero ser explícito con esto:

- **No ejecuta órdenes reales.** El conector OCO contra Binance no está
  construido. Hasta que exista y pase los gates de validación (≥60 días y
  ≥30 trades con el código congelado), esto es un laboratorio, no un bot de
  producción.
- La vela en la que se abre la posición queda fuera del seguimiento de
  SL/TP: hasta 4h sin vigilar justo después de entrar.
- Las salidas por gap se registran al precio exacto del stop, que es
  optimista. Si una vela abre por debajo, en la vida real habrías salido peor.
- El backtest no aplica el filtro de régimen de BTC ni los filtros duros del
  scanner, porque necesitaría un histórico de snapshots de mercado que no se
  persiste.
- No hay `uv.lock` en el repo, así que dos `docker build` en fechas distintas
  pueden resolver versiones distintas de las dependencias.

Los bugs corregidos y las decisiones de diseño que no son obvias están en
[CLAUDE.md](CLAUDE.md) y en el [CHANGELOG.md](CHANGELOG.md), con bastante
detalle sobre qué estaba mal y por qué.

## Estado

- [x] Fase 0 — arquitectura, esquemas, migraciones
- [x] Fase 1 — pipeline técnico, risk engine, paper trading, alertas
- [x] Fase 2 — capa fundamental, clasificador local, veto, scorecard
- [x] Fase 3 — meta-decider, dashboard, informes
- [ ] Fase 4 — ejecución real en testnet con reconciliación

## Aviso

Esto es un proyecto de investigación. El trading de cripto puede hacerte
perder todo el capital. No lo conectes a dinero real sin entender cada línea
de lo que hace y bajo tu propia responsabilidad. Yo tampoco lo he hecho.
