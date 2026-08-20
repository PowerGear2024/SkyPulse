"""
Расписание и пауза, пока живой владелец сидит в Telegram.

- Работа только в WORK_HOURS (по умолчанию 8:00–18:00).
- Если владелец сам пишет с аккаунта — бот молчит, пока тот не выйдет
  (offline / recently) или не истечёт idle-таймаут.
- Исходящие сообщения бота помечаются через bot_outbound(), чтобы
  не принять их за ручной диалог владельца.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, time as dt_time
from typing import Iterator
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


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
        # end_hour=18 → до 18:00 (не включая 18:00)
        self._end_hour = work_end_hour
        self._idle_resume_sec = idle_resume_sec

        self._bot_depth = 0
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

    def is_bot_outbound(self) -> bool:
        return self._bot_depth > 0

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
        # [start, end) — например 8:00 включительно … 18:00 исключительно
        minutes = now.hour * 60 + now.minute
        start_m = self._start.hour * 60
        end_m = self._end_hour * 60
        if start_m < end_m:
            return start_m <= minutes < end_m
        # На случай ночного окна (не используем по умолчанию)
        return minutes >= start_m or minutes < end_m

    def _refresh_idle(self) -> None:
        if not self._owner_paused or self._last_manual_mono <= 0:
            return
        idle = time.monotonic() - self._last_manual_mono
        if idle >= self._idle_resume_sec:
            self.mark_owner_left(reason=f"idle {int(idle)}s")

    def block_reason(self) -> str | None:
        """Почему нельзя отвечать, или None если можно."""
        if not self._within_work_hours():
            return "off_hours"
        self._refresh_idle()
        if self._owner_paused:
            return "owner_present"
        return None

    def can_act(self) -> bool:
        return self.block_reason() is None
