"""
Хендлер обычных текстовых сообщений — диалог с LLM.

Флоу:
  1. Rate-limit + per-user lock
  2. Сохранить/обновить пользователя в SQLite
  3. Подтянуть последние N реплик
  4. Спросить LLM
  5. Сохранить пару реплик в одной транзакции и обрезать историю
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import ensure_user
from bot.services.gate import UserGate
from bot.services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)

router = Router(name="chat")

TELEGRAM_MESSAGE_LIMIT = 4096
# Защита от абсурдно длинного ввода в LLM/БД (Telegram и так режет ~4096)
MAX_USER_CHARS = 4000


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ на куски под лимит Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
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
    gate: UserGate,
) -> None:
    """Обработать текстовое сообщение пользователя через LLM."""
    if message.from_user is None or not message.text:
        return

    user = message.from_user
    user_text = message.text.strip()
    if not user_text:
        return

    if len(user_text) > MAX_USER_CHARS:
        user_text = user_text[:MAX_USER_CHARS]

    wait = gate.seconds_until_allowed(user.id)
    if wait > 0:
        await message.answer(
            f"Эй, притормози на {wait:.1f} сек — я не автомат по спаму."
        )
        return

    async with gate.lock_for(user.id):
        # Повторная проверка после ожидания в очереди лока
        wait = gate.seconds_until_allowed(user.id)
        if wait > 0:
            await message.answer(
                f"Подожди ещё {wait:.1f} сек, предыдущий запрос ещё «остывает»."
            )
            return

        try:
            await ensure_user(db, user)
            history = await db.get_history(user.id, limit=settings.history_limit)

            await message.bot.send_chat_action(
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
            )

            reply = await llm.generate_reply(
                history=history,
                user_message=user_text,
            )

            await db.add_exchange(user.id, user_text, reply)
            await db.trim_history(user.id, keep=settings.history_limit)
            gate.mark_used(user.id)

            for chunk in split_message(reply):
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
                "Упс, что-то сломалось на моей стороне. "
                "Попробуй ещё раз или /reset."
            )
