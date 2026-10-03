from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    volume: int = 0
    spread: float = 0.0


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderAction(str, Enum):
    OPEN = "open"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy instruction. Opens can specify long/short; closes use no side."""

    action: OrderAction
    side: Side | None = None
    quantity: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", OrderAction(self.action))
        if self.side is not None:
            object.__setattr__(self, "side", Side(self.side))
        if self.quantity is not None and (not isfinite(self.quantity) or self.quantity <= 0):
            raise ValueError("Signal quantity must be finite and positive")


@dataclass(frozen=True, slots=True)
class Order:
    sequence: int
    created_at: datetime
    filled_at: datetime
    action: OrderAction
    side: Side
    quantity: float
    reference_price: float
    fill_price: float
    commission: float
    spread_cost: float
    slippage_cost: float


@dataclass(frozen=True, slots=True)
class Position:
    side: Side
    quantity: float
    entry_time: datetime
    entry_price: float
    entry_commission: float


@dataclass(frozen=True, slots=True)
class Trade:
    sequence: int
    side: Side
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    entry_commission: float
    exit_commission: float
    gross_pnl: float
    net_pnl: float


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    cash: float
    unrealized_pnl: float
    equity: float


@dataclass(frozen=True, slots=True)
class ExecutionCosts:
    commission_per_unit: float = 0.0
    commission_rate: float = 0.0
    slippage: float = 0.0
    spread_scale: float = 1.0

    def __post_init__(self) -> None:
        values = (self.commission_per_unit, self.commission_rate, self.slippage, self.spread_scale)
        if not all(isfinite(value) and value >= 0 for value in values):
            raise ValueError("Execution costs must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_cash: float
    final_cash: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_commission: float
    total_spread_cost: float
    total_slippage_cost: float
    open_position: Position | None
    orders: tuple[Order, ...]
    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
