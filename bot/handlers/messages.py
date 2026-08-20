"""
Только ГРУППЫ / супергруппы. Личные сообщения — полный игнор.

- Пишет в память все тексты чата (кроме служебных команд).
- Отвечает по GROUP_REPLY_MODE (all | mention).
- Печатает как человек: typing в шапке + задержка от длины ответа.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import User

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import (
    MAX_USER_CHARS,
    as_telegram_user,
    display_name,
    ensure_user_from_sender,
    safe_reply,
    split_message,
)
from bot.persona import RESET_TEXT, get_start_text
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.typing import generate_with_human_typing, human_pause_typing

logger = logging.getLogger(__name__)


def register_handlers(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    me: User,
) -> None:
    """Подписать обработчики. `me` — наш аккаунт (для mention/reply)."""

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event: events.NewMessage.Event) -> None:
        # --- ЛС: полный игнор ---
        if event.is_private:
            return

        # Только группы / супергруппы (broadcast-каналы — нет)
        if not event.is_group:
            return

        raw = event.message.message if event.message else None
        if not raw:
            return

        text = raw.strip()
        if not text:
            return

        chat_id = int(event.chat_id)
        if not settings.is_chat_allowed(chat_id):
            return

        sender = as_telegram_user(await event.get_sender())
        if sender is None:
            return

        if int(sender.id) == int(me.id):
            return

        if len(text) > MAX_USER_CHARS:
            text = text[:MAX_USER_CHARS]

        await ensure_user_from_sender(db, sender)
        name = display_name(sender)
        lowered = text.lower()

        # Команды — НЕ засоряем память чата
        if lowered.startswith("/start"):
            if settings.is_user_allowed(int(sender.id)):
                await safe_reply(event, get_start_text())
            return
        if lowered.startswith("/reset"):
            if settings.is_user_allowed(int(sender.id)):
                await _cmd_reset(event, db, chat_id)
            return
        if text.startswith("/"):
            return

        # Память: все обычные тексты (даже если не отвечаем)
        try:
            await db.add_chat_message(
                chat_id,
                text,
                sender_id=int(sender.id),
                sender_name=name,
                is_me=False,
                keep=settings.history_limit,
            )
        except Exception:
            logger.exception("Не удалось сохранить сообщение chat_id=%s", chat_id)
            return

        if not settings.is_user_allowed(int(sender.id)):
            return

        if settings.group_reply_mode == "mention":
            if not await _is_addressed_to_me(event, me, text):
                return

        await _reply_in_group(
            event,
            client=client,
            db=db,
            llm=llm,
            settings=settings,
            gate=gate,
            chat_id=chat_id,
            my_id=int(me.id),
        )


async def _is_addressed_to_me(
    event: events.NewMessage.Event,
    me: User,
    text: str,
) -> bool:
    """Упомянули / ответили на моё сообщение."""
    if getattr(event.message, "mentioned", False):
        return True
    username = (me.username or "").lower()
    if username and f"@{username}" in text.lower():
        return True
    if event.is_reply:
        try:
            reply = await event.get_reply_message()
            if reply is not None and reply.sender_id == me.id:
                return True
        except Exception:
            logger.debug("Не удалось прочитать reply", exc_info=True)
    return False


async def _cmd_reset(event: Any, db: Database, chat_id: int) -> None:
    try:
        await db.clear_chat_history(chat_id)
        await safe_reply(event, RESET_TEXT)
    except Exception:
        logger.exception("Ошибка /reset chat_id=%s", chat_id)
        await safe_reply(event, "Не вышло сбросить память чата. Ещё раз?")


async def _reply_in_group(
    event: Any,
    *,
    client: TelegramClient,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    chat_id: int,
    my_id: int,
) -> None:
    if not gate.try_begin(chat_id):
        return

    try:
        if gate.seconds_until_allowed(chat_id) > 0:
            return

        gate.mark_used(chat_id)

        history = await db.get_chat_history_for_llm(
            chat_id, limit=settings.history_limit
        )

        async def _produce() -> str:
            return await llm.generate_reply(history)

        reply = await generate_with_human_typing(client, chat_id, _produce)

        await db.add_chat_message(
            chat_id,
            reply,
            sender_id=my_id,
            sender_name=None,
            is_me=True,
            keep=settings.history_limit,
        )

        chunks = split_message(reply)
        for i, chunk in enumerate(chunks):
            if i > 0:
                await human_pause_typing(client, chat_id, chunk)
            if not await safe_reply(event, chunk):
                break

    except LLMError:
        logger.warning("Сбой генерации chat_id=%s", chat_id)
        await safe_reply(
            event,
            "Блин, мысль оборвалась / инет моргнул. Напиши ещё раз чуть позже.",
        )
    except Exception:
        logger.exception("Ошибка ответа в группе chat_id=%s", chat_id)
        await safe_reply(
            event,
            "Что-то у меня в голове щёлкнуло не так. Кинь /reset или повтори.",
        )
    finally:
        gate.end(chat_id)
