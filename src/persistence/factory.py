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


def create_scoped_ledger(
    db: Any,
    tenant_id: str,
    organisation_id: str,
    initial_treasury: int = 0,
) -> Any:
    """Create an organisation-scoped, tenant-isolated ledger for the active backend."""
    try:
        from src.persistence.postgres_db import PostgresDatabase
        is_pg = isinstance(db, PostgresDatabase)
    except Exception:
        is_pg = False

    from src.tenancy.ledger import TenantScopedLedger
    from src.tenancy.organisation_ledger import OrganisationScopedLedger

    if is_pg:
        from src.persistence.postgres_ledger import PostgresLedger
        raw_ledger = PostgresLedger(db, initial_treasury=0, tenant_id=tenant_id)
    else:
        from src.security.atomic_ledger import AtomicSqliteLedger
        raw_ledger = AtomicSqliteLedger(db, initial_treasury=0)

    tenant_ledger = TenantScopedLedger(raw_ledger, tenant_id=tenant_id, initial_treasury=0)
    return OrganisationScopedLedger(tenant_ledger, organisation_id=organisation_id, initial_treasury=initial_treasury)


def create_work_order_repo(db: Any) -> Any:
    """Create a WorkOrderRepository bound to the database or connection."""
    from src.persistence.work_order_repository import WorkOrderRepository
    return WorkOrderRepository(db)


def create_marketplace_repo(db: Any) -> Any:
    """Create a MarketplaceRepository bound to the database or connection."""
    from src.persistence.marketplace_repository import MarketplaceRepository
    return MarketplaceRepository(db)


