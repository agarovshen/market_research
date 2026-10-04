from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any


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
    MODIFY_STOP = "modify_stop"


class OrderType(str, Enum):
    MARKET = "market"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"


class ExecutionEventType(str, Enum):
    BAR = "bar"
    SIGNAL = "signal"
    ORDER_CREATED = "order_created"
    ORDER_PENDING = "order_pending"
    ORDER_TRIGGERED = "order_triggered"
    EXECUTION = "execution"
    POSITION_OPENED = "position_opened"
    STOP_UPDATED = "stop_updated"
    POSITION_CLOSED = "position_closed"
    TRADE_CREATED = "trade_created"
    PNL_CALCULATED = "pnl_calculated"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Immutable observation emitted by the canonical backtest lifecycle."""

    sequence: int
    timestamp: datetime
    event_type: ExecutionEventType
    bar_index: int | None
    action: OrderAction | None = None
    side: Side | None = None
    order_id: int | None = None
    trade_id: int | None = None
    price: float | None = None
    quantity: float | None = None
    reason: str | None = None
    details: tuple[tuple[str, Any], ...] = ()
    order_type: OrderType | None = None
    trigger_price: float | None = None
    stop_loss: float | None = None

    def __str__(self) -> str:
        fields = [f"[{self.event_type.value.upper()}] {self.timestamp.isoformat()}"]
        if self.bar_index is not None:
            fields.append(f"bar={self.bar_index}")
        if self.action is not None:
            fields.append(f"action={self.action.value.upper()}")
        if self.side is not None:
            fields.append(f"side={self.side.value.upper()}")
        if self.order_id is not None:
            fields.append(f"order_id={self.order_id}")
        if self.order_type is not None:
            fields.append(f"order_type={self.order_type.value.upper()}")
        if self.trade_id is not None:
            fields.append(f"trade_id={self.trade_id}")
        if self.price is not None:
            fields.append(f"price={self.price:g}")
        if self.quantity is not None:
            fields.append(f"quantity={self.quantity:g}")
        if self.trigger_price is not None:
            fields.append(f"trigger_price={self.trigger_price:g}")
        if self.stop_loss is not None:
            fields.append(f"stop_loss={self.stop_loss:g}")
        if self.reason is not None:
            fields.append(f"reason={self.reason}")
        fields.extend(f"{key}={value}" for key, value in self.details)
        return " ".join(fields)


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy instruction; explicit quantity is internal units, not lots.

    In Forex runs, omitting quantity uses the run's ``lots * contract_size``.
    """

    action: OrderAction
    side: Side | None = None
    quantity: float | None = None
    order_type: OrderType = OrderType.MARKET
    trigger_price: float | None = None
    stop_loss: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", OrderAction(self.action))
        object.__setattr__(self, "order_type", OrderType(self.order_type))
        if self.side is not None:
            object.__setattr__(self, "side", Side(self.side))
        if self.quantity is not None and (not isfinite(self.quantity) or self.quantity <= 0):
            raise ValueError("Signal quantity must be finite and positive")
        if self.trigger_price is not None and not isfinite(self.trigger_price):
            raise ValueError("Signal trigger_price must be finite")
        if self.stop_loss is not None and not isfinite(self.stop_loss):
            raise ValueError("Signal stop_loss must be finite")
        if self.action is OrderAction.MODIFY_STOP:
            if self.order_type is not OrderType.MARKET or self.trigger_price is not None:
                raise ValueError("Stop modifications cannot be pending order types")
            if self.stop_loss is None:
                raise ValueError("Stop modifications require a stop_loss value")
        elif self.order_type is OrderType.STOP:
            if self.action is not OrderAction.OPEN:
                raise ValueError("STOP orders are supported only for opening positions")
            if self.side is None or self.trigger_price is None:
                raise ValueError("STOP entry signals require a side and trigger_price")
        elif self.trigger_price is not None:
            raise ValueError("Market signals cannot specify trigger_price")


@dataclass(frozen=True, slots=True)
class Order:
    sequence: int
    created_at: datetime
    filled_at: datetime | None
    action: OrderAction
    side: Side
    quantity: float
    reference_price: float
    fill_price: float | None
    commission: float
    spread_cost: float
    slippage_cost: float
    order_type: OrderType = OrderType.MARKET
    status: OrderStatus = OrderStatus.FILLED
    trigger_price: float | None = None
    stop_loss: float | None = None
    lots: float | None = None
    contract_size: float = 100_000.0
    leverage: float = 30.0
    notional: float | None = None
    margin: float | None = None
    units: float | None = None


@dataclass(frozen=True, slots=True)
class Position:
    side: Side
    quantity: float
    entry_time: datetime
    entry_price: float
    entry_commission: float
    stop_loss: float | None = None
    lots: float | None = None
    contract_size: float = 100_000.0
    leverage: float = 30.0
    notional: float | None = None
    margin: float | None = None
    units: float | None = None


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
    lots: float | None = None
    contract_size: float = 100_000.0
    leverage: float = 30.0
    notional: float | None = None
    margin: float | None = None
    units: float | None = None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """End-of-bar account state; ``cash`` is legacy cash, ``balance`` is Forex balance."""
    timestamp: datetime
    cash: float
    unrealized_pnl: float
    equity: float
    margin_used: float | None = None
    free_margin: float | None = None
    margin_level: float | None = None
    balance: float | None = None


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
    """Canonical simulation output, including nullable Forex account fields."""
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
    execution_trace: tuple[ExecutionEvent, ...] = ()
    balance: float | None = None
    margin_used: float | None = None
    free_margin: float | None = None
    margin_level: float | None = None
    lots: float | None = None
    contract_size: float = 100_000.0
    leverage: float = 30.0
    units: float | None = None

    def format_execution_trace(self) -> str:
        """Return the ordered event stream in a stable, developer-readable form."""
        return "\n".join(str(event) for event in self.execution_trace)

    def format_trade_lifecycle(self, trade_sequence: int) -> str:
        """Show this trade's observed events and authoritative canonical result."""
        trade = next((item for item in self.trades if item.sequence == trade_sequence), None)
        if trade is None:
            raise ValueError(f"No completed trade with sequence {trade_sequence}")
        events = tuple(event for event in self.execution_trace if event.trade_id == trade_sequence)
        lines = [f"Trade #{trade_sequence} lifecycle"]
        lines.extend(str(event) for event in events)
        lines.extend((
            "[CANONICAL TRADE]",
            f"side={trade.side.value.upper()} quantity={trade.quantity:g}",
            f"entry_time={trade.entry_time.isoformat()} entry_price={trade.entry_price:g}",
            f"exit_time={trade.exit_time.isoformat()} exit_price={trade.exit_price:g}",
            f"gross_pnl={trade.gross_pnl:g} net_pnl={trade.net_pnl:g}",
        ))
        return "\n".join(lines)
