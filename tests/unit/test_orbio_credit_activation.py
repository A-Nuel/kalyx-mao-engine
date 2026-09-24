"""Phase 20B — Governed Orbio CREDIT activation unit tests.

No mainnet broadcast.
"""
from __future__ import annotations

import pytest
from eth_hash.auto import keccak

from src.domain.entities import Organisation
from src.domain.exceptions import PolicyViolationError
from src.domain.orbio_activation import (
    ACTIVATE_SELECTOR,
    ACTIVATED_EVENT_TOPIC0,
    ACTIVATION_FEE_EVENT_TOPIC0,
    MAX_ACTIVATION_AMOUNT,
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
from src.settlement.orbio_activation_preflight import OrbioActivationPreflight
from src.settlement.orbio_activation_verifier import (
    ActivationVerificationResult,
    OrbioCreditActivationVerifier,
)

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


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


def _preview_hex(credited: int, fee: int) -> str:
    return "0x" + credited.to_bytes(32, "big").hex() + fee.to_bytes(32, "big").hex()


def _fee_bps_hex(bps: int) -> str:
    return "0x" + bps.to_bytes(32, "big").hex()


class _MockRpc:
    """Configurable read-only RPC for preflight tests."""

    def __init__(
        self,
        *,
        chain_id=4663,
        code="0x60806040",
        balance=100_060_000,
        eth=10**18,
        credited=1_000_000,
        fee_atoms=0,
        fee_bps=0,
        gas=80_000,
        fail_preview=False,
        fail_fee=False,
        fail_gas=False,
        fail_nonce=False,
        fail_balance_call=False,
        empty_preview=False,
        empty_fee=False,
    ):
        self._chain_id = chain_id
        self._code = code
        self._balance = balance
        self._eth = eth
        self._credited = credited
        self._fee_atoms = fee_atoms
        self._fee_bps = fee_bps
        self._gas = gas
        self._fail_preview = fail_preview
        self._fail_fee = fail_fee
        self._fail_gas = fail_gas
        self._fail_nonce = fail_nonce
        self._fail_balance_call = fail_balance_call
        self._empty_preview = empty_preview
        self._empty_fee = empty_fee

    def get_chain_id(self):
        return self._chain_id

    def get_code(self, address):
        return self._code

    def eth_call(self, to, data, from_address=None):
        if data.startswith("0x70a08231"):
            if self._fail_balance_call:
                raise RuntimeError("balanceOf RPC error")
            return "0x" + self._balance.to_bytes(32, "big").hex()
        # previewActivation selector
        if data[2:10] == keccak(b"previewActivation(uint256)").hex()[:8]:
            if self._fail_preview:
                raise RuntimeError("previewActivation RPC error")
            if self._empty_preview:
                return "0x"
            return _preview_hex(self._credited, self._fee_atoms)
        # activationFeeBps
        if data[2:10] == keccak(b"activationFeeBps()").hex()[:8]:
            if self._fail_fee:
                raise RuntimeError("activationFeeBps RPC error")
            if self._empty_fee:
                return "0x"
            return _fee_bps_hex(self._fee_bps)
        return "0x"

    def get_transaction_count(self, address, block="pending"):
        if self._fail_nonce:
            raise RuntimeError("nonce RPC error")
        return 7

    def estimate_gas(self, tx):
        if self._fail_gas:
            raise RuntimeError("estimateGas RPC error")
        return self._gas

    def get_balance(self, address):
        return self._eth


# --- calldata / selector ---

def test_activate_selector_matches_keccak():
    assert ACTIVATE_SELECTOR == keccak(b"activate(uint256)")[:4]
    assert ACTIVATE_SELECTOR.hex() == "b260c42a"


def test_calldata_for_one_credit():
    data = encode_activate_calldata(1_000_000)
    assert data == "0xb260c42a00000000000000000000000000000000000000000000000000000000000f4240"


def test_intent_hash_stable_and_sensitive():
    a, b = _intent(), _intent()
    assert a.compute_activation_intent_hash() == b.compute_activation_intent_hash()
    assert a.compute_activation_intent_hash() != _intent(amount=999_999).compute_activation_intent_hash()


def test_to_blockchain_intent_targets_credit_contract():
    bc = _intent().to_blockchain_intent()
    assert bc.recipient == ORBIO_CREDIT_ACTIVATION_CONTRACT
    assert bc.chain_id == 4663
    assert bc.amount_wei == 0


# --- policy ---

def test_policy_allows_exactly_one_credit_with_human():
    policy = OrbioCreditActivationPolicy()
    intent = _intent(amount=1_000_000)
    assert policy.evaluate(intent).result == ActivationDecisionResult.HUMAN_CONFIRMATION_REQUIRED
    approval = policy.issue_human_approval(intent)
    assert policy.evaluate(intent, human_approval=approval).is_allowed()


@pytest.mark.parametrize("amount", [1_000_001, 2_000_000, 10_000_000, 100_060_000])
def test_policy_rejects_above_ceiling(amount):
    d = OrbioCreditActivationPolicy().evaluate(_intent(amount=amount))
    assert d.result == ActivationDecisionResult.DENY
    assert ActivationDenialCode.AMOUNT_EXCEEDS_CEILING.value in d.denial_codes


def test_policy_rejects_testnet_chain():
    d = OrbioCreditActivationPolicy().evaluate(_intent(chain_id=46630, network="robinhood-testnet"))
    assert ActivationDenialCode.TESTNET_FORBIDDEN.value in d.denial_codes


def test_policy_rejects_wrong_contract():
    d = OrbioCreditActivationPolicy().evaluate(
        _intent(credit_contract="0x0000000000000000000000000000000000000001")
    )
    assert ActivationDenialCode.UNAUTHORIZED_CONTRACT.value in d.denial_codes


def test_policy_rejects_sepolia():
    d = OrbioCreditActivationPolicy().evaluate(_intent(chain_id=11155111, network="sepolia"))
    assert d.result == ActivationDecisionResult.DENY


def test_policy_rejects_simulation_runtime():
    policy = OrbioCreditActivationPolicy()
    intent = _intent()
    approval = policy.issue_human_approval(intent)
    d = policy.evaluate(intent, human_approval=approval, runtime_mode="simulation")
    assert ActivationDenialCode.SIMULATION_FORBIDDEN.value in d.denial_codes


def test_human_approval_does_not_transfer_across_intents():
    policy = OrbioCreditActivationPolicy()
    a = _intent(amount=1_000_000, idempotency_key="a")
    b = _intent(amount=500_000, idempotency_key="b", operation_id="op-2")
    approval_a = policy.issue_human_approval(a)
    assert policy.evaluate(b, human_approval=approval_a).result != ActivationDecisionResult.ALLOW


# --- bridge ---

def test_bridge_creates_operation_after_allow():
    policy = OrbioCreditActivationPolicy()
    bridge = OrbioCreditActivationBridge(policy=policy)
    intent = _intent()
    approval = policy.issue_human_approval(intent)
    prep, op = bridge.prepare_and_create_operation(intent, _org(), human_approval=approval)
    assert prep.is_authorized
    assert op.parameters["activation_amount"] == 1_000_000
    assert op.parameters["chain_id"] == 4663


def test_bridge_denies_without_approval():
    with pytest.raises(PolicyViolationError):
        OrbioCreditActivationBridge().prepare_and_create_operation(_intent(), _org())


# --- preflight hard-fail ---

def test_preflight_ok_on_mainnet_mock():
    result = OrbioActivationPreflight(_MockRpc(), OPERATOR).run(_intent())
    assert result.ok
    assert result.requested_amount == 1_000_000
    assert result.preview_credited == 1_000_000
    assert result.preview_fee_atoms == 0
    assert result.activation_fee_bps == 0
    assert result.to_audit_dict()["broadcast"] is False
    assert result.to_audit_dict()["api_balance_confirmed"] is False


def test_preflight_fails_wrong_chain():
    r = OrbioActivationPreflight(_MockRpc(chain_id=46630), OPERATOR).run(_intent())
    assert not r.ok and any("chain_id" in e for e in r.errors)


def test_preflight_fails_insufficient_credit():
    r = OrbioActivationPreflight(_MockRpc(balance=100), OPERATOR).run(_intent())
    assert not r.ok and any("insufficient CREDIT" in e for e in r.errors)


def test_preflight_fails_missing_bytecode():
    r = OrbioActivationPreflight(_MockRpc(code="0x"), OPERATOR).run(_intent())
    assert not r.ok and any("bytecode" in e for e in r.errors)


def test_preflight_fails_preview_rpc_error():
    r = OrbioActivationPreflight(_MockRpc(fail_preview=True), OPERATOR).run(_intent())
    assert not r.ok and any("previewActivation failed" in e for e in r.errors)


def test_preflight_fails_fee_rpc_error():
    r = OrbioActivationPreflight(_MockRpc(fail_fee=True), OPERATOR).run(_intent())
    assert not r.ok and any("activationFeeBps failed" in e for e in r.errors)


def test_preflight_fails_empty_preview():
    r = OrbioActivationPreflight(_MockRpc(empty_preview=True), OPERATOR).run(_intent())
    assert not r.ok and any("malformed" in e or "empty" in e for e in r.errors)


def test_preflight_fails_empty_fee_bps():
    r = OrbioActivationPreflight(_MockRpc(empty_fee=True), OPERATOR).run(_intent())
    assert not r.ok and any("activationFeeBps" in e for e in r.errors)


def test_preflight_fails_conservation():
    # credited + fee != amount
    r = OrbioActivationPreflight(_MockRpc(credited=900_000, fee_atoms=50_000), OPERATOR).run(_intent())
    assert not r.ok and any("conservation" in e for e in r.errors)


def test_preflight_fails_fee_exceeds_amount():
    r = OrbioActivationPreflight(_MockRpc(credited=0, fee_atoms=2_000_000), OPERATOR).run(_intent())
    assert not r.ok
    assert any("exceeds requested" in e or "conservation" in e for e in r.errors)


def test_preflight_ok_with_nonzero_fee_conserving():
    r = OrbioActivationPreflight(_MockRpc(credited=990_000, fee_atoms=10_000, fee_bps=100), OPERATOR).run(
        _intent()
    )
    assert r.ok
    assert r.preview_credited == 990_000
    assert r.preview_fee_atoms == 10_000
    assert r.activation_fee_bps == 100


def test_preflight_fails_zero_eth():
    r = OrbioActivationPreflight(_MockRpc(eth=0), OPERATOR).run(_intent())
    assert not r.ok and any("ETH balance is zero" in e for e in r.errors)


def test_preflight_fails_gas_estimate_error():
    r = OrbioActivationPreflight(_MockRpc(fail_gas=True), OPERATOR).run(_intent())
    assert not r.ok and any("estimate_gas failed" in e for e in r.errors)


def test_preflight_fails_gas_above_limit():
    r = OrbioActivationPreflight(_MockRpc(gas=999_999), OPERATOR).run(_intent(gas_limit=100_000))
    assert not r.ok and any("exceeds intent gas_limit" in e for e in r.errors)


def test_preflight_fails_nonce():
    r = OrbioActivationPreflight(_MockRpc(fail_nonce=True), OPERATOR).run(_intent())
    assert not r.ok and any("nonce failed" in e for e in r.errors)


# --- verifier ---

def _good_receipt(amount=1_000_000, sender=OPERATOR, to=ORBIO_CREDIT_ACTIVATION_CONTRACT, log_addr=None):
    amount_hex = "0x" + amount.to_bytes(32, "big").hex()
    from_topic = "0x" + "0" * 24 + sender.lower().replace("0x", "")
    return {
        "status": 1,
        "to": to,
        "transactionHash": "0x" + "cd" * 32,
        "logs": [
            {
                "address": log_addr or ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "topics": [
                    ACTIVATED_EVENT_TOPIC0,
                    "0x" + (42).to_bytes(32, "big").hex(),
                    from_topic,
                    "0x" + "0" * 64,
                ],
                "data": amount_hex,
            }
        ],
    }


def test_verifier_requires_activated_event():
    receipt = {"status": "0x1", "to": ORBIO_CREDIT_ACTIVATION_CONTRACT, "logs": [], "transactionHash": "0x" + "ab" * 32}
    report = OrbioCreditActivationVerifier().verify(_intent(), chain_id=4663, receipt=receipt, expected_sender=OPERATOR)
    assert report.result == ActivationVerificationResult.REJECTED
    assert any("Activated event missing" in r for r in report.reasons)


def test_verifier_accepts_matching_activated_event():
    report = OrbioCreditActivationVerifier().verify(
        _intent(), chain_id=4663, receipt=_good_receipt(), expected_sender=OPERATOR
    )
    assert report.result == ActivationVerificationResult.VERIFIED
    assert report.activation_id == 42
    assert report.burned_amount == 1_000_000


def test_verifier_rejects_wrong_amount():
    report = OrbioCreditActivationVerifier().verify(
        _intent(), chain_id=4663, receipt=_good_receipt(amount=2_000_000), expected_sender=OPERATOR
    )
    assert report.result == ActivationVerificationResult.REJECTED


def test_verifier_rejects_missing_to():
    receipt = _good_receipt()
    del receipt["to"]
    report = OrbioCreditActivationVerifier().verify(
        _intent(), chain_id=4663, receipt=receipt, expected_sender=OPERATOR
    )
    assert report.result == ActivationVerificationResult.REJECTED
    assert any("receipt.to is missing" in r for r in report.reasons)


def test_verifier_rejects_wrong_to():
    report = OrbioCreditActivationVerifier().verify(
        _intent(),
        chain_id=4663,
        receipt=_good_receipt(to="0x0000000000000000000000000000000000000001"),
        expected_sender=OPERATOR,
    )
    assert report.result == ActivationVerificationResult.REJECTED


def test_verifier_rejects_wrong_log_address():
    report = OrbioCreditActivationVerifier().verify(
        _intent(),
        chain_id=4663,
        receipt=_good_receipt(log_addr="0x00000000000000000000000000000000000000aa"),
        expected_sender=OPERATOR,
    )
    assert report.result == ActivationVerificationResult.REJECTED


def test_verifier_rejects_wrong_sender():
    report = OrbioCreditActivationVerifier().verify(
        _intent(),
        chain_id=4663,
        receipt=_good_receipt(sender="0x1111111111111111111111111111111111111111"),
        expected_sender=OPERATOR,
    )
    assert report.result == ActivationVerificationResult.REJECTED


def test_verifier_rejects_missing_expected_sender():
    report = OrbioCreditActivationVerifier().verify(
        _intent(), chain_id=4663, receipt=_good_receipt(), expected_sender=None
    )
    assert report.result == ActivationVerificationResult.REJECTED
    assert any("expected_sender is required" in r for r in report.reasons)


def test_verifier_rejects_malformed_event_topics():
    receipt = {
        "status": 1,
        "to": ORBIO_CREDIT_ACTIVATION_CONTRACT,
        "logs": [
            {
                "address": ORBIO_CREDIT_ACTIVATION_CONTRACT,
                "topics": [ACTIVATED_EVENT_TOPIC0],  # too few
                "data": "0x" + (1_000_000).to_bytes(32, "big").hex(),
            }
        ],
    }
    report = OrbioCreditActivationVerifier().verify(
        _intent(), chain_id=4663, receipt=receipt, expected_sender=OPERATOR
    )
    assert report.result == ActivationVerificationResult.REJECTED


def test_activated_event_topic0_matches_official_abi():
    assert ACTIVATED_EVENT_TOPIC0 == "0x" + keccak(
        b"Activated(uint256,address,bytes32,uint256)"
    ).hex()
    assert ACTIVATION_FEE_EVENT_TOPIC0 == "0x" + keccak(
        b"ActivationFeeCharged(uint256,uint256)"
    ).hex()
