"""
Ограничители: per-user слот обработки + rate-limit.

В asyncio один поток — set.add/check без await атомарны:
гонка «проверил → await → занял» исключена без asyncio.Lock.
"""

from __future__ import annotations

import time


class UserGate:
    """Один активный запрос на пользователя + минимальный интервал."""

    def __init__(
        self,
        min_interval_sec: float = 1.5,
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

    def try_begin(self, user_id: int) -> bool:
        """
        Занять слот обработки. False — уже обрабатываем этого юзера.

        Без очереди: второй апдейт сразу получает отказ, не ждёт lock.
        """
        if user_id in self._inflight:
            return False
        self._inflight.add(user_id)
        return True

    def end(self, user_id: int) -> None:
        """Освободить слот (вызывать в finally)."""
        self._inflight.discard(user_id)

    def seconds_until_allowed(self, user_id: int) -> float:
        """Сколько секунд ждать до следующего разрешённого запроса (0 = можно)."""
        last = self._last_ts.get(user_id)
        if last is None:
            return 0.0
        remaining = self._min_interval - (time.monotonic() - last)
        return remaining if remaining > 0 else 0.0

    def mark_used(self, user_id: int) -> None:
        """Зафиксировать момент запроса (в т.ч. неудачного — против спама API)."""
        self._last_ts[user_id] = time.monotonic()
        if len(self._last_ts) > 512:
            self._prune()

    def _prune(self) -> None:
        """Удалить простаивающие timestamp'ы (слот inflight не трогаем)."""
        now = time.monotonic()
        stale = [
            uid
            for uid, ts in self._last_ts.items()
            if now - ts > self._idle_ttl and uid not in self._inflight
        ]
        for uid in stale:
            self._last_ts.pop(uid, None)
