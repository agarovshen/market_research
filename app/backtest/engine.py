from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from app.backtest.models import (
    BacktestResult, Bar, EquityPoint, ExecutionCosts, ExecutionEvent,
    ExecutionEventType, Order, OrderAction,
    Position, Side, Signal, Trade,
)
from app.backtest.strategy import BarHistory, Strategy, StrategyContext


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    initial_cash: float = 100_000.0
    position_size: float = 1.0
    costs: ExecutionCosts = ExecutionCosts()
    close_at_end: bool = True

    def __post_init__(self) -> None:
        if not isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        if not isfinite(self.position_size) or self.position_size <= 0:
            raise ValueError("position_size must be finite and positive")


class BacktestEngine:
    """Single instrument, one net position, next-bar-open deterministic simulator."""

    def __init__(self, settings: BacktestSettings = BacktestSettings()):
        self.settings = settings

    def run(self, bars: Iterable[Bar], strategy: Strategy) -> BacktestResult:
        data = tuple(bars)
        self._validate_bars(data)
        cash = self.settings.initial_cash
        position: Position | None = None
        orders: list[Order] = []
        trades: list[Trade] = []
        curve: list[EquityPoint] = []
        commissions = spread_costs = slippage_costs = 0.0
        realized = 0.0
        events: list[ExecutionEvent] = []
        pending: tuple[Signal, Bar, int | None] | None = None

        def record(event_type: ExecutionEventType, timestamp, bar_index: int | None,
                   **fields) -> None:
            events.append(ExecutionEvent(len(events) + 1, timestamp, event_type,
                                         bar_index, **fields))

        def fill(action: OrderAction, side: Side, qty: float, signal_time, bar: Bar,
                 reference: float) -> Order:
            nonlocal cash, commissions, spread_costs, slippage_costs
            half_spread = bar.spread * self.settings.costs.spread_scale / 2
            direction = 1 if side is Side.LONG else -1
            # Opening pays adverse spread/slippage; closing takes the opposite position in the market.
            signed_cost = direction if action is OrderAction.OPEN else -direction
            price = reference + signed_cost * (half_spread + self.settings.costs.slippage)
            commission = qty * (self.settings.costs.commission_per_unit + abs(price) * self.settings.costs.commission_rate)
            spread = half_spread * qty
            slip = self.settings.costs.slippage * qty
            cash_delta = -direction * qty * price if action is OrderAction.OPEN else direction * qty * price
            cash += cash_delta - commission
            commissions += commission
            spread_costs += spread
            slippage_costs += slip
            order = Order(len(orders) + 1, signal_time, bar.timestamp, action, side,
                          qty, reference, price, commission, spread, slip)
            orders.append(order)
            return order

        for i, bar in enumerate(data):
            record(ExecutionEventType.BAR, bar.timestamp, i,
                   details=(("open", bar.open), ("high", bar.high),
                            ("low", bar.low), ("close", bar.close),
                            ("spread", bar.spread)))
            if pending is not None:
                signal, created_bar, trade_id = pending
                if signal.action is OrderAction.OPEN:
                    if position is None:
                        side = signal.side
                        qty = signal.quantity if signal.quantity is not None else self.settings.position_size
                        if side is None or not isfinite(qty) or qty <= 0:
                            raise ValueError("Open signals require a side and positive finite quantity")
                        order = fill(OrderAction.OPEN, side, qty, created_bar.timestamp, bar, bar.open)
                        record(ExecutionEventType.EXECUTION, bar.timestamp, i,
                               action=order.action, side=order.side, order_id=order.sequence,
                               trade_id=trade_id, price=order.fill_price, quantity=order.quantity,
                               reason="next_bar_open_market_fill",
                               details=(("reference_price", order.reference_price),
                                        ("commission", order.commission),
                                        ("spread_cost", order.spread_cost),
                                        ("slippage_cost", order.slippage_cost)))
                        position = Position(side, qty, bar.timestamp, order.fill_price, order.commission)
                        record(ExecutionEventType.POSITION_OPENED, bar.timestamp, i,
                               action=OrderAction.OPEN, side=position.side,
                               order_id=order.sequence, trade_id=trade_id,
                               price=position.entry_price, quantity=position.quantity)
                elif position is not None:
                    prior = position
                    order = fill(OrderAction.CLOSE, prior.side, prior.quantity, created_bar.timestamp, bar, bar.open)
                    record(ExecutionEventType.EXECUTION, bar.timestamp, i,
                           action=order.action, side=order.side, order_id=order.sequence,
                           trade_id=trade_id, price=order.fill_price, quantity=order.quantity,
                           reason="strategy_close_signal",
                           details=(("reference_price", order.reference_price),
                                    ("commission", order.commission),
                                    ("spread_cost", order.spread_cost),
                                    ("slippage_cost", order.slippage_cost)))
                    gross = (order.fill_price - prior.entry_price) * prior.quantity * (1 if prior.side is Side.LONG else -1)
                    net = gross - prior.entry_commission - order.commission
                    realized += net
                    trade = Trade(len(trades) + 1, prior.side, prior.quantity,
                        prior.entry_time, bar.timestamp, prior.entry_price, order.fill_price,
                        prior.entry_commission, order.commission, gross, net)
                    record(ExecutionEventType.POSITION_CLOSED, bar.timestamp, i,
                           action=OrderAction.CLOSE, side=prior.side,
                           order_id=order.sequence, trade_id=trade.sequence,
                           price=order.fill_price, quantity=prior.quantity,
                           reason="strategy_close_signal")
                    record(ExecutionEventType.PNL_CALCULATED, bar.timestamp, i,
                           side=trade.side, order_id=order.sequence,
                           trade_id=trade.sequence, price=trade.exit_price,
                           quantity=trade.quantity,
                           details=(("gross_pnl", trade.gross_pnl),
                                    ("entry_commission", trade.entry_commission),
                                    ("exit_commission", trade.exit_commission),
                                    ("net_pnl", trade.net_pnl)))
                    trades.append(trade)
                    record(ExecutionEventType.TRADE_CREATED, bar.timestamp, i,
                           side=trade.side, order_id=order.sequence,
                           trade_id=trade.sequence, price=trade.exit_price,
                           quantity=trade.quantity,
                           details=(("entry_time", trade.entry_time.isoformat()),
                                    ("entry_price", trade.entry_price),
                                    ("exit_time", trade.exit_time.isoformat()),
                                    ("exit_price", trade.exit_price),
                                    ("gross_pnl", trade.gross_pnl),
                                    ("net_pnl", trade.net_pnl)))
                    position = None
            signal = strategy.on_bar(StrategyContext(i, BarHistory(data, i + 1), bar))
            pending = None
            if signal is not None:
                executable = i + 1 < len(data) and (
                    (signal.action is OrderAction.OPEN and position is None)
                    or (signal.action is OrderAction.CLOSE and position is not None)
                )
                trade_id = len(trades) + 1 if executable else None
                record(ExecutionEventType.SIGNAL, bar.timestamp, i,
                       action=signal.action, side=signal.side, trade_id=trade_id,
                       quantity=signal.quantity,
                       details=(("strategy_reason", "not exposed by Signal"),))
                if executable:
                    order_id = len(orders) + 1
                    order_side = signal.side if signal.action is OrderAction.OPEN else position.side
                    order_quantity = (signal.quantity if signal.quantity is not None
                                      else self.settings.position_size) if signal.action is OrderAction.OPEN else position.quantity
                    record(ExecutionEventType.ORDER_CREATED, bar.timestamp, i,
                           action=signal.action, side=order_side, order_id=order_id,
                           trade_id=trade_id, quantity=order_quantity,
                           reason="market_signal_next_bar_open")
                    pending = (signal, bar, trade_id)
                else:
                    pending = (signal, bar, None)
            unrealized = self._unrealized(position, bar.close, bar.spread)
            direction = 0 if position is None else (1 if position.side is Side.LONG else -1)
            marked_value = 0.0 if position is None else direction * position.quantity * (
                bar.close - direction * bar.spread * self.settings.costs.spread_scale / 2
            )
            curve.append(EquityPoint(bar.timestamp, cash, unrealized, cash + marked_value))

        if position is not None and self.settings.close_at_end and data:
            prior = position
            last = data[-1]
            order_id, trade_id = len(orders) + 1, len(trades) + 1
            record(ExecutionEventType.ORDER_CREATED, last.timestamp, len(data) - 1,
                   action=OrderAction.CLOSE, side=prior.side,
                   order_id=order_id, trade_id=trade_id,
                   quantity=prior.quantity, reason="end_of_data_liquidation")
            order = fill(OrderAction.CLOSE, prior.side, prior.quantity, last.timestamp, last, last.close)
            record(ExecutionEventType.EXECUTION, last.timestamp, len(data) - 1,
                   action=order.action, side=order.side, order_id=order.sequence,
                   trade_id=trade_id, price=order.fill_price, quantity=order.quantity,
                   reason="end_of_data_liquidation",
                   details=(("reference_price", order.reference_price),
                            ("commission", order.commission),
                            ("spread_cost", order.spread_cost),
                            ("slippage_cost", order.slippage_cost)))
            gross = (order.fill_price - prior.entry_price) * prior.quantity * (1 if prior.side is Side.LONG else -1)
            net = gross - prior.entry_commission - order.commission
            realized += net
            trade = Trade(len(trades) + 1, prior.side, prior.quantity,
                prior.entry_time, last.timestamp, prior.entry_price, order.fill_price,
                prior.entry_commission, order.commission, gross, net)
            record(ExecutionEventType.POSITION_CLOSED, last.timestamp, len(data) - 1,
                   action=OrderAction.CLOSE, side=prior.side, order_id=order.sequence,
                   trade_id=trade.sequence, price=order.fill_price,
                   quantity=prior.quantity, reason="end_of_data_liquidation")
            record(ExecutionEventType.PNL_CALCULATED, last.timestamp, len(data) - 1,
                   side=trade.side, order_id=order.sequence, trade_id=trade.sequence,
                   price=trade.exit_price, quantity=trade.quantity,
                   details=(("gross_pnl", trade.gross_pnl),
                            ("entry_commission", trade.entry_commission),
                            ("exit_commission", trade.exit_commission),
                            ("net_pnl", trade.net_pnl)))
            trades.append(trade)
            record(ExecutionEventType.TRADE_CREATED, last.timestamp, len(data) - 1,
                   side=trade.side, order_id=order.sequence, trade_id=trade.sequence,
                   price=trade.exit_price, quantity=trade.quantity,
                   details=(("entry_time", trade.entry_time.isoformat()),
                            ("entry_price", trade.entry_price),
                            ("exit_time", trade.exit_time.isoformat()),
                            ("exit_price", trade.exit_price),
                            ("gross_pnl", trade.gross_pnl),
                            ("net_pnl", trade.net_pnl)))
            position = None
            curve[-1] = EquityPoint(last.timestamp, cash, 0.0, cash)

        unrealized = 0.0 if position is None or not data else self._unrealized(position, data[-1].close, data[-1].spread)
        if position is None or not data:
            final_equity = cash
        else:
            direction = 1 if position.side is Side.LONG else -1
            liquidation = data[-1].close - direction * data[-1].spread * self.settings.costs.spread_scale / 2
            final_equity = cash + direction * position.quantity * liquidation
        return BacktestResult(self.settings.initial_cash, cash, final_equity, realized,
            unrealized, commissions, spread_costs, slippage_costs,
            position, tuple(orders), tuple(trades), tuple(curve), tuple(events))

    def _unrealized(self, position: Position | None, close: float, spread: float) -> float:
        if position is None:
            return 0.0
        direction = 1 if position.side is Side.LONG else -1
        liquidation = close - direction * spread * self.settings.costs.spread_scale / 2
        return (liquidation - position.entry_price) * position.quantity * direction

    @staticmethod
    def _validate_bars(bars: tuple[Bar, ...]) -> None:
        previous = None
        for bar in bars:
            values = (bar.open, bar.high, bar.low, bar.close, bar.spread)
            if not all(isfinite(value) for value in values) or bar.spread < 0:
                raise ValueError("Bars require finite prices and nonnegative spread")
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
                raise ValueError("Invalid OHLC values")
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("Bars must have strictly increasing timestamps")
            previous = bar.timestamp
