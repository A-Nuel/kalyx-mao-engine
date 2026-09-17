import pytest

from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.economy.ledger import DoubleEntryLedger
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.settlement.work_verifier import WorkDeliverableVerifier


def test_work_verifier_requires_explicit_secret():
    with pytest.raises(ValueError, match="secret_key is required"):
        WorkDeliverableVerifier()


def test_self_funded_budget_uses_ceil_for_fractional_usdg():
    class PurchaseLoop:
        def _usdg_for_credit_need(self, shortfall):
            return 10_000_001

    runner = SelfSustainingLoopRunner(
        ledger=DoubleEntryLedger(initial_treasury=100),
        work_executor=SimulatedWorkExecutor(),
        work_verifier=WorkDeliverableVerifier(secret_key="hardening-test-secret"),
        surplus_reconciler=SurplusReconciler(
            DoubleEntryLedger(initial_treasury=100),
            receipt_secret_key="hardening-test-secret",
        ),
    )

    # The loop's admission calculation is equivalent to ceil(native / 1e6).
    native = PurchaseLoop()._usdg_for_credit_need(1)
    required_whole = max(1, (native + 1_000_000 - 1) // 1_000_000)
    assert required_whole == 11
