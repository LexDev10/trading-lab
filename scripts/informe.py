"""Genera el informe diario bajo demanda (el mismo que el scheduler envía
a la hora local configurada, sección 17) — útil para probar el formato sin
esperar al cron, o para reenviarlo si Telegram estaba caído.

Uso (con el stack levantado):
    docker compose exec app uv run python -m scripts.informe            # imprime y guarda
    docker compose exec app uv run python -m scripts.informe --enviar   # además notifica
    docker compose exec app uv run python -m scripts.informe --no-guardar

# DECISION (2026-09-04): por defecto NO envía a Telegram — un comando
# manual no debe generar ruido en el canal por accidente; el envío es
# opt-in explícito con `--enviar`. El job del scheduler sí envía siempre.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from db.session import get_session
from notifications.telegram import send_message
from services.reporting.daily_summary import build_daily_summary, next_report_run, save_report


async def informe(*, enviar: bool, guardar: bool) -> None:
    settings = get_settings()
    now = datetime.now(tz=UTC)

    async with get_session() as session:
        text = await build_daily_summary(session, settings, now)

    print(text)
    print("=" * 60)

    if guardar:
        path = save_report(text, settings, now)
        print(f"Guardado en: {path}" if path else "Copia en disco desactivada (REPORTS_DIR vacío)")

    if enviar:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            print("Telegram sin configurar (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — no se envía nada.")
        else:
            await send_message(settings, text)
            print("Enviado por Telegram.")

    proximo = next_report_run(settings, now)
    local = proximo.astimezone(settings.report_tzinfo)
    print(f"Próximo informe automático: {local:%Y-%m-%d %H:%M %Z}  ({proximo.isoformat()})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Informe diario del sistema (sección 17).")
    parser.add_argument("--enviar", action="store_true", help="además de imprimir, notifica por Telegram")
    parser.add_argument("--no-guardar", action="store_true", help="no escribe la copia en REPORTS_DIR")
    args = parser.parse_args()
    asyncio.run(informe(enviar=args.enviar, guardar=not args.no_guardar))


if __name__ == "__main__":
    main()
