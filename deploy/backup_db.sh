#!/usr/bin/env bash
# Backup del Postgres del sistema (VPS). Uso:
#
#   ./deploy/backup_db.sh                 # a ./backups
#   ./deploy/backup_db.sh /var/backups/tl # a otro directorio
#
# Pensado para cron. Ejemplo (03:15 cada dia):
#   15 3 * * * cd /opt/trading-lab && ./deploy/backup_db.sh >> /var/log/trading-lab-backup.log 2>&1
#
# Por que hace falta: el historico de paper trading (>=60 dias / >=30
# trades) es lo unico que puede desbloquear los gates de capital real
# (seccion 15 del spec). Vive en un volumen Docker del VPS; sin copia,
# un `docker compose down -v` o un disco muerto obliga a empezar de cero.
set -euo pipefail

DEST="${1:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

cd "$(dirname "$0")/.."

# Las credenciales salen del .env del proyecto, no se duplican aqui.
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- | tr -d '"' || echo trading)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- | tr -d '"' || echo trading_lab)"
POSTGRES_USER="${POSTGRES_USER:-trading}"
POSTGRES_DB="${POSTGRES_DB:-trading_lab}"

mkdir -p "$DEST"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="$DEST/trading_lab-$STAMP.sql.gz"

# `pg_dump` dentro del contenedor: no hace falta cliente psql en el host.
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUT"

# Fail-closed: un dump vacio o truncado es peor que no tener backup,
# porque da falsa sensacion de seguridad. Se borra y se sale con error.
if [ ! -s "$OUT" ] || [ "$(stat -c%s "$OUT")" -lt 1000 ]; then
    echo "ERROR: dump vacio o sospechosamente pequeno ($OUT) — se descarta" >&2
    rm -f "$OUT"
    exit 1
fi

echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"

find "$DEST" -name 'trading_lab-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
echo "Retencion: borrados los backups de mas de $RETENTION_DAYS dias en $DEST"
