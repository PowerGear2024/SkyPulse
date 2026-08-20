"""
Модуль конфигурации.

Загружает переменные из .env через python-dotenv и валидирует
обязательные параметры при старте приложения.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (рядом с requirements.txt)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Settings:
    """Неизменяемый снимок настроек бота."""

    bot_token: str
    llm_provider: str
    openai_api_key: str
    anthropic_api_key: str
    openai_model: str
    anthropic_model: str
    llm_temperature: float
    history_limit: int
    database_path: Path
    log_level: str


def _require(name: str) -> str:
    """Вернуть обязательную переменную окружения или упасть с понятной ошибкой."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Не задана обязательная переменная окружения: {name}. "
            f"Скопируй .env.example в .env и заполни значения."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    """Вернуть опциональную переменную или значение по умолчанию."""
    return os.getenv(name, default).strip() or default


def load_settings() -> Settings:
    """
    Прочитать и провалидировать настройки из окружения.

    Raises:
        ValueError: если токен/провайдер/ключ API некорректны.
    """
    bot_token = _require("BOT_TOKEN")
    llm_provider = _optional("LLM_PROVIDER", "openai").lower()

    if llm_provider not in {"openai", "anthropic"}:
        raise ValueError(
            f"LLM_PROVIDER должен быть 'openai' или 'anthropic', получено: {llm_provider!r}"
        )

    openai_api_key = _optional("OPENAI_API_KEY")
    anthropic_api_key = _optional("ANTHROPIC_API_KEY")

    # Проверяем, что ключ выбранного провайдера действительно задан
    if llm_provider == "openai" and not openai_api_key:
        raise ValueError("Для LLM_PROVIDER=openai нужен OPENAI_API_KEY в .env")
    if llm_provider == "anthropic" and not anthropic_api_key:
        raise ValueError("Для LLM_PROVIDER=anthropic нужен ANTHROPIC_API_KEY в .env")

    try:
        temperature = float(_optional("LLM_TEMPERATURE", "0.85"))
    except ValueError as exc:
        raise ValueError("LLM_TEMPERATURE должна быть числом, например 0.85") from exc

    if not 0.0 <= temperature <= 2.0:
        raise ValueError("LLM_TEMPERATURE должна быть в диапазоне 0.0–2.0")

    try:
        history_limit = int(_optional("HISTORY_LIMIT", "14"))
    except ValueError as exc:
        raise ValueError("HISTORY_LIMIT должна быть целым числом") from exc

    # Рекомендуемый коридор — 10–15 реплик; жёстко не режем, но предупреждаем
    if history_limit < 2:
        raise ValueError("HISTORY_LIMIT должен быть >= 2")
    if history_limit > 40:
        logger.warning(
            "HISTORY_LIMIT=%s довольно большой — контекст LLM раздуется и подорожает",
            history_limit,
        )

    db_path = Path(_optional("DATABASE_PATH", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path

    log_level = _optional("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Некорректный LOG_LEVEL: {log_level}")

    settings = Settings(
        bot_token=bot_token,
        llm_provider=llm_provider,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        openai_model=_optional("OPENAI_MODEL", "gpt-4o"),
        anthropic_model=_optional("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        llm_temperature=temperature,
        history_limit=history_limit,
        database_path=db_path,
        log_level=log_level,
    )

    logger.info(
        "Конфиг загружен: provider=%s, model=%s, temperature=%.2f, history=%s",
        settings.llm_provider,
        settings.openai_model if settings.llm_provider == "openai" else settings.anthropic_model,
        settings.llm_temperature,
        settings.history_limit,
    )
    return settings
