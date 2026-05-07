"""TTL in-memory cache для Brave Search results.

Минимальный самодельный кэш — без cachetools зависимости. Ключ —
любой Hashable (мы используем кортеж `(query, freshness, lang)`).
TTL=1ч по умолчанию. Eviction: при переполнении выкидываем самую
раннюю expiry-запись.

Не thread-safe для глубокой нагрузки, но threading.Lock даёт корректность
на FastAPI/sync-обработчиках. Для multi-worker prod не подходит — кэш
in-process. Для MVP/демо достаточно.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Hashable, Optional


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600, max_size: int = 256):
        self._ttl = ttl_seconds
        self._max = max_size
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._lock = Lock()

    def _now(self) -> float:
        return time.monotonic()

    def get(self, key: Hashable) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires < self._now():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self._max and key not in self._data:
                # Простой evict: выкидываем запись с самой ранней expiry.
                oldest_key = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest_key, None)
            self._data[key] = (self._now() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


__all__ = ["TTLCache"]
