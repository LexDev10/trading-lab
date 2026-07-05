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


async def send_message(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except Exception:
        logger.exception("telegram.send_failed")
