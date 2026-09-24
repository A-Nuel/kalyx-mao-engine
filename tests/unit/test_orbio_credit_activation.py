"""Phase 20B — Governed Orbio CREDIT activation unit tests.

No mainnet broadcast. Calldata and policy only.
"""
from __future__ import annotations

import pytest

from eth_hash.auto import keccak

from src.domain.entities import Organisation
from src.domain.exceptions import PolicyViolationError
from src.domain.orbio_activation import (
    ACTIVATE_SELECTOR,
    ACTIVATED_EVENT_TOPIC0,
    MAX_ACTIVATION_AMOUNT,
    ORBIO_ACTIVATION_CHAIN_ID,
    ORBIO_CREDIT_ACTIVATION_CONTRACT,
    OrbioCreditActivationIntent,
    encode_activate_calldata,
)
from src.execution.orbio_activation import OrbioCreditActivationBridge
from src.governance.orbio_activation_rules import (
    ActivationDecisionResult,
    ActivationDenialCode,
    OrbioCreditActivationPolicy,
)
from src.settlement.orbio_activation_preflight import ActivationPreflightResult, OrbioActivationPreflight
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationResult,
    OrbioCreditActivationVerifier,
)


def _intent(**kw) -> OrbioCreditActivationIntent:
    base = dict(
        tenant_id="tenant-a",
        organisation_id="org-a",
        operation_id="op-act-1",
        amount=MAX_ACTIVATION_AMOUNT,
        idempotency_key="idem-act-1",
        policy_decision_id="pol-1",
        authorization_token_hash="auth-hash",
    )
    base.update(kw)
    return OrbioCreditActivationIntent(**base)


def _org() -> Organisation:
    return Organisation(id="org-a", mission="20b", tenant_id="tenant-a", treasury_balance=10)


# --- calldata / selector ---

def test_activate_selector_matches_keccak():
    assert ACTIVATE_SELECTOR == keccak(b"activate(uint256)")[:4]
    assert ACTIVATE_SELECTOR.hex() == "b260c42a"


def test_calldata_for_one_credit():
    data = encode_activate_calldata(1_000_000)
    assert data.startswith("0xb260c42a")
    assert data == "0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240"
    intent = _intent(amount=1_000_000)
    assert intent.encode_calldata() == data


def test_intent_hash_stable_and_sensitive():
    a = _intent()
    b = _intent()
    assert a.compute_activation_intent_hash() == b.compute_activation_intent_hash()
    c = _intent(amount=999_999)
    assert a.compute_activation_intent_hash() != c.compute_activation_intent_hash()


def test_to_blockchain_intent_targets_credit_contract():
    intent = _intent()
    bc = intent.to_blockchain_intent()
    assert bc.recipient == ORBIO_CREDIT_ACTIVATION_CONTRACT
    assert bc.chain_id == 4663
    assert bc.amount_wei == 0
    assert bc.data_payload == intent.encode_calldata()


# --- policy allowlist / ceiling ---

def test_policy_allows_exactly_one_credit_with_human():
    policy = OrbioCreditActivationPolicy()
    intent = _intent(amount=1_000_000)
    d1 = policy.evaluate(intent)
    assert d1.result == ActivationDecisionResult.HUMAN_CONFIRMATION_REQUIRED
    approval = policy.issue_human_approval(intent)
    d2 = policy.evaluate(intent, human_approval=approval)
    assert d2.is_allowed()


@pytest.mark.parametrize("amount", [1_000_001, 2_000_000, 10_000_000, 100_060_000])
def test_policy_rejects_above_ceiling(amount):
    policy = OrbioCreditActivationPolicy()
    intent = _intent(amount=amount)
    # issue approval would still fail amount check first
    d = policy.evaluate(intent)
    assert d.result == ActivationDecisionResult.DENY
    assert ActivationDenialCode.AMOUNT_EXCEEDS_CEILING.value in d.denial_codes


def test_policy_rejects_testnet_chain():
    policy = OrbioCreditActivationPolicy()
    intent = _intent(chain_id=46630, network="robinhood-testnet")
    d = policy.evaluate(intent)
    assert d.result == ActivationDecisionResult.DENY
    assert ActivationDenialCode.TESTNET_FORBIDDEN.value in d.denial_codes


def test_policy_rejects_wrong_contract():
    policy = OrbioCreditActivationPolicy()
    intent = _intent(credit_contract="0x0000000000000000000000000000000000000001")
    d = policy.evaluate(intent)
    assert d.result == ActivationDecisionResult.DENY
    assert ActivationDenialCode.UNAUTHORIZED_CONTRACT.value in d.denial_codes


def test_policy_rejects_sepolia():
    policy = OrbioCreditActivationPolicy()
    intent = _intent(chain_id=11155111, network="sepolia")
    d = policy.evaluate(intent)
    assert d.result == ActivationDecisionResult.DENY


def test_policy_rejects_simulation_runtime():
    policy = OrbioCreditActivationPolicy()
    intent = _intent()
    approval = policy.issue_human_approval(intent)
    d = policy.evaluate(intent, human_approval=approval, runtime_mode="simulation")
    assert d.result == ActivationDecisionResult.DENY
    assert ActivationDenialCode.SIMULATION_FORBIDDEN.value in d.denial_codes


