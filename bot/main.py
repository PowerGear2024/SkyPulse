"""
Точка входа Telegram-бота.

Запуск:
    python -m bot
    python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from aiogram import Bot, Dispatcher

from bot.config import Settings, load_settings
from bot.database import Database
from bot.handlers import setup_routers
from bot.services.gate import UserGate
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
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


async def on_startup(db: Database) -> None:
    """Подключение к БД при старте."""
    await db.connect()
    logger.info("Бот запущен и готов принимать апдейты")


async def on_shutdown(db: Database, llm: LLMService) -> None:
    """
    Закрыть БД и LLM-клиенты.

    Сессию Bot закрывает сам aiogram в finally start_polling —
    руками её не трогаем (иначе двойное закрытие).
    """
    logger.info("Остановка бота…")
    try:
        await llm.close()
    except Exception:
        logger.exception("Ошибка при закрытии LLM-клиента")
    try:
        await db.close()
    except Exception:
        logger.exception("Ошибка при закрытии БД")


async def run_bot() -> None:
    """Собрать зависимости и запустить long-polling."""
    try:
        settings: Settings = load_settings()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    llm = LLMService(settings)
    gate = UserGate(min_interval_sec=1.5)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(setup_routers())

    cleaned_up = False

    async def cleanup() -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        await on_shutdown(db, llm)

    async def _startup(*_args: Any, **_kwargs: Any) -> None:
        await on_startup(db)

    async def _shutdown(*_args: Any, **_kwargs: Any) -> None:
        await cleanup()

    dispatcher.startup.register(_startup)
    dispatcher.shutdown.register(_shutdown)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(
            bot,
            db=db,
            llm=llm,
            settings=settings,
            gate=gate,
        )
    except Exception:
        logger.exception("Критическая ошибка в polling-цикле")
        raise
    finally:
        await cleanup()


def main() -> None:
    """Синхронная обёртка для `python -m bot` / entrypoint."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (Ctrl+C)")


if __name__ == "__main__":
    main()
