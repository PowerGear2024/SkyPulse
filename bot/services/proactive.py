"""
Проактив: иногда взять 10 смс одного юзера, по теме написать в чат.
Не больше PROACTIVE_MAX_PER_DAY раз в сутки.
"""

from __future__ import annotations

import asyncio
import logging
import random

from telethon import TelegramClient
from telethon.tl.types import User

from bot.config import Settings
from bot.database import Database
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.responder import send_prepared

logger = logging.getLogger(__name__)

_ANALYZE_COUNT = 10


async def proactive_loop(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    me: User,
    stop_event: asyncio.Event,
) -> None:
    """Фоновый цикл. Крутится, пока не set(stop_event)."""
    if not settings.proactive_enabled or settings.proactive_max_per_day <= 0:
        logger.info("Проактив выключен")
        return

    my_id = int(me.id)
    logger.info(
        "Проактив: до %s/день, проверка ~каждые %ss, шанс %.0f%%",
        settings.proactive_max_per_day,
        settings.proactive_check_sec,
        settings.proactive_chance * 100,
    )

    # Первая пауза — не стрелять сразу после старта
    first_delay = random.uniform(120.0, min(600.0, float(settings.proactive_check_sec)))
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=first_delay)
        return
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        try:
            await _maybe_proactive_once(
                client,
                db=db,
                llm=llm,
                settings=settings,
                gate=gate,
                my_id=my_id,
            )
        except Exception:
            logger.exception("Ошибка проактивного цикла")

        # Джиттер, чтобы не было ровной сетки
        delay = settings.proactive_check_sec * random.uniform(0.7, 1.35)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            continue


async def _maybe_proactive_once(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    my_id: int,
) -> None:
    used = await db.count_proactive_today()
    if used >= settings.proactive_max_per_day:
        logger.debug("Проактив: лимит дня исчерпан (%s)", used)
        return

    if random.random() > settings.proactive_chance:
        logger.debug("Проактив: пропуск по шансу")
        return

    chat_ids = await _candidate_chats(db, settings)
    if not chat_ids:
        logger.debug("Проактив: нет чатов-кандидатов")
        return

    random.shuffle(chat_ids)
    for chat_id in chat_ids:
        users = await db.list_users_with_min_messages(
            chat_id,
            min_count=_ANALYZE_COUNT,
            exclude_sender_id=my_id,
        )
        # Только разрешённые юзеры (если whitelist задан)
        users = [
            u
            for u in users
            if settings.is_user_allowed(int(u["sender_id"]))
        ]
        if not users:
            continue

        pick = random.choice(users)
        sender_id = int(pick["sender_id"])
        name = str(pick["sender_name"])
        texts = await db.get_user_recent_texts(
            chat_id, sender_id, limit=_ANALYZE_COUNT
        )
        if len(texts) < _ANALYZE_COUNT:
            continue

        try:
            reply = await llm.generate_proactive(
                user_name=name,
                messages=texts,
            )
        except LLMError:
            logger.warning("Проактив: LLM не дал текст chat=%s", chat_id)
            return

        ok = await send_prepared(
            client,
            db=db,
            settings=settings,
            gate=gate,
            chat_id=chat_id,
            my_id=my_id,
            text=reply,
        )
        if not ok:
            logger.warning("Проактив: не отправилось chat=%s", chat_id)
            return

        await db.record_proactive(chat_id, sender_id)
        logger.info(
            "Проактив: пост про %s в chat=%s (%s/%s сегодня)",
            name,
            chat_id,
            used + 1,
            settings.proactive_max_per_day,
        )
        return


async def _candidate_chats(db: Database, settings: Settings) -> list[int]:
    active = await db.list_active_chat_ids()
    if settings.allowed_chat_ids:
        allowed = set(settings.allowed_chat_ids)
        return [c for c in active if c in allowed]
    return list(active)
