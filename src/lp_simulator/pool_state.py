"""Maintain owner-range liquidity and a cached prefix sum of initialized ticks."""

from bisect import bisect_right
from itertools import accumulate
from math import log

from .tape import Initialize, LiquidityChange, PoolMetadata, PositionRange, Swap

Q96 = 2**96
TICK_BASE = 1.0001


class PoolState:
    def __init__(self, pool: PoolMetadata):
        self.spacing = pool.tick_spacing
        self.positions: dict[tuple[str, int, int], int] = {}
        self.liquidity_net: dict[int, int] = {}
        self.tick: int | None = None
        self.sqrt_price_x96: int | None = None
        self._ticks: list[int] = []
        self._prefix: list[int] = []
        self._dirty = False
        if pool.opening:
            seen = set()
            for position in pool.opening.positions:
                key = (position.owner, position.lower_tick, position.upper_tick)
                if key in seen:
                    raise ValueError("duplicate range in opening snapshot")
                seen.add(key)
                self._change(position, position.liquidity)
            self._set_price(pool.opening.tick, pool.opening.sqrt_price_x96)
            self._validate_active(pool.opening.active_liquidity)

    def _change(self, position: PositionRange, delta: int) -> None:
        lower, upper = position.lower_tick, position.upper_tick
        if lower % self.spacing or upper % self.spacing:
            raise ValueError("position endpoints must be aligned to pool tick spacing")
        if not delta:
            return
        key = (position.owner, lower, upper)
        next_liquidity = self.positions.get(key, 0) + delta
        if next_liquidity < 0:
            raise ValueError("liquidity burn exceeds reconstructed owner-range balance")
        if next_liquidity:
            self.positions[key] = next_liquidity
        else:
            del self.positions[key]
        for tick, change in ((lower, delta), (upper, -delta)):
            value = self.liquidity_net.get(tick, 0) + change
            if value:
                self.liquidity_net[tick] = value
            else:
                self.liquidity_net.pop(tick, None)
        self._dirty = True

    def active_at(self, tick: int) -> int:
        self._refresh()
        index = bisect_right(self._ticks, tick) - 1
        return self._prefix[index] if index >= 0 else 0

    def _refresh(self) -> None:
        if self._dirty:
            self._ticks = sorted(self.liquidity_net)
            self._prefix = list(accumulate(self.liquidity_net[tick] for tick in self._ticks))
            self._dirty = False

    def interior_ticks(self, lower: int, upper: int) -> list[int]:
        self._refresh()
        return self._ticks[bisect_right(self._ticks, lower):bisect_right(self._ticks, upper)]

    def _validate_active(self, reported: int) -> None:
        active = self.active_at(self.tick)
        if active != reported:
            raise ValueError(f"reconstructed active liquidity {active} differs from emitted {reported}")

    def _set_price(self, tick: int, sqrt_price_x96: int) -> None:
        implied = 2 * log(sqrt_price_x96 / Q96) / log(TICK_BASE)
        # At an exact tick boundary a zero-for-one swap can report the preceding tick.
        if not tick - 0.001 <= implied <= tick + 1.001:
            raise ValueError("reported tick and sqrt price disagree")
        self.tick, self.sqrt_price_x96 = tick, sqrt_price_x96

    def apply(self, event: Initialize | LiquidityChange | Swap) -> None:
        if isinstance(event, Initialize):
            self._set_price(event.tick, event.sqrt_price_x96)
        elif isinstance(event, LiquidityChange):
            self._change(event, event.liquidity if event.kind == "mint" else -event.liquidity)
        else:
            try:
                change = event.sqrt_price_x96 - self.sqrt_price_x96
                if (change > 0 and event.amount0 > 0) or (change < 0 and event.amount1 > 0):
                    raise ValueError("swap direction disagrees with the price movement")
                self._set_price(event.tick, event.sqrt_price_x96)
                self._validate_active(event.active_liquidity)
            except ValueError as exc:
                raise ValueError(f"block {event.block}, log {event.log_index}: {exc}") from exc
