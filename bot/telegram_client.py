"""
Сборка Telethon-клиента и настройка логов.

Вынесено из main, чтобы login не импортировал точку входа (без циклов).
"""

from __future__ import annotations

import logging
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.config import Settings


def setup_logging(level: str) -> None:
    """Корневой логгер + приглушение шумных библиотек."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def build_client(settings: Settings) -> TelegramClient:
    """Собрать Telethon-клиент из StringSession или файла сессии."""
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    if settings.session_string:
        session: StringSession | str = StringSession(settings.session_string)
    else:
        session = str(settings.session_path)

    return TelegramClient(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        flood_sleep_threshold=60,
        connection_retries=5,
        retry_delay=2,
        auto_reconnect=True,
    )