def test_human_approval_does_not_transfer_across_intents():
    policy = OrbioCreditActivationPolicy()
    a = _intent(amount=1_000_000, idempotency_key="a")
    b = _intent(amount=500_000, idempotency_key="b", operation_id="op-2")
    approval_a = policy.issue_human_approval(a)
    d = policy.evaluate(b, human_approval=approval_a)
    assert d.result != ActivationDecisionResult.ALLOW


# --- bridge ---

def test_bridge_creates_operation_after_allow():
    policy = OrbioCreditActivationPolicy()
    bridge = OrbioCreditActivationBridge(policy=policy)
    intent = _intent()
    approval = policy.issue_human_approval(intent)
    prep, op = bridge.prepare_and_create_operation(intent, _org(), human_approval=approval)
    assert prep.is_authorized
    assert op.parameters["activation_amount"] == 1_000_000
    assert op.parameters["data_payload"] == intent.encode_calldata()
    assert op.parameters["recipient"] == ORBIO_CREDIT_ACTIVATION_CONTRACT
    assert op.parameters["chain_id"] == 4663


def test_bridge_denies_without_approval():
    bridge = OrbioCreditActivationBridge()
    intent = _intent()
    with pytest.raises(PolicyViolationError):
        bridge.prepare_and_create_operation(intent, _org())


# --- preflight (mock RPC) ---

class _MockRpc:
    def __init__(self, chain_id=4663, code="0x60806040", balance=100_060_000, eth=10**18):
        self._chain_id = chain_id
        self._code = code
        self._balance = balance
        self._eth = eth

    def get_chain_id(self):
        return self._chain_id

    def get_code(self, address):
        return self._code

    def eth_call(self, to, data, from_address=None):
        if data.startswith("0x70a08231"):
            return hex(self._balance)
        # previewActivation returns credited=amount, fee=0
        if data.startswith("0x"):
            # naive: return two words
            amt = 1_000_000
            return "0x" + amt.to_bytes(32, "big").hex() + (0).to_bytes(32, "big").hex()
        return "0x"

    def get_transaction_count(self, address, block="pending"):
        return 7

    def estimate_gas(self, tx):
        return 80_000

    def get_balance(self, address):
        return self._eth


def test_preflight_ok_on_mainnet_mock():
    pf = OrbioActivationPreflight(_MockRpc(), "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    result = pf.run(_intent())
    assert result.ok
    assert result.chain_id == 4663
    assert result.contract_has_code
    assert result.operator_credit_balance >= 1_000_000
    assert result.broadcast is False if hasattr(result, "broadcast") else result.raw.get("broadcast") is False
    assert result.to_audit_dict()["broadcast"] is False


def test_preflight_fails_wrong_chain():
    pf = OrbioActivationPreflight(_MockRpc(chain_id=46630), "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    result = pf.run(_intent())
    assert not result.ok
    assert any("chain_id" in e for e in result.errors)


def test_preflight_fails_insufficient_credit():
    pf = OrbioActivationPreflight(_MockRpc(balance=100), "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    result = pf.run(_intent())
    assert not result.ok
    assert any("insufficient CREDIT" in e for e in result.errors)


# --- verifier ---

def test_verifier_requires_activated_event():
    intent = _intent()
    receipt = {"status": "0x1", "to": ORBIO_CREDIT_ACTIVATION_CONTRACT, "logs": [], "transactionHash": "0x" + "ab" * 32}
    report = OrbioCreditActivationVerifier().verify(intent, chain_id=4663, receipt=receipt)
    assert report.result == ActivationVerificationResult.REJECTED
    assert any("Activated event missing" in r for r in report.reasons)


def test_verifier_accepts_matching_activated_event():
    intent = _intent()
    amount_hex = "0x" + (1_000_000).to_bytes(32, "big").hex()
    activation_id_topic = "0x" + (42).to_bytes(32, "big").hex()
    from_topic = "0x" + "0" * 24 + "70997970c51812dc3a010c7d01b50e0d17dc79c8"
    beneficiary_topic = "0x" + "0" * 64
    receipt = {
        "status": 1,
        "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
        "transactionHash": "0x" + "cd" * 32,
        "logs": [
            {
                "address": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "topics": [
                    ACTIVATED_EVENT_TOPIC0,
                    activation_id_topic,
                    from_topic,
                    beneficiary_topic,
                ],
                "data": amount_hex,
            }
        ],
    }
    report = OrbioCreditActivationVerifier().verify(
        intent,
        chain_id=4663,
        receipt=receipt,
        expected_sender="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    )
    assert report.result == ActivationVerificationResult.VERIFIED
    assert report.activation_id == 42
    assert report.burned_amount == 1_000_000


def test_verifier_rejects_wrong_amount_in_event():
    intent = _intent()
    amount_hex = "0x" + (2_000_000).to_bytes(32, "big").hex()
    receipt = {
        "status": 1,
        "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
        "logs": [
            {
                "address": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "topics": [
                    ACTIVATED_EVENT_TOPIC0,
                    "0x" + "0" * 64,
                    "0x" + "0" * 64,
                    "0x" + "0" * 64,
                ],
                "data": amount_hex,
            }
        ],
    }
    report = OrbioCreditActivationVerifier().verify(intent, chain_id=4663, receipt=receipt)
    assert report.result == ActivationVerificationResult.REJECTED
