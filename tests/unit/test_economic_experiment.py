import pytest
from src.economy.experiment import (
    EconomicExperiment,
    AllocationStrategy,
    ScenarioType,
    ScenarioResult,
    ComparativeExperimentReport,
    OrgSimulationResult
)

def test_economic_experiment_run():
    # Use 2 rounds and single seed for rapid unit test verification
    report = EconomicExperiment.run(num_rounds=2, initial_treasury=160, seeds=[42])
    assert isinstance(report, ComparativeExperimentReport)
    assert report.num_rounds == 2
    assert report.initial_treasury == 160

    # Aggregate results for all 3 strategies exist
    for strat in [AllocationStrategy.STATIC, AllocationStrategy.PERFORMANCE, AllocationStrategy.ADAPTIVE]:
        assert strat in report.results
        agg = report.results[strat]
        assert isinstance(agg, OrgSimulationResult)
        # 2 rounds * 3 scenarios = 6 missions attempted
        assert agg.missions_attempted == 6
        assert agg.total_credits_spent >= 0
        assert agg.credit_burn_rate >= 0

    # Scenario results exist for all (scenario, strategy) pairs
    assert len(report.scenario_results) == 9
    for sc in ScenarioType:
        for strat in AllocationStrategy:
            key = f"{sc.value}_{strat.value}"
            assert key in report.scenario_results
            sr = report.scenario_results[key]
            assert isinstance(sr, ScenarioResult)
            assert sr.scenario == sc
            assert sr.strategy == strat
            assert sr.missions_attempted == 2
            assert 0.0 <= sr.success_rate <= 100.0
            assert sr.credit_efficiency >= 0.0
            assert isinstance(sr.survived, bool)
            assert isinstance(sr.agent_final_statuses, dict)

    # Markdown table formatting
    table = report.format_table()
    assert "### Scenario: STEADY_STATE" in table
    assert "### Scenario: HIGH_RISK_MARKET" in table
    assert "### Scenario: TREASURY_SHOCK" in table
    assert "| Strategy |" in table
    assert "| STATIC |" in table
    assert "| PERFORMANCE |" in table
    assert "| ADAPTIVE |" in table

    # Summary analysis contains objective synthesis
    assert "Multi-Scenario Empirical Benchmark Synthesis" in report.summary_analysis
    assert "Objective Conclusion" in report.summary_analysis


def test_single_scenario_simulation():
    # Test steady state simulation directly
    res_steady = EconomicExperiment._simulate_single_run(
        scenario=ScenarioType.STEADY_STATE,
        strategy=AllocationStrategy.ADAPTIVE,
        num_rounds=2,
        seed=101
    )
    assert isinstance(res_steady, ScenarioResult)
    assert res_steady.scenario == ScenarioType.STEADY_STATE
    assert res_steady.strategy == AllocationStrategy.ADAPTIVE
    assert res_steady.missions_attempted == 2
    assert res_steady.ending_treasury > 0
    assert res_steady.survived is True

    # Test treasury shock simulation
    res_shock = EconomicExperiment._simulate_single_run(
        scenario=ScenarioType.TREASURY_SHOCK,
        strategy=AllocationStrategy.STATIC,
        num_rounds=2,
        seed=101
    )
    assert isinstance(res_shock, ScenarioResult)
    assert res_shock.scenario == ScenarioType.TREASURY_SHOCK
    assert res_shock.missions_attempted == 2
