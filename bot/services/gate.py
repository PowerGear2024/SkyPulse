"""
Ограничители: per-user lock и простой rate-limit.

Нужны, чтобы:
  - параллельные сообщения одного юзера не перемешивали историю;
  - спам не сжигал бюджет LLM;
  - словарь локов не рос бесконечно.
"""

from __future__ import annotations

import asyncio
import time


class UserGate:
    """Пер-пользовательский замок + минимальный интервал между запросами."""

    def __init__(
        self,
        min_interval_sec: float = 1.5,
        idle_ttl_sec: float = 3600.0,
    ) -> None:
        self._min_interval = min_interval_sec
        self._idle_ttl = idle_ttl_sec
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_ts: dict[int, float] = {}

    def lock_for(self, user_id: int) -> asyncio.Lock:
        """Вернуть (или создать) lock для конкретного telegram_id."""
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def is_busy(self, user_id: int) -> bool:
        """True, если по этому юзеру уже идёт обработка."""
        lock = self._locks.get(user_id)
        return lock.locked() if lock is not None else False

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
        # Ленивая уборка: не на каждый вызов, а когда накопилось много ключей
        if len(self._last_ts) > 512:
            self._prune_unlocked()

    def _prune_unlocked(self) -> None:
        """Удалить простаивающие записи без активного lock."""
        now = time.monotonic()
        stale = [
            uid
            for uid, ts in self._last_ts.items()
            if now - ts > self._idle_ttl and not self.is_busy(uid)
        ]
        for uid in stale:
            self._last_ts.pop(uid, None)
            lock = self._locks.get(uid)
            if lock is not None and not lock.locked():
                self._locks.pop(uid, None)
