from pathlib import Path
import json

import pytest

from lp_simulator.events import decode_logs
from lp_simulator.fixtures import demo_tape
from lp_simulator.pool_state import PoolState
from lp_simulator.replay import holdout_width_study, replay_lp
from lp_simulator.tape import LiquidityChange, PoolMetadata, ReplayConfig, Swap, Tape


def test_bad_swaps_do_not_move_state_and_zero_net_ticks_stay_initialized():
    tape = demo_tape()
    state = PoolState(tape)
    for event in tape.events[:3]:
        state.apply(event)
    swap = tape.events[3]
    before = state.tick, state.sqrt_price_x96
    with pytest.raises(ValueError, match="differs from emitted"):
        state.apply(swap.model_copy(update={"active_liquidity": 0}))
    assert (state.tick, state.sqrt_price_x96) == before
    owner = "0x" + "03" * 20
    for lower, upper in [(40000, 41000), (41000, 42000)]:
        state.apply(LiquidityChange(
            kind="mint", block=11000, log_index=1, owner=owner, lower_tick=lower, upper_tick=upper, liquidity=10,
        ))
    assert state.liquidity_gross[41000] == 20 and 41000 not in state.liquidity_net

def test_prefix_state_excludes_future_liquidity_and_invalid_widths():
    tape = demo_tape()
    events = list(tape.events[:5])
    after_swap = LiquidityChange(
        kind="mint", block=10001, log_index=2, owner="0x" + "12" * 20,
        lower_tick=42000, upper_tick=45000, liquidity=5,
    )
    events.insert(4, after_swap)
    sparse = Tape.model_validate({**tape.model_dump(exclude={"events"}), "events": events})
    report, _ = replay_lp(sparse, until_swap=1, include_pool_state=True)
    assert report["reconstructed_positions"] == 2
    assert report["first_evaluated_block"] == report["end_block"]
    with pytest.raises(ValueError, match="widths"):
        holdout_width_study(sparse, ReplayConfig(), widths=[0])

def test_rebalance_economics_and_peak_to_trough_drawdown():
    report, ledger = replay_lp(demo_tape())
    dynamic = report["strategies"]["rebalanced"]
    assert dynamic["turnover_quote"] > 0 and dynamic["max_drawdown_quote"] > 0
    assert dynamic["turnover_cost_quote"] == pytest.approx(dynamic["turnover_quote"] * 0.0015)
    assert any(row["rebalance_reason"] == "range_shift" for row in ledger)
    assert all(int(row["sqrt_price_x96_before"]) > 0 for row in ledger)

def test_persisted_rpc_capture_validates_ordinals():
    examples = Path(__file__).resolve().parents[1] / "examples"
    meta = PoolMetadata.model_validate_json((examples / "arbitrum-pool.json").read_bytes())
    logs = json.loads((examples / "arbitrum-opening-logs.json").read_bytes())
    assert len(decode_logs(meta, logs).events) == 8
    logs[0]["logIndex"] = "0x000"
    with pytest.raises(ValueError, match="canonical uint64"):
        decode_logs(meta, logs)

def test_terminal_state_includes_trailing_mints_after_the_last_swap():
    tape = demo_tape()
    trailing = LiquidityChange(
        kind="mint", block=10002, log_index=0, owner="0x" + "0a" * 20,
        lower_tick=42000, upper_tick=44000, liquidity=10,
    )
    fixture = Tape.model_validate({
        **tape.model_dump(exclude={"events"}), "events": [*tape.events[:4], trailing],
    })
    report, _ = replay_lp(fixture, include_pool_state=True)
    assert report["end_block"] == 10001
    assert report["pool_state"]["state_block"] == 10002
    assert report["pool_state"]["state_log_index"] == 0
