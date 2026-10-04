"""Versioned first-party research strategies."""

from app.strategies.sma_crossover import SMACrossoverFactory
from app.strategies.h4_breakout import H4BreakoutFactory

STRATEGY_FACTORIES = {
    SMACrossoverFactory.strategy_id: SMACrossoverFactory(),
    H4BreakoutFactory.strategy_id: H4BreakoutFactory(),
}


def get_strategy_factory(strategy_id: str):
    try:
        return STRATEGY_FACTORIES[strategy_id]
    except KeyError as error:
        raise ValueError(f"Unavailable strategy: {strategy_id}") from error


__all__ = ["H4BreakoutFactory", "SMACrossoverFactory", "STRATEGY_FACTORIES",
           "get_strategy_factory"]
