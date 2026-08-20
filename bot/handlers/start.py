"""
Хендлеры команд /start и /reset.

/start — регистрация пользователя в SQLite + приветствие в стиле анонимки.
/reset — очистка истории диалога (свежий контекст без старых тем).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.database import Database
from bot.handlers.helpers import ensure_user, safe_answer
from bot.persona import RESET_TEXT, get_start_text

logger = logging.getLogger(__name__)

router = Router(name="start")
router.message.filter(F.chat.type == ChatType.PRIVATE)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    """Зарегистрировать пользователя и отправить приветствие."""
    if message.from_user is None:
        return

    try:
        await ensure_user(db, message.from_user)
        await safe_answer(message, get_start_text())
    except Exception:
        logger.exception("Ошибка в /start для user_id=%s", message.from_user.id)
        await safe_answer(
            message,
            "Что-то пошло не так на моей стороне. "
            "Попробуй /start ещё раз через секунду.",
        )


@router.message(Command("reset"))
async def cmd_reset(message: Message, db: Database) -> None:
    """Очистить историю диалога пользователя."""
    if message.from_user is None:
        return

    try:
        await db.clear_history(message.from_user.id)
        await safe_answer(message, RESET_TEXT)
    except Exception:
        logger.exception(
            "Ошибка в /reset для user_id=%s", message.from_user.id
        )
        await safe_answer(
            message,
            "Не вышло сбросить историю. Попробуй ещё раз.",
        )
