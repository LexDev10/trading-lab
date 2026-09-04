# Despliegue en VPS

Guía para poner el sistema a correr 24/7 en un servidor propio, en modo
**paper** (que es lo único que el proyecto puede hacer hoy: no hay
executor real, ver `CLAUDE.md` → "Deuda operativa antes de dinero real").

Todo lo que hace falta en el VPS es Docker. El código no se instala en el
host: se construye la imagen y corre en contenedores, igual que en local.

---

## 0. Qué vas a conseguir

- El scheduler corriendo cada 15 min (ingesta → cierres → escaneo).
- Un **informe diario por Telegram** a las 22:00 hora de Madrid, con
  copia en `reports/informe-YYYY-MM-DD.md` dentro del VPS.
- Alertas inmediatas por Telegram al abrir/cerrar posición.
- Dashboard y API accesibles **solo por túnel SSH** (no publicados a
  Internet: ninguno de los dos tiene login).

---

## 1. Requisitos del servidor

| Recurso | Mínimo | Recomendado | Por qué |
|---|---|---|---|
| RAM | 2 GB | 4 GB | `pandas` + `vectorbt` + `numpy` + Streamlit + Postgres en la misma máquina. Con 1 GB el build muere por OOM. |
| Disco | 20 GB | 40 GB | Imagen Docker ~1.5 GB, velas de 10 activos × 2 timeframes creciendo, logs y backups. |
| CPU | 1 vCPU | 2 vCPU | El ciclo es corto; el pico real es el `docker build`. |
| SO | Debian 12 / Ubuntu 22.04+ | — | Docker oficial. |

Con `MODE=technical_only` (el default) **no hace falta Ollama**, que es lo
que pediría GPU o mucha RAM. Si algún día quieres el modo fundamental
completo, el clasificador necesita un Ollama accesible: en un VPS pequeño
eso significa usarlo remoto, no instalarlo al lado.

> El swap ayuda si te quedas en 2 GB: `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile` (y la línea correspondiente en `/etc/fstab`).

---

## 2. Preparar el VPS

Como root, la primera vez:

```bash
apt update && apt upgrade -y
adduser trading && usermod -aG sudo trading
```

Instalar Docker (repositorio oficial, no el `docker.io` de la distro):

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker trading
```

Endurecer SSH — **hazlo antes de exponer nada**. En `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
```

y `systemctl restart ssh`. Copia tu clave con `ssh-copy-id trading@IP`
**antes** de desactivar la contraseña, o te quedas fuera.

Cortafuegos: solo SSH abierto. El dashboard y la API no se publican.

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw enable
```

---

## 3. Subir el código

El repositorio es `https://github.com/LexDev10/trading-lab`. Como usuario
`trading`:

```bash
sudo mkdir -p /opt/trading-lab && sudo chown trading:trading /opt/trading-lab
git clone https://github.com/LexDev10/trading-lab.git /opt/trading-lab
cd /opt/trading-lab
```

Si el repo es privado, usa una *deploy key* de solo lectura
(`ssh-keygen -t ed25519 -f ~/.ssh/deploy_key` y súbela en
Settings → Deploy keys del repo) en vez de tu clave personal.

---

## 4. Configurar `.env`

**`.env` no está en git y no debe estarlo** (contiene el token real de
Telegram). Créalo en el VPS a partir del ejemplo:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Lo que **tienes que** cambiar respecto al ejemplo:

```dotenv
# Contraseña de Postgres: NO dejes la de ejemplo en un servidor
POSTGRES_PASSWORD=<algo largo y aleatorio: openssl rand -base64 24>
DATABASE_URL=postgresql+asyncpg://trading:<esa misma password>@postgres:5432/trading_lab

# Telegram (sin esto no hay informes ni alertas)
TELEGRAM_BOT_TOKEN=<token de @BotFather>
TELEGRAM_CHAT_ID=<tu chat id>

# Informe diario
REPORT_TIMEZONE=Europe/Madrid
DAILY_REPORT_HOUR=22
```

Ojo con `DATABASE_URL`: dentro de Docker el host de la base de datos es
`postgres` (el nombre del servicio), **no** `localhost`. El
`docker-compose.yml` ya inyecta el valor correcto en los contenedores,
pero si editas la variable a mano, respeta ese host.

`BINANCE_API_KEY` / `BINANCE_API_SECRET` pueden quedarse vacías: la
ingesta de mercado usa endpoints públicos y el ledger es de papel. No
pongas claves reales en el VPS hasta que exista el executor y hayan
pasado los gates de la sección 15 del spec.

Para sacar tu `TELEGRAM_CHAT_ID`: escribe algo a tu bot y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates`; el chat id sale en
`message.chat.id`.

---

## 5. Levantar el stack

Siempre con los **dos** ficheros de compose — el segundo es el que cierra
los puertos y activa la rotación de logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

El primer build tarda varios minutos (compila `numpy`/`pandas`/`vectorbt`).

Comprobar:

```bash
docker compose ps
docker compose logs -f app          # Ctrl-C para salir
curl http://127.0.0.1:8000/health
```

`/health` debe responder `"status":"ok"` y, tras el primer ciclo,
`"data_fresh":true`.

Como escribirás ese comando a menudo, un alias ayuda
(`~/.bashrc`):

```bash
alias tl='docker compose -f /opt/trading-lab/docker-compose.yml -f /opt/trading-lab/docker-compose.prod.yml'
```

---

## 6. Ver el dashboard desde tu portátil

Nada está publicado a Internet a propósito. Abre un túnel SSH desde tu
máquina (Windows incluido, PowerShell):

```bash
ssh -N -L 8501:127.0.0.1:8501 -L 8000:127.0.0.1:8000 trading@TU_IP
```

Y abre `http://localhost:8501` en el navegador mientras el túnel esté
levantado.

