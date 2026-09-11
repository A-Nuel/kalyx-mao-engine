import pytest
from unittest.mock import patch, MagicMock
import socket
from src.domain.entities import ActionProposal, Organisation, AgentRecord
from src.domain.enums import OrgState, AgentRole, ActionType, PolicyResult
from src.domain.exceptions import UnauthorizedActionError, ExternalExecutionError
from src.governance.policy_engine import PolicyEngine
from src.economy.ledger import DoubleEntryLedger
from src.execution.executor import ControlledExternalExecutor

@pytest.fixture
def ssrf_env():
    ledger = DoubleEntryLedger(initial_treasury=100)
    engine = PolicyEngine(signing_secret="ssrf-secret-test")
    org = Organisation(id="org-ssrf", mission="SSRF Test", treasury_balance=100, state=OrgState.EXECUTING)
    analyst = AgentRecord(
        id="agent-analyst",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=30,
        allowed_action_types=[ActionType.DATA_FETCH, ActionType.EXTERNAL_API_CALL]
    )
    org.agents["agent-analyst"] = analyst

    executor = ControlledExternalExecutor(
        policy_engine=engine,
        ledger=ledger,
        allowlist={
            "https://api.github.com/repos/",
            "https://httpbin.org/get",
            "http://10.0.0.1/admin",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/internal",
            "http://[fe80::1]/admin",
            "https://dns-rebinding-test.com/data"
        },
        timeout_seconds=1.0,
        max_payload_bytes=1024
    )

    return {"ledger": ledger, "engine": engine, "org": org, "executor": executor}

