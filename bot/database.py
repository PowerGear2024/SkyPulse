"""
Память группового чата + профили отправителей.

Все текстовые сообщения группы пишутся в chat_messages.
Один asyncio.Lock сериализует транзакции на общем aiosqlite-соединении.
"""

from __future__ import annotations

import asyncio
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
        self._lock = asyncio.Lock()
        # Инвалидация in-flight reflect после /reset
        self._pulse_epoch: dict[int, int] = {}

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
        async with self._lock:
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

            CREATE INDEX IF NOT EXISTS idx_chat_messages_sender
                ON chat_messages (chat_id, sender_id, id);

            CREATE TABLE IF NOT EXISTS proactive_posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id         INTEGER NOT NULL,
                target_user_id  INTEGER NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_proactive_posts_day
                ON proactive_posts (created_at);

            -- Эмоциональный пульс персонажа по чату
            CREATE TABLE IF NOT EXISTS persona_mood (
                chat_id     INTEGER PRIMARY KEY,
                mood        TEXT NOT NULL DEFAULT 'дерзкий дружеский',
                vibe        TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS persona_feelings (
                chat_id     INTEGER NOT NULL,
                peer_name   TEXT NOT NULL,
                stance      TEXT NOT NULL DEFAULT 'neutral',
                note        TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (chat_id, peer_name)
            );
            """
        )
        await self.connection.commit()

    async def _rollback_quiet(self) -> None:
        try:
            await self.connection.rollback()
        except aiosqlite.Error:
            logger.exception("Не удалось откатить транзакцию SQLite")

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

        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
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
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка upsert_user для telegram_id=%s", telegram_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
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

        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
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
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка add_chat_message chat_id=%s", chat_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
                raise

    async def get_chat_history_for_llm(
        self,
        chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            return []
        async with self._lock:
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
            except aiosqlite.Error:
                logger.exception(
                    "Ошибка get_chat_history_for_llm chat_id=%s", chat_id
                )
                raise

        result: list[dict[str, Any]] = []
        for row in rows:
            if row["is_me"]:
                result.append({"role": "assistant", "content": row["content"]})
            else:
                name = (row["sender_name"] or "Кто-то").strip() or "Кто-то"
                result.append(
                    {
                        "role": "user",
                        "content": f"[{name}]: {row['content']}",
                    }
                )
        return result

    async def _trim_chat_locked(self, chat_id: int, keep: int) -> int:
        """Вызывать только под self._lock внутри открытой транзакции."""
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
        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                await self.connection.execute(
                    "DELETE FROM chat_messages WHERE chat_id = ?",
                    (chat_id,),
                )
                await self.connection.execute(
                    "DELETE FROM persona_mood WHERE chat_id = ?",
                    (chat_id,),
                )
                await self.connection.execute(
                    "DELETE FROM persona_feelings WHERE chat_id = ?",
                    (chat_id,),
                )
                await self.connection.commit()
                self._pulse_epoch[chat_id] = self._pulse_epoch.get(chat_id, 0) + 1
                logger.info(
                    "Память чата очищена chat_id=%s epoch=%s",
                    chat_id,
                    self._pulse_epoch[chat_id],
                )
            except Exception:
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка clear_chat_history chat_id=%s", chat_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
                raise

    def pulse_epoch(self, chat_id: int) -> int:
        return self._pulse_epoch.get(chat_id, 0)

    async def add_chat_messages_batch(
        self,
        chat_id: int,
        items: list[dict[str, Any]],
        *,
        keep: int | None = None,
    ) -> None:
        """Атомарно записать несколько сообщений (notes + reply) в одном BEGIN."""
        if not items:
            return
        if keep is not None and keep < 2:
            raise ValueError("keep должен быть >= 2")

        prepared: list[tuple[Any, ...]] = []
        for item in items:
            content = (item.get("content") or "").strip()[:_MAX_CONTENT_LEN]
            if not content:
                continue
            prepared.append(
                (
                    chat_id,
                    item.get("sender_id"),
                    _clip(item.get("sender_name"), _MAX_SENDER_NAME),
                    1 if item.get("is_me") else 0,
                    content,
                )
            )
        if not prepared:
            return

        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                await self.connection.executemany(
                    """
                    INSERT INTO chat_messages
                        (chat_id, sender_id, sender_name, is_me, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    prepared,
                )
                if keep is not None:
                    await self._trim_chat_locked(chat_id, keep)
                await self.connection.commit()
            except Exception:
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка add_chat_messages_batch chat_id=%s", chat_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
                raise

    async def get_persona_pulse(self, chat_id: int) -> dict[str, Any]:
        """mood + vibe + feelings для system prompt."""
        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    """
                    SELECT mood, vibe FROM persona_mood WHERE chat_id = ?
                    """,
                    (chat_id,),
                )
                row = await cursor.fetchone()
                cursor = await self.connection.execute(
                    """
                    SELECT peer_name, stance, note
                    FROM persona_feelings
                    WHERE chat_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 8
                    """,
                    (chat_id,),
                )
                feel_rows = await cursor.fetchall()
            except aiosqlite.Error:
                logger.exception("Ошибка get_persona_pulse chat_id=%s", chat_id)
                raise

        return {
            "mood": (row["mood"] if row else None),
            "vibe": (row["vibe"] if row else None),
            "feelings": [
                {
                    "name": r["peer_name"],
                    "stance": r["stance"],
                    "note": r["note"] or "",
                }
                for r in feel_rows
            ],
        }

    async def save_persona_pulse(
        self,
        chat_id: int,
        *,
        mood: str,
        vibe: str,
        feelings: list[dict[str, Any]],
        expected_epoch: int | None = None,
    ) -> bool:
        """
        Сохранить пульс. Если expected_epoch задан и не совпал (после /reset) — no-op.
        Возвращает True если записали.
        """
        if expected_epoch is not None and self.pulse_epoch(chat_id) != expected_epoch:
            logger.debug(
                "save_persona_pulse skip chat=%s epoch %s!=%s",
                chat_id,
                expected_epoch,
                self.pulse_epoch(chat_id),
            )
            return False

        _valid_stances = frozenset(
            {"warm", "grudge", "crush", "annoyed", "neutral"}
        )
        mood = (mood or "дерзкий")[:64]
        vibe = (vibe or "")[:400]
        # Дедуп по имени (casefold), last wins
        by_name: dict[str, dict[str, Any]] = {}
        for f in feelings[:16]:
            name = str(f.get("name") or "").strip()[:64]
            if not name:
                continue
            stance = str(f.get("stance") or "neutral").strip().lower()[:16]
            if stance not in _valid_stances:
                stance = "neutral"
            by_name[name.casefold()] = {
                "name": name,
                "stance": stance,
                "note": str(f.get("note") or "")[:160],
            }
        clean = list(by_name.values())[:8]

        async with self._lock:
            if expected_epoch is not None and self.pulse_epoch(chat_id) != expected_epoch:
                return False
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                await self.connection.execute(
                    """
                    INSERT INTO persona_mood (chat_id, mood, vibe, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(chat_id) DO UPDATE SET
                        mood = excluded.mood,
                        vibe = excluded.vibe,
                        updated_at = datetime('now')
                    """,
                    (chat_id, mood, vibe),
                )
                await self.connection.execute(
                    "DELETE FROM persona_feelings WHERE chat_id = ?",
                    (chat_id,),
                )
                for f in clean:
                    await self.connection.execute(
                        """
                        INSERT INTO persona_feelings
                            (chat_id, peer_name, stance, note, updated_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(chat_id, peer_name) DO UPDATE SET
                            stance = excluded.stance,
                            note = excluded.note,
                            updated_at = datetime('now')
                        """,
                        (chat_id, f["name"], f["stance"], f["note"]),
                    )
                await self.connection.commit()
                return True
            except Exception:
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка save_persona_pulse chat_id=%s", chat_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
                raise

    async def count_proactive_since(self, since_utc: str) -> int:
        """Сколько проактивных постов с момента since_utc (UTC 'YYYY-MM-DD HH:MM:SS')."""
        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM proactive_posts
                    WHERE created_at >= ?
                    """,
                    (since_utc,),
                )
                row = await cursor.fetchone()
                return int(row["c"]) if row else 0
            except aiosqlite.Error:
                logger.exception("Ошибка count_proactive_since")
                raise

    async def try_reserve_proactive(
        self,
        chat_id: int,
        target_user_id: int,
        *,
        max_per_day: int,
        since_utc: str,
    ) -> int | None:
        """
        Атомарно зарезервировать слот проактива.
        Возвращает id строки или None, если лимит исчерпан.
        """
        if max_per_day <= 0:
            return None
        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                cursor = await self.connection.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM proactive_posts
                    WHERE created_at >= ?
                    """,
                    (since_utc,),
                )
                row = await cursor.fetchone()
                used = int(row["c"]) if row else 0
                if used >= max_per_day:
                    await self.connection.rollback()
                    return None
                cursor = await self.connection.execute(
                    """
                    INSERT INTO proactive_posts (chat_id, target_user_id)
                    VALUES (?, ?)
                    """,
                    (chat_id, target_user_id),
                )
                await self.connection.commit()
                return int(cursor.lastrowid) if cursor.lastrowid else None
            except Exception:
                await self._rollback_quiet()
                logger.exception(
                    "Ошибка try_reserve_proactive chat_id=%s", chat_id
                )
                raise
            except BaseException:
                await self._rollback_quiet()
                raise

    async def release_proactive(self, reservation_id: int) -> None:
        """Откатить резерв, если отправка не удалась."""
        async with self._lock:
            try:
                await self.connection.execute(
                    "DELETE FROM proactive_posts WHERE id = ?",
                    (reservation_id,),
                )
                await self.connection.commit()
            except aiosqlite.Error:
                logger.exception(
                    "Ошибка release_proactive id=%s", reservation_id
                )
                raise

    async def list_active_chat_ids(self) -> list[int]:
        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    """
                    SELECT DISTINCT chat_id
                    FROM chat_messages
                    ORDER BY chat_id
                    """
                )
                rows = await cursor.fetchall()
                return [int(r["chat_id"]) for r in rows]
            except aiosqlite.Error:
                logger.exception("Ошибка list_active_chat_ids")
                raise

    async def list_users_with_min_messages(
        self,
        chat_id: int,
        *,
        min_count: int = 10,
        exclude_sender_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if min_count < 1:
            return []
        async with self._lock:
            try:
                # Берём имя из самого свежего сообщения юзера (не MAX лексикографический)
                if exclude_sender_id is None:
                    cursor = await self.connection.execute(
                        """
                        SELECT m.sender_id,
                               (
                                   SELECT m2.sender_name
                                   FROM chat_messages m2
                                   WHERE m2.chat_id = m.chat_id
                                     AND m2.sender_id = m.sender_id
                                     AND m2.is_me = 0
                                   ORDER BY m2.id DESC
                                   LIMIT 1
                               ) AS sender_name,
                               COUNT(*) AS msg_count
                        FROM chat_messages m
                        WHERE m.chat_id = ?
                          AND m.is_me = 0
                          AND m.sender_id IS NOT NULL
                        GROUP BY m.sender_id
                        HAVING COUNT(*) >= ?
                        ORDER BY msg_count DESC
                        """,
                        (chat_id, min_count),
                    )
                else:
                    cursor = await self.connection.execute(
                        """
                        SELECT m.sender_id,
                               (
                                   SELECT m2.sender_name
                                   FROM chat_messages m2
                                   WHERE m2.chat_id = m.chat_id
                                     AND m2.sender_id = m.sender_id
                                     AND m2.is_me = 0
                                   ORDER BY m2.id DESC
                                   LIMIT 1
                               ) AS sender_name,
                               COUNT(*) AS msg_count
                        FROM chat_messages m
                        WHERE m.chat_id = ?
                          AND m.is_me = 0
                          AND m.sender_id IS NOT NULL
                          AND m.sender_id != ?
                        GROUP BY m.sender_id
                        HAVING COUNT(*) >= ?
                        ORDER BY msg_count DESC
                        """,
                        (chat_id, exclude_sender_id, min_count),
                    )
                rows = await cursor.fetchall()
            except aiosqlite.Error:
                logger.exception(
                    "Ошибка list_users_with_min_messages chat_id=%s", chat_id
                )
                raise

        return [
            {
                "sender_id": int(r["sender_id"]),
                "sender_name": (r["sender_name"] or "Кто-то").strip()
                or "Кто-то",
                "msg_count": int(r["msg_count"]),
            }
            for r in rows
        ]

    async def get_user_recent_texts(
        self,
        chat_id: int,
        sender_id: int,
        limit: int = 10,
    ) -> list[str]:
        if limit < 1:
            return []
        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    """
                    SELECT content
                    FROM (
                        SELECT content, id
                        FROM chat_messages
                        WHERE chat_id = ?
                          AND sender_id = ?
                          AND is_me = 0
                        ORDER BY id DESC
                        LIMIT ?
                    ) AS recent
                    ORDER BY id ASC
                    """,
                    (chat_id, sender_id, limit),
                )
                rows = await cursor.fetchall()
                return [str(r["content"]) for r in rows]
            except aiosqlite.Error:
                logger.exception(
                    "Ошибка get_user_recent_texts chat_id=%s sender=%s",
                    chat_id,
                    sender_id,
                )
                raise
