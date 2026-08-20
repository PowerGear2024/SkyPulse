"""
Исходящие сообщения владельца + статус online/offline → пауза бота.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import (
    UpdateUserStatus,
    User,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import MAX_USER_CHARS
from bot.services.presence import OwnerGuard

logger = logging.getLogger(__name__)


def register_presence_handlers(
    client: TelegramClient,
    *,
    db: Database,
    settings: Settings,
    guard: OwnerGuard,
    me: User,
) -> None:
    my_id = int(me.id)

    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event: events.NewMessage.Event) -> None:
        # Сообщения, которые шлёт сам бот — не пауза
        if guard.is_bot_outbound():
            return

        guard.mark_owner_active(where=f"chat={event.chat_id}")

        # В группах — пишем ручные реплики владельца в память чата
        if not event.is_group:
            return
        chat_id = int(event.chat_id)
        if not settings.is_chat_allowed(chat_id):
            return
        raw = event.message.message if event.message else None
        if not raw:
            return
        text = raw.strip()
        if not text or text.startswith("/"):
            return
        if len(text) > MAX_USER_CHARS:
            text = text[:MAX_USER_CHARS]
        try:
            await db.add_chat_message(
                chat_id,
                text,
                sender_id=my_id,
                sender_name=None,
                is_me=True,
                keep=settings.history_limit,
            )
        except Exception:
            logger.exception(
                "Не удалось сохранить исходящее владельца chat_id=%s", chat_id
            )

    @client.on(events.Raw)
    async def on_raw_status(update: Any) -> None:
        if not isinstance(update, UpdateUserStatus):
            return
        if int(update.user_id) != my_id:
            return

        status = update.status
        if isinstance(status, (UserStatusOffline, UserStatusRecently)):
            guard.mark_owner_left(
                reason="offline" if isinstance(status, UserStatusOffline) else "recently"
            )
        elif isinstance(status, UserStatusOnline):
            # Только зашёл — ещё не пауза; пауза после первого ручного сообщения.
            logger.debug("Владелец online (пауза только после своего сообщения)")
