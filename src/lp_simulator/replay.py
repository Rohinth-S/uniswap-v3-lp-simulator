"""Replay observed pool events with static and actively rebalanced LPs."""

from .pool_state import Q96, PoolState
from .positions import RangePosition, swap_fees, swap_notional_quote
from .tape import ReplayConfig, Swap, Tape


def replay_lp(
    tape: Tape, config: ReplayConfig = ReplayConfig(), *, from_swap: int = 0, until_swap: int | None = None,
    include_pool_state: bool = False,
) -> tuple[dict, list[dict]]:
    if from_swap < 0 or (until_swap is not None and until_swap <= from_swap):
        raise ValueError("invalid swap evaluation range")
    market = PoolState(tape)
    positions = {}
    hold_tokens = {}
    rows = []
    observed = 0
    scale = 10 ** ((tape.token0_decimals - tape.token1_decimals) / 2)
    for event in tape.events:
        if until_swap is not None and observed >= until_swap:
            break
        if not isinstance(event, Swap):
            market.apply(event)
            continue
        swap_index = observed
        observed += 1
        if swap_index < from_swap:
            market.apply(event)
            continue
        if not positions:
            opening_sqrt = market.sqrt_price_x96 / Q96 * scale
            for name in ("static", "rebalanced"):
                position = RangePosition.open(market.tick, opening_sqrt, config.capital_quote, tape, config)
                positions[name] = position
                hold_tokens[name] = position.amounts(opening_sqrt)
        swap_fee = {name: swap_fees(position, market, event, tape, config) for name, position in positions.items()}
        tick_before, sqrt_x96_before = market.tick, market.sqrt_price_x96
        market.apply(event)
        sqrt_price = market.sqrt_price_x96 / Q96 * scale
        price = sqrt_price**2
        notional = swap_notional_quote(event, tape, price)
        for name, position in positions.items():
            was_active = position.contains(market.tick)
            position.fees_quote += swap_fee[name]
            rebalanced = name == "rebalanced" and position.maybe_rebalance(market.tick, sqrt_price, tape, config)
            x, y = position.amounts(sqrt_price)
            hold_x, hold_y = hold_tokens[name]
            value = x * price + y
            hold = hold_x * price + hold_y
            total = value + position.fees_quote
            rows.append({
                "strategy": name, "swap_index": swap_index, "block": event.block, "log_index": event.log_index,
                "tick": market.tick, "price_token1": price, "active_liquidity_raw": event.active_liquidity,
                "tick_before": tick_before, "sqrt_price_x96_before": str(sqrt_x96_before),
                "sqrt_price_x96_after": str(event.sqrt_price_x96),
                "swap_notional_quote": notional,
                "lower_tick": position.lower_tick, "upper_tick": position.upper_tick,
                "inside_range_at_swap_end": was_active, "rebalanced": rebalanced, "fee_quote": swap_fee[name],
                "rebalance_reason": position.last_rebalance_reason if name == "rebalanced" else "static_policy",
                "cumulative_fees_quote": position.fees_quote, "cumulative_gas_quote": position.gas_quote,
                "cumulative_turnover_cost_quote": position.slippage_quote,
                "cumulative_turnover_quote": position.turnover_quote,
                "token0_units": x, "token1_units": y, "position_value_quote": value,
                "hold_value_quote": hold, "net_value_quote": total, "value_minus_hold_quote": total - hold,
                "inventory_vs_hold_pct": 100 * (value / hold - 1),
            })
    if not rows:
        raise ValueError("dataset does not contain a swap in the evaluation range")
    summaries = {}
    for name in positions:
        selected = [row for row in rows if row["strategy"] == name]
        last = selected[-1]
        peak, drawdown, drawdown_pct = config.capital_quote, 0.0, 0.0
        for row in selected:
            mark = row["net_value_quote"]
            peak = max(peak, mark)
            drawdown = max(drawdown, peak - mark)
            drawdown_pct = max(drawdown_pct, (peak - mark) / peak * 100)
        summaries[name] = {
            "max_drawdown_quote": drawdown, "max_drawdown_pct": drawdown_pct,
            "net_value_quote": last["net_value_quote"],
            "net_pnl_quote": last["net_value_quote"] - config.capital_quote,
            "hold_value_quote": last["hold_value_quote"], "value_minus_hold_quote": last["value_minus_hold_quote"],
            "inventory_vs_hold_pct": last["inventory_vs_hold_pct"],
            "fee_income_quote": last["cumulative_fees_quote"], "gas_quote": last["cumulative_gas_quote"],
            "turnover_cost_quote": last["cumulative_turnover_cost_quote"],
            "turnover_quote": last["cumulative_turnover_quote"],
            "rebalances": positions[name].rebalances,
            "in_range_swap_end_fraction": sum(row["inside_range_at_swap_end"] for row in selected) / len(selected),
        }
    report = {
        "chain_id": tape.chain_id, "pool": tape.pool, "quote_unit": "token1",
        "config": config.model_dump(), "first_evaluated_swap": from_swap, "swaps": len(rows) // 2,
        "end_block": rows[-1]["block"], "end_tick": market.tick,
        "first_evaluated_block": rows[0]["block"], "last_evaluated_swap": rows[-1]["swap_index"],
        "first_evaluated_log": rows[0]["log_index"], "last_evaluated_log": rows[-1]["log_index"],
        "reconstructed_positions": len(market.positions),
        "nonzero_net_ticks": len(market.liquidity_net),
        "initialized_ticks": len(market.liquidity_gross),
        "strategies": summaries,
    }
    if include_pool_state:
        report["pool_state"] = {
            "active_liquidity_raw": str(market.active_at(market.tick)),
            "sqrt_price_x96": str(market.sqrt_price_x96),
            "positions": [
                {"owner": owner, "lower_tick": lower, "upper_tick": upper, "liquidity_raw": str(raw)}
                for (owner, lower, upper), raw in sorted(market.positions.items())
            ],
            "ticks": [
                {"tick": tick, "gross_liquidity_raw": str(gross), "net_liquidity_raw": str(market.liquidity_net.get(tick, 0))}
                for tick, gross in sorted(market.liquidity_gross.items())
            ],
        }
    return report, rows


