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
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.presence import OwnerGuard
from bot.services.typing import generate_with_human_typing, human_pause_typing
from bot.textutil import split_message

logger = logging.getLogger(__name__)


async def generate_and_send(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    guard: OwnerGuard,
    chat_id: int,
    my_id: int,
    history_extra: list[dict[str, Any]] | None = None,
    persist_user_notes: list[dict[str, Any]] | None = None,
    reply_to: int | None = None,
) -> bool:
    """
    Сгенерировать ответ по памяти чата (+ optional extra) и отправить.

    Память пишется только после успешной отправки:
    сначала persist_user_notes (если есть), затем ответ бота.
    """
    blocked = guard.block_reason()
    if blocked:
        logger.debug("Пропуск ответа chat=%s: %s", chat_id, blocked)
        return False

    if not gate.try_begin(chat_id):
        return False

    sent_any = False
    delivered: list[str] = []
    try:
        blocked = guard.block_reason()
        if blocked:
            logger.debug("Пропуск ответа chat=%s: %s", chat_id, blocked)
            return False

        if gate.seconds_until_allowed(chat_id) > 0:
            return False

        history = await db.get_chat_history_for_llm(
            chat_id, limit=settings.history_limit
        )
        if history_extra:
            history = [*history, *history_extra]

        async def _produce() -> str:
            return await llm.generate_reply(history)

        reply = await generate_with_human_typing(client, chat_id, _produce)

        blocked = guard.block_reason()
        if blocked:
            logger.info("Ответ отменён после генерации chat=%s: %s", chat_id, blocked)
            return False

        for i, chunk in enumerate(split_message(reply)):
            if i > 0:
                await human_pause_typing(client, chat_id, chunk)
            if await _safe_send(
                client,
                chat_id,
                chunk,
                guard=guard,
                reply_to=reply_to if i == 0 else None,
            ):
                delivered.append(chunk)
                sent_any = True
            else:
                break

        if delivered:
            gate.mark_used(chat_id)
            try:
                if persist_user_notes:
                    for note in persist_user_notes:
                        content = (note.get("content") or "").strip()
                        if not content:
                            continue
                        await db.add_chat_message(
                            chat_id,
                            content,
                            sender_id=note.get("sender_id"),
                            sender_name=note.get("sender_name"),
                            is_me=False,
                            keep=settings.history_limit,
                        )
                await db.add_chat_message(
                    chat_id,
                    "\n".join(delivered),
                    sender_id=my_id,
                    sender_name=None,
                    is_me=True,
                    keep=settings.history_limit,
                )
            except Exception:
                logger.exception(
                    "Не удалось сохранить ответ в память chat_id=%s "
                    "(в Telegram уже ушло)",
                    chat_id,
                )

        return sent_any

    except LLMError:
        logger.warning("Сбой генерации chat_id=%s", chat_id)
        if guard.can_act():
            await _safe_send(
                client,
                chat_id,
                "Блин, мысль оборвалась / инет моргнул. Напиши ещё раз чуть позже.",
                guard=guard,
                reply_to=reply_to,
            )
        return False
    except Exception:
        logger.exception("Ошибка ответа в группе chat_id=%s", chat_id)
        if guard.can_act() and not sent_any:
            await _safe_send(
                client,
                chat_id,
                "Что-то у меня в голове щёлкнуло не так. Кинь /reset или повтори.",
                guard=guard,
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
    guard: OwnerGuard,
    chat_id: int,
    my_id: int,
    text: str,
    reply_to: int | None = None,
) -> bool:
    """Отправить уже готовый текст с typing (для проактивных постов)."""
    if not text.strip():
        return False
    blocked = guard.block_reason()
    if blocked:
        logger.debug("Пропуск proactive chat=%s: %s", chat_id, blocked)
        return False
    if not gate.try_begin(chat_id):
        return False

    sent_any = False
    delivered: list[str] = []
    try:
        blocked = guard.block_reason()
        if blocked:
            return False
        if gate.seconds_until_allowed(chat_id) > 0:
            return False

        async def _produce() -> str:
            return text.strip()

        reply = await generate_with_human_typing(client, chat_id, _produce)

        blocked = guard.block_reason()
        if blocked:
            logger.info("Proactive отменён chat=%s: %s", chat_id, blocked)
            return False

        for i, chunk in enumerate(split_message(reply)):
            if i > 0:
                await human_pause_typing(client, chat_id, chunk)
            if await _safe_send(
                client,
                chat_id,
                chunk,
                guard=guard,
                reply_to=reply_to if i == 0 else None,
            ):
                delivered.append(chunk)
                sent_any = True
            else:
                break

        if delivered:
            gate.mark_used(chat_id)
            try:
                await db.add_chat_message(
                    chat_id,
                    "\n".join(delivered),
                    sender_id=my_id,
                    sender_name=None,
                    is_me=True,
                    keep=settings.history_limit,
                )
            except Exception:
                logger.exception(
                    "Не удалось сохранить proactive в память chat_id=%s",
                    chat_id,
                )
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
    guard: OwnerGuard,
    reply_to: int | None = None,
) -> bool:
    if not text:
        return False
    try:
        with guard.bot_outbound():
            await client.send_message(chat_id, text, reply_to=reply_to)
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю отправку", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить в chat_id=%s", chat_id)
        return False
