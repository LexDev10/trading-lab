# trading-lab

Sistema modular de trading cuantitativo y laboratorio de investigación para spot crypto (estrategias de swing corto / intradía en velas de 1h y 4h). 

Construido en **Python 3.10+, PostgreSQL, FastAPI, Streamlit y Docker**, con un enfoque en **disciplina de riesgo estricta, reproducibilidad sin sesgo de anticipación (*lookahead bias*) y fusión determinista técnico-fundamental**.

---

## 🎯 Filosofía del proyecto

La mayoría de bots fallan por tres motivos: sobreoptimización en backtest (*curve fitting*), ejecución poco realista (asumir fills inmediatos a precio de cierre) y riesgo descontrolado en caídas de mercado. 

Este proyecto se diseñó con principios claros:

1. **Riesgo primero, estrategia después**: Antes de considerar cualquier entrada, el *Risk Engine* evalúa límites de exposición, beta frente a BTC, límites de pérdida diaria y drawdown acumulado con *Killswitch* automático.
2. **Fusión determinista (Técnico × Fundamental)**: La capa fundamental (noticias y sentimiento analizados con LLM local vía Ollama) no genera órdenes por sí sola; actúa como **veto de seguridad** y modula el tamaño de posición mediante una matriz de política explícita y auditable.
3. **Simulación de ejecución realista**: El motor de *Paper Trading* no asume ejecuciones mágicas; coloca órdenes pendientes con TTL, verifica si el rango de la siguiente vela realmente tocó el precio de entrada, descuenta comisiones de taker y gestiona invalidaciones técnicas y por tiempo.
4. **Cero dependencias complejas en el host**: Todo el ecosistema (Postgres, API, scheduler, dashboard) corre contenerizado en Docker.

---

## 🏗️ Arquitectura del Sistema

```
                      ┌─────────────────────────────────────────┐
                      │            Fuentes de Datos             │
                      │  • Binance Market Data (1h/4h OHLCV)    │
                      │  • RSS/JSON (CoinDesk, The Block, etc.) │
                      │  • Reddit Data API (r/CryptoCurrency)   │
                      └────────────────────┬────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
       ┌────────────────────────┐                   ┌────────────────────────┐
       │   Pipeline Técnico     │                   │   Capa Fundamental     │
       │  • Régimen de BTC      │                   │  • Ingesta PIT         │
       │  • Filtros de liquidez │                   │  • Clasificador Ollama │
       │  • Indicadores (EMA/ATR)│                  │  • Veto fundamental    │
       │  • Setups (Breakout)   │                   │  • Scorecard semanal   │
       └────────────┬───────────┘                   └────────────┬───────────┘
                    │                                            │
                    └──────────────────────┬─────────────────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │     Risk Engine     │
                                │  • Tamaño por ATR   │
                                │  • Drawdown limit   │
                                │  • Killswitch       │
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │    Meta-Decider     │
                                │  (Tabla de Política)│
                                └──────────┬──────────┘
                                           │
                                           ▼
                                ┌─────────────────────┐
                                │    Paper Ledger     │
                                │ (Órdenes pendientes,│
                                │  fills, SL/TP/exit) │
                                └──────────┬──────────┘
                                           │
                     ┌─────────────────────┴─────────────────────┐
                     ▼                                           ▼
       ┌──────────────────────────┐                ┌──────────────────────────┐
       │     Dashboard & UI       │                │  Alertas y Registro      │
       │  • Streamlit (puerto 8501│                │  • Bot de Telegram       │
       │  • FastAPI (puerto 8000) │                │  • PostgreSQL Journal    │
       └──────────────────────────┘                └──────────────────────────┘
```

### Módulos principales

- **`services/scanner/`**: Scanner periódico (cada 15 min por defecto). Filtra activos por volumen mínimo 24h, spread, rango y régimen de BTC.
- **`services/technical/`**: Cálculo de medias, canales Donchian, ATR, RSI y generador de señales técnicas (setups de ruptura con confirmación de volumen).
- **`services/fundamental/`**: Ingesta *Point-in-Time* (PIT) de noticias y redes sociales. Clasificación local con **Ollama** (`qwen3.5:9b`) para detectar postura de mercado (`bullish_strong`, `bearish_strong`, etc.) y activar vetos inmediatos ante eventos adversos.
- **`services/risk/`**: Motor central de riesgo. Dimensiona posiciones por riesgo fijo (ej. 0.5% del capital), limita posiciones simultáneas, correlación y frena la operativa ante pérdidas sucesivas.
- **`services/decision/policy.py`**: Matriz de decisión determinista que unifica la señal técnica con la postura fundamental (con modos de ablación: `technical_only`, `technical_plus_fundamental`, `full`).
- **`services/execution/paper_ledger.py`**: Simulador de ejecución vela a vela sobre datos reales, con tracking de equity, comisiones y tipos de salida (SL, TP, invalidación técnica, caducidad temporal o veto fundamental).
- **`dashboard/` & `notifications/`**: Dashboard interactivo en Streamlit con visualización de trades y decisiones, endpoint web FastAPI y alertas en Telegram.

