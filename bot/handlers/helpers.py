"""
Общие хелперы хендлеров (без дублирования upsert / answer).
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import Message, User

from bot.database import Database

logger = logging.getLogger(__name__)


async def ensure_user(db: Database, user: User) -> bool:
    """Зарегистрировать/обновить пользователя. True — если новый."""
    return await db.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


async def safe_answer(message: Message, text: str) -> bool:
    """
    Ответить в чат, глотая типовые ошибки Telegram.

    Returns:
        False, если отправить не удалось (блок, удаление чата и т.п.).
    """
    try:
        await message.answer(text)
        return True
    except TelegramForbiddenError:
        logger.info(
            "Не могу писать user_id=%s — бот заблокирован",
            message.from_user.id if message.from_user else "?",
        )
        return False
    except TelegramAPIError:
        logger.exception(
            "Telegram API error при ответе chat_id=%s", message.chat.id
        )
        return False
