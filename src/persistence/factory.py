"""Database factory — domain/application code should prefer this entry point."""

from __future__ import annotations

from typing import Any, Union

from src.persistence.config import Backend, PersistenceConfig, load_persistence_config
from src.persistence.database import Database as SqliteDatabase


def create_database(config: PersistenceConfig | None = None) -> Any:
    """Create a Database-compatible object for the configured backend.

    Returns:
      - SqliteDatabase for local/demo/tests
      - PostgresDatabase when KALYX_DATABASE_URL is postgresql://...

    Both expose `.conn` and `.close()` for the transitional period while
    repositories still use a connection-centric API.
    """
    cfg = config or load_persistence_config()
    if cfg.backend == Backend.POSTGRES:
        from src.persistence.postgres_db import PostgresDatabase

        if not cfg.database_url:
            raise RuntimeError("PostgreSQL backend selected but KALYX_DATABASE_URL is empty")
        return PostgresDatabase(
            dsn=cfg.database_url,
            pool_min=cfg.pool_min,
            pool_max=cfg.pool_max,
        )
    return SqliteDatabase(cfg.sqlite_path)


def create_sqlite(path: str = ":memory:") -> SqliteDatabase:
    """Explicit SQLite constructor for tests and deterministic demos."""
    return SqliteDatabase(path)
