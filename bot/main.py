"""
Точка входа: Telegram USER-сессия (Telethon), не Bot API.

Запуск:
    python -m bot
Первый логин / StringSession:
    python -m bot.login
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from bot.config import Settings, load_settings
from bot.database import Database
from bot.handlers import register_handlers
from bot.services.gate import UserGate
from bot.services.llm import LLMService

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def build_client(settings: Settings) -> TelegramClient:
    """Собрать Telethon-клиент из StringSession или файла сессии."""
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    if settings.session_string:
        session: StringSession | str = StringSession(settings.session_string)
    else:
        session = str(settings.session_path)

    return TelegramClient(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


async def run_userbot() -> None:
    """Подключить user-сессию и слушать входящие ЛС."""
    try:
        settings = load_settings()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    llm = LLMService(settings)
    gate = UserGate(min_interval_sec=1.5)
    client = build_client(settings)

    await db.connect()
    register_handlers(
        client,
        db=db,
        llm=llm,
        settings=settings,
        gate=gate,
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(
                "Сессия не авторизована. Сначала выполни: python -m bot.login"
            )
            sys.exit(1)

        me = await client.get_me()
        logger.info(
            "User-сессия активна как %s (id=%s). Слушаю личные сообщения…",
            getattr(me, "username", None) or me.first_name,
            me.id,
        )
        await client.run_until_disconnected()
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
        if client.is_connected():
            await client.disconnect()


def main() -> None:
    try:
        asyncio.run(run_userbot())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (Ctrl+C)")


if __name__ == "__main__":
    main()