def holdout_width_study(
    tape: Tape, config: ReplayConfig, *, widths: list[int], fraction: float = 0.25,
    include_pool_state: bool = False,
) -> tuple[dict, list[dict]]:
    if not 0 < fraction < 1 or not widths:
        raise ValueError("holdout fraction must be between 0 and 1 and widths must be supplied")
    if len(widths) > 16 or any(not 1 <= width <= 300_000 for width in widths):
        raise ValueError("provide at most 16 widths between 1 and 300000 ticks")
    swaps = sum(isinstance(event, Swap) for event in tape.events)
    cutoff = int(swaps * (1 - fraction))
    if cutoff < 2 or swaps - cutoff < 2:
        result, ledger = replay_lp(tape, config, include_pool_state=include_pool_state)
        return {**result, "selection": {"status": "insufficient_swaps"}}, ledger

    train = []
    for width in sorted(set(widths)):
        values = {**config.model_dump(), "half_width_ticks": width}
        candidate = ReplayConfig.model_validate(values)
        result, _ = replay_lp(tape, candidate, until_swap=cutoff)
        score = result["strategies"]["rebalanced"]["value_minus_hold_quote"]
        train.append({"half_width_ticks": width, "training_value_minus_hold_quote": score})
    best = max(train, key=lambda row: row["training_value_minus_hold_quote"])["half_width_ticks"]
    chosen = ReplayConfig.model_validate({**config.model_dump(), "half_width_ticks": best})
    result, ledger = replay_lp(tape, chosen, from_swap=cutoff, include_pool_state=include_pool_state)
    return {
        **result, "selection": {
            "status": "holdout", "training_swaps": cutoff, "holdout_swaps": swaps - cutoff,
            "selected_half_width_ticks": best, "training_candidates": train, "holdout_fraction": fraction,
        },
    }, ledger
