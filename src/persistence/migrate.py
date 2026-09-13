"""Ordered PostgreSQL migration runner.

SQLite continues to use Database._init_schema for local/demo.
Production schema evolution must go through this path.
"""

from __future__ import annotations

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


def _execute_script(conn, sql: str) -> None:
    """Execute a multi-statement SQL script.

    Supports both the Postgres connection proxy and a raw psycopg connection.
    """
    # Prefer psycopg Connection.execute with multiple statements when available.
    raw = getattr(conn, "_raw", conn)
    # Strip line comments for naive splitting while preserving dollar-quotes simply
    # by relying on statement terminators at the end of lines.
    statements = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buf).strip())
            buf = []
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            statements.append(tail)

    for stmt in statements:
        if not stmt or stmt == ";":
            continue
        # Use proxy.execute when present so placeholder translation still works.
        if hasattr(conn, "execute") and not isinstance(conn, type(raw)):
            conn.execute(stmt)
        else:
            raw.execute(stmt)


def run_migrations(conn) -> List[str]:
    """Apply pending *.sql migrations in lexical order. Returns applied versions."""
    applied: List[str] = []
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
        _execute_script(conn, sql)
        try:
            conn.commit()
        except Exception:
            pass
        # Ensure version row exists even if the migration body already inserted it.
        try:
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
                (version,),
            )
            conn.commit()
        except Exception:
            # Fallback for SQLite-style ? if a test ever points here
            try:
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?) ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
                conn.commit()
            except Exception:
                pass
        applied.append(version)
    return applied
