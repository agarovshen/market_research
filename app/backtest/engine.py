from dataclasses import dataclass, replace
from math import isfinite
from typing import Iterable

from app.backtest.models import (
    BacktestResult, Bar, EquityPoint, ExecutionCosts, ExecutionEvent,
    ExecutionEventType, Order, OrderAction, OrderStatus, OrderType,
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
        pending_stop: tuple[Signal, Bar, int, int] | None = None

        def record(event_type: ExecutionEventType, timestamp, bar_index: int | None,
                   **fields) -> None:
            events.append(ExecutionEvent(len(events) + 1, timestamp, event_type,
                                         bar_index, **fields))

        def fill(action: OrderAction, side: Side, qty: float, signal_time, bar: Bar,
                 reference: float, *, order_type: OrderType = OrderType.MARKET,
                 trigger_price: float | None = None, stop_loss: float | None = None,
                 replace_sequence: int | None = None) -> Order:
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
            sequence = replace_sequence if replace_sequence is not None else len(orders) + 1
            order = Order(sequence, signal_time, bar.timestamp, action, side,
                          qty, reference, price, commission, spread, slip,
                          order_type, OrderStatus.FILLED, trigger_price, stop_loss)
            if replace_sequence is None:
                orders.append(order)
            else:
                orders[replace_sequence - 1] = order
            return order

        def record_closed_trade(prior: Position, order: Order, index: int,
                                reason: str, trade_id: int) -> Trade:
            nonlocal realized
            direction = 1 if prior.side is Side.LONG else -1
            gross = (order.fill_price - prior.entry_price) * prior.quantity * direction
            net = gross - prior.entry_commission - order.commission
            realized += net
            trade = Trade(trade_id, prior.side, prior.quantity, prior.entry_time,
                          order.filled_at, prior.entry_price, order.fill_price,
                          prior.entry_commission, order.commission, gross, net)
            record(ExecutionEventType.POSITION_CLOSED, order.filled_at, index,
                   action=OrderAction.CLOSE, side=prior.side, order_id=order.sequence,
                   order_type=order.order_type, trade_id=trade.sequence,
                   price=order.fill_price, quantity=prior.quantity,
                   stop_loss=order.stop_loss, reason=reason)
            record(ExecutionEventType.PNL_CALCULATED, order.filled_at, index,
                   side=trade.side, order_id=order.sequence,
                   trade_id=trade.sequence, price=trade.exit_price,
                   quantity=trade.quantity,
                   details=(("gross_pnl", trade.gross_pnl),
                            ("entry_commission", trade.entry_commission),
                            ("exit_commission", trade.exit_commission),
                            ("net_pnl", trade.net_pnl)))
            trades.append(trade)
            record(ExecutionEventType.TRADE_CREATED, order.filled_at, index,
                   side=trade.side, order_id=order.sequence,
                   trade_id=trade.sequence, price=trade.exit_price,
                   quantity=trade.quantity,
                   details=(("entry_time", trade.entry_time.isoformat()),
                            ("entry_price", trade.entry_price),
                            ("exit_time", trade.exit_time.isoformat()),
                            ("exit_price", trade.exit_price),
                            ("gross_pnl", trade.gross_pnl),
                            ("net_pnl", trade.net_pnl)))
            return trade

        def finish_stop_position(prior: Position, bar: Bar, index: int,
                                 reference: float, trigger: float, reason: str) -> None:
            nonlocal position
            trade_id = len(trades) + 1
            order_id = len(orders) + 1
            record(ExecutionEventType.ORDER_CREATED, bar.timestamp, index,
                   action=OrderAction.CLOSE, side=prior.side, order_id=order_id,
                   order_type=OrderType.STOP, trade_id=trade_id,
                   quantity=prior.quantity, trigger_price=trigger,
                   stop_loss=trigger, reason=reason)
            order = fill(OrderAction.CLOSE, prior.side, prior.quantity,
                         bar.timestamp, bar, reference, order_type=OrderType.STOP,
                         trigger_price=trigger, stop_loss=trigger)
            record(ExecutionEventType.ORDER_TRIGGERED, bar.timestamp, index,
                   action=OrderAction.CLOSE, side=prior.side, order_id=order.sequence,
                   order_type=OrderType.STOP, trade_id=trade_id, quantity=prior.quantity,
                   trigger_price=trigger, price=reference, reason=reason)
            record(ExecutionEventType.EXECUTION, bar.timestamp, index,
                   action=order.action, side=order.side, order_id=order.sequence,
                   order_type=OrderType.STOP, trade_id=trade_id,
                   price=order.fill_price, trigger_price=trigger, quantity=order.quantity,
                   reason=reason,
                   details=(("reference_price", order.reference_price),
                            ("commission", order.commission),
                            ("spread_cost", order.spread_cost),
                            ("slippage_cost", order.slippage_cost)))
            record_closed_trade(prior, order, index, reason, trade_id)
            position = None

        for i, bar in enumerate(data):
            position_at_bar_start = position
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
                        order = fill(OrderAction.OPEN, side, qty, created_bar.timestamp, bar, bar.open,
                                     stop_loss=signal.stop_loss)
                        record(ExecutionEventType.EXECUTION, bar.timestamp, i,
                               action=order.action, side=order.side, order_id=order.sequence,
                               order_type=order.order_type,
                               trade_id=trade_id, price=order.fill_price, quantity=order.quantity,
                               reason="next_bar_open_market_fill",
                               details=(("reference_price", order.reference_price),
                                        ("commission", order.commission),
                                        ("spread_cost", order.spread_cost),
                                        ("slippage_cost", order.slippage_cost)))
                        position = Position(side, qty, bar.timestamp, order.fill_price, order.commission,
                                            signal.stop_loss)
                        record(ExecutionEventType.POSITION_OPENED, bar.timestamp, i,
                               action=OrderAction.OPEN, side=position.side,
                               order_id=order.sequence, order_type=order.order_type, trade_id=trade_id,
                               price=position.entry_price, quantity=position.quantity,
                               stop_loss=position.stop_loss)
                elif position is not None:
                    prior = position
                    order = fill(OrderAction.CLOSE, prior.side, prior.quantity, created_bar.timestamp, bar, bar.open)
                    record(ExecutionEventType.EXECUTION, bar.timestamp, i,
                           action=order.action, side=order.side, order_id=order.sequence,
                           order_type=order.order_type,
                           trade_id=trade_id, price=order.fill_price, quantity=order.quantity,
                           reason="strategy_close_signal",
                           details=(("reference_price", order.reference_price),
                                    ("commission", order.commission),
                                    ("spread_cost", order.spread_cost),
                                    ("slippage_cost", order.slippage_cost)))
                    record_closed_trade(prior, order, i, "strategy_close_signal",
                                        len(trades) + 1)
                    position = None
            pending = None
            if (position_at_bar_start is not None
                    and position is position_at_bar_start
                    and position.stop_loss is not None):
                stop = position.stop_loss
                if position.side is Side.LONG and bar.low <= stop:
                    finish_stop_position(position, bar, i, bar.open if bar.open <= stop else stop,
                                         stop, "stop_loss")
                elif position.side is Side.SHORT and bar.high >= stop:
                    finish_stop_position(position, bar, i, bar.open if bar.open >= stop else stop,
                                         stop, "stop_loss")

            if pending_stop is not None and position is None:
                stop_signal, created_bar, created_index, stop_order_id = pending_stop
                if i > created_index:
                    trigger = stop_signal.trigger_price
                    assert trigger is not None and stop_signal.side is not None
                    hit = (bar.high >= trigger if stop_signal.side is Side.LONG
                           else bar.low <= trigger)
                    if hit:
                        if stop_signal.side is Side.LONG:
                            reference = bar.open if bar.open >= trigger else trigger
                        else:
                            reference = bar.open if bar.open <= trigger else trigger
                        qty = stop_signal.quantity if stop_signal.quantity is not None else self.settings.position_size
                        trade_id = len(trades) + 1
                        order = fill(OrderAction.OPEN, stop_signal.side, qty,
                                     created_bar.timestamp, bar, reference,
                                     order_type=OrderType.STOP, trigger_price=trigger,
                                     stop_loss=stop_signal.stop_loss,
                                     replace_sequence=stop_order_id)
                        record(ExecutionEventType.ORDER_TRIGGERED, bar.timestamp, i,
                               action=OrderAction.OPEN, side=order.side,
                               order_id=order.sequence, order_type=OrderType.STOP,
                               trade_id=trade_id, price=reference, quantity=qty,
                               trigger_price=trigger, stop_loss=order.stop_loss,
                               reason="stop_triggered")
                        record(ExecutionEventType.EXECUTION, bar.timestamp, i,
                               action=OrderAction.OPEN, side=order.side,
                               order_id=order.sequence, order_type=OrderType.STOP,
                               trade_id=trade_id, price=order.fill_price,
                               quantity=qty, trigger_price=trigger,
                               stop_loss=order.stop_loss,
                               reason="stop_entry",
                               details=(("reference_price", order.reference_price),
                                        ("commission", order.commission),
                                        ("spread_cost", order.spread_cost),
                                        ("slippage_cost", order.slippage_cost)))
                        position = Position(order.side, qty, bar.timestamp, order.fill_price,
                                            order.commission, order.stop_loss)
                        record(ExecutionEventType.POSITION_OPENED, bar.timestamp, i,
                               action=OrderAction.OPEN, side=position.side,
                               order_id=order.sequence, order_type=order.order_type,
                               trade_id=trade_id, price=position.entry_price,
                               quantity=position.quantity, stop_loss=position.stop_loss)
                        pending_stop = None

            signal = strategy.on_bar(StrategyContext(i, BarHistory(data, i + 1), bar))
            if signal is not None:
                if signal.action is OrderAction.MODIFY_STOP:
                    record(ExecutionEventType.SIGNAL, bar.timestamp, i,
                           action=signal.action, side=position.side if position else None,
                           stop_loss=signal.stop_loss,
                           details=(("strategy_reason", "not exposed by Signal"),))
                    if position is None:
                        raise ValueError("Cannot modify stop_loss without an open position")
                    old_stop = position.stop_loss
                    position = replace(position, stop_loss=signal.stop_loss)
                    record(ExecutionEventType.STOP_UPDATED, bar.timestamp, i,
                           action=OrderAction.MODIFY_STOP, side=position.side,
                           stop_loss=position.stop_loss,
                           details=(("previous_stop_loss", old_stop),))
                    signal = None
                if signal is not None and signal.order_type is OrderType.STOP:
                    if position is not None:
                        raise ValueError("Cannot create a STOP entry while a position is open")
                    if pending_stop is not None:
                        raise ValueError("Only one pending STOP entry is supported")
                    executable = i + 1 < len(data)
                    trade_id = len(trades) + 1 if executable else None
                    record(ExecutionEventType.SIGNAL, bar.timestamp, i,
                           action=signal.action, side=signal.side,
                           order_type=OrderType.STOP, trade_id=trade_id,
                           quantity=signal.quantity,
                           trigger_price=signal.trigger_price,
                           stop_loss=signal.stop_loss,
                           details=(("strategy_reason", "not exposed by Signal"),))
                    if executable:
                        qty = signal.quantity if signal.quantity is not None else self.settings.position_size
                        if not isfinite(qty) or qty <= 0:
                            raise ValueError("Open signals require a side and positive finite quantity")
                        trade_id = len(trades) + 1
                        order_id = len(orders) + 1
                        order = Order(order_id, bar.timestamp, None, OrderAction.OPEN,
                                      signal.side, qty, signal.trigger_price, None,
                                      0.0, 0.0, 0.0, OrderType.STOP, OrderStatus.PENDING,
                                      signal.trigger_price, signal.stop_loss)
                        orders.append(order)
                        record(ExecutionEventType.ORDER_CREATED, bar.timestamp, i,
                               action=OrderAction.OPEN, side=signal.side,
                               order_id=order_id, order_type=OrderType.STOP,
                               trade_id=trade_id, quantity=qty,
                               trigger_price=signal.trigger_price,
                               stop_loss=signal.stop_loss, reason="stop_entry_pending")
                        record(ExecutionEventType.ORDER_PENDING, bar.timestamp, i,
                               action=OrderAction.OPEN, side=signal.side,
                               order_id=order_id, order_type=OrderType.STOP,
                               trade_id=trade_id, quantity=qty,
                               trigger_price=signal.trigger_price,
                               stop_loss=signal.stop_loss, reason="eligible_next_bar")
                        pending_stop = (signal, bar, i, order_id)
                elif signal is not None:
                    if signal.action is OrderAction.OPEN and pending_stop is not None:
                        raise ValueError("Cannot create a market entry while a STOP entry is pending")
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
                               order_type=OrderType.MARKET,
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
            record_closed_trade(prior, order, len(data) - 1,
                                "end_of_data_liquidation", trade_id)
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
