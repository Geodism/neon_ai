from __future__ import annotations

import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from neon_ai.config import get_required_env, load_neon_env


_POOL_LOCK = threading.Lock()
_CONNECTION_POOL: ThreadedConnectionPool | None = None
_POOL_MIN_CONN = 1
_POOL_MAX_CONN = 5


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


class _PooledConnection:
    def __init__(self, pool: ThreadedConnectionPool, connection) -> None:
        self._pool = pool
        self._connection = connection
        self._returned = False

    @property
    def closed(self) -> int:
        if self._returned:
            return 1
        return self._connection.closed

    def _ensure_open(self) -> None:
        if self._returned:
            raise psycopg2.InterfaceError("connection already closed")

    def cursor(self, *args, **kwargs):
        self._ensure_open()
        if "cursor_factory" not in kwargs:
            kwargs["cursor_factory"] = RealDictCursor
        return self._connection.cursor(*args, **kwargs)

    def close(self) -> None:
        if self._returned:
            return
        try:
            try:
                self._connection.rollback()
            except psycopg2.Error:
                pass
            self._pool.putconn(self._connection)
        finally:
            self._returned = True

    def __getattr__(self, name):
        self._ensure_open()
        return getattr(self._connection, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _get_connection_pool() -> ThreadedConnectionPool:
    global _CONNECTION_POOL
    if _CONNECTION_POOL is not None:
        return _CONNECTION_POOL

    with _POOL_LOCK:
        if _CONNECTION_POOL is None:
            load_neon_env()
            db_url = get_required_env("DB_URL")
            _CONNECTION_POOL = ThreadedConnectionPool(
                minconn=_POOL_MIN_CONN,
                maxconn=_POOL_MAX_CONN,
                dsn=db_url,
            )
    return _CONNECTION_POOL


def get_connection():
    started_at = time.perf_counter()
    try:
        pool = _get_connection_pool()
        return _PooledConnection(pool, pool.getconn())
    finally:
        _perf_log("db", "get_connection", started_at)


def close_all_connections() -> None:
    global _CONNECTION_POOL
    with _POOL_LOCK:
        if _CONNECTION_POOL is not None:
            _CONNECTION_POOL.closeall()
            _CONNECTION_POOL = None
