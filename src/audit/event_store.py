from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from src.domain.events import AuditEvent, compute_payload_hash, compute_event_hash
from src.domain.exceptions import TamperedAuditLogError

GENESIS_PREVIOUS_HASH = "0" * 64

class AppendOnlyEventStore:
    """
    Append-only event store with SHA-256 cryptographic hash-chaining.
    Guarantees tamper-evidence for all consequential system actions.
    """
    def __init__(self):
        self._events: List[AuditEvent] = []

    def append_event(
        self,
        actor_id: str,
        event_type: str,
        entity_id: str,
        payload: Dict[str, Any]
    ) -> AuditEvent:
        sequence_id = len(self._events) + 1
        timestamp = datetime.utcnow()
        timestamp_iso = timestamp.isoformat()
        
        previous_event_hash = (
            self._events[-1].event_hash if self._events else GENESIS_PREVIOUS_HASH
        )
        
        payload_hash = compute_payload_hash(payload)
        event_hash = compute_event_hash(
            sequence_id=sequence_id,
            timestamp_iso=timestamp_iso,
            actor_id=actor_id,
            event_type=event_type,
            entity_id=entity_id,
            payload_hash=payload_hash,
            previous_event_hash=previous_event_hash
        )
        
        event = AuditEvent(
            sequence_id=sequence_id,
            timestamp=timestamp,
            actor_id=actor_id,
            event_type=event_type,
            entity_id=entity_id,
            payload=payload,
            payload_hash=payload_hash,
            previous_event_hash=previous_event_hash,
            event_hash=event_hash
        )
        self._events.append(event)
        return event

    def get_events(self) -> List[AuditEvent]:
        return list(self._events)

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        for idx, event in enumerate(self._events):
            expected_seq = idx + 1
            if event.sequence_id != expected_seq:
                return False, f"Sequence mismatch at index {idx}: expected {expected_seq}, got {event.sequence_id}"

            expected_prev_hash = (
                self._events[idx - 1].event_hash if idx > 0 else GENESIS_PREVIOUS_HASH
            )
            if event.previous_event_hash != expected_prev_hash:
                return False, f"Hash chain broken at sequence {event.sequence_id}: previous_event_hash does not match"

            recomputed_payload_hash = compute_payload_hash(event.payload)
            if event.payload_hash != recomputed_payload_hash:
                return False, f"Payload hash corrupted at sequence {event.sequence_id}"

            recomputed_event_hash = compute_event_hash(
                sequence_id=event.sequence_id,
                timestamp_iso=event.timestamp.isoformat(),
                actor_id=event.actor_id,
                event_type=event.event_type,
                entity_id=event.entity_id,
                payload_hash=event.payload_hash,
                previous_event_hash=event.previous_event_hash
            )
            if event.event_hash != recomputed_event_hash:
                return False, f"Event hash mismatch at sequence {event.sequence_id}"

        return True, None
