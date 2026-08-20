"""
Обработка входящих личных сообщений от имени user-аккаунта.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import events

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import (
    MAX_USER_CHARS,
    ensure_user_from_sender,
    safe_reply,
    split_message,
)
from bot.persona import RESET_TEXT, get_start_text
from bot.services.gate import UserGate
from bot.services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)


def register_handlers(
    client: Any,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: UserGate,
) -> None:
    """Подписать обработчики на Telethon-клиент."""

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_private_message(event: events.NewMessage.Event) -> None:
        """Только входящие ЛС (не группы, не свои исходящие)."""
        if not event.message or not event.message.message:
            return

        text = event.message.message.strip()
        if not text:
            return

        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False):
            return

        user_id = int(sender.id)

        # Команды
        lowered = text.lower()
        if lowered.startswith("/start"):
            await _cmd_start(event, db, sender)
            return
        if lowered.startswith("/reset"):
            await _cmd_reset(event, db, user_id)
            return
        if text.startswith("/"):
            # Чужие слэш-команды игнорим молча
            return

        await _handle_chat(
            event,
            client=client,
            db=db,
            llm=llm,
            settings=settings,
            gate=gate,
            sender=sender,
            user_text=text,
        )


async def _cmd_start(event: Any, db: Database, sender: Any) -> None:
    try:
        await ensure_user_from_sender(db, sender)
        await safe_reply(event, get_start_text())
    except Exception:
        logger.exception("Ошибка /start для user_id=%s", sender.id)
        await safe_reply(
            event,
            "Что-то пошло не так на моей стороне. "
            "Попробуй /start ещё раз через секунду.",
        )


async def _cmd_reset(event: Any, db: Database, user_id: int) -> None:
    try:
        await db.clear_history(user_id)
        await safe_reply(event, RESET_TEXT)
    except Exception:
        logger.exception("Ошибка /reset для user_id=%s", user_id)
        await safe_reply(event, "Не вышло сбросить историю. Попробуй ещё раз.")


async def _handle_chat(
    event: Any,
    *,
    client: Any,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: UserGate,
    sender: Any,
    user_text: str,
) -> None:
    user_id = int(sender.id)
    if len(user_text) > MAX_USER_CHARS:
        user_text = user_text[:MAX_USER_CHARS]

    if not gate.try_begin(user_id):
        await safe_reply(
            event,
            "Я ещё ковыряюсь с прошлым сообщением — секунду, не долби.",
        )
        return

    try:
        wait = gate.seconds_until_allowed(user_id)
        if wait > 0:
            await safe_reply(
                event,
                f"Эй, притормози на {wait:.1f} сек — я не автомат по спаму.",
            )
            return

        gate.mark_used(user_id)
        await ensure_user_from_sender(db, sender)
        history = await db.get_history(user_id, limit=settings.history_limit)

        # «Печатает…» пока ждём LLM
        async with client.action(event.chat_id, "typing"):
            reply = await llm.generate_reply(
                history=history,
                user_message=user_text,
            )

        await db.add_exchange(
            user_id,
            user_text,
            reply,
            keep=settings.history_limit,
        )

        for chunk in split_message(reply):
            if not await safe_reply(event, chunk):
                break

    except LLMError:
        logger.warning("Сбой генерации для user_id=%s", user_id)
        await safe_reply(
            event,
            "Инет у меня моргнул / сервис подвис. Кинь ещё раз через минуту.",
        )
    except Exception:
        logger.exception("Ошибка обработки сообщения user_id=%s", user_id)
        await safe_reply(
            event,
            "Что-то у меня в голове щёлкнуло не так. "
            "Попробуй ещё раз или /reset.",
        )
    finally:
        gate.end(user_id)
