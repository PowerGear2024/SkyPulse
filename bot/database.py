"""
Асинхронный слой работы с SQLite.

Хранит:
  - пользователей (telegram_id + метаданные);
  - историю диалога (последние N реплик на пользователя).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """Обёртка над aiosqlite с безопасными транзакциями."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("База данных не инициализирована. Вызови await db.connect().")
        return self._connection

    async def connect(self) -> None:
        """Открыть соединение и создать таблицы, если их ещё нет."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._db_path)
        # Возвращать строки как dict-like объекты
        self._connection.row_factory = aiosqlite.Row
        # Надёжность при конкурентных записях
        await self._connection.execute("PRAGMA foreign_keys = ON;")
        await self._connection.execute("PRAGMA journal_mode = WAL;")
        await self._create_tables()
        logger.info("SQLite подключена: %s", self._db_path)

    async def close(self) -> None:
        """Корректно закрыть соединение."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            logger.info("Соединение с SQLite закрыто")

    async def _create_tables(self) -> None:
        """Схема БД: пользователи + сообщения чата."""
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_created
                ON messages (telegram_id, created_at);
            """
        )
        await self.connection.commit()

    async def upsert_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> bool:
        """
        Сохранить или обновить пользователя.

        Returns:
            True, если пользователь новый; False, если уже был в БД.
        """
        try:
            cursor = await self.connection.execute(
                "SELECT 1 FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            exists = await cursor.fetchone() is not None

            await self.connection.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    last_name  = excluded.last_name,
                    updated_at = datetime('now')
                """,
                (telegram_id, username, first_name, last_name),
            )
            await self.connection.commit()

            if not exists:
                logger.info("Новый пользователь: id=%s username=%s", telegram_id, username)
            return not exists
        except aiosqlite.Error:
            logger.exception("Ошибка upsert_user для telegram_id=%s", telegram_id)
            raise

    async def add_message(self, telegram_id: int, role: str, content: str) -> None:
        """Добавить реплику в историю диалога."""
        if role not in {"user", "assistant"}:
            raise ValueError(f"Недопустимая роль сообщения: {role!r}")
        try:
            await self.connection.execute(
                """
                INSERT INTO messages (telegram_id, role, content)
                VALUES (?, ?, ?)
                """,
                (telegram_id, role, content),
            )
            await self.connection.commit()
        except aiosqlite.Error:
            logger.exception(
                "Ошибка add_message: telegram_id=%s role=%s", telegram_id, role
            )
            raise

    async def get_history(
        self,
        telegram_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Вернуть последние `limit` реплик пользователя в хронологическом порядке.

        Формат: [{"role": "user"|"assistant", "content": "..."}, ...]
        """
        try:
            cursor = await self.connection.execute(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, created_at, id
                    FROM messages
                    WHERE telegram_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                ) AS recent
                ORDER BY created_at ASC, id ASC
                """,
                (telegram_id, limit),
            )
            rows = await cursor.fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in rows]
        except aiosqlite.Error:
            logger.exception("Ошибка get_history для telegram_id=%s", telegram_id)
            raise

    async def trim_history(self, telegram_id: int, keep: int) -> int:
        """
        Удалить старые сообщения, оставив только последние `keep`.

        Returns:
            Количество удалённых строк.
        """
        try:
            cursor = await self.connection.execute(
                """
                DELETE FROM messages
                WHERE telegram_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM messages
                      WHERE telegram_id = ?
                      ORDER BY created_at DESC, id DESC
                      LIMIT ?
                  )
                """,
                (telegram_id, telegram_id, keep),
            )
            await self.connection.commit()
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            if deleted:
                logger.debug(
                    "Обрезана история пользователя %s: удалено %s сообщений",
                    telegram_id,
                    deleted,
                )
            return deleted
        except aiosqlite.Error:
            logger.exception("Ошибка trim_history для telegram_id=%s", telegram_id)
            raise

    async def clear_history(self, telegram_id: int) -> None:
        """Полностью очистить историю диалога пользователя (команда /reset)."""
        try:
            await self.connection.execute(
                "DELETE FROM messages WHERE telegram_id = ?",
                (telegram_id,),
            )
            await self.connection.commit()
            logger.info("История очищена для telegram_id=%s", telegram_id)
        except aiosqlite.Error:
            logger.exception("Ошибка clear_history для telegram_id=%s", telegram_id)
            raise
