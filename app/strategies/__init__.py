"""Versioned first-party research strategies."""

from app.strategies.sma_crossover import SMACrossoverFactory

STRATEGY_FACTORIES = {SMACrossoverFactory.strategy_id: SMACrossoverFactory()}


def get_strategy_factory(strategy_id: str):
    try:
        return STRATEGY_FACTORIES[strategy_id]
    except KeyError as error:
        raise ValueError(f"Unavailable strategy: {strategy_id}") from error


__all__ = ["SMACrossoverFactory", "STRATEGY_FACTORIES", "get_strategy_factory"]
