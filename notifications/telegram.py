"""Alertas salientes de Telegram (sección 17): nueva posición, cierre con
PnL, halt/rearme, resumen diario. Solo alertas — sin comandos interactivos
(`/analiza`/`/estado` por Telegram quedan fuera, ya existen como CLI).

# DECISION: a diferencia del resto del sistema (fail-closed: ante la duda,
# no operar), este módulo es FAIL-OPEN — un fallo de Telegram (red, token
# inválido, rate limit) nunca debe bloquear una decisión de trading ni
# tumbar un ciclo del scheduler. Se loguea y se sigue.

Módulo "hoja": no importa `db.models` ni `core.schemas` — los call sites
construyen el texto y solo pasan un string, para no acoplar el envío al
resto del dominio.
"""

import httpx

from app.config import Settings
from core.logging import get_logger

logger = get_logger("telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"

# Límite duro de la Bot API para `sendMessage` (4096 caracteres). Se trocea
# por debajo porque el sufijo de paginación ("(1/3)") también ocupa.
TELEGRAM_MAX_CHARS = 4096
CHUNK_LIMIT = 3800


def split_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Trocea un texto largo en mensajes que la Bot API acepte.

    # DECISION (2026-09-04): el informe diario enriquecido puede superar
    # los 4096 caracteres de `sendMessage` (varias posiciones abiertas +
    # muchos motivos de rechazo). Antes, ese caso devolvía 400 y el
    # informe se perdía entero (fail-open: se logueaba y ya). Se trocea
    # por LÍNEAS completas para no partir a la mitad una etiqueta HTML de
    # `parse_mode`; una línea individual más larga que el límite (no
    # esperable en los informes actuales) se corta en duro como último
    # recurso, preferible a no enviar nada.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for raw_line in text.split("\n"):
        line = raw_line
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def send_message(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    chunks = split_message(text)
    total = len(chunks)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for index, chunk in enumerate(chunks, start=1):
                body = chunk if total == 1 else f"{chunk}\n\n({index}/{total})"
                payload = {"chat_id": settings.telegram_chat_id, "text": body, "parse_mode": "HTML"}
                response = await client.post(url, json=payload)
                response.raise_for_status()
    except Exception:
        logger.exception("telegram.send_failed")
