"""
Модуль конфигурации.

Загружает переменные из .env и валидирует параметры user-сессии Telegram.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Settings:
    """Неизменяемый снимок настроек userbot'а."""

    telegram_api_id: int
    telegram_api_hash: str
    session_name: str
    session_string: str
    session_dir: Path
    llm_provider: str
    openai_api_key: str
    anthropic_api_key: str
    openai_model: str
    anthropic_model: str
    llm_temperature: float
    history_limit: int
    database_path: Path
    log_level: str

    @property
    def session_path(self) -> Path:
        """Путь к файлу сессии Telethon (без суффикса .session — его добавит Telethon)."""
        return self.session_dir / self.session_name


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Не задана обязательная переменная: {name}. "
            f"Скопируй .env.example в .env и заполни значения."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip() or default


def load_settings() -> Settings:
    """Прочитать и провалидировать настройки из окружения."""
    try:
        api_id = int(_require("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID должен быть целым числом") from exc

    api_hash = _require("TELEGRAM_API_HASH")
    session_name = _optional("TELEGRAM_SESSION_NAME", "user")
    session_string = _optional("TELEGRAM_SESSION_STRING")

    llm_provider = _optional("LLM_PROVIDER", "openai").lower()
    if llm_provider not in {"openai", "anthropic"}:
        raise ValueError(
            f"LLM_PROVIDER должен быть 'openai' или 'anthropic', получено: {llm_provider!r}"
        )

    openai_api_key = _optional("OPENAI_API_KEY")
    anthropic_api_key = _optional("ANTHROPIC_API_KEY")
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
    if history_limit < 2:
        raise ValueError("HISTORY_LIMIT должен быть >= 2")
    if history_limit > 40:
        logger.warning(
            "HISTORY_LIMIT=%s довольно большой — контекст LLM раздуется",
            history_limit,
        )

    session_dir = _PROJECT_ROOT / "data"
    db_path = Path(_optional("DATABASE_PATH", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path

    log_level = _optional("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Некорректный LOG_LEVEL: {log_level}")

    settings = Settings(
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        session_name=session_name,
        session_string=session_string,
        session_dir=session_dir,
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
        "Конфиг: user-session=%s, provider=%s, model=%s, temp=%.2f, history=%s",
        "string" if settings.session_string else settings.session_name,
        settings.llm_provider,
        settings.openai_model
        if settings.llm_provider == "openai"
        else settings.anthropic_model,
        settings.llm_temperature,
        settings.history_limit,
    )
    return settings
