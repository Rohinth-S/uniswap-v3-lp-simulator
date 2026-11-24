"""Concentrated-range inventory and counterfactual fee attribution."""

from dataclasses import dataclass
from math import floor, log

from .pool_state import Q96, TICK_BASE, PoolState
from .tape import MAX_TICK, MIN_TICK, PoolMetadata, ReplayConfig, Swap

LOG_TICK = log(TICK_BASE)


def _sqrt_raw(tick: int) -> float:
    return TICK_BASE ** (tick / 2)


def _inventory(sqrt_price: float, lower: float, upper: float, liquidity: float) -> tuple[float, float]:
    clamped = min(max(sqrt_price, lower), upper)
    return liquidity * (1 / clamped - 1 / upper), liquidity * (clamped - lower)


@dataclass
class RangePosition:
    lower_tick: int
    upper_tick: int
    liquidity: float
    sqrt_scale: float
    fees_quote: float = 0
    gas_quote: float = 0
    slippage_quote: float = 0
    rebalances: int = 0

    def contains(self, tick: int) -> bool:
        return self.lower_tick <= tick < self.upper_tick

    def amounts(self, sqrt_price: float) -> tuple[float, float]:
        return _inventory(
            sqrt_price, _sqrt_raw(self.lower_tick) * self.sqrt_scale,
            _sqrt_raw(self.upper_tick) * self.sqrt_scale, self.liquidity,
        )

    @classmethod
    def open(
        cls, tick: int, sqrt_price: float, capital: float, pool: PoolMetadata, config: ReplayConfig,
    ) -> "RangePosition":
        spacing = pool.tick_spacing
        lower = max(-(abs(MIN_TICK) // spacing) * spacing, ((tick - config.half_width_ticks) // spacing) * spacing)
        upper = min((MAX_TICK // spacing) * spacing, -(-(tick + config.half_width_ticks) // spacing) * spacing)
        if lower >= upper:
            raise ValueError("no usable strategy range near the current tick")
        scale = 10 ** ((pool.token0_decimals - pool.token1_decimals) / 2)
        position = cls(lower, upper, 1.0, scale)
        x, y = position.amounts(sqrt_price)
        position.liquidity = capital / (x * sqrt_price**2 + y)
        return position

    def maybe_rebalance(self, tick: int, sqrt_price: float, pool: PoolMetadata, config: ReplayConfig) -> bool:
        if self.contains(tick):
            return False
        x, y = self.amounts(sqrt_price)
        price = sqrt_price**2
        gross_value = x * price + y
        new = self.open(tick, sqrt_price, gross_value, pool, config)
        if (new.lower_tick, new.upper_tick) == (self.lower_tick, self.upper_tick):
            return False
        new_x, _ = new.amounts(sqrt_price)
        slippage = abs(new_x - x) * price * config.turnover_slippage_bps / 10_000
        cost = config.gas_quote + slippage
        if gross_value <= cost:
            return False
        self.lower_tick, self.upper_tick = new.lower_tick, new.upper_tick
        self.liquidity = new.liquidity * (gross_value - cost) / gross_value
        self.gas_quote += config.gas_quote
        self.slippage_quote += slippage
        self.rebalances += 1
        return True


def _fee_share(position: RangePosition, state: PoolState, start: float, end: float, token0_in: bool, raw_liquidity: float) -> float:
    if start == end:
        if not position.contains(state.tick):
            return 0
        return raw_liquidity / (state.active_at(state.tick) + raw_liquidity)
    lower, upper = sorted((start, end))
    low_tick = floor(2 * log(lower) / LOG_TICK)
    high_tick = floor(2 * log(upper) / LOG_TICK) + 1
    ticks = set(state.interior_ticks(low_tick - 1, high_tick))
    ticks.update((position.lower_tick, position.upper_tick))
    boundaries = [lower, *sorted({value for tick in ticks if lower < (value := _sqrt_raw(tick)) < upper}), upper]
    volume = lp_weight = 0.0
    for left, right in zip(boundaries, boundaries[1:]):
        tick = floor((log(left) + log(right)) / LOG_TICK)
        active = state.active_at(tick)
        movement = (right - left) / (left * right) if token0_in else right - left
        weight = active * movement
        volume += weight
        if position.contains(tick):
            lp_weight += weight * raw_liquidity / (active + raw_liquidity)
    return lp_weight / volume if volume else 0.0


def swap_fees(
    position: RangePosition, state: PoolState, event: Swap, pool: PoolMetadata, config: ReplayConfig,
) -> float:
    start = state.sqrt_price_x96 / Q96
    end = event.sqrt_price_x96 / Q96
    token0_in = event.amount0 > 0
    price = (end * position.sqrt_scale) ** 2
    notional = (
        event.amount0 / 10**pool.token0_decimals * price if token0_in else event.amount1 / 10**pool.token1_decimals
    )
    raw_liquidity = position.liquidity * 10 ** ((pool.token0_decimals + pool.token1_decimals) / 2)
    share = _fee_share(position, state, start, end, token0_in, raw_liquidity)
    return notional * pool.fee_pips / 10**6 * (1 - config.protocol_fee_share) * share
