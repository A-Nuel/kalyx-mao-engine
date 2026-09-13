"""Persistence backend configuration.

SQLite remains the default for local development, deterministic tests, and demo.
PostgreSQL is selected only when KALYX_DATABASE_URL is an explicit postgres URL.

Production (KALYX_ENV=production) must not silently fall back to SQLite when a
Postgres URL is expected but missing — callers that require production must
fail closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Backend(str, Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


@dataclass(frozen=True)
class PersistenceConfig:
    backend: Backend
    sqlite_path: str = "data/kalyx.db"
    database_url: Optional[str] = None
    pool_min: int = 1
    pool_max: int = 10


def _is_postgres_url(url: str) -> bool:
    u = url.strip().lower()
    return u.startswith("postgres://") or u.startswith("postgresql://")


def load_persistence_config() -> PersistenceConfig:
    """Resolve persistence backend from environment.

    Priority:
      1. KALYX_DATABASE_URL if it is a postgres URL -> PostgreSQL
      2. Otherwise SQLite via KALYX_DB (default data/kalyx.db)
    """
    url = os.getenv("KALYX_DATABASE_URL", "").strip()
    sqlite_path = os.getenv("KALYX_DB", "data/kalyx.db").strip() or "data/kalyx.db"
    pool_min = int(os.getenv("KALYX_DB_POOL_MIN", "1"))
    pool_max = int(os.getenv("KALYX_DB_POOL_MAX", "10"))

    if url and _is_postgres_url(url):
        return PersistenceConfig(
            backend=Backend.POSTGRES,
            sqlite_path=sqlite_path,
            database_url=url,
            pool_min=max(1, pool_min),
            pool_max=max(1, pool_max),
        )
    return PersistenceConfig(
        backend=Backend.SQLITE,
        sqlite_path=sqlite_path,
        database_url=url or None,
        pool_min=pool_min,
        pool_max=pool_max,
    )


def require_production_postgres() -> None:
    """Fail closed in production when Postgres is required but not configured."""
    env = os.getenv("KALYX_ENV", "demo").strip().lower()
    if env != "production":
        return
    cfg = load_persistence_config()
    if cfg.backend != Backend.POSTGRES:
        raise RuntimeError(
            "KALYX_ENV=production requires KALYX_DATABASE_URL pointing at PostgreSQL. "
            "SQLite is not permitted as a silent production fallback."
        )
