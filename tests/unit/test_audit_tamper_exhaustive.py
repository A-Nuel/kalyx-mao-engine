import pytest
from datetime import datetime, timedelta
from src.audit.event_store import AppendOnlyEventStore

def test_audit_detects_deletion_in_middle():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    store.append_event("CEO", "TASK_DELEGATED", "task-01", {"agent": "research"})
    store.append_event("RESEARCHER", "EVIDENCE_SUBMITTED", "task-01", {"data": "findings"})

    # Adversary deletes the second event
    del store._events[1]

    valid, err = store.verify_integrity()
    assert valid is False
    assert "Sequence mismatch" in err or "Hash chain broken" in err

def test_audit_detects_deletion_of_genesis():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    store.append_event("CEO", "TASK_DELEGATED", "task-01", {"agent": "research"})

    del store._events[0]
    valid, err = store.verify_integrity()
    assert valid is False

def test_audit_detects_reordering_of_events():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    store.append_event("CEO", "TASK_DELEGATED", "task-01", {"agent": "research"})
    store.append_event("RESEARCHER", "EVIDENCE_SUBMITTED", "task-01", {"data": "findings"})

    # Swap events 1 and 2
    store._events[1], store._events[2] = store._events[2], store._events[1]

    valid, err = store.verify_integrity()
    assert valid is False

def test_audit_detects_timestamp_tampering():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    
    # Backdate event timestamp
    store._events[0].timestamp = store._events[0].timestamp - timedelta(days=5)

    valid, err = store.verify_integrity()
    assert valid is False
    assert "Event hash mismatch" in err

def test_audit_detects_actor_tampering():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    
    # Impersonate actor
    store._events[0].actor_id = "ROGUE_ADMIN"

    valid, err = store.verify_integrity()
    assert valid is False
    assert "Event hash mismatch" in err