def test_direct_private_ipv4_blocked(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    for bad_url in ["http://10.0.0.1/admin", "http://192.168.1.1/router", "http://169.254.169.254/latest/meta-data"]:
        prop = ActionProposal(
            id="prop-priv-ip",
            task_id="t-01",
            proposing_agent_id="agent-analyst",
            action_type=ActionType.EXTERNAL_API_CALL,
            target=bad_url,
            requested_credits=5,
            expected_value_score=0.5,
            risk_assessment="High",
            rationale="Test direct private IP"
        )
        decision = engine.evaluate(prop, org)
        token = engine.generate_token(prop, org, decision_id=decision.id)
        decision.authorization_token = token
        decision.result = PolicyResult.APPROVED

        with pytest.raises(UnauthorizedActionError) as exc:
            executor.execute(prop, decision, org)
        assert "SSRF blocked" in str(exc.value) or "restricted/private network range" in str(exc.value)

def test_direct_private_ipv6_blocked(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    for bad_url in ["http://[::1]/internal", "http://[fe80::1]/admin"]:
        prop = ActionProposal(
            id="prop-priv-ipv6",
            task_id="t-01",
            proposing_agent_id="agent-analyst",
            action_type=ActionType.EXTERNAL_API_CALL,
            target=bad_url,
            requested_credits=5,
            expected_value_score=0.5,
            risk_assessment="High",
            rationale="Test direct private IPv6"
        )
        decision = engine.evaluate(prop, org)
        token = engine.generate_token(prop, org, decision_id=decision.id)
        decision.authorization_token = token
        decision.result = PolicyResult.APPROVED

        with pytest.raises(UnauthorizedActionError) as exc:
            executor.execute(prop, decision, org)
        assert "forbidden loopback" in str(exc.value) or "SSRF blocked" in str(exc.value)

def test_dns_resolved_private_ip_blocked(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    prop = ActionProposal(
        id="prop-dns-rebinding",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target="https://dns-rebinding-test.com/data",
        requested_credits=5,
        expected_value_score=0.7,
        risk_assessment="High",
        rationale="Test domain resolving to internal IP"
    )
    decision = engine.evaluate(prop, org)
    token = engine.generate_token(prop, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    # Simulate getaddrinfo returning an internal private IP (e.g. 10.200.1.5)
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.200.1.5', 443))]
    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        with pytest.raises(UnauthorizedActionError) as exc:
            executor.execute(prop, decision, org)
        assert "resolved to restricted IP '10.200.1.5'" in str(exc.value)

def test_redirect_to_private_ip_blocked(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    prop = ActionProposal(
        id="prop-redirect-leak",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target="https://httpbin.org/get",
        requested_credits=5,
        expected_value_score=0.8,
        risk_assessment="Low",
        rationale="Test redirect to private network"
    )
    decision = engine.evaluate(prop, org)
    token = engine.generate_token(prop, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    # Simulate server returning a 302 redirect pointing to 127.0.0.1
    mock_resp = MagicMock()
    mock_resp.is_redirect = True
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "http://127.0.0.1:8080/admin/secrets"}

    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]):
        with patch("httpx.Client.get", return_value=mock_resp):
            with pytest.raises(UnauthorizedActionError) as exc:
                executor.execute(prop, decision, org)
            assert "outbound allowlist" in str(exc.value) or "forbidden loopback" in str(exc.value)

def test_disallowed_http_method_rejected(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    # Propose external call with DELETE method
    prop = ActionProposal(
        id="prop-delete-method",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="https://api.github.com/repos/",
        parameters={"method": "DELETE"},
        requested_credits=5,
        expected_value_score=0.5,
        risk_assessment="High",
        rationale="Attempting DELETE method"
    )
    decision = engine.evaluate(prop, org)
    token = engine.generate_token(prop, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('140.82.121.4', 443))]):
        with pytest.raises(UnauthorizedActionError) as exc:
            executor.execute(prop, decision, org)
        assert "HTTP method 'DELETE' is not permitted" in str(exc.value)

def test_request_payload_limit_enforced(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]

    # Exceed max_payload_bytes (1024 bytes)
    huge_data = {"junk": "x" * 2000}
    prop = ActionProposal(
        id="prop-huge-request",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.EXTERNAL_API_CALL,
        target="https://api.github.com/repos/",
        parameters=huge_data,
        requested_credits=5,
        expected_value_score=0.5,
        risk_assessment="Low",
        rationale="Testing huge payload"
    )
    decision = engine.evaluate(prop, org)
    token = engine.generate_token(prop, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('140.82.121.4', 443))]):
        with pytest.raises(UnauthorizedActionError) as exc:
            executor.execute(prop, decision, org)
        assert "exceeds limit" in str(exc.value)

def test_response_payload_limit_enforced(ssrf_env):
    executor = ssrf_env["executor"]
    engine = ssrf_env["engine"]
    org = ssrf_env["org"]
    ledger = ssrf_env["ledger"]

    prop = ActionProposal(
        id="prop-huge-response",
        task_id="t-01",
        proposing_agent_id="agent-analyst",
        action_type=ActionType.DATA_FETCH,
        target="https://api.github.com/repos/",
        requested_credits=10,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Testing huge response payload"
    )
    decision = engine.evaluate(prop, org)
    token = engine.generate_token(prop, org, decision_id=decision.id)
    decision.authorization_token = token
    decision.result = PolicyResult.APPROVED

    # Upstream returns 50 KB body which exceeds 1024 bytes
    mock_resp = MagicMock()
    mock_resp.is_redirect = False
    mock_resp.status_code = 200
    mock_resp.content = b"x" * 50000
    mock_resp.json.return_value = {"blob": "x" * 50000}

    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('140.82.121.4', 443))]):
        with patch("httpx.Client.get", return_value=mock_resp):
            with pytest.raises(ExternalExecutionError) as exc:
                executor.execute(prop, decision, org)
            assert "exceeds payload limit" in str(exc.value) or "exceeds limit" in str(exc.value)

    # Invariant: When response payload limit is breached, escrow is rolled back to Treasury
    assert ledger.get_balance("TREASURY") == 100
    assert ledger.get_balance("ESCROW") == 0
    assert ledger.get_balance("EXTERNAL_SINK") == 0
