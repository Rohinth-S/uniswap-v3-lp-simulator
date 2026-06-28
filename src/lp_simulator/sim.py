from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

TICK_BASE = 1.0001


def price_to_tick(price: float, *, rounding: str = "nearest") -> int:
    """Convert a token1/token0 price to a Uniswap V3 tick."""
    if price <= 0 or not np.isfinite(price):
        raise ValueError("price must be finite and positive")
    raw = np.log(price) / np.log(TICK_BASE)
    if rounding == "floor":
        return int(np.floor(raw))
    if rounding == "ceil":
        return int(np.ceil(raw))
    if rounding != "nearest":
        raise ValueError("rounding must be floor, ceil, or nearest")
    return int(np.round(raw))


def tick_to_price(tick: int) -> float:
    """Return the exact geometric price represented by an integer tick."""
    if not isinstance(tick, (int, np.integer)):
        raise TypeError("tick must be an integer")
    return float(TICK_BASE ** int(tick))


@dataclass(frozen=True)
class TickRange:
    lower_tick: int
    upper_tick: int
    fee_tier: float = 0.0005

    def __post_init__(self) -> None:
        if self.lower_tick >= self.upper_tick:
            raise ValueError("lower_tick must be less than upper_tick")
        if self.fee_tier <= 0 or self.fee_tier > 1:
            raise ValueError("fee_tier must be between 0 and 1")

    @property
    def lower_price(self) -> float:
        return tick_to_price(self.lower_tick)

    @property
    def upper_price(self) -> float:
        return tick_to_price(self.upper_tick)

    def contains(self, price: float) -> bool:
        tick = price_to_tick(price, rounding="floor")
        return self.lower_tick <= tick < self.upper_tick


def accrue_fees(volume_usd: float, fee_tier: float, liquidity_share: float = 1.0) -> float:
    """Estimate LP fee income from swap volume and share of active liquidity."""
    if volume_usd < 0 or fee_tier < 0 or liquidity_share < 0:
        raise ValueError("volume, fee tier, and liquidity share cannot be negative")
    return volume_usd * fee_tier * min(liquidity_share, 1.0)

@dataclass(frozen=True)
class Position:
    lower: float
    upper: float
    capital: float
    fee_rate: float = 0.0005
    gas_usd: float = 2.5

    def __post_init__(self) -> None:
        if not (0 < self.lower < self.upper):
            raise ValueError("position bounds must satisfy 0 < lower < upper")
        if self.capital <= 0 or self.fee_rate < 0 or self.gas_usd < 0:
            raise ValueError("capital must be positive; fee and gas cannot be negative")


def rebalance_position(position: Position, current_price: float, width: float = 0.1) -> tuple[Position, TickRange]:
    """Create a tick-aligned range centred on ``current_price``."""
    if current_price <= 0 or width <= 0:
        raise ValueError("current_price and width must be positive")
    lower_tick = price_to_tick(current_price * (1 - width), rounding="floor")
    upper_tick = price_to_tick(current_price * (1 + width), rounding="ceil")
    ticks = TickRange(lower_tick, upper_tick, position.fee_rate)
    return Position(ticks.lower_price, ticks.upper_price, position.capital, position.fee_rate, position.gas_usd), ticks


def simulate_rebalanced(price_path, pos: Position, width: float = 0.1) -> pd.DataFrame:
    """Simulate active LP capital with an automatic tick-range rebalance.

    A rebalance is triggered only after the current range is exited.  The
    position's capital is preserved, while gas is charged once per event and
    fee income accrues only on price movement while in range.
    """
    path = np.asarray(price_path, dtype=float)
    if path.ndim != 1 or len(path) < 2 or not np.isfinite(path).all() or (path <= 0).any():
        raise ValueError("price_path must contain at least two finite positive prices")
    current, ticks = rebalance_position(pos, float(path[0]), width)
    rows: list[dict] = []
    previous = float(path[0])
    cumulative_fees = cumulative_gas = 0.0
    rebalances = 0
    for price in path:
        did_rebalance = False
        if not ticks.contains(float(price)):
            current, ticks = rebalance_position(current, float(price), width)
            rebalances += 1
            cumulative_gas += current.gas_usd
            did_rebalance = True
        active = ticks.contains(float(price))
        volume = current.capital * abs(np.log(float(price) / previous))
        cumulative_fees += accrue_fees(volume, current.fee_rate, 1.0 if active else 0.0)
        gross = current.capital * (0.5 * (float(price) / path[0]) + 0.5) if active else current.capital
        rows.append({"price": float(price), "lower_tick": ticks.lower_tick, "upper_tick": ticks.upper_tick,
                     "active": active, "fees": cumulative_fees, "gas": cumulative_gas,
                     "gross_value": gross, "rebalanced": did_rebalance, "rebalances": rebalances})
        previous = float(price)
    out = pd.DataFrame(rows)
    out["net_value"] = out.gross_value + out.fees - out.gas
    out["tick"] = out.price.map(lambda value: price_to_tick(value, rounding="floor"))
    return out

def prices(n: int = 240, seed: int = 3) -> np.ndarray:
    if n < 2:
        raise ValueError("n must be at least 2")
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))

def simulate(price_path, pos: Position, dynamic: bool = False) -> pd.DataFrame:
    path = np.asarray(price_path, dtype=float)
    if path.ndim != 1 or len(path) < 2 or not np.isfinite(path).all() or (path <= 0).any():
        raise ValueError("price_path must contain at least two finite positive prices")
    value, fees, gas = [], [], []
    active = pos.lower <= path[0] <= pos.upper
    last = path[0]
    for price in path:
        now = pos.lower <= price <= pos.upper
        gas.append(pos.gas_usd if dynamic and now != active else 0.0)
        active = now
        # This is a transparent 50/50 inventory approximation, not tick-exact accounting.
        share = 1.0 if now else 0.0
        fees.append(pos.capital * pos.fee_rate * abs(np.log(price / last)) * share)
        value.append(pos.capital * (0.5 * (price / path[0]) + 0.5) * share + pos.capital * (1 - share))
        last = price
    out = pd.DataFrame({"price": path, "gross_value": value, "fees": fees, "gas": gas})
    out["net_value"] = out.gross_value + out.fees.cumsum() - out.gas.cumsum()
    hold_value = pos.capital * (out.price / path[0] + 1) / 2
    out["hold_value"] = hold_value
    out["impermanent_loss_pct"] = 100 * (out.gross_value / hold_value - 1)
    out["active"] = out.price.between(pos.lower, pos.upper)
    return out

def summary(out: pd.DataFrame, initial: float) -> dict:
    if out.empty or initial <= 0:
        raise ValueError("simulation output must be non-empty and initial capital positive")
    return {
        "final_net_pnl": float(out.net_value.iloc[-1] - initial),
        "fee_income": float(out.fees.sum()),
        "gas_spend": float(out.gas.sum()),
        "time_in_range": float(out.active.mean()),
        "hold_return_pct": float((out.hold_value.iloc[-1] / initial - 1) * 100),
        "lp_return_pct": float((out.net_value.iloc[-1] / initial - 1) * 100),
    }
