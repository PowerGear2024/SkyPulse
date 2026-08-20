"""
Проактив: иногда взять 10 смс одного юзера, по теме написать в чат.
Не больше PROACTIVE_MAX_PER_DAY раз в локальные сутки (TIMEZONE).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.tl.types import User

from bot.config import Settings
from bot.database import Database
from bot.services.emotions import format_emotional_block
from bot.services.gate import ChatGate
from bot.services.llm import LLMError, LLMService
from bot.services.presence import OwnerGuard
from bot.services.responder import send_prepared

logger = logging.getLogger(__name__)

_ANALYZE_COUNT = 10


def local_day_start_utc(timezone_name: str) -> str:
    """Начало текущих локальных суток → UTC-строка для сравнения с datetime('now')."""
    tz = ZoneInfo(timezone_name)
    local_now = datetime.now(tz)
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)
    return start_utc.strftime("%Y-%m-%d %H:%M:%S")


async def proactive_loop(
    client: TelegramClient,
    *,
    db: Database,
    llm: LLMService,
    settings: Settings,
    gate: ChatGate,
    guard: OwnerGuard,
    me: User,
    stop_event: asyncio.Event,
) -> None:
    if not settings.proactive_enabled or settings.proactive_max_per_day <= 0:
        logger.info("Проактив выключен")
        return

    my_id = int(me.id)
    logger.info(
        "Проактив: до %s/день (%s), проверка ~каждые %ss, шанс %.0f%%",
        settings.proactive_max_per_day,
        settings.timezone,
        settings.proactive_check_sec,
        settings.proactive_chance * 100,
    )

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
                guard=guard,
                my_id=my_id,
            )
        except Exception:
            logger.exception("Ошибка проактивного цикла")

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
    guard: OwnerGuard,
    my_id: int,
) -> None:
    blocked = guard.block_reason()
    if blocked:
        logger.debug("Проактив: пауза (%s)", blocked)
        return

    since_utc = local_day_start_utc(settings.timezone)
    used = await db.count_proactive_since(since_utc)
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
        users = [
            u for u in users if settings.is_user_allowed(int(u["sender_id"]))
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

        reservation_id = await db.try_reserve_proactive(
            chat_id,
            sender_id,
            max_per_day=settings.proactive_max_per_day,
            since_utc=since_utc,
        )
        if reservation_id is None:
            logger.debug("Проактив: слот не зарезервирован (лимит)")
            return

        try:
            pulse = await db.get_persona_pulse(chat_id)
            emotional = format_emotional_block(
                mood=pulse.get("mood"),
                vibe=pulse.get("vibe"),
                feelings=list(pulse.get("feelings") or []),
            )
            reply = await llm.generate_proactive(
                user_name=name,
                messages=texts,
                emotional_block=emotional,
            )
        except LLMError:
            logger.warning("Проактив: LLM не дал текст chat=%s", chat_id)
            await db.release_proactive(reservation_id)
            return

        ok = await send_prepared(
            client,
            db=db,
            settings=settings,
            gate=gate,
            guard=guard,
            chat_id=chat_id,
            my_id=my_id,
            text=reply,
        )
        if not ok:
            logger.warning("Проактив: не отправилось chat=%s", chat_id)
            await db.release_proactive(reservation_id)
            return

        logger.info(
            "Проактив: пост про %s в chat=%s (резерв id=%s)",
            name,
            chat_id,
            reservation_id,
        )
        return


async def _candidate_chats(db: Database, settings: Settings) -> list[int]:
    active = await db.list_active_chat_ids()
    return [c for c in active if settings.is_chat_allowed(c)]
