from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.request import Request, urlopen

RPC_NETWORKS = {
    "ethereum": "https://eth.llamarpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "polygon": "https://polygon-rpc.com",
    "ethereum-sepolia": "https://rpc.sepolia.org",
    "arbitrum-sepolia": "https://sepolia-rollup.arbitrum.io/rpc",
    "polygon-amoy": "https://rpc-amoy.polygon.technology",
}

@dataclass
class JsonRpcClient:
    network: str = "ethereum"
    url: str | None = None
    timeout: float = 15.0

    def __post_init__(self) -> None:
        selected_network = os.getenv("RPC_NETWORK", self.network)
        explicit = self.url or os.getenv("RPC_URL")
        if selected_network not in RPC_NETWORKS and not explicit:
            raise ValueError(f"unknown network {selected_network!r}; provide an explicit url")
        self.network = selected_network
        self.url = explicit or RPC_NETWORKS[self.network]

    def request_payload(self, method: str, params: list) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()

    def call(self, method: str, params: list) -> object:
        req = Request(
            self.url, data=self.request_payload(method, params),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "uniswap-v3-lp-simulator/0.2",
            },
        )
        with urlopen(req, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
            raise RuntimeError(f"JSON-RPC {method} returned an invalid response envelope")
        if "error" in payload:
            error = payload["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise RuntimeError(f"JSON-RPC {method} failed with error code {code}")
        if "result" not in payload:
            raise RuntimeError(f"JSON-RPC {method} returned no result")
        return payload["result"]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def get_logs(
        self, *, from_block: int, to_block: int, address: str | None = None,
        topics: list[str | list[str] | None] | None = None,
    ) -> list[dict]:
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid block range")
        log_filter = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        if address:
            log_filter["address"] = address
        if topics:
            log_filter["topics"] = topics
        return self.call("eth_getLogs", [log_filter])
