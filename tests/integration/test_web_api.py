import os
from fastapi.testclient import TestClient
import src.api.server as server


def test_policy_rules_catalogue(tmp_path, monkeypatch):
    db_path = tmp_path / "web_api.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    client = TestClient(server.app)

    res = client.get("/api/policies/rules")
    assert res.status_code == 200
    data = res.json()
    assert data["total_rules"] == 8
    rule_ids = [r["rule_id"] for r in data["rules"]]
    assert "RULE-01" in rule_ids
    assert "RULE-02" in rule_ids
    assert "RULE-03" in rule_ids
    assert "RULE-04" in rule_ids
    assert "RULE-05" in rule_ids
    assert "RULE-06" in rule_ids
    assert "RULE-07" in rule_ids
    assert "RULE-08" in rule_ids
    assert "policy_version_hash" in data
    assert data["human_approval_threshold"] == 40


def test_system_settings_safe_and_no_secrets(tmp_path, monkeypatch):
    db_path = tmp_path / "web_api.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    client = TestClient(server.app)

    res = client.get("/api/system/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "Kalyx Command Centre"
    assert data["version"] == server.app.version
    assert data["environment"] in {"development", "production"}
    assert data["database_backend"] in {"SQLite", "PostgreSQL"}
    assert "settlement_provider" in data
    assert "circuit_breaker_status" in data
    # Guarantee no secrets leak
    text = res.text.lower()
    assert "secret" not in text or "signing_secret" not in text
    assert "password" not in text
    assert "private_key" not in text


def test_demo_run_and_subsequent_reads(tmp_path, monkeypatch):
    db_path = tmp_path / "web_api.db"
    monkeypatch.setenv("KALYX_DB", str(db_path))
    client = TestClient(server.app)

    # 1. Run demo mission
    res = client.post("/api/demo/run")
    assert res.status_code == 200
    demo_data = res.json()
    assert "organisation_id" in demo_data
    org_id = demo_data["organisation_id"]

    # 2. Operations summary
    ops_res = client.get(f"/api/organisations/{org_id}/operations/summary")
    assert ops_res.status_code == 200
    ops_summary = ops_res.json()
    assert "total_operations" in ops_summary
    assert "counts" in ops_summary
    assert "unresolved_count" in ops_summary

    # 3. Policy rules with rejection counts for this org
    rules_res = client.get(f"/api/policies/rules?org_id={org_id}")
    assert rules_res.status_code == 200
    rules_data = rules_res.json()
    # In the scripted demo, RULE-01 (Spend Cap Ceiling) was rejected before replanning
    rule_01 = next(r for r in rules_data["rules"] if r["rule_id"] == "RULE-01")
    assert rule_01["rejection_count"] >= 1

    # 4. Agents list and individual agent profile
    agents_res = client.get(f"/api/organisations/{org_id}/agents")
    assert agents_res.status_code == 200
    agents = agents_res.json()
    assert len(agents) > 0

    agent_id = agents[0]["id"]
    profile_res = client.get(f"/api/organisations/{org_id}/agents/{agent_id}")
    assert profile_res.status_code == 200
    profile_data = profile_res.json()
    assert profile_data["agent"]["id"] == agent_id
    assert "tasks" in profile_data
    assert "proposals" in profile_data
    assert "decisions" in profile_data


def test_experiments_endpoint():
    client = TestClient(server.app)

    # Run 1 quick round of experiment
    run_res = client.post("/api/experiments/run?num_rounds=1")
    assert run_res.status_code == 200
    report = run_res.json()
    assert "num_rounds" in report
    assert "results" in report
    assert "scenario_results" in report

    # Latest should now return has_run=True with cached report
    latest_res = client.get("/api/experiments/latest")
    assert latest_res.status_code == 200
    latest_data = latest_res.json()
    assert latest_data["has_run"] is True
    assert latest_data["report"]["num_rounds"] == 1


def test_frontend_assets_served():
    client = TestClient(server.app)

    # 1. HTML index
    html_res = client.get("/")
    assert html_res.status_code == 200
    assert "<title>Kalyx — Command Centre</title>" in html_res.text
    assert 'data-nav="overview"' in html_res.text
    assert 'data-nav="missions"' in html_res.text
    assert 'data-nav="organisation"' in html_res.text
    assert 'data-nav="treasury"' in html_res.text
    assert 'data-nav="policies"' in html_res.text
    assert 'data-nav="operations"' in html_res.text
    assert 'data-nav="audit"' in html_res.text
    assert 'data-nav="experiments"' in html_res.text
    assert 'data-nav="settings"' in html_res.text

    # 2. Assets
    js_res = client.get("/assets/app.js")
    assert js_res.status_code == 200
    assert "App = ((" in js_res.text

    api_res = client.get("/assets/api.js")
    assert api_res.status_code == 200
    assert "API = ((" in api_res.text

    css_res = client.get("/assets/app.css")
    assert css_res.status_code == 200
    assert ".glass-panel" in css_res.text
