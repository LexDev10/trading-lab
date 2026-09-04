"""Unit tests (sin DB) de las piezas nuevas del informe diario:

- `notifications.telegram.split_message`: la Bot API corta en 4096
  caracteres; el informe enriquecido puede pasarse y antes se perdía
  entero (el envío es fail-open y solo logueaba el 400).
- `Settings.report_tzinfo` / `daily_summary.next_report_run`: la hora de
  ENVÍO es local (`REPORT_TIMEZONE`), la VENTANA agregada sigue siendo el
  día UTC (bug #17, CLAUDE.md).
- `daily_summary.save_report`: copia en disco fail-open.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from notifications.telegram import CHUNK_LIMIT, TELEGRAM_MAX_CHARS, split_message
from services.reporting.daily_summary import next_report_run, save_report


def _settings(**overrides) -> Settings:
    # `_env_file=None` para no arrastrar el .env real de la máquina.
    return Settings(_env_file=None, **overrides)


def test_split_message_deja_intacto_un_texto_corto():
    text = "linea 1\nlinea 2"
    assert split_message(text) == [text]


def test_split_message_trocea_por_lineas_sin_pasarse_del_limite():
    line = "x" * 200
    text = "\n".join([line] * 60)  # 12_059 caracteres, ~3.2x el limite

    chunks = split_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_LIMIT for chunk in chunks)
    assert all(len(chunk) <= TELEGRAM_MAX_CHARS for chunk in chunks)
    # Ninguna linea se parte a la mitad: reconstruyendo se recupera el original.
    assert "\n".join(chunks) == text


def test_split_message_corta_en_duro_una_linea_gigante():
    text = "y" * (CHUNK_LIMIT * 2 + 10)

    chunks = split_message(text)

    assert all(len(chunk) <= CHUNK_LIMIT for chunk in chunks)
    assert "".join(chunks) == text


def test_report_tzinfo_cae_a_utc_si_la_zona_no_existe():
    assert _settings(report_timezone="Europe/Madrid").report_tzinfo == ZoneInfo("Europe/Madrid")
    assert _settings(report_timezone="Marte/Olympus").report_tzinfo == ZoneInfo("UTC")


def test_next_report_run_es_hoy_si_la_hora_local_no_ha_pasado():
    settings = _settings(report_timezone="Europe/Madrid", daily_report_hour=22, daily_report_minute=0)
    # 12:00 UTC = 14:00 en Madrid (verano) -> las 22:00 locales son hoy.
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    proximo = next_report_run(settings, now)

    assert proximo.astimezone(settings.report_tzinfo).hour == 22
    assert proximo.date() == now.date()
    assert proximo > now


def test_next_report_run_salta_a_manana_si_la_hora_local_ya_paso():
    settings = _settings(report_timezone="Europe/Madrid", daily_report_hour=22, daily_report_minute=0)
    # 23:00 UTC = 01:00 del dia siguiente en Madrid: las 22:00 locales ya pasaron.
    now = datetime(2026, 7, 15, 23, 0, tzinfo=UTC)

    proximo = next_report_run(settings, now)
    local = proximo.astimezone(settings.report_tzinfo)

    assert local.hour == 22
    assert local.date() == datetime(2026, 7, 16).date()


def test_save_report_escribe_un_fichero_por_dia(tmp_path):
    settings = _settings(reports_dir=str(tmp_path / "reports"))
    now = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)

    path = save_report("contenido del informe", settings, now)

    assert path is not None
    assert path.name == "informe-2026-09-04.md"
    assert path.read_text(encoding="utf-8") == "contenido del informe"


def test_save_report_desactivado_con_reports_dir_vacio(tmp_path):
    settings = _settings(reports_dir="")

    assert save_report("texto", settings, datetime(2026, 9, 4, tzinfo=UTC)) is None
