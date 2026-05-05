from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

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
