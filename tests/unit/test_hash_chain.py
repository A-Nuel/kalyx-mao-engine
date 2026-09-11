import pytest
from src.audit.event_store import AppendOnlyEventStore, GENESIS_PREVIOUS_HASH
from src.domain.events import compute_payload_hash

def test_event_store_genesis():
    store = AppendOnlyEventStore()
    event = store.append_event(
        actor_id="HUMAN_OPERATOR",
        event_type="ORG_CREATED",
        entity_id="org-01",
        payload={"mission": "Test mission", "budget": 100}
    )
    assert event.sequence_id == 1
    assert event.previous_event_hash == GENESIS_PREVIOUS_HASH
    assert len(event.event_hash) == 64
    
    valid, err = store.verify_integrity()
    assert valid is True
    assert err is None

def test_hash_chain_sequential_integrity():
    store = AppendOnlyEventStore()
    e1 = store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"mission": "Build MAO"})
    e2 = store.append_event("CEO", "PLAN_CREATED", "plan-01", {"tasks": 3})
    e3 = store.append_event("POLICY_ENGINE", "POLICY_CHECK", "prop-01", {"result": "REJECTED"})

    assert e2.previous_event_hash == e1.event_hash
    assert e3.previous_event_hash == e2.event_hash
    
    valid, err = store.verify_integrity()
    assert valid is True
    assert err is None

def test_tamper_detection_on_payload():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    store.append_event("POLICY_ENGINE", "DECISION", "prop-01", {"result": "REJECTED"})

    # Tamper with an event payload
    events = store.get_events()
    events[1].payload["result"] = "APPROVED"
    store._events = events

    valid, err = store.verify_integrity()
    assert valid is False
    assert "Payload hash corrupted" in err

def test_tamper_detection_on_event_hash():
    store = AppendOnlyEventStore()
    store.append_event("OPERATOR", "ORG_CREATED", "org-01", {"budget": 100})
    
    # Tamper with event hash
    store._events[0].event_hash = "f" * 64
    valid, err = store.verify_integrity()
    assert valid is False
    assert "Event hash mismatch" in err
