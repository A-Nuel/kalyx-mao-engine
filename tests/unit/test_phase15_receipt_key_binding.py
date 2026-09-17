import pytest

from src.agents.self_sustaining_loop import SelfSustainingLoopRunner
from src.economy.ledger import DoubleEntryLedger
from src.economy.surplus_accounting import SurplusReconciler
from src.execution.work_executor import SimulatedWorkExecutor
from src.settlement.work_verifier import WorkDeliverableVerifier


def test_self_sustaining_runner_rejects_mismatched_receipt_keys_before_mission():
    with pytest.raises(
        ValueError,
        match="work_verifier.secret_key and surplus_reconciler.receipt_secret_key must match",
    ):
        SelfSustainingLoopRunner(
            ledger=DoubleEntryLedger(initial_treasury=100),
            work_executor=SimulatedWorkExecutor(),
            work_verifier=WorkDeliverableVerifier(secret_key="verifier-key"),
            surplus_reconciler=SurplusReconciler(
                DoubleEntryLedger(initial_treasury=100),
                receipt_secret_key="different-reconciler-key",
            ),
        )
