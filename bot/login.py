"""
Интерактивный логин в Telegram USER-аккаунт.

Создаёт data/<name>.session и печатает StringSession для .env.

Запуск:
    python -m bot.login
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telethon.sessions import StringSession

from bot.config import load_settings
from bot.telegram_client import build_client, setup_logging

logger = logging.getLogger(__name__)


async def _login() -> None:
    try:
        settings = load_settings()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Ошибка конфигурации: %s", exc)
        sys.exit(1)

    setup_logging(settings.log_level)
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    if settings.session_string:
        print(
            "В .env уже есть TELEGRAM_SESSION_STRING — "
            "можно сразу: python -m bot"
        )
        return

    client = build_client(settings)
    session_string: str | None = None
    try:
        await client.start()
        me = await client.get_me()
        logger.info(
            "Успешный вход: %s (id=%s)",
            getattr(me, "username", None) or me.first_name,
            me.id,
        )
        session_string = StringSession.save(client.session)
    finally:
        if client.is_connected():
            await client.disconnect()

    if not session_string:
        logger.error("Логин не завершён — StringSession не получен")
        sys.exit(1)

    print("\n=== Готово ===")
    print(f"Файловая сессия: {settings.session_path}.session")
    print("\nОпционально добавь в .env (храни как пароль!):")
    print(f"TELEGRAM_SESSION_STRING={session_string}")
    print("\nЗапуск: python -m bot\n")


def main() -> None:
    try:
        asyncio.run(_login())
    except KeyboardInterrupt:
        print("\nОтменено.")


if __name__ == "__main__":
    main()
