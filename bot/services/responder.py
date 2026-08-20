"""
Общая отправка ответа в группу: gate + LLM + человеческий typing.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import split_message
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.typing import generate_with_human_typing, human_pause_typing

logger = logging.getLogger(__name__)


async def generate_and_send(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    chat_id: int,
    my_id: int,
    history_extra: list[dict[str, Any]] | None = None,
    reply_to: int | None = None,
) -> bool:
    """
    Сгенерировать ответ по памяти чата (+ optional extra) и отправить.
    Возвращает True, если хотя бы один кусок ушёл в чат.
    """
    if not gate.try_begin(chat_id):
        return False

    sent_any = False
    try:
        if gate.seconds_until_allowed(chat_id) > 0:
            return False

        gate.mark_used(chat_id)

        history = await db.get_chat_history_for_llm(
            chat_id, limit=settings.history_limit
        )
        if history_extra:
            history = [*history, *history_extra]

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

        for i, chunk in enumerate(split_message(reply)):
            if i > 0:
                await human_pause_typing(client, chat_id, chunk)
            if await _safe_send(client, chat_id, chunk, reply_to=reply_to if i == 0 else None):
                sent_any = True
            else:
                break

        return sent_any

    except LLMError:
        logger.warning("Сбой генерации chat_id=%s", chat_id)
        await _safe_send(
            client,
            chat_id,
            "Блин, мысль оборвалась / инет моргнул. Напиши ещё раз чуть позже.",
            reply_to=reply_to,
        )
        return False
    except Exception:
        logger.exception("Ошибка ответа в группе chat_id=%s", chat_id)
        await _safe_send(
            client,
            chat_id,
            "Что-то у меня в голове щёлкнуло не так. Кинь /reset или повтори.",
            reply_to=reply_to,
        )
        return False
    finally:
        gate.end(chat_id)


async def send_prepared(
    client: TelegramClient,
    *,
    db: Database,
    settings: Settings,
    gate: ChatGate,
    chat_id: int,
    my_id: int,
    text: str,
    reply_to: int | None = None,
) -> bool:
    """Отправить уже готовый текст с typing (для проактивных постов)."""
    if not text.strip():
        return False
    if not gate.try_begin(chat_id):
        return False

    sent_any = False
    try:
        if gate.seconds_until_allowed(chat_id) > 0:
            return False
        gate.mark_used(chat_id)

        async def _produce() -> str:
            return text.strip()

        reply = await generate_with_human_typing(client, chat_id, _produce)

        await db.add_chat_message(
            chat_id,
            reply,
            sender_id=my_id,
            sender_name=None,
            is_me=True,
            keep=settings.history_limit,
        )

        for i, chunk in enumerate(split_message(reply)):
            if i > 0:
                await human_pause_typing(client, chat_id, chunk)
            if await _safe_send(
                client, chat_id, chunk, reply_to=reply_to if i == 0 else None
            ):
                sent_any = True
            else:
                break
        return sent_any
    except Exception:
        logger.exception("Ошибка send_prepared chat_id=%s", chat_id)
        return False
    finally:
        gate.end(chat_id)


async def _safe_send(
    client: TelegramClient,
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
) -> bool:
    if not text:
        return False
    try:
        await client.send_message(chat_id, text, reply_to=reply_to)
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю отправку", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить в chat_id=%s", chat_id)
        return False
