"""Replay normalized pool events or ingest bounded finalized RPC logs."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from .artifacts import write_text_atomic

from . import __version__
from .artifacts import require_distinct_paths

from .events import decode_logs, fetch_logs
from .fixtures import demo_tape
from .replay import holdout_width_study, replay_lp
from .rpc import JsonRpcClient
from .tape import PoolMetadata, ReplayConfig, Tape


def _write(path: Path | None, payload: str) -> None:
    if path:
        write_text_atomic(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct Uniswap V3 ticks and compare LP strategies")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, help="normalized pool event JSON")
    inputs.add_argument("--raw-logs", type=Path, help="captured eth_getLogs result array")
    inputs.add_argument("--rpc-range", type=int, nargs=2, metavar=("FROM_BLOCK", "TO_BLOCK"))
    parser.add_argument("--metadata", type=Path, help="pool metadata for raw or RPC logs")
    parser.add_argument("--network", default="ethereum")
    parser.add_argument("--chunk-blocks", type=int, default=400)
    parser.add_argument("--config", type=Path, help="capital, width, fee share and rebalance costs as JSON")
    parser.add_argument("--width-candidates", type=int, nargs="+", help="select a width on a training prefix")
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--from-swap", type=int, default=0)
    parser.add_argument("--until-swap", type=int)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ledger", type=Path, help="optional per-strategy swap CSV")
    parser.add_argument("--write-tape", type=Path, help="export the normalized replay input")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    try:
        require_distinct_paths(
            tape=args.input,
            raw_logs=args.raw_logs,
            metadata=args.metadata,
            configuration=args.config,
            report=args.output,
            ledger=args.ledger,
            normalized_output=args.write_tape,
        )
        if args.input:
            raw = args.input.read_bytes()
            tape = Tape.model_validate_json(raw)
            source = {"kind": "normalized_tape", "sha256": hashlib.sha256(raw).hexdigest()}
        elif args.raw_logs or args.rpc_range:
            if args.metadata is None:
                parser.error("raw logs and RPC ranges require --metadata")
            meta_raw = args.metadata.read_bytes()
            meta = PoolMetadata.model_validate_json(meta_raw)
            if args.rpc_range:
                first, last = args.rpc_range
                logs = fetch_logs(
                    JsonRpcClient(network=args.network), meta, from_block=first, to_block=last,
                    chunk_blocks=args.chunk_blocks,
                )
                source = {"kind": "finalized_rpc_range", "from_block": first, "to_block": last}
            else:
                raw = args.raw_logs.read_bytes()
                logs = json.loads(raw)
                if not isinstance(logs, list):
                    raise ValueError("raw logs must be a JSON array")
                source = {"kind": "captured_rpc_logs", "sha256": hashlib.sha256(raw).hexdigest()}
            tape = decode_logs(meta, logs)
            source["metadata_sha256"] = hashlib.sha256(meta_raw).hexdigest()
        else:
            tape = demo_tape(args.seed)
            source = {"kind": "synthetic", "seed": args.seed}
        config_raw = args.config.read_bytes() if args.config else None
        cfg = ReplayConfig.model_validate_json(config_raw) if config_raw is not None else ReplayConfig()
        if config_raw is not None:
            source["configuration_sha256"] = hashlib.sha256(config_raw).hexdigest()
        if args.width_candidates:
            if args.from_swap or args.until_swap is not None:
                raise ValueError("explicit swap windows cannot be combined with width selection")
            report, ledger = holdout_width_study(
                tape, cfg, widths=args.width_candidates, fraction=args.holdout_fraction,
            )
        else:
            report, ledger = replay_lp(tape, cfg, from_swap=args.from_swap, until_swap=args.until_swap)
        report["source"] = {**source, "events": len(tape.events)}
        if args.ledger:
            args.ledger.parent.mkdir(parents=True, exist_ok=True)
            with args.ledger.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(ledger[0]))
                writer.writeheader()
                writer.writerows(ledger)
        _write(args.write_tape, tape.model_dump_json(indent=2) + "\n")
        report["producer"] = {"name": "uniswap-v3-lp-simulator", "version": __version__, "format_version": 1}
        payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
        _write(args.output, payload)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(payload, end="")


if __name__ == "__main__":
    main()
