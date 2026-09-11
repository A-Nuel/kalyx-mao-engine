import pytest
from src.economy.experiment import EconomicExperiment, AllocationStrategy, ComparativeExperimentReport

def test_economic_experiment_run():
    report = EconomicExperiment.run(num_rounds=4, initial_treasury=160)
    assert isinstance(report, ComparativeExperimentReport)
    assert report.num_rounds == 4
    assert report.initial_treasury == 160

    static_res = report.results[AllocationStrategy.STATIC]
    perf_res = report.results[AllocationStrategy.PERFORMANCE]
    adapt_res = report.results[AllocationStrategy.ADAPTIVE]

    # Verify all completed simulation records
    assert static_res.missions_attempted == 4
    assert perf_res.missions_attempted == 4
    assert adapt_res.missions_attempted == 4

    # Performance and Adaptive strategies preserve more treasury than Static
    assert adapt_res.remaining_treasury >= static_res.remaining_treasury
    assert adapt_res.average_efficiency >= 0.8

    # Check markdown table formatting
    table = report.format_table()
    assert "| Strategy |" in table
    assert "| STATIC |" in table
    assert "| PERFORMANCE |" in table
    assert "| ADAPTIVE |" in table
    assert "Economic Experiment Analysis" in report.summary_analysis
