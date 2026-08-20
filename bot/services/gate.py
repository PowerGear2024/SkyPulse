"""
Ограничители: один активный ответ на чат + rate-limit.
"""

from __future__ import annotations

import time


class ChatGate:
    """Слот обработки + минимальный интервал на chat_id."""

    def __init__(
        self,
        min_interval_sec: float = 2.0,
        idle_ttl_sec: float = 3600.0,
    ) -> None:
        if min_interval_sec < 0:
            raise ValueError("min_interval_sec должен быть >= 0")
        if idle_ttl_sec < 0:
            raise ValueError("idle_ttl_sec должен быть >= 0")
        self._min_interval = min_interval_sec
        self._idle_ttl = idle_ttl_sec
        self._inflight: set[int] = set()
        self._last_ts: dict[int, float] = {}

    def try_begin(self, chat_id: int) -> bool:
        if chat_id in self._inflight:
            return False
        self._inflight.add(chat_id)
        return True

    def is_busy(self, chat_id: int) -> bool:
        """Чат занят ответом или ещё в rate-limit."""
        return chat_id in self._inflight or self.seconds_until_allowed(chat_id) > 0

    def end(self, chat_id: int) -> None:
        self._inflight.discard(chat_id)

    def seconds_until_allowed(self, chat_id: int) -> float:
        last = self._last_ts.get(chat_id)
        if last is None:
            return 0.0
        remaining = self._min_interval - (time.monotonic() - last)
        return remaining if remaining > 0 else 0.0

    def mark_used(self, chat_id: int) -> None:
        self._last_ts[chat_id] = time.monotonic()
        if len(self._last_ts) > 512:
            self._prune()

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [
            cid
            for cid, ts in self._last_ts.items()
            if now - ts > self._idle_ttl and cid not in self._inflight
        ]
        for cid in stale:
            self._last_ts.pop(cid, None)
