"""
Память группового чата + профили отправителей.

Все текстовые сообщения группы пишутся в chat_messages,
чтобы модель видела весь ход переписки, смысл и логику.
ЛС не пишем сюда — они полностью игнорируются на уровне хендлера.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_MAX_USERNAME_LEN = 64
_MAX_NAME_LEN = 128
_MAX_CONTENT_LEN = 4096
_MAX_SENDER_NAME = 64


def _clip(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[:max_len]


class Database:
    """SQLite: users + лента сообщений по chat_id."""

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
        if self._connection is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._db_path, timeout=30.0)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON;")
        await self._connection.execute("PRAGMA journal_mode = WAL;")
        await self._connection.execute("PRAGMA busy_timeout = 30000;")
        await self._create_tables()
        logger.info("SQLite подключена: %s", self._db_path)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            logger.info("Соединение с SQLite закрыто")

    async def _create_tables(self) -> None:
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

            -- Лента ВСЕХ сообщений группы (память чата)
            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                sender_id   INTEGER,
                sender_name TEXT,
                is_me       INTEGER NOT NULL DEFAULT 0
                            CHECK (is_me IN (0, 1)),
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_id
                ON chat_messages (chat_id, id);
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
        if telegram_id <= 0:
            raise ValueError(f"Некорректный telegram_id: {telegram_id}")

        username = _clip(username, _MAX_USERNAME_LEN)
        first_name = _clip(first_name, _MAX_NAME_LEN)
        last_name = _clip(last_name, _MAX_NAME_LEN)

        try:
            await self.connection.execute("BEGIN")
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
            return is_new
        except Exception:
            try:
                await self.connection.rollback()
            except aiosqlite.Error:
                logger.exception("Не удалось откатить upsert_user")
            logger.exception("Ошибка upsert_user для telegram_id=%s", telegram_id)
            raise

    async def add_chat_message(
        self,
        chat_id: int,
        content: str,
        *,
        sender_id: int | None = None,
        sender_name: str | None = None,
        is_me: bool = False,
        keep: int | None = None,
    ) -> None:
        """Добавить одно сообщение в память чата (+ optional trim)."""
        content = (content or "").strip()[:_MAX_CONTENT_LEN]
        if not content:
            raise ValueError("add_chat_message: пустой content")
        if keep is not None and keep < 2:
            raise ValueError("keep должен быть >= 2")
        sender_name = _clip(sender_name, _MAX_SENDER_NAME)

        try:
            await self.connection.execute("BEGIN")
            await self.connection.execute(
                """
                INSERT INTO chat_messages
                    (chat_id, sender_id, sender_name, is_me, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    sender_id,
                    sender_name,
                    1 if is_me else 0,
                    content,
                ),
            )
            if keep is not None:
                await self._trim_chat_locked(chat_id, keep)
            await self.connection.commit()
        except Exception:
            try:
                await self.connection.rollback()
            except aiosqlite.Error:
                logger.exception("Не удалось откатить add_chat_message")
            logger.exception(
                "Ошибка add_chat_message chat_id=%s", chat_id
            )
            raise

    async def get_chat_history_for_llm(
        self,
        chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """
        Последние `limit` сообщений чата → формат Chat API.

        Чужие: role=user, content='[Имя]: текст'
        Мои:    role=assistant, content=текст
        """
        if limit < 1:
            return []
        try:
            cursor = await self.connection.execute(
                """
                SELECT sender_name, is_me, content
                FROM (
                    SELECT sender_name, is_me, content, id
                    FROM chat_messages
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) AS recent
                ORDER BY id ASC
                """,
                (chat_id, limit),
            )
            rows = await cursor.fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                if row["is_me"]:
                    result.append(
                        {"role": "assistant", "content": row["content"]}
                    )
                else:
                    name = (row["sender_name"] or "Кто-то").strip() or "Кто-то"
                    result.append(
                        {
                            "role": "user",
                            "content": f"[{name}]: {row['content']}",
                        }
                    )
            return result
        except aiosqlite.Error:
            logger.exception(
                "Ошибка get_chat_history_for_llm chat_id=%s", chat_id
            )
            raise

    async def _trim_chat_locked(self, chat_id: int, keep: int) -> int:
        cursor = await self.connection.execute(
            """
            DELETE FROM chat_messages
            WHERE chat_id = ?
              AND id < COALESCE(
                  (
                      SELECT MIN(id)
                      FROM (
                          SELECT id
                          FROM chat_messages
                          WHERE chat_id = ?
                          ORDER BY id DESC
                          LIMIT ?
                      )
                  ),
                  0
              )
            """,
            (chat_id, chat_id, keep),
        )
        return cursor.rowcount if cursor.rowcount is not None else 0

    async def clear_chat_history(self, chat_id: int) -> None:
        """Сброс памяти конкретного чата (/reset)."""
        try:
            await self.connection.execute(
                "DELETE FROM chat_messages WHERE chat_id = ?",
                (chat_id,),
            )
            await self.connection.commit()
            logger.info("Память чата очищена chat_id=%s", chat_id)
        except aiosqlite.Error:
            logger.exception(
                "Ошибка clear_chat_history chat_id=%s", chat_id
            )
            raise
