import pytest
from src.domain.enums import CurrencyAsset, DeliverableStatus, WorkOrderStatus
from src.domain.exceptions import InsufficientCreditsError
from src.domain.work_order import WorkOrder, WorkDeliverable
from src.execution.work_executor import SimulatedWorkExecutor
from src.settlement.work_verifier import WorkDeliverableVerifier

def create_work_order(deliv_type="SECURITY_AUDIT", credits_needed=10):
    return WorkOrder(
        work_order_id="wo-audit-01",
        tenant_id="tenant-1",
        organisation_id="org-1",
        client_id="client-dao",
        title="Protocol Security Audit",
        description="Audit protocol smart contracts",
        deliverable_type=deliv_type,
        required_orbio_credits=credits_needed,
        bounty_amount=50,
        bounty_asset=CurrencyAsset.USDG,
    )

def test_executor_insufficient_credits():
    store = {"org-1": 5} # Needs 10
    executor = SimulatedWorkExecutor(credit_store=store)
    wo = create_work_order(credits_needed=10)
    
    with pytest.raises(InsufficientCreditsError, match="Insufficient Orbio CREDIT"):
        executor.execute_work(wo, producer_agent_id="agent-eng", organisation_id="org-1")
    
    # Balance must remain untouched
    assert store["org-1"] == 5

def test_executor_and_verifier_happy_path():
    store = {"org-1": 20}
    executor = SimulatedWorkExecutor(credit_store=store)
    verifier = WorkDeliverableVerifier(secret_key="test-secret")
    wo = create_work_order(credits_needed=10)
    
    deliverable = executor.execute_work(wo, producer_agent_id="agent-eng", organisation_id="org-1")
    
    # Check credit deduction
    assert store["org-1"] == 10
    assert deliverable.orbio_credits_consumed == 10
    assert deliverable.content_payload["audit_verdict"] == "PASSED_SECURE"
    
    # Verify deliverable
    receipt = verifier.verify(wo, deliverable)
    assert receipt.status == DeliverableStatus.ACCEPTED
    assert receipt.is_verified() is True
    assert receipt.verify_hmac("test-secret") is True

def test_verifier_tampered_content_rejected():
    store = {"org-1": 20}
    executor = SimulatedWorkExecutor(credit_store=store)
    verifier = WorkDeliverableVerifier(secret_key="test-secret")
    wo = create_work_order()
    
    deliverable = executor.execute_work(wo, producer_agent_id="agent-eng", organisation_id="org-1")
    
    # Malicious tampering: payload changed without updating content_hash
    deliverable.content_payload["audit_verdict"] = "TAMPERED_VERDICT"
    
    receipt = verifier.verify(wo, deliverable)
    assert receipt.status == DeliverableStatus.REJECTED
    assert "Content hash corruption" in receipt.verification_notes
    assert receipt.verify_hmac("test-secret") is True

def test_verifier_excess_credit_consumption_rejected():
    verifier = WorkDeliverableVerifier(secret_key="test-secret")
    wo = create_work_order(credits_needed=10)
    
    # Manually constructed deliverable claiming 50 credits consumed for a 10 credit order
    deliverable = WorkDeliverable.create(
        work_order_id=wo.work_order_id,
        producer_agent_id="agent-eng",
        content_payload={"deliverable_type": "SECURITY_AUDIT", "result": "OK"},
        orbio_credits_consumed=50,
    )
    
    receipt = verifier.verify(wo, deliverable)
    assert receipt.status == DeliverableStatus.REJECTED
    assert "Excessive compute consumed" in receipt.verification_notes

def test_verifier_mismatched_work_order_id():
    verifier = WorkDeliverableVerifier(secret_key="test-secret")
    wo = create_work_order()
    
    deliverable = WorkDeliverable.create(
        work_order_id="wrong-wo-id",
        producer_agent_id="agent-eng",
        content_payload={"deliverable_type": "SECURITY_AUDIT"},
        orbio_credits_consumed=10,
    )
    
    receipt = verifier.verify(wo, deliverable)
    assert receipt.status == DeliverableStatus.REJECTED
    assert "Work order ID mismatch" in receipt.verification_notes
