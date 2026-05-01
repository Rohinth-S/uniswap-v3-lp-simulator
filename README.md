# Uniswap V3 Market Microstructure & Liquidity Provision Simulator

A historical-replay style simulator for concentrated liquidity positions. It measures time-in-range, fee income, gas/rebalancing cost, and net value under static or dynamic range policies.

## Architecture
```mermaid
flowchart LR
  P[Price/tick replay] --> M[Range membership]
  M --> C[Fee + inventory model]
  C --> G[Gas/rebalance ledger]
  G --> O[Net PnL + IL report]
```

## Workflow
1. Replay a deterministic price path.
2. Mark whether price is inside the LP range.
3. Accrue fees from realized movement while in range.
4. Charge gas when a dynamic policy changes exposure.
5. Compare net value with a passive hold benchmark.

## Design rationale
The simulator keeps inventory and costs explicit instead of hiding them behind an APY formula. Static ranges provide a clean benchmark; dynamic ranges are useful only when the extra fees exceed rebalancing and adverse-selection costs.

## Run
```bash
pip install -e .
python -m lp_simulator.cli
pytest -q
```

## Limitations
The current engine uses a price-level approximation rather than full Uniswap tick liquidity and sqrt-price accounting. It is a research scaffold; validate against decoded pool state before using it for capital allocation.
