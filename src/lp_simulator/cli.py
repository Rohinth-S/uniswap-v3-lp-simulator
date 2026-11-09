import argparse, json
from .sim import prices, Position, simulate, summary

def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a concentrated-liquidity position")
    parser.add_argument("--output", help="optional JSON summary path")
    args = parser.parse_args()
    position = Position(92, 108, 10_000)
    result = summary(simulate(prices(), position, dynamic=True), position.capital)
    if args.output:
        from pathlib import Path
        destination = Path(args.output); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(result, indent=2))
    print(result)

if __name__ == "__main__":
    main()
