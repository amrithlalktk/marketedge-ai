"""Redis cache with an in-process fallback (used in tests / when Redis is down).
Values are stored as JSON."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from .config import get_settings

log = logging.getLogger(__name__)


class _Memory:
    def __init__(self):
        self._d, self._lock = {}, threading.Lock()

    def get(self, k):
        with self._lock:
            v = self._d.get(k)
            if v is None:
                return None
            if v[1] and v[1] < time.time():
                del self._d[k]
                return None
            return v[0]

    def set(self, k, v, ex=None):
        with self._lock:
            self._d[k] = (v, time.time() + ex if ex else None)

    def delete(self, *keys):
        with self._lock:
            for k in keys:
                self._d.pop(k, None)

    def incr(self, k, ex):
        with self._lock:
            v = self._d.get(k)
            if v is None or (v[1] and v[1] < time.time()):
                self._d[k] = (1, time.time() + ex)
                return 1
            self._d[k] = (v[0] + 1, v[1])
            return v[0] + 1

    def scan_delete(self, prefix):
        with self._lock:
            for k in [k for k in self._d if k.startswith(prefix)]:
                del self._d[k]


class Cache:
    def __init__(self):
        self._redis = None
        self._mem = _Memory()
        url = get_settings().redis_url
        if url and get_settings().environment != "test":
            try:
                import redis

                self._redis = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=2)
                self._redis.ping()
            except Exception as exc:  # pragma: no cover - depends on environment
                log.warning("Redis unavailable (%s); using in-process cache", exc)
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def _call(self, name, *a, **kw):
        if self._redis is not None:
            try:
                return getattr(self._redis, name)(*a, **kw)
            except Exception as exc:  # pragma: no cover
                log.warning("Redis error %s; falling back to memory", exc)
        return getattr(self._mem, name)(*a, **kw)

    def get_json(self, key: str) -> Optional[Any]:
        v = self._call("get", key)
        return json.loads(v) if v is not None else None

    def set_json(self, key: str, value: Any, ttl: int = 300) -> None:
        self._call("set", key, json.dumps(value, default=str), ex=ttl)

    def get_frame(self, key: str):
        """DataFrames are cached as JSON records (never pickle: cache contents are not trusted code)."""
        import pandas as pd

        rows = self.get_json(key)
        return pd.DataFrame(rows) if rows is not None else None

    def set_frame(self, key: str, df, ttl: int = 3600) -> None:
        self._call("set", key, df.to_json(orient="records", date_format="iso"), ex=ttl)

    def incr_window(self, key: str, window_s: int) -> int:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, window_s, nx=True)
                return int(pipe.execute()[0])
            except Exception:  # pragma: no cover  # noqa: S110  # nosec B110
                pass
        return self._mem.incr(key, window_s)  # Redis unavailable → in-process fallback

    def invalidate_prefix(self, prefix: str) -> None:
        if self._redis is not None:
            try:
                for k in self._redis.scan_iter(match=prefix + "*", count=500):
                    self._redis.delete(k)
            except Exception:  # pragma: no cover  # noqa: S110  # nosec B110
                pass
        self._mem.scan_delete(prefix)

    def ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            return bool(self._redis.ping())
        except Exception:
            return False


_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
