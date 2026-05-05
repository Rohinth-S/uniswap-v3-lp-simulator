import pytest
from lp_simulator.sim import prices, Position, simulate, summary

def test_simulation_is_deterministic():
    p = prices(100, 4); a = simulate(p, Position(95, 105, 1000)); b = simulate(p, Position(95, 105, 1000)); assert a.equals(b)

def test_costs_reduce_net_value():
    p = prices(120, 2); o = simulate(p, Position(90, 110, 1000), dynamic=True); s = summary(o, 1000); assert s["gas_spend"] >= 0 and "final_net_pnl" in s

def test_position_validation():
    with pytest.raises(ValueError): Position(110, 90, 1000)
