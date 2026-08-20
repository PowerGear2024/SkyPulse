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
from bot.services.emotions import format_emotional_block, reflect_scheduler
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.presence import OwnerGuard
from bot.services.typing import generate_with_human_typing, human_pause_typing
from bot.textutil import split_message

logger = logging.getLogger(__name__)


async def _emotional_block_for_chat(db: Database, chat_id: int) -> str:
    try:
        pulse = await db.get_persona_pulse(chat_id)
    except Exception:
        logger.exception("Не удалось загрузить пульс chat_id=%s", chat_id)
        return ""
    return format_emotional_block(
        mood=pulse.get("mood"),
        vibe=pulse.get("vibe"),
        feelings=list(pulse.get("feelings") or []),
    )


def _schedule_reflect(
    llm: LLMService,
    db: Database,
    chat_id: int,
    history: list[dict[str, Any]],
    reply: str,
) -> None:
    epoch = db.pulse_epoch(chat_id)

    async def _run() -> None:
        data = await llm.reflect_emotions(
            history_tail=history, my_reply=reply
        )
        if not data:
            return
        await db.save_persona_pulse(
            chat_id,
            mood=str(data.get("mood") or "дерзкий"),
            vibe=str(data.get("vibe") or ""),
            feelings=list(data.get("feelings") or []),
            expected_epoch=epoch,
        )

    reflect_scheduler.schedule(chat_id, epoch=epoch, runner=_run)


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
    notes + reply — одной транзакцией.
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

        emotional = await _emotional_block_for_chat(db, chat_id)

        async def _produce() -> str:
            return await llm.generate_reply(
                history, emotional_block=emotional
            )

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
            full_reply = "\n".join(delivered)
            batch: list[dict[str, Any]] = []
            if persist_user_notes:
                for note in persist_user_notes:
                    content = (note.get("content") or "").strip()
                    if not content:
                        continue
                    batch.append(
                        {
                            "content": content,
                            "sender_id": note.get("sender_id"),
                            "sender_name": note.get("sender_name"),
                            "is_me": False,
                        }
                    )
            batch.append(
                {
                    "content": full_reply,
                    "sender_id": my_id,
                    "sender_name": None,
                    "is_me": True,
                }
            )
            try:
                await db.add_chat_messages_batch(
                    chat_id, batch, keep=settings.history_limit
                )
            except Exception:
                logger.exception(
                    "Не удалось сохранить ответ в память chat_id=%s "
                    "(в Telegram уже ушло)",
                    chat_id,
                )
            _schedule_reflect(llm, db, chat_id, history, full_reply)

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
    llm: LLMService | None = None,
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
            full_reply = "\n".join(delivered)
            try:
                await db.add_chat_message(
                    chat_id,
                    full_reply,
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
            if llm is not None:
                history = await db.get_chat_history_for_llm(
                    chat_id, limit=min(12, settings.history_limit)
                )
                _schedule_reflect(llm, db, chat_id, history, full_reply)
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
            msg = await client.send_message(chat_id, text, reply_to=reply_to)
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            guard.note_bot_message(chat_id, int(msg_id))
        return True
    except FloodWaitError as exc:
        logger.warning("FloodWait %ss — пропускаю отправку", exc.seconds)
        return False
    except RPCError:
        logger.exception("Не удалось отправить в chat_id=%s", chat_id)
        return False
