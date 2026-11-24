"""Create a seeded pool with independent background liquidity and swap amounts."""

import numpy as np

from .pool_state import Q96, TICK_BASE
from .tape import Initialize, LiquidityChange, Swap, Tape


def demo_tape(seed: int = 11) -> Tape:
    rng = np.random.default_rng(seed)
    first = 46_050
    tick = first

    def sqrt_x96(t):
        return round(Q96 * TICK_BASE ** (t / 2))

    owner = "0x" + "02" * 20
    foundational = 60_000 * 10**18
    inner = 30_000 * 10**18
    events = [
        Initialize(block=10_000, log_index=0, tick=first, sqrt_price_x96=sqrt_x96(first)),
        LiquidityChange(
            kind="mint", block=10_000, log_index=1, owner=owner,
            lower_tick=42_000, upper_tick=50_000, liquidity=foundational,
        ),
        LiquidityChange(
            kind="mint", block=10_000, log_index=2, owner=owner,
            lower_tick=45_700, upper_tick=46_650, liquidity=inner,
        ),
    ]
    for index in range(100):
        block = 10_001 + index
        if index == 30:
            events.append(LiquidityChange(
                kind="burn", block=block, log_index=0, owner=owner,
                lower_tick=45_700, upper_tick=46_650, liquidity=10_000 * 10**18,
            ))
            inner -= 10_000 * 10**18
        previous = tick
        regime = (0.0, 90.0, -100.0, 45.0)[index // 25]
        tick = max(42_500, min(49_500, tick + int(rng.normal(regime, 160))))
        notional = int(rng.integers(5_000, 35_000))
        approx_price = TICK_BASE ** tick
        direction = 1 if tick > previous else -1
        events.append(Swap(
            block=block, log_index=1, tick=tick, sqrt_price_x96=sqrt_x96(tick),
            active_liquidity=foundational + (inner if 45_700 <= tick < 46_650 else 0),
            amount0=-direction * round(notional / approx_price * 10**18),
            amount1=direction * notional * 10**18,
        ))
    return Tape(
        chain_id=1, pool="0x" + "01" * 20, token0_decimals=18, token1_decimals=18,
        fee_pips=3_000, tick_spacing=10, events=events,
    )
