"""Normalized Uniswap V3 pool events and replay configuration."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MIN_TICK = -887_272
MAX_TICK = 887_272
MIN_SQRT_X96 = 4_295_128_739
MAX_SQRT_X96 = 1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342

Address = Annotated[str, StringConstraints(to_lower=True, pattern=r"^0x[0-9a-fA-F]{40}$")]
Hash = Annotated[str, StringConstraints(to_lower=True, pattern=r"^0x[0-9a-fA-F]{64}$")]
Tick = Annotated[int, Field(ge=MIN_TICK, le=MAX_TICK)]
SqrtPrice = Annotated[int, Field(ge=MIN_SQRT_X96, lt=MAX_SQRT_X96)]
Liquidity = Annotated[int, Field(ge=0, lt=2**128)]
Ordinal = Annotated[int, Field(ge=0, le=2**64 - 1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PositionRange(Contract):
    owner: Address
    lower_tick: Tick
    upper_tick: Tick
    liquidity: Liquidity

    @model_validator(mode="after")
    def check_bounds(self) -> "PositionRange":
        if self.lower_tick >= self.upper_tick:
            raise ValueError("position lower tick must precede its upper tick")
        return self


class OpeningState(Contract):
    block: Ordinal
    tick: Tick
    sqrt_price_x96: SqrtPrice
    active_liquidity: Liquidity
    positions: list[PositionRange]


class PoolMetadata(Contract):
    chain_id: Annotated[int, Field(gt=0)]
    pool: Address
    token0_decimals: Annotated[int, Field(ge=0, le=36)]
    token1_decimals: Annotated[int, Field(ge=0, le=36)]
    fee_pips: Annotated[int, Field(gt=0, lt=1_000_000)]
    tick_spacing: Annotated[int, Field(ge=1, le=16_384)]
    opening: OpeningState | None = None


class Event(Contract):
    block: Ordinal
    log_index: Ordinal
    block_hash: Hash | None = None
    tx_hash: Hash | None = None


class Initialize(Event):
    kind: Literal["initialize"] = "initialize"
    tick: Tick
    sqrt_price_x96: SqrtPrice


class LiquidityChange(Event, PositionRange):
    kind: Literal["mint", "burn"]


class Swap(Event):
    kind: Literal["swap"] = "swap"
    tick: Tick
    sqrt_price_x96: SqrtPrice
    active_liquidity: Liquidity
    amount0: Annotated[int, Field(ge=-(2**255), lt=2**255)]
    amount1: Annotated[int, Field(ge=-(2**255), lt=2**255)]

    @model_validator(mode="after")
    def check_deltas(self) -> "Swap":
        same_sign = self.amount0 * self.amount1 > 0
        unfunded_output = min(self.amount0, self.amount1) < 0 and max(self.amount0, self.amount1) <= 0
        if same_sign or unfunded_output:
            raise ValueError("Swap amounts must have valid signed pool deltas")
        return self


PoolEvent = Annotated[Initialize | LiquidityChange | Swap, Field(discriminator="kind")]


class Tape(PoolMetadata):
    events: list[PoolEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def check_order(self) -> "Tape":
        order = [(event.block, event.log_index) for event in self.events]
        if order != sorted(set(order)):
            raise ValueError("pool events must be strictly sorted by block and log index")
        initializations = [i for i, event in enumerate(self.events) if isinstance(event, Initialize)]
        if self.opening is None and initializations != [0]:
            raise ValueError("replay requires a first Initialize event or a complete opening snapshot")
        if self.opening is not None and (initializations or self.opening.block >= self.events[0].block):
            raise ValueError("opening snapshot must predate the first event and cannot be combined with Initialize")
        hashes, block_by_hash, block_by_tx = {}, {}, {}
        for event in self.events:
            if event.block_hash and hashes.setdefault(event.block, event.block_hash) != event.block_hash:
                raise ValueError("events mix block hashes at the same height")
            if event.block_hash and block_by_hash.setdefault(event.block_hash, event.block) != event.block:
                raise ValueError("one block hash cannot identify multiple heights")
            if event.tx_hash and block_by_tx.setdefault(event.tx_hash, event.block) != event.block:
                raise ValueError("one transaction hash cannot occur at multiple heights")
        return self


class ReplayConfig(Contract):
    capital_quote: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 10_000
    half_width_ticks: Annotated[int, Field(ge=1, le=300_000)] = 600
    gas_quote: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 3
    turnover_slippage_bps: Annotated[float, Field(ge=0, lt=10_000)] = 15
    protocol_fee_share: Annotated[float, Field(ge=0, le=1)] = 0
