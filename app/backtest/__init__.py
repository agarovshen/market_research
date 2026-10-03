"""Deterministic historical backtesting, independent of the web application."""

from app.backtest.engine import BacktestEngine, BacktestSettings
from app.backtest.models import (
    Bar, BacktestResult, ExecutionCosts, Order, OrderAction, Position, Side,
    Signal, Trade,
)
from app.backtest.runner import BacktestRunConfig, BacktestRunner
from app.backtest.strategy import Strategy, StrategyContext

__all__ = [
    "BacktestEngine", "BacktestResult", "BacktestRunConfig", "BacktestRunner",
    "BacktestSettings", "Bar",
    "ExecutionCosts", "MarketDataRepository", "Order", "OrderAction",
    "Position", "Side", "Signal", "Strategy", "StrategyContext", "Trade",
]


def __getattr__(name: str):
    # Keep the simulation core importable without database configuration.
    if name == "MarketDataRepository":
        from app.backtest.data import MarketDataRepository
        return MarketDataRepository
    raise AttributeError(name)
