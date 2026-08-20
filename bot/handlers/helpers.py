"""
Хелперы для Telethon-хендлеров.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon.errors import FloodWaitError, RPCError

from bot.database import Database

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_USER_CHARS = 4000


async def ensure_user_from_sender(db: Database, sender: Any) -> bool:
    """Записать/обновить собеседника по объекту Telethon User."""
    return await db.upsert_user(
        telegram_id=int(sender.id),
        username=getattr(sender, "username", None),
        first_name=getattr(sender, "first_name", None),
        last_name=getattr(sender, "last_name", None),
    )


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ под лимит Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


async def safe_reply(event: Any, text: str) -> bool:
    """Ответить в тот же чат, глотая типовые RPC-ошибки."""
    try:
        await event.respond(text)
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю ответ", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить ответ в chat")
        return False
