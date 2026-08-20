"""
Ограничители: per-user lock и простой rate-limit.

Нужны, чтобы:
  - параллельные сообщения одного юзера не перемешивали историю;
  - спам не сжигал бюджет LLM.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class UserGate:
    """Пер-пользовательский замок + минимальный интервал между запросами."""

    def __init__(self, min_interval_sec: float = 1.5) -> None:
        self._min_interval = min_interval_sec
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_ts: dict[int, float] = {}

    def lock_for(self, user_id: int) -> asyncio.Lock:
        """Вернуть (или создать) lock для конкретного telegram_id."""
        return self._locks[user_id]

    def seconds_until_allowed(self, user_id: int) -> float:
        """Сколько секунд ждать до следующего разрешённого запроса (0 = можно)."""
        last = self._last_ts.get(user_id)
        if last is None:
            return 0.0
        elapsed = time.monotonic() - last
        remaining = self._min_interval - elapsed
        return remaining if remaining > 0 else 0.0

    def mark_used(self, user_id: int) -> None:
        """Зафиксировать момент успешного/принятого запроса."""
        self._last_ts[user_id] = time.monotonic()
