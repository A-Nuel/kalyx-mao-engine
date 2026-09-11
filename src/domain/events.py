import json
import hashlib
from datetime import datetime
from typing import Any, Dict
from pydantic import BaseModel, Field

def canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)

def compute_payload_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

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
