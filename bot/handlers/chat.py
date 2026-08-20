"""
Хендлер обычных текстовых сообщений — диалог с LLM.

Флоу:
  1. Только private-чат, rate-limit + per-user lock
  2. Сохранить/обновить пользователя в SQLite
  3. Подтянуть последние N реплик
  4. Спросить LLM
  5. Сохранить пару реплик и обрезать историю в одной транзакции
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from bot.config import Settings
from bot.database import Database
from bot.handlers.helpers import ensure_user, safe_answer
from bot.services.gate import UserGate
from bot.services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)

router = Router(name="chat")
router.message.filter(F.chat.type == ChatType.PRIVATE)

TELEGRAM_MESSAGE_LIMIT = 4096
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

    # Не копим очередь: если уже думаем — сразу отшиваем
    if gate.is_busy(user.id):
        await safe_answer(
            message,
            "Я ещё ковыряюсь с прошлым сообщением — секунду, не долби.",
        )
        return

    wait = gate.seconds_until_allowed(user.id)
    if wait > 0:
        await safe_answer(
            message,
            f"Эй, притормози на {wait:.1f} сек — я не автомат по спаму.",
        )
        return

    async with gate.lock_for(user.id):
        wait = gate.seconds_until_allowed(user.id)
        if wait > 0:
            await safe_answer(
                message,
                f"Подожди ещё {wait:.1f} сек, я чуть раньше уже отвечал.",
            )
            return

        # Сразу ставит кулдаун — даже если дальше упадём, API не разнесут
        gate.mark_used(user.id)

        try:
            await ensure_user(db, user)
            history = await db.get_history(user.id, limit=settings.history_limit)

            try:
                await message.bot.send_chat_action(
                    chat_id=message.chat.id,
                    action=ChatAction.TYPING,
                )
            except TelegramAPIError:
                # Блок / недоступный чат — всё равно пробуем ответить ниже
                logger.info("Не удалось отправить typing user_id=%s", user.id)

            reply = await llm.generate_reply(
                history=history,
                user_message=user_text,
            )

            await db.add_exchange(
                user.id,
                user_text,
                reply,
                keep=settings.history_limit,
            )

            for chunk in split_message(reply):
                if not await safe_answer(message, chunk):
                    break

        except LLMError:
            # Детали уже в логах LLMService; юзеру — по-человечески
            logger.warning("Сбой генерации для user_id=%s", user.id)
            await safe_answer(
                message,
                "Инет у меня моргнул / сервис подвис. Кинь ещё раз через минуту.",
            )
        except Exception:
            logger.exception("Ошибка обработки сообщения user_id=%s", user.id)
            await safe_answer(
                message,
                "Что-то у меня в голове щёлкнуло не так. "
                "Попробуй ещё раз или /reset.",
            )
