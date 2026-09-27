"""SQLite-side schema contract for fields that are BOOLEAN in production PostgreSQL."""

from src.api.product_plane import ensure_product_schema
from src.persistence.database import Database


def _column_type(db, table: str, column: str) -> str:
    rows = db.conn.execute(f"PRAGMA table_info({table})").fetchall()
    for row in rows:
        if row["name"] == column:
            return str(row["type"]).upper()
    raise AssertionError(f"{table}.{column} not found")


def test_boolean_schema_contract_matches_postgres():
    db = Database(":memory:")
    try:
        ensure_product_schema(db)

        expected = {
            ("principals", "active"): "BOOLEAN",
            ("tenant_memberships", "active"): "BOOLEAN",
            ("wallet_challenges", "consumed"): "BOOLEAN",
            ("product_sessions", "revoked"): "BOOLEAN",
            ("agent_credentials", "revoked"): "BOOLEAN",
            ("organisation_policies", "enabled"): "BOOLEAN",
        }
        for (table, column), expected_type in expected.items():
            assert _column_type(db, table, column) == expected_type

        # SQLite accepts TRUE/FALSE for BOOLEAN columns, so the same SQL
        # predicates used by PostgreSQL remain valid on the local backend.
        db.conn.execute(
            "INSERT INTO tenants (id,name,status,created_at) VALUES (?,?,?,?)",
            ("tenant-bool-test", "Boolean Test", "active", "2026-09-27T00:00:00"),
        )
        db.conn.execute(
            "INSERT INTO principals (id,name,active,created_at) VALUES (?,?,?,?)",
            ("principal-bool-test", "Boolean Test", True, "2026-09-27T00:00:00"),
        )
        db.conn.execute(
            "INSERT INTO tenant_memberships (principal_id,tenant_id,role,active,created_at) VALUES (?,?,?,?,?)",
            ("principal-bool-test", "tenant-bool-test", "owner", True, "2026-09-27T00:00:00"),
        )
        row = db.conn.execute(
            "SELECT p.active AS principal_active, m.active AS membership_active "
            "FROM principals p JOIN tenant_memberships m ON m.principal_id=p.id "
            "WHERE p.id=? AND m.active=TRUE",
            ("principal-bool-test",),
        ).fetchone()
        assert row["principal_active"] == 1
        assert row["membership_active"] == 1
    finally:
        db.close()
