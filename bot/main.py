"""
Точка входа Telegram-бота.

Запуск:
    python -m bot.main
или:
    python bot/main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Settings, load_settings
from bot.database import Database
from bot.handlers import setup_routers
from bot.services.llm import LLMService

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    """Настроить корневой логгер: консоль + единый формат."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # Приглушаем болтливые HTTP-библиотеки
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


async def on_startup(db: Database) -> None:
    """Действия при старте: подключение к БД."""
    await db.connect()
    logger.info("Бот запущен и готов принимать апдейты")


async def on_shutdown(db: Database, llm: LLMService, bot: Bot) -> None:
    """Корректное завершение: закрыть БД, LLM-клиенты и сессию бота."""
    logger.info("Остановка бота…")
    try:
        await llm.close()
    except Exception:
        logger.exception("Ошибка при закрытии LLM-клиента")
    try:
        await db.close()
    except Exception:
        logger.exception("Ошибка при закрытии БД")
    try:
        await bot.session.close()
    except Exception:
        logger.exception("Ошибка при закрытии сессии Bot")


async def main() -> None:
    """Собрать зависимости и запустить long-polling."""
    try:
        settings: Settings = load_settings()
    except ValueError as exc:
        # Конфиг — фатальная ошибка: без токена/ключа бот бессмысленен
        logging.basicConfig(level=logging.ERROR)
        logging.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    llm = LLMService(settings)

    # Без parse_mode по умолчанию: ответы LLM часто содержат <, > и код —
    # HTML/Markdown иначе падают с ошибкой парсинга в Telegram.
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(setup_routers())

    cleaned_up = False

    async def cleanup() -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        await on_shutdown(db, llm, bot)

    async def _startup(*_args: Any, **_kwargs: Any) -> None:
        await on_startup(db)

    async def _shutdown(*_args: Any, **_kwargs: Any) -> None:
        await cleanup()

    dispatcher.startup.register(_startup)
    dispatcher.shutdown.register(_shutdown)

    try:
        # Сбрасываем накопившиеся апдейты — бот не отвечает на древние сообщения
        await bot.delete_webhook(drop_pending_updates=True)
        # DI через kwargs: db/llm/settings попадут в хендлеры по имени аргумента
        await dispatcher.start_polling(
            bot,
            db=db,
            llm=llm,
            settings=settings,
        )
    except Exception:
        logger.exception("Критическая ошибка в polling-цикле")
        raise
    finally:
        # На случай, если shutdown-хук не сработал
        await cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (Ctrl+C)")
