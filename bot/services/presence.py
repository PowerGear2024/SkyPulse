"""
Расписание и пауза, пока живой владелец сидит в Telegram.

- Работа только в WORK_HOURS (по умолчанию 8:00–18:00).
- Если владелец сам пишет с аккаунта — бот молчит, пока тот не выйдет
  (offline / recently) или не истечёт idle-таймаут.
- Исходящие бота: bot_outbound() + grace + msg_id, чтобы не принять за ручные.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, time as dt_time
from typing import Iterator
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_BOT_GRACE_SEC = 4.0
_BOT_MSG_TTL_SEC = 30.0
_MAX_BOT_MSGS = 256


class OwnerGuard:
    def __init__(
        self,
        *,
        timezone: str = "Europe/Kyiv",
        work_start_hour: int = 8,
        work_end_hour: int = 18,
        idle_resume_sec: float = 600.0,
    ) -> None:
        try:
            self._tz = ZoneInfo(timezone)
        except Exception as exc:
            raise ValueError(f"Некорректный TIMEZONE: {timezone}") from exc
        if not 0 <= work_start_hour <= 23:
            raise ValueError("WORK_HOURS_START: 0–23")
        if not 1 <= work_end_hour <= 24:
            raise ValueError("WORK_HOURS_END: 1–24")
        if work_start_hour == work_end_hour:
            raise ValueError("WORK_HOURS_START и WORK_HOURS_END не должны совпадать")
        if idle_resume_sec < 30:
            raise ValueError("OWNER_IDLE_RESUME_SEC должна быть >= 30")

        self._start = dt_time(work_start_hour, 0)
        self._end_hour = work_end_hour
        self._idle_resume_sec = idle_resume_sec

        self._bot_depth = 0
        self._bot_grace_until = 0.0
        self._bot_msg_ids: dict[tuple[int, int], float] = {}
        self._owner_paused = False
        self._last_manual_mono = 0.0

        logger.info(
            "OwnerGuard: часы %02d:00–%02d:00 (%s), idle-resume=%ss",
            work_start_hour,
            work_end_hour,
            timezone,
            int(idle_resume_sec),
        )

    @contextmanager
    def bot_outbound(self) -> Iterator[None]:
        """Пометить исходящее как от бота (не триггерить паузу)."""
        self._bot_depth += 1
        try:
            yield
        finally:
            self._bot_depth = max(0, self._bot_depth - 1)
            # Grace: UpdateNewMessage может прийти после выхода из send_message
            self._bot_grace_until = time.monotonic() + _BOT_GRACE_SEC

    def note_bot_message(self, chat_id: int, msg_id: int) -> None:
        now = time.monotonic()
        self._bot_msg_ids[(int(chat_id), int(msg_id))] = now
        if len(self._bot_msg_ids) > _MAX_BOT_MSGS:
            stale = [
                k
                for k, ts in self._bot_msg_ids.items()
                if now - ts > _BOT_MSG_TTL_SEC
            ]
            for k in stale:
                self._bot_msg_ids.pop(k, None)
            while len(self._bot_msg_ids) > _MAX_BOT_MSGS:
                oldest = min(self._bot_msg_ids.items(), key=lambda kv: kv[1])[0]
                self._bot_msg_ids.pop(oldest, None)

    def is_bot_message(self, chat_id: int, msg_id: int | None) -> bool:
        if msg_id is None:
            return False
        key = (int(chat_id), int(msg_id))
        ts = self._bot_msg_ids.get(key)
        if ts is None:
            return False
        if time.monotonic() - ts > _BOT_MSG_TTL_SEC:
            self._bot_msg_ids.pop(key, None)
            return False
        return True

    def is_bot_outbound(self) -> bool:
        if self._bot_depth > 0:
            return True
        return time.monotonic() < self._bot_grace_until

    def mark_owner_active(self, *, where: str = "") -> None:
        """Владелец сам написал / начал диалог — ставим паузу боту."""
        was = self._owner_paused
        self._owner_paused = True
        self._last_manual_mono = time.monotonic()
        if not was:
            logger.info(
                "Пауза бота: владелец активен%s",
                f" ({where})" if where else "",
            )

    def mark_owner_left(self, *, reason: str = "offline") -> None:
        if not self._owner_paused:
            return
        self._owner_paused = False
        self._last_manual_mono = 0.0
        logger.info("Пауза снята: владелец вышел (%s)", reason)

    def _within_work_hours(self) -> bool:
        now = datetime.now(self._tz)
        minutes = now.hour * 60 + now.minute
        start_m = self._start.hour * 60
        end_m = self._end_hour * 60
        if start_m < end_m:
            return start_m <= minutes < end_m
        return minutes >= start_m or minutes < end_m

    def _refresh_idle(self) -> None:
        if not self._owner_paused or self._last_manual_mono <= 0:
            return
        idle = time.monotonic() - self._last_manual_mono
        if idle >= self._idle_resume_sec:
            self.mark_owner_left(reason=f"idle {int(idle)}s")

    def block_reason(self) -> str | None:
        if not self._within_work_hours():
            return "off_hours"
        self._refresh_idle()
        if self._owner_paused:
            return "owner_present"
        return None

    def can_act(self) -> bool:
        return self.block_reason() is None
