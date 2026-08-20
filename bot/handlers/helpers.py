"""
Хелперы для Telethon-хендлеров.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import User

from bot.database import Database

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_USER_CHARS = 4000


def as_telegram_user(sender: Any) -> User | None:
    """Вернуть User или None, если отправитель не человек-аккаунт."""
    if sender is None or not isinstance(sender, User):
        return None
    if getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return None
    if not getattr(sender, "id", None):
        return None
    return sender


async def ensure_user_from_sender(db: Database, sender: User) -> bool:
    """Записать/обновить собеседника."""
    return await db.upsert_user(
        telegram_id=int(sender.id),
        username=sender.username,
        first_name=sender.first_name,
        last_name=sender.last_name,
    )


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ под лимит Telegram."""
    if limit < 1:
        raise ValueError("split_message: limit должен быть >= 1")
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
    if not text:
        return False
    try:
        await event.respond(text)
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю ответ", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить ответ в chat")
        return False
