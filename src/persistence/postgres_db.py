"""PostgreSQL database adapter with connection pooling.

Exposes a transitional `.conn` compatible surface so existing repositories can
be migrated incrementally. Prefer create_database() from factory.py.
"""

from __future__ import annotations

from typing import Any, Optional

from src.persistence.migrate import run_migrations


class _DictRow(dict):
    """Minimal row that supports both index and key access like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(super().values())[key]
        return super().__getitem__(key)

    def keys(self):
        return super().keys()


class _PgConnectionProxy:
    """Thin wrapper so callers can use ? placeholders and .execute like SQLite."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Any = None):
        # Translate SQLite-style ? placeholders to %s for psycopg.
        converted = sql.replace("?", "%s")
        if params is None:
            cur = self._raw.execute(converted)
        else:
            cur = self._raw.execute(converted, params)
        return _PgCursorProxy(cur)

    def cursor(self):
        # SQLite-style `conn.cursor()` idiom, used throughout
        # src/persistence/repositories.py. Returns a fresh psycopg cursor
        # wrapped so its own .execute() also gets ? -> %s translation and
        # its .fetchone()/.fetchall() return SQLite-Row-compatible dicts.
        return _PgCursorProxy(self._raw.cursor())

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        return False


class _PgCursorProxy:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params: Any = None):
        # Same ? -> %s translation as _PgConnectionProxy.execute, so code
        # written as `cur = conn.cursor(); cur.execute(...)` (the SQLite
        # idiom used throughout src/persistence/repositories.py) works
        # unmodified against Postgres too.
        converted = sql.replace("?", "%s")
        if params is None:
            self._cur.execute(converted)
        else:
            self._cur.execute(converted, params)
        return self

    @property
    def rowcount(self) -> int:
        """Expose psycopg rowcount through the SQLite-compatible cursor proxy."""
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if hasattr(row, "keys"):
            return _DictRow({k: row[k] for k in row.keys()})
        return row

    def fetchall(self):
        rows = self._cur.fetchall()
        out = []
        for row in rows:
            if hasattr(row, "keys"):
                out.append(_DictRow({k: row[k] for k in row.keys()}))
            else:
                out.append(row)
        return out

    def __iter__(self):
        return iter(self.fetchall())


import threading

_SHARED_POOLS: dict[str, Any] = {}
_SHARED_POOLS_LOCK = threading.Lock()
_MIGRATED_DSNS: set[str] = set()


def close_all_pools() -> None:
    """Close all shared connection pools (for server shutdown / test teardown)."""
    with _SHARED_POOLS_LOCK:
        for pool in _SHARED_POOLS.values():
            try:
                pool.close()
            except Exception:
                pass
        _SHARED_POOLS.clear()
        _MIGRATED_DSNS.clear()


class PostgresDatabase:
    """Pooled PostgreSQL backend.

    Attributes:
        conn: a connection proxy borrowed from the pool (held for the life of
              this Database instance — suitable for request-scoped usage).
    """

    def __init__(
        self,
        dsn: str,
        pool_min: int = 1,
        pool_max: int = 10,
        run_migrate: bool = True,
        shared_pool: bool = True,
    ):
        try:
            from psycopg_pool import ConnectionPool
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL backend requires psycopg[binary,pool]. "
                "Install with: pip install 'psycopg[binary,pool]'"
            ) from exc

        self.dsn = dsn
        self.shared_pool = shared_pool

        if shared_pool:
            with _SHARED_POOLS_LOCK:
                if dsn not in _SHARED_POOLS or _SHARED_POOLS[dsn].closed:
                    _SHARED_POOLS[dsn] = ConnectionPool(
                        conninfo=dsn,
                        min_size=pool_min,
                        max_size=pool_max,
                        kwargs={"row_factory": dict_row, "autocommit": False},
                        open=True,
                    )
                self._pool = _SHARED_POOLS[dsn]
        else:
            self._pool = ConnectionPool(
                conninfo=dsn,
                min_size=pool_min,
                max_size=pool_max,
                kwargs={"row_factory": dict_row, "autocommit": False},
                open=True,
            )

        # Borrow one connection for this Database instance (transitional API).
        self._raw = self._pool.getconn()
        self.conn = _PgConnectionProxy(self._raw)

        if run_migrate:
            with _SHARED_POOLS_LOCK:
                needs_migrate = dsn not in _MIGRATED_DSNS
            if needs_migrate:
                run_migrations(self.conn)
                with _SHARED_POOLS_LOCK:
                    _MIGRATED_DSNS.add(dsn)

    def close(self) -> None:
        if self._raw is not None:
            # Cleanly roll back any open/uncommitted transaction (e.g. from read-only SELECTs)
            # to prevent the psycopg_pool warning: "rolling back returned connection ... INTRANS"
            try:
                if hasattr(self._raw, "info") and getattr(self._raw.info, "transaction_status", None) is not None:
                    status = self._raw.info.transaction_status
                    if status == 2 or getattr(status, "name", "") == "INTRANS":
                        self._raw.rollback()
            except Exception:
                pass

            try:
                self._pool.putconn(self._raw)
            except Exception:
                try:
                    self._raw.close()
                except Exception:
                    pass
            self._raw = None

        if not self.shared_pool and self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
            self._pool = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
