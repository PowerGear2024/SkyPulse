"""
Модуль конфигурации (user-сессия, группы, whitelist).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    session_name: str
    session_string: str
    session_dir: Path
    allowed_chat_ids: frozenset[int]
    allow_all_chats: bool
    allowed_user_ids: frozenset[int]
    group_reply_mode: str
    reply_on_reactions: bool
    proactive_enabled: bool
    proactive_max_per_day: int
    proactive_check_sec: int
    proactive_chance: float
    timezone: str
    work_hours_start: int
    work_hours_end: int
    owner_idle_resume_sec: float
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
        return self.session_dir / self.session_name

    def is_chat_allowed(self, chat_id: int) -> bool:
        if self.allow_all_chats or not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids

    def is_user_allowed(self, user_id: int) -> bool:
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


def _parse_bool(raw: str) -> bool:
    return raw.lower() in {"1", "true", "yes", "on"}


def _parse_chat_ids(raw: str) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise ValueError(
                f"ALLOWED_CHAT_IDS: ожидались целые id, кусок={part!r}"
            ) from exc
        if value == 0:
            raise ValueError("ALLOWED_CHAT_IDS: id не может быть 0")
        ids.add(value)
    return frozenset(ids)


def _parse_user_ids(raw: str) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise ValueError(
                f"ALLOWED_USER_IDS: ожидались целые id, кусок={part!r}"
            ) from exc
        if value <= 0:
            raise ValueError(
                f"ALLOWED_USER_IDS: user id должен быть > 0, получено {value}"
            )
        ids.add(value)
    return frozenset(ids)


def load_settings(*, require_llm: bool = True) -> Settings:
    """
    require_llm=False — для python -m bot.login (ключ LLM не обязателен).
    """
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
            "TELEGRAM_SESSION_NAME: только латиница/цифры/_/- , длина 1–64"
        )

    session_string = _optional("TELEGRAM_SESSION_STRING")
    allowed_chat_ids = _parse_chat_ids(_optional("ALLOWED_CHAT_IDS"))
    allow_all_chats = _parse_bool(_optional("ALLOW_ALL_CHATS", "false"))
    if not allowed_chat_ids and not allow_all_chats:
        raise ValueError(
            "Укажи ALLOWED_CHAT_IDS=-100... или явно ALLOW_ALL_CHATS=true "
            "(иначе user-сессия может отвечать во всех группах)."
        )
    if allow_all_chats and not allowed_chat_ids:
        logger.warning(
            "ALLOW_ALL_CHATS=true — бот активен во ВСЕХ группах аккаунта"
        )

    allowed_user_ids = _parse_user_ids(_optional("ALLOWED_USER_IDS"))

    group_reply_mode = _optional("GROUP_REPLY_MODE", "mention").lower()
    if group_reply_mode not in {"all", "mention"}:
        raise ValueError("GROUP_REPLY_MODE: 'all' или 'mention'")

    reply_on_reactions = _parse_bool(_optional("REPLY_ON_REACTIONS", "true"))
    proactive_enabled = _parse_bool(_optional("PROACTIVE_ENABLED", "true"))

    try:
        proactive_max_per_day = int(_optional("PROACTIVE_MAX_PER_DAY", "5"))
    except ValueError as exc:
        raise ValueError("PROACTIVE_MAX_PER_DAY должна быть целым числом") from exc
    if proactive_max_per_day < 0 or proactive_max_per_day > 50:
        raise ValueError("PROACTIVE_MAX_PER_DAY: 0–50")

    try:
        proactive_check_sec = int(_optional("PROACTIVE_CHECK_SEC", "1800"))
    except ValueError as exc:
        raise ValueError("PROACTIVE_CHECK_SEC должна быть целым числом") from exc
    if proactive_check_sec < 60:
        raise ValueError("PROACTIVE_CHECK_SEC должна быть >= 60")

    try:
        proactive_chance = float(_optional("PROACTIVE_CHANCE", "0.35"))
    except ValueError as exc:
        raise ValueError("PROACTIVE_CHANCE должна быть числом") from exc
    if not 0.0 <= proactive_chance <= 1.0:
        raise ValueError("PROACTIVE_CHANCE: 0.0–1.0")

    timezone = _optional("TIMEZONE", "Europe/Kyiv")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Некорректный TIMEZONE: {timezone}") from exc

    try:
        work_hours_start = int(_optional("WORK_HOURS_START", "8"))
        work_hours_end = int(_optional("WORK_HOURS_END", "18"))
    except ValueError as exc:
        raise ValueError("WORK_HOURS_START/END должны быть целыми") from exc
    if not 0 <= work_hours_start <= 23:
        raise ValueError("WORK_HOURS_START: 0–23")
    if not 1 <= work_hours_end <= 24:
        raise ValueError("WORK_HOURS_END: 1–24")
    if work_hours_start == work_hours_end:
        raise ValueError("WORK_HOURS_START и WORK_HOURS_END не должны совпадать")

    try:
        owner_idle_resume_sec = float(_optional("OWNER_IDLE_RESUME_SEC", "600"))
    except ValueError as exc:
        raise ValueError("OWNER_IDLE_RESUME_SEC должна быть числом") from exc
    if owner_idle_resume_sec < 30:
        raise ValueError("OWNER_IDLE_RESUME_SEC должна быть >= 30")

    llm_provider = _optional("LLM_PROVIDER", "openai").lower()
    if llm_provider not in {"openai", "anthropic"}:
        raise ValueError(
            f"LLM_PROVIDER должен быть 'openai' или 'anthropic', получено: {llm_provider!r}"
        )

    openai_api_key = _optional("OPENAI_API_KEY")
    anthropic_api_key = _optional("ANTHROPIC_API_KEY")
    if require_llm:
        if llm_provider == "openai" and not openai_api_key:
            raise ValueError("Для LLM_PROVIDER=openai нужен OPENAI_API_KEY")
        if llm_provider == "anthropic" and not anthropic_api_key:
            raise ValueError("Для LLM_PROVIDER=anthropic нужен ANTHROPIC_API_KEY")

    try:
        temperature = float(_optional("LLM_TEMPERATURE", "0.85"))
    except ValueError as exc:
        raise ValueError("LLM_TEMPERATURE должна быть числом") from exc
    if not 0.0 <= temperature <= 2.0:
        raise ValueError("LLM_TEMPERATURE должна быть в диапазоне 0.0–2.0")

    try:
        history_limit = int(_optional("HISTORY_LIMIT", "30"))
    except ValueError as exc:
        raise ValueError("HISTORY_LIMIT должна быть целым числом") from exc
    if history_limit < 4:
        raise ValueError("HISTORY_LIMIT должен быть >= 4 (память чата)")
    if history_limit > 80:
        logger.warning("HISTORY_LIMIT=%s большой — дорогой контекст", history_limit)

    try:
        llm_timeout_sec = float(_optional("LLM_TIMEOUT_SEC", "90"))
    except ValueError as exc:
        raise ValueError("LLM_TIMEOUT_SEC должна быть числом") from exc
    if llm_timeout_sec < 5:
        raise ValueError("LLM_TIMEOUT_SEC должна быть >= 5")

    session_dir = _PROJECT_ROOT / "data"
    db_raw = _optional("DATABASE_PATH", "data/bot.db")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = (_PROJECT_ROOT / db_path).resolve()
    else:
        db_path = db_path.resolve()
        try:
            db_path.relative_to(_PROJECT_ROOT.resolve())
        except ValueError:
            if not _parse_bool(_optional("ALLOW_UNSAFE_DB_PATH", "false")):
                raise ValueError(
                    "DATABASE_PATH вне проекта. Перенеси в data/ "
                    "или поставь ALLOW_UNSAFE_DB_PATH=true"
                )

    log_level = _optional("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Некорректный LOG_LEVEL: {log_level}")

    settings = Settings(
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        session_name=session_name,
        session_string=session_string,
        session_dir=session_dir,
        allowed_chat_ids=allowed_chat_ids,
        allow_all_chats=allow_all_chats,
        allowed_user_ids=allowed_user_ids,
        group_reply_mode=group_reply_mode,
        reply_on_reactions=reply_on_reactions,
        proactive_enabled=proactive_enabled,
        proactive_max_per_day=proactive_max_per_day,
        proactive_check_sec=proactive_check_sec,
        proactive_chance=proactive_chance,
        timezone=timezone,
        work_hours_start=work_hours_start,
        work_hours_end=work_hours_end,
        owner_idle_resume_sec=owner_idle_resume_sec,
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
        "Конфиг: groups-only, reply=%s, reactions=%s, proactive=%s/%s/day, "
        "hours=%02d-%02d %s, chats=%s, users=%s, history=%s",
        settings.group_reply_mode,
        settings.reply_on_reactions,
        settings.proactive_enabled,
        settings.proactive_max_per_day,
        settings.work_hours_start,
        settings.work_hours_end,
        settings.timezone,
        "ALL" if settings.allow_all_chats and not settings.allowed_chat_ids
        else len(settings.allowed_chat_ids),
        len(settings.allowed_user_ids) or "ALL",
        settings.history_limit,
    )
    return settings
