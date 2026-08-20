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

MAX_USER_CHARS = 4000

__all__ = [
    "MAX_USER_CHARS",
    "as_telegram_user",
    "memory_sender_info",
    "display_name",
    "ensure_user_from_sender",
    "safe_reply",
]


def as_telegram_user(sender: Any) -> User | None:
    """Живой человек (не бот) — можно триггерить ответ."""
    if sender is None or not isinstance(sender, User):
        return None
    if getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return None
    if not getattr(sender, "id", None):
        return None
    return sender


def memory_sender_info(sender: Any) -> tuple[int, str] | None:
    """
    Любой User (включая ботов) для записи в память чата.
    Возвращает (sender_id, display_name) или None.
    """
    if sender is None or not isinstance(sender, User):
        return None
    if getattr(sender, "deleted", False):
        return None
    if not getattr(sender, "id", None):
        return None
    parts = [sender.first_name or "", sender.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    if not name:
        name = sender.username or f"id{sender.id}"
    if getattr(sender, "bot", False) and not name.endswith("bot"):
        name = f"{name} (bot)"
    return int(sender.id), name


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


async def safe_reply(event: Any, text: str, *, guard: Any | None = None) -> bool:
    if not text:
        return False
    try:
        if guard is not None:
            with guard.bot_outbound():
                msg = await event.respond(text)
            chat_id = getattr(event, "chat_id", None)
            msg_id = getattr(msg, "id", None)
            if chat_id is not None and msg_id is not None:
                guard.note_bot_message(int(chat_id), int(msg_id))
        else:
            await event.respond(text)
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю ответ", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить ответ в chat")
        return False
