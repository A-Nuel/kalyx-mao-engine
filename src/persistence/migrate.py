"""Ordered PostgreSQL migration runner.

SQLite continues to use Database._init_schema for local/demo.
Production schema evolution must go through this path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


def migrations_dir() -> Path:
    # repo_root/migrations/postgres
    here = Path(__file__).resolve()
    return here.parents[2] / "migrations" / "postgres"


def list_migration_files() -> List[Path]:
    d = migrations_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.sql") if p.is_file())


def applied_versions(conn) -> set:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    except Exception:
        return set()
    out = set()
    for r in rows:
        if hasattr(r, "keys"):
            out.add(r["version"] if "version" in r.keys() else r[0])
        else:
            out.add(r[0])
    return out


def run_migrations(conn) -> List[str]:
    """Apply pending *.sql migrations in lexical order. Returns applied versions."""
    applied: List[str] = []
    # Ensure tracking table exists even before first migration content runs.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )
    conn.commit()
    already = applied_versions(conn)
    for path in list_migration_files():
        version = path.stem
        if version in already:
            continue
        sql = path.read_text(encoding="utf-8")
        # Migrations may contain their own BEGIN/COMMIT; execute as a script.
        conn.execute(sql)
        # Some drivers require explicit commit after multi-statement scripts.
        try:
            conn.commit()
        except Exception:
            pass
        # Record if migration body did not insert itself.
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
            (version,),
        )
        conn.commit()
        applied.append(version)
    return applied
