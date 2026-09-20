import time

from fastapi.testclient import TestClient

import src.api.server as server


def test_live_judge_demo_reveals_real_engine_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "live_demo.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
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
    assert execution["evidence"]["receipt"]["authorization_token"] == "[REDACTED]"
    assert settlement["evidence"]["ledger"]["conserved"] is True


def test_judge_mode_pauses_at_real_boundaries_and_auto_advances_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "judge_demo.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_LIVE_DEMO_STAGE_DELAY", "0")
    monkeypatch.setenv("KALYX_JUDGE_PROPOSAL_AUTO_SECONDS", "1")

    client = TestClient(server.app)
    started = client.post("/api/demo/live/start?mode=judge")
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    deadline = time.time() + 10
    snapshot = {}
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["current_stage"] == "PROPOSE" and snapshot["waiting_for_judge"]:
            break
        time.sleep(0.03)
    assert snapshot["current_stage"] == "PROPOSE"

    # Proposal review is agent-owned: it auto-releases without a judge click.
    deadline = time.time() + 5
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["current_stage"] == "AUTHORIZE" and snapshot["waiting_for_judge"]:
            break
        time.sleep(0.03)
    assert snapshot["current_stage"] == "AUTHORIZE"

    # Every later boundary is explicitly released by the judge.
    for expected in ["EXECUTE", "VERIFY", "SETTLE", "AUDIT"]:
        response = client.post(f"/api/demo/live/{session_id}/continue")
        assert response.status_code == 200, response.text
        deadline = time.time() + 5
        while time.time() < deadline:
            snapshot = client.get(f"/api/demo/live/{session_id}").json()
            if snapshot["current_stage"] == expected and snapshot["waiting_for_judge"]:
                break
            time.sleep(0.03)
        assert snapshot["current_stage"] == expected, snapshot

    client.post(f"/api/demo/live/{session_id}/continue")
    deadline = time.time() + 5
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["status"] in {"completed", "failed"}:
            break
        time.sleep(0.03)
    assert snapshot["status"] == "completed", snapshot


def test_singular_organization_compatibility_route_is_public_demo_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "compat.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
    client = TestClient(server.app)
    result = client.post("/api/demo/public-run")
    assert result.status_code == 200, result.text
    org_id = result.json()["organisation_id"]
    response = client.get(f"/api/v1/organization/{org_id}", headers={"X-Tenant-ID": "tenant-demo"})
    assert response.status_code == 200, response.text
    assert response.json()["organisation"]["id"] == org_id