---

## 🚀 Puesta en marcha rápida

### 1. Requisitos previos
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (con Docker Compose v2).
- *(Opcional para capa fundamental)* [Ollama](https://ollama.ai/) corriendo localmente en tu máquina con el modelo `qwen3.5:9b` (`ollama run qwen3.5:9b`).

### 2. Configuración
Clona el repositorio y crea tu archivo de configuración:

```bash
git clone https://github.com/LexDev10/trading-lab.git
cd trading-lab
cp .env.example .env
```

Edita `.env` según tus preferencias. Los valores por defecto funcionan directamente para operar en modo simulación (paper trading) sin necesidad de claves de API.

### 3. Levantar el entorno
```bash
docker compose up -d --build
```

Esto iniciará:
- **Base de datos**: PostgreSQL en el puerto `5432` con migraciones automáticas de Alembic.
- **App Core & Scheduler**: Motor de análisis, ingesta y monitor de órdenes.
- **Dashboard Streamlit**: Interfaz gráfica en [http://localhost:8501](http://localhost:8501).
- **Dashboard FastAPI / Healthcheck**: [http://localhost:8000/health](http://localhost:8000/health).

---

## 💻 Uso y comandos útiles

### Análisis bajo demanda (`/analiza`)
Puedes analizar cualquier activo en cualquier momento sin esperar al ciclo programado del scanner:

```bash
# Modo informe (solo analiza y genera el reporte técnico/fundamental)
docker compose exec app uv run python -m scripts.analiza SOLUSDT

# Modo operar (analiza y, si el Risk Engine aprueba, coloca orden en el Paper Ledger)
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
```

### Consultar estado de la cartera (`/estado`)
Muestra el régimen de mercado de BTC, equity actual, drawdown acumulado y el desglose de posiciones abiertas y cerradas:

```bash
docker compose exec app uv run python -m scripts.estado
```

### Gestión de emergencias (*Killswitch*)
Si el mercado presenta anomalías o mantenimiento, puedes congelar o rearmar manualmente el sistema:

```bash
# Pausar todas las nuevas entradas
docker compose exec app uv run python -m scripts.halt "Mantenimiento / volatilidad extrema"

# Reanudar operativa normal
docker compose exec app uv run python -m scripts.rearm
```

---

## 📊 Backtesting y Validación

El proyecto incluye un motor de simulación *Walk-Forward* para evaluar las estrategias con datos históricos fuera de muestra (*out-of-sample*) y pruebas de regresión contra *lookahead bias*.

```bash
# Descargar 800 días de histórico para el universo
docker compose exec app uv run python -m backtests.download_history --days 800

# Ejecutar validación walk-forward
docker compose exec app uv run python -m backtests.walk_forward

# Simular comportamiento del killswitch ante rachas de pérdidas
docker compose exec app uv run python -m backtests.simulate_killswitch
```

*Los resultados y metodología detallada pueden consultarse en [backtests/RESULTS.md](backtests/RESULTS.md).*

---

## 🧪 Tests y Calidad de Código

El proyecto cuenta con una amplia suite de pruebas unitarias y de integración:

```bash
# Tests unitarios rápidos (sin base de datos)
docker compose run --rm --no-deps app uv run pytest tests/unit -v

# Tests de integración (con PostgreSQL)
docker compose exec app uv run pytest tests/integration -v

# Comprobación de tipos estáticos con MyPy
docker compose run --rm --no-deps app uv run mypy
```

---

## 🗺️ Estado del Proyecto y Roadmap

- [x] **Fase 0**: Fundamentos, arquitectura modular, esquemas Pydantic inmutables y base de datos con Alembic.
- [x] **Fase 1**: Pipeline técnico, scanner de mercado, motor de riesgo (Risk Engine), paper trading con órdenes pendientes y alertas de Telegram.
- [x] **Fase 2**: Capa fundamental, almacén inmutable PIT, ingesta RSS/Noticias, clasificador local LLM con Ollama, veto fundamental automático y scorecard de rendimiento.
- [x] **Fase 3**: Meta-decider por tabla de política, dashboard interactivo en Streamlit y panel de métricas.
- [ ] **Fase 4**: Conector de ejecución OCO en Binance Testnet/Live con reconciliación de órdenes y balance real.

---

## ⚠️ Descargo de responsabilidad

Este software está diseñado únicamente con fines de **investigación cuantitativa y educación**. El trading de criptomonedas y activos financieros conlleva un riesgo sustancial de pérdida de capital. No utilices este software con fondos reales sin una validación exhaustiva y bajo tu propia responsabilidad.
