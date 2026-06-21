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
