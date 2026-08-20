"""
Общие хелперы хендлеров (без дублирования upsert в каждом месте).
"""

from __future__ import annotations

from aiogram.types import User

from bot.database import Database


async def ensure_user(db: Database, user: User) -> bool:
    """Зарегистрировать/обновить пользователя. True — если новый."""
    return await db.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
