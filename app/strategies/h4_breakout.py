"""Two-candle H4 breakout strategy expressed only as canonical Signals."""

from math import fabs
from typing import Mapping

from app.backtest import Bar, OrderAction, OrderType, Side, Signal, Strategy, StrategyContext
from app.research.parameters import ParameterValue


class H4Breakout:
    """Break out of the second of two qualifying directional candles.

    Supply H4 bars to the runner. This strategy only emits STOP entries and
    stop modifications; the canonical engine remains responsible for fills,
    position state, stop execution, and accounting.
    """

    def on_bar(self, context: StrategyContext) -> Signal | None:
        if context.position is not None:
            # Do not re-trail on the bar that opened the position. Its initial
            # stop is effective from the next bar under the engine's bar policy.
            if context.position.entry_time == context.bar.timestamp:
                return None
            previous = context.history[-2]
            stop = (previous.low if context.position.side is Side.LONG
                    else previous.high)
            return Signal(OrderAction.MODIFY_STOP, stop_loss=stop)

        if context.pending_order is not None or len(context.history) < 2:
            return None

        first, second = context.history[-2], context.bar
        if not (_normal_body(first) and _normal_body(second)):
            return None

        if first.close > first.open and second.close > second.open:
            if second.close > first.high:
                return Signal(OrderAction.OPEN, Side.LONG,
                              order_type=OrderType.STOP,
                              trigger_price=second.high,
                              stop_loss=second.low)
        elif first.close < first.open and second.close < second.open:
            if second.close < first.low:
                return Signal(OrderAction.OPEN, Side.SHORT,
                              order_type=OrderType.STOP,
                              trigger_price=second.low,
                              stop_loss=second.high)
        return None


def _normal_body(bar: Bar) -> bool:
    candle_range = bar.high - bar.low
    return candle_range > 0 and fabs(bar.close - bar.open) / candle_range >= 0.50


class H4BreakoutFactory:
    strategy_id = "breakout.h4_two_candle"
    strategy_version = "1.0.0"
    parameter_schema = ()

    def validate(self, parameters: Mapping[str, ParameterValue]) -> None:
        if parameters:
            raise ValueError("H4 breakout has no parameters")

    def create(self, parameters: Mapping[str, ParameterValue]) -> Strategy:
        self.validate(parameters)
        return H4Breakout()
