"""
Хелперы групповых хендлеров Telethon.
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
    if sender is None or not isinstance(sender, User):
        return None
    if getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return None
    if not getattr(sender, "id", None):
        return None
    return sender


def display_name(user: User) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    if user.username:
        return user.username
    return f"id{user.id}"


async def ensure_user_from_sender(db: Database, sender: User) -> bool:
    return await db.upsert_user(
        telegram_id=int(sender.id),
        username=sender.username,
        first_name=sender.first_name,
        last_name=sender.last_name,
    )


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
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
