"""
Модуль конфигурации.

Загружает переменные из .env и валидирует параметры user-сессии Telegram.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# Имя сессии: только безопасные символы (защита от path traversal)
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Settings:
    """Неизменяемый снимок настроек userbot'а."""

    telegram_api_id: int
    telegram_api_hash: str
    session_name: str
    session_string: str
    session_dir: Path
    allowed_user_ids: frozenset[int]
    llm_provider: str
    openai_api_key: str
    anthropic_api_key: str
    openai_model: str
    anthropic_model: str
    llm_temperature: float
    history_limit: int
    llm_timeout_sec: float
    database_path: Path
    log_level: str

    @property
    def session_path(self) -> Path:
        """Путь к файлу сессии Telethon (без суффикса .session)."""
        return self.session_dir / self.session_name

    def is_user_allowed(self, user_id: int) -> bool:
        """True, если whitelist пуст (все) или user_id в списке."""
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids


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


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    """
    ALLOWED_USER_IDS=123,456 или пусто (= все).

    Пустой whitelist = отвечать всем (удобно, но жрёт бюджет).
    """
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            uid = int(part)
        except ValueError as exc:
            raise ValueError(
                f"ALLOWED_USER_IDS: ожидались целые id через запятую, кусок={part!r}"
            ) from exc
        if uid <= 0:
            raise ValueError(f"ALLOWED_USER_IDS: id должен быть > 0, получено {uid}")
        ids.add(uid)
    return frozenset(ids)


def load_settings() -> Settings:
    """Прочитать и провалидировать настройки из окружения."""
    try:
        api_id = int(_require("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID должен быть целым числом") from exc
    if api_id <= 0:
        raise ValueError("TELEGRAM_API_ID должен быть положительным")

    api_hash = _require("TELEGRAM_API_HASH")
    if len(api_hash) < 16:
        raise ValueError("TELEGRAM_API_HASH выглядит слишком коротким")

    session_name = _optional("TELEGRAM_SESSION_NAME", "user")
    if not _SESSION_NAME_RE.fullmatch(session_name):
        raise ValueError(
            "TELEGRAM_SESSION_NAME: только латиница/цифры/_/- , длина 1–64 "
            "(без путей вроде ../)"
        )

    session_string = _optional("TELEGRAM_SESSION_STRING")
    allowed_user_ids = _parse_allowed_ids(_optional("ALLOWED_USER_IDS"))

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

    try:
        llm_timeout_sec = float(_optional("LLM_TIMEOUT_SEC", "90"))
    except ValueError as exc:
        raise ValueError("LLM_TIMEOUT_SEC должна быть числом") from exc
    if llm_timeout_sec < 5:
        raise ValueError("LLM_TIMEOUT_SEC должна быть >= 5")

    session_dir = _PROJECT_ROOT / "data"
    db_path = Path(_optional("DATABASE_PATH", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = (_PROJECT_ROOT / db_path).resolve()
    else:
        db_path = db_path.resolve()

    log_level = _optional("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Некорректный LOG_LEVEL: {log_level}")

    settings = Settings(
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        session_name=session_name,
        session_string=session_string,
        session_dir=session_dir,
        allowed_user_ids=allowed_user_ids,
        llm_provider=llm_provider,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        openai_model=_optional("OPENAI_MODEL", "gpt-4o"),
        anthropic_model=_optional("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        llm_temperature=temperature,
        history_limit=history_limit,
        llm_timeout_sec=llm_timeout_sec,
        database_path=db_path,
        log_level=log_level,
    )

    logger.info(
        "Конфиг: session=%s, allowlist=%s, provider=%s, model=%s, temp=%.2f, history=%s",
        "string" if settings.session_string else settings.session_name,
        len(settings.allowed_user_ids) or "ALL",
        settings.llm_provider,
        settings.openai_model
        if settings.llm_provider == "openai"
        else settings.anthropic_model,
        settings.llm_temperature,
        settings.history_limit,
    )
    if not settings.allowed_user_ids:
        logger.warning(
            "ALLOWED_USER_IDS пуст — отвечаю ВСЕМ в ЛС. "
            "Для защиты бюджета укажи id через запятую."
        )
    return settings
