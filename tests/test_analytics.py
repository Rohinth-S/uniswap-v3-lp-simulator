import pytest

from lp_simulator.analytics import analyze_rebalances, rebalance_events
from lp_simulator.sim import Position, simulate_rebalanced


def test_rebalance_analytics_exposes_fee_yield_and_events():
    output = simulate_rebalanced([100, 101, 130, 128, 99], Position(90, 110, 1_000, gas_usd=3), width=.1)
    analytics = analyze_rebalances(output, 1_000, observations_per_year=365)
    assert analytics.rebalances >= 1
    assert analytics.gas_spend >= 3
    assert analytics.fee_apr_pct >= 0
    assert len(rebalance_events(output)) == analytics.rebalances
    assert analytics.as_dict()["net_return_pct"] == pytest.approx(analytics.net_return_pct)