> Si algún día quieres el dashboard accesible desde el móvil sin túnel,
> **no** basta con abrir el puerto: hace falta un reverse proxy (Caddy o
> nginx) con TLS **y** autenticación básica delante, porque la pestaña
> "Analizar" de Streamlit ejecuta trabajo en el sistema. Publicar el 8501
> tal cual deja eso al alcance de cualquiera.

---

## 7. Informes diarios y notificaciones

Ya están automatizados: el scheduler dispara `daily_summary_job` a la
hora local que fijes en `.env`.

- **Cuándo**: `DAILY_REPORT_HOUR`:`DAILY_REPORT_MINUTE` en
  `REPORT_TIMEZONE` (por defecto 22:00 Europe/Madrid).
- **Dónde**: Telegram (troceado automáticamente si pasa de 4096
  caracteres) + `reports/informe-YYYY-MM-DD.md` en el host, gracias al
  volumen que monta `docker-compose.prod.yml`.
- **Qué lleva**: estado del sistema y frescura de datos, equity y
  drawdown contra sus límites, trades cerrados hoy uno a uno, órdenes
  registradas y cuántas expiraron sin fill, posiciones abiertas con PnL
  no realizado, rechazos por motivo, vetos fundamentales activos,
  vigilancia de los activos core y el acumulado histórico (win rate,
  expectancy, profit factor).

La **ventana** que agrega el informe es el día **UTC**, aunque se envíe a
las 22:00 de Madrid: tiene que coincidir con la ventana del
`daily_loss_limit` del risk engine, o el informe diría una cosa y el
freno de riesgo otra.

Probarlo sin esperar al cron:

```bash
docker compose exec app uv run python -m scripts.informe            # imprime y guarda
docker compose exec app uv run python -m scripts.informe --enviar   # además lo manda a Telegram
```

Bajarte los informes al portátil:

```bash
scp trading@TU_IP:/opt/trading-lab/reports/*.md ./
```

Además del informe diario ya recibes alertas inmediatas al abrir y
cerrar posición, y el resumen de cada ciclo de escaneo.

---

## 8. Backups

El histórico de paper trading es lo único que puede desbloquear los gates
de capital real (≥60 días / ≥30 trades). Vive en un volumen Docker: sin
copia, un `docker compose down -v` lo borra sin preguntar.

```bash
./deploy/backup_db.sh                       # a ./backups
crontab -e
```

```cron
15 3 * * * cd /opt/trading-lab && ./deploy/backup_db.sh >> /var/log/trading-lab-backup.log 2>&1
```

Restaurar:

```bash
gunzip -c backups/trading_lab-20260904-031500.sql.gz | \
  docker compose exec -T postgres psql -U trading -d trading_lab
```

Llévate una copia **fuera** del VPS de vez en cuando (`scp`): un backup
en el mismo disco que la base de datos no protege del fallo del disco.

---

## 9. Actualizar el código

**No hay bind mount**: el contenedor no ve los cambios del host en
caliente. Después de cada `git pull` hay que reconstruir, o estarás
ejecutando código viejo (y un `alembic upgrade head` puede fallar con
"Can't locate revision"):

```bash
cd /opt/trading-lab
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f app
```

Las migraciones se aplican solas al arrancar (`alembic upgrade head`
está en el `command` del servicio `app`).

---

## 10. Operación diaria

```bash
# Estado
docker compose exec app uv run python -m scripts.estado
curl http://127.0.0.1:8000/health

# Parar el sistema en caliente (rechaza toda entrada nueva)
docker compose exec app uv run python -m scripts.halt "motivo"
docker compose exec app uv run python -m scripts.rearm

# Analizar un activo a mano
docker compose exec app uv run python -m scripts.analiza SOLUSDT

# Logs
docker compose logs -f app
docker compose logs --since 24h app | grep -i error
```

El `restart: unless-stopped` de todos los servicios hace que el stack
vuelva solo tras un reinicio del VPS (Docker arranca con el sistema).

---

## 11. Antes de dar por bueno el despliegue

- [ ] `/health` devuelve `status: ok` y `data_fresh: true`.
- [ ] Ha llegado un mensaje de prueba a Telegram
      (`scripts.informe --enviar`).
- [ ] `docker compose ps` no muestra el 5432 ni el 8000/8501 en `0.0.0.0`.
- [ ] `ufw status` solo deja SSH.
- [ ] El cron de backup ha generado su primer `.sql.gz`.
- [ ] Los tests pasan **dentro del contenedor construido en el VPS**:
      `docker compose exec app uv run pytest -q` y
      `docker compose exec app uv run pytest tests/integration -q`.

---

## Limitaciones conocidas de este despliegue

- **Builds no reproducibles**: no hay `uv.lock` en el repo, así que el
  `uv sync` del Dockerfile resuelve versiones en cada build. Dos
  despliegues en fechas distintas pueden acabar con dependencias
  distintas. Generar y commitear el lock (`uv lock`) es la solución;
  hasta entonces, si un build falla tras funcionar antes, sospecha de
  esto primero.
- Los contenedores corren como **root** y el volumen `./reports` queda a
  nombre de root en el host (léelo con `sudo` o cambia el owner).
- No hay alerta de "el sistema se ha caído": si el contenedor muere y no
  se recupera, lo notarás porque deja de llegar el informe diario. Un
  ping externo (Uptime Kuma, Healthchecks.io) contra `/health` cubriría
  ese hueco.
- El sistema sigue siendo **paper**: no ejecuta órdenes reales, y no debe
  hacerlo hasta completar los gates de la sección 15 del spec.
