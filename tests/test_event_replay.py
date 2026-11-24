import json
from pathlib import Path

import pytest

from lp_simulator.events import CODEC, TOPICS, decode_logs, fetch_logs
from lp_simulator.fixtures import demo_tape
from lp_simulator.pool_state import PoolState, Q96
from lp_simulator.replay import holdout_width_study, replay_lp
from lp_simulator.tape import PoolMetadata, ReplayConfig, Tape


def test_prefix_reconstructs_liquidity_and_static_and_dynamic_economics():
    tape = demo_tape()
    result, rows = replay_lp(tape)
    assert result["swaps"] == 100 and len(rows) == 200
    assert result["strategies"]["static"]["gas_quote"] == 0
    assert result["strategies"]["rebalanced"]["rebalances"] > 0
    for row in rows:
        assert row["position_value_quote"] + row["cumulative_fees_quote"] == pytest.approx(row["net_value_quote"])
        assert row["fee_quote"] >= 0
    assert rows[0]["fee_quote"] == rows[1]["fee_quote"]

    inputs = tape.model_dump()
    first_swap = next(e for e in inputs["events"] if e["kind"] == "swap")
    first_swap["active_liquidity"] += 1
    with pytest.raises(ValueError, match="differs from emitted"):
        replay_lp(Tape.model_validate(inputs))


def test_width_study_is_chronological_and_does_not_fit_on_holdout():
    tape = demo_tape()
    config = ReplayConfig()
    summary, rows = holdout_width_study(tape, config, widths=[250, 500, 750])
    assert summary["selection"]["training_swaps"] == rows[0]["swap_index"] == 75
    assert summary["swaps"] == 25
    winners = summary["selection"]["training_candidates"]
    assert summary["selection"]["selected_half_width_ticks"] == max(
        winners, key=lambda row: row["training_value_minus_hold_quote"],
    )["half_width_ticks"]
    assert all(row["strategy"] in {"static", "rebalanced"} for row in rows)


def test_raw_abi_decode_applies_signed_ticks_and_liquidity_moves():
    pool = "0x" + "11" * 20
    owner = "0x" + "22" * 20
    meta = PoolMetadata(chain_id=1, pool=pool, token0_decimals=18, token1_decimals=6, fee_pips=500, tick_spacing=10)
    kind = {value: topic for topic, value in TOPICS.items()}

    def encoded(types, values):
        return "0x" + CODEC.encode(types, values).hex()

    def log(index, name, types, data, indexed=()):
        return {
            "address": pool, "blockNumber": "0x64", "logIndex": hex(index), "blockHash": "0x" + "ab" * 32,
            "transactionHash": "0x" + "cd" * 32, "topics": [kind[name], *indexed], "data": encoded(types, data),
        }

    bounds = [encoded(["address"], [owner]), encoded(["int24"], [-10]), encoded(["int24"], [10])]
    raw = [
        log(0, "initialize", ["uint160", "int24"], [Q96, 0]),
        log(1, "mint", ["address", "uint128", "uint256", "uint256"], [owner, 20, 0, 0], bounds),
        log(2, "burn", ["uint128", "uint256", "uint256"], [5, 0, 0], bounds),
        log(3, "swap", ["int256", "int256", "uint160", "uint128", "int24"],
            [10, -10, Q96, 15, 0], [bounds[0], bounds[0]]),
    ]
    tape = decode_logs(meta, list(reversed(raw)))
    state = PoolState(tape)
    for event in tape.events:
        state.apply(event)
    assert state.active_at(0) == 15 and state.active_at(10) == 0
    assert state.positions[(owner, -10, 10)] == 15


def test_rpc_rejects_unfinalized_range_and_chunks_requests():
    meta = PoolMetadata(
        chain_id=42161, pool="0x" + "11" * 20, token0_decimals=6, token1_decimals=18, fee_pips=500, tick_spacing=10,
    )

    class Provider:
        captured = []

        def call(self, method, args):
            return hex(42161) if method == "eth_chainId" else {"number": "0x15"}

        def get_logs(self, **options):
            assert options["address"] == meta.pool and set(options["topics"][0]) == set(TOPICS)
            self.captured.append((options["from_block"], options["to_block"]))
            return []

    provider = Provider()
    assert fetch_logs(provider, meta, from_block=14, to_block=21, chunk_blocks=3) == []
    assert provider.captured == [(14, 16), (17, 19), (20, 21)]
    with pytest.raises(ValueError, match="finalized"):
        fetch_logs(provider, meta, from_block=14, to_block=22)


def test_archived_arbitrum_pool_reconciles_emitted_liquidity():
    examples = Path(__file__).resolve().parents[1] / "examples"
    metadata = PoolMetadata.model_validate_json((examples / "arbitrum-pool.json").read_bytes())
    raw_logs = json.loads((examples / "arbitrum-opening-logs.json").read_bytes())
    tape = decode_logs(metadata, raw_logs)
    report, ledger = replay_lp(tape)
    assert report["chain_id"] == 42161
    assert tape.events[0].block == 100_909
    assert report["end_block"] == 228_740 and report["swaps"] == 4
    assert len(ledger) == 8
