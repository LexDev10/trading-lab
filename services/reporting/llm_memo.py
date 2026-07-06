"""Memo LLM opcional (sección 13, fase 3): un LLM externo redacta un
resumen legible del trade a partir de los payloads YA decididos
(técnico + fundamental + risk + meta-decider) — **nunca** influye la
decisión, solo la explica después de tomada.

# DECISION: igual que `notifications/telegram.py`, este módulo es
# FAIL-OPEN — un memo perdido (red caída, rate limit, API key inválida)
# nunca debe bloquear la apertura de una posición de papel ya aprobada.
# Sin `USE_REMOTE_LLM=true` o sin `REMOTE_LLM_API_KEY`, no toca la red
# (mismo criterio que Reddit/Telegram sin credenciales).
#
# Se llama a la API de mensajes de Anthropic directamente vía `httpx`
# (ya en el stack) en vez de añadir el SDK oficial como dependencia
# nueva — mismo criterio que `services/fundamental/classify.py` con
# Ollama."""

import json
from typing import Any

import httpx

from app.config import Settings
from core.logging import get_logger

logger = get_logger("llm_memo")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
MAX_TOKENS = 300

PROMPT_TEMPLATE = """Redacta un memo breve (3-5 frases, en español, tono neutro y \
profesional) que explique esta decisión de trading ya tomada por un sistema \
determinista. No opines sobre si es acertada ni añadas recomendaciones nuevas — \
solo resume, para un lector humano, por qué el sistema decidió lo que decidió a \
partir de estos datos:

{payload}
"""


def _build_prompt(decision_payload: dict[str, Any]) -> str:
    return PROMPT_TEMPLATE.format(payload=json.dumps(decision_payload, indent=2, ensure_ascii=False, default=str))


async def generate_trade_memo(settings: Settings, decision_payload: dict[str, Any]) -> str | None:
    if not settings.use_remote_llm or not settings.remote_llm_api_key:
        return None

    payload = {
        "model": settings.remote_llm_model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": _build_prompt(decision_payload)}],
    }
    headers = {
        "x-api-key": settings.remote_llm_api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
            response.raise_for_status()
        content = response.json()["content"]
        return str(content[0]["text"]).strip() if content else None
    except Exception:
        logger.exception("llm_memo.generate_failed")
        return None
