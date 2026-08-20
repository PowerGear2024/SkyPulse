"""
Точка входа: USER-сессия, только групповые чаты.

Запуск:  python -m bot
Логин:   python -m bot.login
"""

from __future__ import annotations

import asyncio
import logging
import sys

from bot.config import load_settings
from bot.database import Database
from bot.handlers import register_handlers
from bot.services.gate import ChatGate
from bot.services.llm import LLMService
from bot.telegram_client import build_client, setup_logging

logger = logging.getLogger(__name__)


async def run_userbot() -> None:
    try:
        settings = load_settings()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    llm = LLMService(settings)
    gate = ChatGate(min_interval_sec=2.0)
    client = build_client(settings)

    await db.connect()

    exit_code = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(
                "Сессия не авторизована. Сначала: python -m bot.login"
            )
            exit_code = 1
            return

        me = await client.get_me()
        if me is None:
            logger.error("get_me() вернул None — сессия битая, перелогинься")
            exit_code = 1
            return

        register_handlers(
            client,
            db=db,
            llm=llm,
            settings=settings,
            gate=gate,
            me=me,
        )

        logger.info(
            "User-сессия как %s (id=%s). Режим: ТОЛЬКО группы, ЛС игнор.",
            getattr(me, "username", None) or me.first_name,
            me.id,
        )
        await client.run_until_disconnected()
    except Exception:
        logger.exception("Критическая ошибка user-сессии")
        exit_code = 1
        raise
    finally:
        logger.info("Остановка…")
        try:
            await llm.close()
        except Exception:
            logger.exception("Ошибка при закрытии LLM")
        try:
            await db.close()
        except Exception:
            logger.exception("Ошибка при закрытии БД")
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            logger.exception("Ошибка при disconnect Telethon")

    if exit_code:
        sys.exit(exit_code)


def main() -> None:
    try:
        asyncio.run(run_userbot())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (Ctrl+C)")


if __name__ == "__main__":
    main()
