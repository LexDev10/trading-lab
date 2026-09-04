# Comandos útiles — trading-lab

Referencia rápida de los comandos más usados. Todo corre dentro de
Docker (no hay nada que instalar en el host salvo Docker Desktop). Para
el detalle de cada pieza ver [README.md](README.md); para reglas de
negocio y bugs ya corregidos ver [CLAUDE.md](CLAUDE.md).

## Arranque / parada

```bash
cp .env.example .env                  # solo la primera vez
docker compose up -d --build          # levanta postgres + app + dashboard (LOCAL)
docker compose ps                     # ver estado de los contenedores
docker compose logs -f app            # logs en vivo del scheduler/API
docker compose down                   # parar todo (mantiene el volumen de postgres)
```

**Importante**: no hay bind mount — tras editar código hay que
reconstruir para que el contenedor lo vea:

```bash
docker compose up -d --build app
docker compose up -d --build dashboard
```

Verificar que la app responde:

```bash
curl http://localhost:8000/health
```

En un **VPS** hay que añadir siempre el override de producción (cierra
puertos, rota logs, monta `reports/`) — ver
[docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Analizar un activo (equivalente a `/analiza`)

```bash
# Modo informe — nunca ejecuta, solo registra la decisión
docker compose exec app uv run python -m scripts.analiza SOLUSDT

# Modo operar — respeta el risk engine; si aprueba, abre posición de PAPEL
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
```

Funciona con cualquier par USDT, esté o no en `UNIVERSE` (lo descarga
on-demand). Rechaza stablecoins y pares que no existan en Binance Spot.

También disponible visualmente en el dashboard (pestaña "🔍 Analizar",
selector del universo o texto libre) — ver más abajo.

## Estado del sistema (equivalente a `/estado`)

```bash
docker compose exec app uv run python -m scripts.estado
```

Régimen BTC, equity, drawdown, posiciones de papel abiertas, resumen de
cerradas (win rate, PnL, profit factor).

## Dashboards

```bash
# Streamlit — interactivo, selector de activo, tabs (resumen, analizar,
# posiciones, decisiones, fundamental, trades)
# http://localhost:8501  (levantado automáticamente por docker compose up)

# HTML minimalista — solo lectura, sin selector de activo
# http://localhost:8000/dashboard
```

## Informe diario

Se envía solo por Telegram a la hora local configurada
(`DAILY_REPORT_HOUR` en `REPORT_TIMEZONE`, por defecto 22:00
Europe/Madrid) y deja copia en `REPORTS_DIR/informe-YYYY-MM-DD.md`.
Para generarlo a mano sin esperar al cron:

```bash
docker compose exec app uv run python -m scripts.informe            # imprime y guarda
docker compose exec app uv run python -m scripts.informe --enviar   # además notifica por Telegram
docker compose exec app uv run python -m scripts.informe --no-guardar
```

La ventana que agrega el informe es el día UTC (la misma que el
`daily_loss_limit` del risk engine), aunque el envío sea a hora local.

## Halt / rearme del killswitch

```bash
docker compose exec app uv run python -m scripts.halt "motivo del halt"
docker compose exec app uv run python -m scripts.rearm
```

Con el sistema en `halt`, el risk engine rechaza toda entrada nueva
hasta el rearme explícito (nunca automático).

## Backtesting

```bash
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
docker compose exec app uv run python -m backtests.simulate_killswitch
```

Resultados y metodología en [backtests/RESULTS.md](backtests/RESULTS.md)
(recalculado 2026-07-07 con el motor de fill corregido).

## Tests

```bash
# Unitarios (sin DB, rápidos)
docker compose run --rm --no-deps app uv run pytest -v

# Integración (necesitan Postgres real levantado)
docker compose exec app uv run pytest tests/integration -v

# Todo junto
docker compose exec app uv run pytest -q

# Tipado estricto
docker compose run --rm --no-deps app uv run mypy
```

## Migraciones (Alembic)

```bash
docker compose exec app uv run alembic revision --autogenerate -m "mensaje"
docker compose exec app uv run alembic upgrade head
docker compose exec app uv run alembic current      # ver revisión actual
docker compose exec app uv run alembic history       # ver historial
```

## Backups (VPS)

```bash
./deploy/backup_db.sh                 # dump comprimido en ./backups
RETENTION_DAYS=30 ./deploy/backup_db.sh /var/backups/trading-lab
```

Restaurar:

```bash
gunzip -c backups/trading_lab-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T postgres psql -U trading -d trading_lab
```

## Base de datos (acceso directo)

```bash
docker compose exec postgres psql -U trading -d trading_lab
```

## Flujo típico tras editar código

```bash
docker compose up -d --build app           # o "dashboard" si el cambio fue ahí
docker compose exec app uv run pytest -q
docker compose run --rm --no-deps app uv run mypy
```
