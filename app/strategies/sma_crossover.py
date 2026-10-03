"""A compact, versioned long-only SMA crossover strategy for research workflows."""

from typing import Mapping

from app.backtest import OrderAction, Signal, Side, Strategy, StrategyContext
from app.research.parameters import ParameterValue


class SMACrossover:
    def __init__(self, fast_period: int, slow_period: int):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def on_bar(self, context: StrategyContext) -> Signal | None:
        required = self.slow_period + 1
        if len(context.history) < required:
            return None
        closes = [bar.close for bar in context.history]
        prior_fast = sum(closes[-self.fast_period - 1:-1]) / self.fast_period
        prior_slow = sum(closes[-self.slow_period - 1:-1]) / self.slow_period
        current_fast = sum(closes[-self.fast_period:]) / self.fast_period
        current_slow = sum(closes[-self.slow_period:]) / self.slow_period
        if prior_fast <= prior_slow and current_fast > current_slow:
            return Signal(OrderAction.OPEN, Side.LONG)
        if prior_fast >= prior_slow and current_fast < current_slow:
            return Signal(OrderAction.CLOSE)
        return None


class SMACrossoverFactory:
    strategy_id = "moving_average.sma_crossover"
    strategy_version = "1.0.0"
    parameter_schema = (
        {"name": "fast_period", "label": "Fast period", "kind": "integer",
         "default": 10, "minimum": 2, "maximum": 100, "step": 1},
        {"name": "slow_period", "label": "Slow period", "kind": "integer",
         "default": 30, "minimum": 5, "maximum": 300, "step": 1},
    )

    def validate(self, parameters: Mapping[str, ParameterValue]) -> None:
        if set(parameters) != {"fast_period", "slow_period"}:
            raise ValueError("SMA crossover requires fast_period and slow_period")
        fast, slow = parameters["fast_period"], parameters["slow_period"]
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in (fast, slow)):
            raise ValueError("SMA periods must be positive integers")
        if fast >= slow:
            raise ValueError("fast_period must be smaller than slow_period")

    def create(self, parameters: Mapping[str, ParameterValue]) -> Strategy:
        self.validate(parameters)
        return SMACrossover(parameters["fast_period"], parameters["slow_period"])
