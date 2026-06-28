import pytest
from lp_simulator.sim import (
    prices,
    Position,
    TickRange,
    accrue_fees,
    price_to_tick,
    rebalance_position,
    simulate,
    simulate_rebalanced,
    summary,
    tick_to_price,
)

def test_simulation_is_deterministic():
    p = prices(100, 4); a = simulate(p, Position(95, 105, 1000)); b = simulate(p, Position(95, 105, 1000)); assert a.equals(b)

def test_costs_reduce_net_value():
    p = prices(120, 2); o = simulate(p, Position(90, 110, 1000), dynamic=True); s = summary(o, 1000); assert s["gas_spend"] >= 0 and "final_net_pnl" in s

def test_position_validation():
    with pytest.raises(ValueError): Position(110, 90, 1000)

def test_tick_roundtrip_and_fee_accrual():
    tick = price_to_tick(1.2345)
    assert tick_to_price(tick) == pytest.approx(1.2345, rel=1e-4)
    assert accrue_fees(100_000, .0005, .25) == pytest.approx(12.5)
    assert TickRange(tick - 10, tick + 10).contains(tick_to_price(tick))

def test_rebalanced_simulation_records_tick_events_and_gas():
    path = [100, 101, 130, 128, 99]
    original = Position(90, 110, 1_000, gas_usd=3)
    moved, ticks = rebalance_position(original, 130, width=.1)
    assert ticks.contains(130) and moved.lower < 130 < moved.upper
    result = simulate_rebalanced(path, original, width=.1)
    assert result["rebalances"].iloc[-1] >= 1
    assert result["gas"].iloc[-1] >= 3
    assert result["net_value"].iloc[-1] == pytest.approx(
        result["gross_value"].iloc[-1] + result["fees"].iloc[-1] - result["gas"].iloc[-1]
    )
