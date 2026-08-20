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

# Жёсткие потолки под лимиты Telegram (защита от раздувания БД)
_MAX_USERNAME_LEN = 64
_MAX_NAME_LEN = 128
_MAX_CONTENT_LEN = 4096


def _clip(value: str | None, max_len: int) -> str | None:
    """Обрезать строку до max_len; None остаётся None."""
    if value is None:
        return None
    return value[:max_len]


class Database:
    """Обёртка над aiosqlite с параметризованными запросами."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError(
                "База данных не инициализирована. Вызови await db.connect()."
            )
        return self._connection

    async def connect(self) -> None:
        """Открыть соединение и создать таблицы, если их ещё нет."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON;")
        await self._connection.execute("PRAGMA journal_mode = WAL;")
        await self._create_tables()
        logger.info("SQLite подключена: %s", self._db_path)

    async def close(self) -> None:
        """Корректно закрыть соединение (идемпотентно)."""
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
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_id
                ON messages (telegram_id, id);
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
        Сохранить или обновить пользователя атомарно.

        Returns:
            True, если пользователь новый; False, если уже был в БД.
        """
        username = _clip(username, _MAX_USERNAME_LEN)
        first_name = _clip(first_name, _MAX_NAME_LEN)
        last_name = _clip(last_name, _MAX_NAME_LEN)

        try:
            # Атомарно: INSERT OR IGNORE → rowcount=1 только для нового юзера.
            # Потом, если уже был — отдельный UPDATE профиля.
            cursor = await self.connection.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO NOTHING
                """,
                (telegram_id, username, first_name, last_name),
            )
            is_new = cursor.rowcount == 1

            if not is_new:
                await self.connection.execute(
                    """
                    UPDATE users
                    SET username   = ?,
                        first_name = ?,
                        last_name  = ?,
                        updated_at = datetime('now')
                    WHERE telegram_id = ?
                    """,
                    (username, first_name, last_name, telegram_id),
                )

            await self.connection.commit()

            if is_new:
                logger.info(
                    "Новый пользователь: id=%s username=%s", telegram_id, username
                )
            return is_new
        except aiosqlite.Error:
            logger.exception("Ошибка upsert_user для telegram_id=%s", telegram_id)
            raise

    async def add_exchange(
        self,
        telegram_id: int,
        user_content: str,
        assistant_content: str,
        *,
        keep: int | None = None,
    ) -> None:
        """
        Сохранить пару user/assistant в одной транзакции.

        Если передан `keep` — тут же обрезает историю до последних keep
        сообщений в той же транзакции (не оставляем «хвост» при сбое trim).
        """
        user_content = user_content[:_MAX_CONTENT_LEN]
        assistant_content = assistant_content[:_MAX_CONTENT_LEN]
        try:
            await self.connection.execute("BEGIN")
            await self.connection.execute(
                """
                INSERT INTO messages (telegram_id, role, content)
                VALUES (?, 'user', ?)
                """,
                (telegram_id, user_content),
            )
            await self.connection.execute(
                """
                INSERT INTO messages (telegram_id, role, content)
                VALUES (?, 'assistant', ?)
                """,
                (telegram_id, assistant_content),
            )
            if keep is not None:
                await self._trim_history_locked(telegram_id, keep)
            await self.connection.commit()
        except Exception:
            try:
                await self.connection.rollback()
            except aiosqlite.Error:
                logger.exception("Не удалось откатить транзакцию add_exchange")
            logger.exception(
                "Ошибка add_exchange для telegram_id=%s", telegram_id
            )
            raise

    async def get_history(
        self,
        telegram_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Вернуть последние `limit` реплик в хронологическом порядке.

        Формат: [{"role": "user"|"assistant", "content": "..."}, ...]
        """
        try:
            cursor = await self.connection.execute(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, id
                    FROM messages
                    WHERE telegram_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) AS recent
                ORDER BY id ASC
                """,
                (telegram_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                {"role": row["role"], "content": row["content"]} for row in rows
            ]
        except aiosqlite.Error:
            logger.exception(
                "Ошибка get_history для telegram_id=%s", telegram_id
            )
            raise

    async def _trim_history_locked(self, telegram_id: int, keep: int) -> int:
        """Trim внутри уже открытой транзакции (без commit)."""
        cursor = await self.connection.execute(
            """
            DELETE FROM messages
            WHERE telegram_id = ?
              AND id < COALESCE(
                  (
                      SELECT MIN(id)
                      FROM (
                          SELECT id
                          FROM messages
                          WHERE telegram_id = ?
                          ORDER BY id DESC
                          LIMIT ?
                      )
                  ),
                  0
              )
            """,
            (telegram_id, telegram_id, keep),
        )
        return cursor.rowcount if cursor.rowcount is not None else 0

    async def trim_history(self, telegram_id: int, keep: int) -> int:
        """
        Удалить старые сообщения, оставив только последние `keep`.

        Сортировка по id (монотонный AUTOINCREMENT) надёжнее datetime
        с секундной точностью при быстрых подряд записях.
        """
        try:
            deleted = await self._trim_history_locked(telegram_id, keep)
            await self.connection.commit()
            if deleted:
                logger.debug(
                    "Обрезана история пользователя %s: удалено %s",
                    telegram_id,
                    deleted,
                )
            return deleted
        except aiosqlite.Error:
            logger.exception(
                "Ошибка trim_history для telegram_id=%s", telegram_id
            )
            raise

    async def clear_history(self, telegram_id: int) -> None:
        """Полностью очистить историю диалога пользователя (/reset)."""
        try:
            await self.connection.execute(
                "DELETE FROM messages WHERE telegram_id = ?",
                (telegram_id,),
            )
            await self.connection.commit()
            logger.info("История очищена для telegram_id=%s", telegram_id)
        except aiosqlite.Error:
            logger.exception(
                "Ошибка clear_history для telegram_id=%s", telegram_id
            )
            raise
