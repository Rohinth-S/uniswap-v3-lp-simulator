"""Decode Uniswap V3 historical pool logs and read finalized, bounded ranges."""

import re

from eth_abi.exceptions import DecodingError
from web3 import Web3

from .rpc import JsonRpcClient
from .tape import Initialize, LiquidityChange, PoolMetadata, Swap, Tape

SIGNATURES = {
    "initialize": "Initialize(uint160,int24)",
    "mint": "Mint(address,address,int24,int24,uint128,uint256,uint256)",
    "burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
    "swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
}
TOPICS = {"0x" + Web3.keccak(text=signature).hex(): kind for kind, signature in SIGNATURES.items()}
CODEC = Web3().codec


def _decode(types: list[str], data: str) -> tuple:
    if not re.fullmatch(r"0x[0-9a-fA-F]*", str(data)) or len(data) != 2 + 64 * len(types):
        raise ValueError("incomplete ABI word sequence")
    try:
        return CODEC.decode(types, bytes.fromhex(data[2:]))
    except (DecodingError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid pool event ABI") from exc


def decode_logs(meta: PoolMetadata, logs: list[dict]) -> Tape:
    events = []
    for log in logs:
        try:
            address = log["address"].lower()
            topics = [topic.lower() for topic in log["topics"]]
            kind = TOPICS.get(topics[0]) if topics else None
            if address != meta.pool or kind is None:
                continue
            if log.get("removed") or not log.get("blockHash") or not log.get("transactionHash"):
                raise ValueError("pool log must be mined and canonical in its capture")
            key = {
                "block": int(log["blockNumber"], 16), "log_index": int(log["logIndex"], 16),
                "block_hash": log["blockHash"], "tx_hash": log["transactionHash"],
            }
            if kind == "initialize" and len(topics) == 1:
                price, tick = _decode(["uint160", "int24"], log["data"])
                event = Initialize(**key, sqrt_price_x96=price, tick=tick)
            elif kind in {"mint", "burn"} and len(topics) == 4:
                owner = _decode(["address"], topics[1])[0]
                lower = _decode(["int24"], topics[2])[0]
                upper = _decode(["int24"], topics[3])[0]
                types = ["uint128", "uint256", "uint256"]
                values = _decode(["address", *types] if kind == "mint" else types, log["data"])
                event = LiquidityChange(
                    **key, kind=kind, owner=owner, lower_tick=lower, upper_tick=upper, liquidity=values[-3],
                )
            elif kind == "swap" and len(topics) == 3:
                amount0, amount1, price, liquidity, tick = _decode(
                    ["int256", "int256", "uint160", "uint128", "int24"], log["data"],
                )
                event = Swap(
                    **key, amount0=amount0, amount1=amount1, sqrt_price_x96=price,
                    active_liquidity=liquidity, tick=tick,
                )
            else:
                raise ValueError("unexpected number of indexed values for a pool event")
            events.append(event)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("pool log is missing a required JSON-RPC field") from exc
    return Tape(**meta.model_dump(), events=sorted(events, key=lambda event: (event.block, event.log_index)))


def fetch_logs(
    rpc: JsonRpcClient, meta: PoolMetadata, *, from_block: int, to_block: int, chunk_blocks: int = 400,
) -> list[dict]:
    if not 0 <= from_block <= to_block or not 1 <= chunk_blocks <= 2_000 or to_block - from_block >= 50_000:
        raise ValueError("provide an ordered range of at most 50,000 blocks and chunk size in [1, 2000]")
    if int(rpc.call("eth_chainId", []), 16) != meta.chain_id:
        raise ValueError("RPC chain ID does not match pool metadata")
    finalized = rpc.call("eth_getBlockByNumber", ["finalized", False])
    if not isinstance(finalized, dict) or not finalized.get("number") or int(finalized["number"], 16) < to_block:
        raise ValueError("end block must be finalized on the selected provider")
    logs = []
    for start in range(from_block, to_block + 1, chunk_blocks):
        end = min(start + chunk_blocks - 1, to_block)
        result = rpc.get_logs(from_block=start, to_block=end, address=meta.pool, topics=[list(TOPICS)])
        if not isinstance(result, list) or any(
            not start <= int(log.get("blockNumber", "-0x1"), 16) <= end for log in result
        ):
            raise ValueError("provider returned logs outside the requested block interval")
        logs.extend(result)
    return logs
