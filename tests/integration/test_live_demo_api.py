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
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("KALYX_LIVE_DEMO_STAGE_DELAY", "0")
    monkeypatch.setenv("KALYX_JUDGE_PROPOSAL_AUTO_SECONDS", "1")

    client = TestClient(server.app)
    started = client.post("/api/demo/live/start?mode=judge")
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    # The agent submits its proposal without a judge click. The proposal
    # checkpoint then auto-releases after the configured review window.
    deadline = time.time() + 10
    snapshot = {}
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["current_stage"] == "PROPOSE" and snapshot["waiting_for_judge"]:
            break
        time.sleep(0.03)
    assert snapshot["current_stage"] == "PROPOSE"
    assert snapshot["waiting_for_judge"] is True

    deadline = time.time() + 5
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["current_stage"] == "AUTHORIZE" and snapshot["waiting_for_judge"]:
            break
        time.sleep(0.03)
    assert snapshot["current_stage"] == "AUTHORIZE"

    # The deterministic mission may replan after a policy rejection. In either
    # case, every consequential boundary is still released by the judge.
    seen_waiting_stages = set()
    deadline = time.time() + 20
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["status"] == "completed":
            break
        if snapshot["status"] == "failed":
            raise AssertionError(snapshot)

        if snapshot["waiting_for_judge"]:
            stage = snapshot["current_stage"]
            if stage in {"AUTHORIZE", "EXECUTE", "VERIFY", "SETTLE", "AUDIT"}:
                seen_waiting_stages.add(stage)
                response = client.post(f"/api/demo/live/{session_id}/continue")
                assert response.status_code == 200, response.text
        time.sleep(0.03)

    assert snapshot["status"] == "completed", snapshot
    assert {"AUTHORIZE", "EXECUTE", "VERIFY", "SETTLE", "AUDIT"} <= seen_waiting_stages
    sources = {item["source_event"] for item in snapshot["history"]}
    assert {"PROPOSAL_SUBMITTED", "POLICY_EVALUATED", "ACTION_EXECUTED", "VERIFIED", "SETTLED", "MISSION_COMPLETED"} <= sources


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


def test_marketplace_mode_uses_real_live_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "marketplace_demo.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("KALYX_MARKETPLACE_DEMO_STAGE_DELAY", "0")

    client = TestClient(server.app)
    started = client.post("/api/demo/live/start?mode=marketplace")
    assert started.status_code == 200, started.text
    assert started.json()["mode"] == "marketplace"
    session_id = started.json()["session_id"]

    deadline = time.time() + 15
    snapshot = {}
    while time.time() < deadline:
        snapshot = client.get(f"/api/demo/live/{session_id}").json()
        if snapshot["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert snapshot["status"] == "completed", snapshot
    assert snapshot["current_stage"] == "COMPLETE"
    assert "Kalyx Marketplace" in snapshot["result"]["mission"]
    sources = {item["source_event"] for item in snapshot["history"]}
    assert {"PROPOSAL_SUBMITTED", "POLICY_EVALUATED", "ACTION_EXECUTED", "VERIFIED", "SETTLED", "MISSION_COMPLETED"} <= sources


def test_six_stage_b2b_marketplace_loop_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_DB", str(tmp_path / "mkt_loop.db"))
    monkeypatch.setenv("KALYX_PUBLIC_DEMO", "true")
    monkeypatch.setenv("KALYX_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("KALYX_MARKETPLACE_DEMO_STAGE_DELAY", "0")

    client = TestClient(server.app)
    started = client.post("/api/demo/marketplace/start")
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    assert session_id.startswith("mkt-demo-")

    deadline = time.time() + 15
    snapshot = {}
    while time.time() < deadline:
        res = client.get(f"/api/demo/marketplace/{session_id}")
        assert res.status_code == 200, res.text
        snapshot = res.json()
        if snapshot["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert snapshot.get("status") == "completed", snapshot
    assert snapshot.get("current_stage") == "COMPLETE"

    stages = [item["source_event"] for item in snapshot.get("history", [])]
    expected_stages = [
        "ORDER_PROPOSED",
        "CAPABILITY_EXPANSION",
        "ORBIO_EXECUTION",
        "INDEPENDENT_AUDIT",
        "ESCROW_SETTLEMENT",
        "MISSION_CHAINING",
    ]
    for expected in expected_stages:
        assert expected in stages, f"Missing stage: {expected} in {stages}"

    # Verify cryptographic and dual-ledger evidence
    audit_item = next(i for i in snapshot["history"] if i["source_event"] == "INDEPENDENT_AUDIT")
    assert audit_item["evidence"]["hmac_signature_verified"] is True
    assert "VERIFIED" in audit_item["evidence"]["audit_verdict"]

    settle_item = next(i for i in snapshot["history"] if i["source_event"] == "ESCROW_SETTLEMENT")
    assert settle_item["evidence"]["client_escrow_released"] == 300
    assert settle_item["evidence"]["conservation_verified"] is True

    chain_item = next(i for i in snapshot["history"] if i["source_event"] == "MISSION_CHAINING")
    assert chain_item["evidence"]["invariant_holds"] is True
    assert chain_item["evidence"]["cryptographic_lineage_verified"] is True

    # Test continue endpoint
    cont_res = client.post(f"/api/demo/marketplace/{session_id}/continue")
    assert cont_res.status_code == 200, cont_res.text
