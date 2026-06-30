"""Fee, gas, and range-rebalance analytics for LP simulations."""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class RebalanceAnalytics:
    fee_income: float
    gas_spend: float
    fee_apr_pct: float
    gas_drag_pct: float
    net_return_pct: float
    rebalances: int
    time_in_range: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def rebalance_events(output: pd.DataFrame) -> pd.DataFrame:
    """Return a compact event stream from ``simulate_rebalanced`` output."""
    required = {"rebalanced", "price", "lower_tick", "upper_tick", "gas"}
    missing = required.difference(output.columns)
    if missing:
        raise ValueError(f"simulation output missing columns: {sorted(missing)}")
    return output.loc[output["rebalanced"].astype(bool), ["price", "lower_tick", "upper_tick", "gas"]].copy()


def analyze_rebalances(output: pd.DataFrame, initial_capital: float, observations_per_year: int = 365) -> RebalanceAnalytics:
    """Measure fee yield and rebalance drag over a simulation path."""
    if output.empty or initial_capital <= 0 or observations_per_year < 1:
        raise ValueError("output must be non-empty, capital positive, and observations_per_year valid")
    required = {"fees", "gas", "net_value", "gross_value", "active", "rebalanced"}
    missing = required.difference(output.columns)
    if missing:
        raise ValueError(f"simulation output missing columns: {sorted(missing)}")
    fee_income = float(output["fees"].iloc[-1])
    gas_spend = float(output["gas"].iloc[-1])
    periods = max(len(output) - 1, 1)
    fee_apr = fee_income / initial_capital * observations_per_year / periods * 100
    return RebalanceAnalytics(fee_income, gas_spend, fee_apr, gas_spend / initial_capital * 100,
                              (float(output["net_value"].iloc[-1]) / initial_capital - 1) * 100,
                              int(output["rebalanced"].sum()), float(output["active"].mean()))
