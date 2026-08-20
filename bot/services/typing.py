"""
Человеческий набор текста: задержка от длины + индикатор «печатает…».
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
    """
    Сколько секунд «печатать» ответ.

    Ориентир: набор с телефона ~4–7 символов/сек + пауза «подумать».
    """
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
    Пока думаем и «печатаем» — в шапке чата горит typing.
    После генерации добираем паузу под длину текста.
    """
    reply: str | None = None
    try:
        async with client.action(chat_id, "typing"):
            started = time.monotonic()
            reply = await produce()
            need = human_typing_delay_sec(reply)
            left = need - (time.monotonic() - started)
            if left > 0:
                await asyncio.sleep(left)
    except Exception:
        logger.debug("typing action failed chat_id=%s", chat_id, exc_info=True)
        if reply is None:
            reply = await produce()

    return reply
