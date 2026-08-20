"""
Хендлер обычных текстовых сообщений — диалог с LLM.

Флоу:
  1. Сохранить/обновить пользователя в SQLite
  2. Подтянуть последние N реплик
  3. Спросить LLM
  4. Сохранить обе реплики и обрезать хвост истории
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

from bot.config import Settings
from bot.database import Database
from bot.services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)

router = Router(name="chat")

# Telegram лимит длины сообщения — 4096 символов
TELEGRAM_MESSAGE_LIMIT = 4096


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ на куски, умещающиеся в лимит Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Режем по последнему переносу строки в окне, иначе жёстко по лимиту
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(
    message: Message,
    db: Database,
    llm: LLMService,
    settings: Settings,
) -> None:
    """Обработать текстовое сообщение пользователя через LLM."""
    if message.from_user is None or not message.text:
        return

    user = message.from_user
    user_text = message.text.strip()
    if not user_text:
        return

    try:
        await db.upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )

        history = await db.get_history(user.id, limit=settings.history_limit)

        # Индикатор «печатает…», пока ждём ответ модели
        await message.bot.send_chat_action(
            chat_id=message.chat.id,
            action=ChatAction.TYPING,
        )

        reply = await llm.generate_reply(history=history, user_message=user_text)

        # Сначала пишем в БД, потом отвечаем — история не потеряется при сетевом глюке
        await db.add_message(user.id, "user", user_text)
        await db.add_message(user.id, "assistant", reply)
        await db.trim_history(user.id, keep=settings.history_limit)

        for chunk in _split_message(reply):
            await message.answer(chunk)

    except LLMError:
        logger.exception("LLM недоступен для user_id=%s", user.id)
        await message.answer(
            "Модель сейчас откинулась. Подожди минуту и кинь ещё раз — "
            "я не специально, честно."
        )
    except Exception:
        logger.exception("Ошибка обработки сообщения user_id=%s", user.id)
        await message.answer(
            "Упс, что-то сломалось на моей стороне. Попробуй ещё раз или /reset."
        )
