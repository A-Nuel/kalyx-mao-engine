import os

from src.persistence.config import Backend, load_persistence_config, require_production_postgres


def test_default_is_sqlite(monkeypatch):
    monkeypatch.delenv("KALYX_DATABASE_URL", raising=False)
    monkeypatch.setenv("KALYX_DB", "data/test.db")
    cfg = load_persistence_config()
    assert cfg.backend == Backend.SQLITE
    assert cfg.sqlite_path == "data/test.db"


def test_postgres_url_selects_postgres(monkeypatch):
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://kalyx:kalyx@localhost:5432/kalyx")
    cfg = load_persistence_config()
    assert cfg.backend == Backend.POSTGRES
    assert cfg.database_url.startswith("postgresql://")


def test_production_requires_postgres(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.delenv("KALYX_DATABASE_URL", raising=False)
    try:
        require_production_postgres()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "PostgreSQL" in str(exc)


def test_production_ok_with_postgres(monkeypatch):
    monkeypatch.setenv("KALYX_ENV", "production")
    monkeypatch.setenv("KALYX_DATABASE_URL", "postgresql://u:p@localhost/db")
    require_production_postgres()  # must not raise
