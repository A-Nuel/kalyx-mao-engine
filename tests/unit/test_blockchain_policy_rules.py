"""Unit tests for blockchain policy rules RULE-BC-01 to RULE-BC-05."""

import pytest

from src.domain.entities import ActionProposal, AgentRecord, Organisation
from src.domain.enums import ActionType, AgentRole
from src.governance.blockchain_rules import (
    AllowedChainRule,
    GasExposureRule,
    IntentMatchRule,
    RecipientAllowlistRule,
    TransactionAmountCeilingRule,
)


@pytest.fixture
def org():
    return Organisation(id="org-bc-test", mission="Blockchain Policy Test", treasury_balance=100)


@pytest.fixture
def agent():
    return AgentRecord(
        id="agent-bc-1",
        role=AgentRole.FINANCIAL_ANALYST,
        authority_ceiling=50,
        allowed_action_types=[ActionType.BLOCKCHAIN_TRANSACTION, ActionType.EXTERNAL_API_CALL],
    )


def _build_proposal(
    id: str = "p1",
    agent_id: str = "agent-bc-1",
    action_type: ActionType = ActionType.BLOCKCHAIN_TRANSACTION,
    target: str = "evm://0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    parameters: dict = None,
    requested_credits: int = 10,
) -> ActionProposal:
    return ActionProposal(
        id=id,
        task_id="t1",
        proposing_agent_id=agent_id,
        action_type=action_type,
        target=target,
        parameters=parameters or {},
        requested_credits=requested_credits,
        expected_value_score=0.9,
        risk_assessment="Low",
        rationale="Policy test proposal",
    )


def test_rule_bc_01_allowed_chain(org, agent):
    rule = AllowedChainRule(allowed_chain_ids={11155111})

    # Allowed: Sepolia (11155111)
    prop_ok = _build_proposal(
        parameters={"chain_id": 11155111},
    )
    assert rule.evaluate(prop_ok, agent, org) is None

    # Prohibited: Mainnet (1)
    prop_mainnet = _build_proposal(
        parameters={"chain_id": 1},
    )
    res = rule.evaluate(prop_mainnet, agent, org)
    assert res is not None
    assert "mainnet and unapproved chains are strictly prohibited" in res


def test_rule_bc_02_recipient_allowlist(org, agent):
    approved_addr = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
    rule = RecipientAllowlistRule(approved_recipients={approved_addr})

    # Approved recipient
    prop_ok = _build_proposal(
        target=f"evm://{approved_addr}",
        parameters={"chain_id": 11155111, "recipient": approved_addr},
    )
    assert rule.evaluate(prop_ok, agent, org) is None

    # Unapproved recipient
    prop_bad = _build_proposal(
        target="evm://0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
        parameters={"chain_id": 11155111, "recipient": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"},
    )
    res = rule.evaluate(prop_bad, agent, org)
    assert res is not None
    assert "not on the approved destination allowlist" in res


def test_rule_bc_03_amount_ceiling(org, agent):
    rule = TransactionAmountCeilingRule(max_credits=25, max_wei=10**16)

    # Exceeding credit ceiling
    prop_exceed_credits = _build_proposal(
        parameters={"chain_id": 11155111, "recipient": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"},
        requested_credits=30,
    )
    assert "exceeds blockchain action ceiling" in rule.evaluate(prop_exceed_credits, agent, org)

    # Exceeding wei ceiling
    prop_exceed_wei = _build_proposal(
        parameters={
            "chain_id": 11155111,
            "recipient": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "amount_wei": 2 * 10**16,
        },
        requested_credits=20,
    )
    assert "exceeds maximum allowable ceiling" in rule.evaluate(prop_exceed_wei, agent, org)


def test_rule_bc_04_gas_exposure(org, agent):
    rule = GasExposureRule(max_fee_per_gas=50_000_000_000, max_gas_limit=100_000)

    # Exceeding max fee
    prop_gas = _build_proposal(
        parameters={
            "chain_id": 11155111,
            "recipient": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "max_fee_per_gas": 60_000_000_000,
        },
    )
    assert "exceeds maximum allowable ceiling" in rule.evaluate(prop_gas, agent, org)


def test_rule_bc_05_intent_match(org, agent):
    rule = IntentMatchRule()

    # Mismatch in amount_credits
    prop_mismatch = _build_proposal(
        parameters={
            "chain_id": 11155111,
            "recipient": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "amount_credits": 15,
        },
        requested_credits=20,
    )
    assert "does not match proposal requested_credits" in rule.evaluate(prop_mismatch, agent, org)
