"""Application-level orchestration for running a strategy over stored history."""

from dataclasses import dataclass
from datetime import datetime

from app.backtest.engine import BacktestEngine, BacktestSettings
from app.backtest.models import BacktestResult, ExecutionCosts
from app.backtest.strategy import Strategy


@dataclass(frozen=True, slots=True)
class BacktestRunConfig:
    symbol: str
    timeframe: str = "M1"
    start: datetime | None = None
    end: datetime | None = None
    initial_cash: float = 100_000.0
    position_size: float = 1.0
    commission_per_unit: float = 0.0
    commission_rate: float = 0.0
    # MarketData spread is imported as integer feed points (MT5 CSV). Convert
    # those points to quote-price units by default; callers can override for
    # instruments/feed conventions with a different point size.
    spread_scale: float = 0.00001
    slippage: float = 0.0
    final_liquidation: bool = True

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol cannot be empty")
        object.__setattr__(self, "symbol", symbol)
        timeframe = self.timeframe.upper()
        if timeframe not in {"M1", "M5", "M15", "H1", "H4", "D1"}:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        object.__setattr__(self, "timeframe", timeframe)
        if self.start is not None and self.end is not None:
            try:
                if self.start > self.end:
                    raise ValueError("start must be earlier than or equal to end")
            except TypeError as error:
                raise ValueError("start and end must use compatible timezone awareness") from error
        # Validate all execution-cost inputs when the run is configured.
        ExecutionCosts(
            commission_per_unit=self.commission_per_unit,
            commission_rate=self.commission_rate,
            spread_scale=self.spread_scale,
            slippage=self.slippage,
        )
        BacktestSettings(initial_cash=self.initial_cash, position_size=self.position_size)


class BacktestRunner:
    """Load bars and delegate to the engine using a one-run strategy instance.

    Supply a fresh strategy object for every independent run. Mutable strategy
    state is neither reset nor cloned by the runner.
    """

    def __init__(self, repository):
        self.repository = repository

    def run(self, *, strategy: Strategy, config: BacktestRunConfig) -> BacktestResult:
        bars = self.repository.load(
            config.symbol,
            start=config.start,
            end=config.end,
            timeframe=config.timeframe,
        )
        settings = BacktestSettings(
            initial_cash=config.initial_cash,
            position_size=config.position_size,
            costs=ExecutionCosts(
                commission_per_unit=config.commission_per_unit,
                commission_rate=config.commission_rate,
                spread_scale=config.spread_scale,
                slippage=config.slippage,
            ),
            close_at_end=config.final_liquidation,
        )
        return BacktestEngine(settings).run(bars, strategy)
