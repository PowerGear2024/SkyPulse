"""
Человеческий набор текста: задержка от длины + индикатор «печатает…».

Ошибки LLM/производства ответа НЕ глотаем и НЕ ретраим — только typing best-effort.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from telethon import TelegramClient

logger = logging.getLogger(__name__)

ProduceReply = Callable[[], Awaitable[str]]


def human_typing_delay_sec(text: str) -> float:
    """Пауза набора: ~4–7 зн/сек + «подумать», clamp 1.8–28с."""
    chars = max(len(text.strip()), 1)
    chars_per_sec = random.uniform(4.0, 6.8)
    think = random.uniform(0.6, 1.8)
    delay = think + chars / chars_per_sec
    return max(1.8, min(delay, 28.0))


async def generate_with_human_typing(
    client: TelegramClient,
    chat_id: int,
    produce: ProduceReply,
) -> str:
    """
    1) Включаем typing в шапке (если Telegram даёт).
    2) Генерим ответ (ошибки пробрасываем как есть).
    3) Добираем паузу под длину текста, пока горит typing.
    """
    started = time.monotonic()
    typing_cm = client.action(chat_id, "typing")
    typing_on = False
    try:
        await typing_cm.__aenter__()
        typing_on = True
    except Exception:
        logger.debug(
            "Не удалось включить typing chat_id=%s", chat_id, exc_info=True
        )

    try:
        reply = await produce()
        left = human_typing_delay_sec(reply) - (time.monotonic() - started)
        if left > 0:
            await asyncio.sleep(left)
        return reply
    finally:
        if typing_on:
            try:
                # Не передаём exc_info — иначе __aexit__ теоретически может подавить ошибку produce()
                await typing_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug(
                    "Ошибка при выключении typing chat_id=%s",
                    chat_id,
                    exc_info=True,
                )


async def human_pause_typing(
    client: TelegramClient,
    chat_id: int,
    text: str,
) -> None:
    """Короткая допечатка между чанками длинного ответа."""
    # Для кусков — пропорционально короче, без длинного «думать»
    chars = max(len(text.strip()), 1)
    delay = min(max(chars / 8.0, 0.4), 4.0)
    try:
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(delay)
    except Exception:
        await asyncio.sleep(delay)
