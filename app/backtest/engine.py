from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from app.backtest.models import (
    BacktestResult, Bar, EquityPoint, ExecutionCosts, Order, OrderAction,
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
        pending: tuple[Signal, Bar] | None = None

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
            if pending is not None:
                signal, created_bar = pending
                if signal.action is OrderAction.OPEN:
                    if position is None:
                        side = signal.side
                        qty = signal.quantity if signal.quantity is not None else self.settings.position_size
                        if side is None or not isfinite(qty) or qty <= 0:
                            raise ValueError("Open signals require a side and positive finite quantity")
                        order = fill(OrderAction.OPEN, side, qty, created_bar.timestamp, bar, bar.open)
                        position = Position(side, qty, bar.timestamp, order.fill_price, order.commission)
                elif position is not None:
                    prior = position
                    order = fill(OrderAction.CLOSE, prior.side, prior.quantity, created_bar.timestamp, bar, bar.open)
                    gross = (order.fill_price - prior.entry_price) * prior.quantity * (1 if prior.side is Side.LONG else -1)
                    net = gross - prior.entry_commission - order.commission
                    realized += net
                    trades.append(Trade(len(trades) + 1, prior.side, prior.quantity,
                        prior.entry_time, bar.timestamp, prior.entry_price, order.fill_price,
                        prior.entry_commission, order.commission, gross, net))
                    position = None
            signal = strategy.on_bar(StrategyContext(i, BarHistory(data, i + 1), bar))
            pending = (signal, bar) if signal is not None else None
            unrealized = self._unrealized(position, bar.close, bar.spread)
            direction = 0 if position is None else (1 if position.side is Side.LONG else -1)
            marked_value = 0.0 if position is None else direction * position.quantity * (
                bar.close - direction * bar.spread * self.settings.costs.spread_scale / 2
            )
            curve.append(EquityPoint(bar.timestamp, cash, unrealized, cash + marked_value))

        if position is not None and self.settings.close_at_end and data:
            prior = position
            last = data[-1]
            order = fill(OrderAction.CLOSE, prior.side, prior.quantity, last.timestamp, last, last.close)
            gross = (order.fill_price - prior.entry_price) * prior.quantity * (1 if prior.side is Side.LONG else -1)
            net = gross - prior.entry_commission - order.commission
            realized += net
            trades.append(Trade(len(trades) + 1, prior.side, prior.quantity,
                prior.entry_time, last.timestamp, prior.entry_price, order.fill_price,
                prior.entry_commission, order.commission, gross, net))
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
            position, tuple(orders), tuple(trades), tuple(curve))

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
