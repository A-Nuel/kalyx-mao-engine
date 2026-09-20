import json
import hashlib
from datetime import datetime
from typing import Any, Dict
from pydantic import BaseModel, Field

def canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)

def compute_payload_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

def canonical_timestamp_iso(value: datetime) -> str:
    """Canonical ISO string used for event-hash computation, stable across
    SQLite (which stores timestamps as TEXT and returns exactly the string
    that was written) and Postgres (whose TIMESTAMPTZ columns return a
    timezone-AWARE datetime via psycopg even when a naive
    datetime.utcnow() was written -- e.g. Postgres assumes the session
    timezone, typically UTC, and attaches +00:00 on read).

    A naive value's .isoformat() and that same instant's timezone-aware
    .isoformat() differ by exactly the trailing offset
    ("...123456" vs "...123456+00:00"), which produces a different
    SHA-256 input and therefore a different hash for an event that was
    never actually tampered with -- this was the root cause of
    "Event hash mismatch at sequence N" / TamperedAuditLogError firing on
    every single Postgres deployment, 100% reproducibly, the moment any
    event was written and then read back.

    Stripping tzinfo before formatting makes the string identical
    regardless of which backend round-tripped the value, as long as both
    sides represent the same wall-clock instant (which they do -- Postgres
    is not changing the actual moment, only how it reports the offset).
    """
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return value.isoformat()

def compute_event_hash(
    sequence_id: int,
    timestamp_iso: str,
    actor_id: str,
    event_type: str,
    entity_id: str,
    payload_hash: str,
    previous_event_hash: str
) -> str:
    serialized = f"{sequence_id}|{timestamp_iso}|{actor_id}|{event_type}|{entity_id}|{payload_hash}|{previous_event_hash}"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

class AuditEvent(BaseModel):
    sequence_id: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor_id: str
    event_type: str
    entity_id: str
    payload: Dict[str, Any]
    payload_hash: str
    previous_event_hash: str
    event_hash: str
