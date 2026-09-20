import time

from fastapi.testclient import TestClient

import src.api.server as server


def test_live_judge_demo_reveals_real_engine_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "live_demo.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_LIVE_DEMO_STAGE_DELAY", "0")

    client = TestClient(server.app)
    started = client.post("/api/demo/live/start")
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    deadline = time.time() + 15
    snapshot = None
    while time.time() < deadline:
        response = client.get(f"/api/demo/live/{session_id}")
        assert response.status_code == 200, response.text
        snapshot = response.json()
        if snapshot["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert snapshot is not None
    assert snapshot["status"] == "completed", snapshot
    assert snapshot["current_stage"] == "COMPLETE"
    assert snapshot["result"]["organisation_id"]
    assert snapshot["result"]["receipt"]

    sources = [item["source_event"] for item in snapshot["history"]]
    assert "PROPOSAL_SUBMITTED" in sources
    assert "POLICY_EVALUATED" in sources
    assert "ACTION_EXECUTED" in sources
    assert "VERIFIED" in sources
    assert "SETTLED" in sources
    assert "MISSION_COMPLETED" in sources

    policy = next(item for item in snapshot["history"] if item["source_event"] == "POLICY_EVALUATED")
    execution = next(item for item in snapshot["history"] if item["source_event"] == "ACTION_EXECUTED")
    settlement = next(item for item in snapshot["history"] if item["source_event"] == "SETTLED")

    assert policy["evidence"]["decision"]["result"]
    assert execution["evidence"]["receipt"]["id"]
    assert settlement["evidence"]["ledger"]["conserved"] is True
