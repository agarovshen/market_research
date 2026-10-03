"""Application boundary used by the FastAPI Research GUI and API."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func

from app.analysis import AnalysisSettings
from app.backtest import BacktestRunConfig, BacktestRunner
from app.backtest.data import MarketDataRepository
from app.database import SessionLocal
from app.models import Instrument, MarketData
from app.research import (
    ChoiceParameter, DateRange, FixedParameter, FloatRange, IntegerRange,
    ParameterSet, ParameterSpace, ResearchEngine, SelectionRule, TrainTestSplit,
    WalkForwardConfig,
)
from app.research.storage import ResearchResultRepository
from app.strategies import STRATEGY_FACTORIES, get_strategy_factory

MAX_RESEARCH_CANDIDATES = 10_000


def _parameter_space(items: list[dict[str, Any]]) -> ParameterSpace:
    specs = []
    for item in items:
        kind, name = item.get("kind"), item.get("name")
        if kind == "fixed":
            specs.append(FixedParameter(name, item.get("value")))
        elif kind == "integer":
            specs.append(IntegerRange(name, item["minimum"], item["maximum"], item.get("step", 1)))
        elif kind == "float":
            specs.append(FloatRange(name, item["minimum"], item["maximum"], item.get("step")))
        elif kind == "choice":
            specs.append(ChoiceParameter(name, tuple(item["choices"])))
        else:
            raise ValueError(f"Unsupported parameter kind: {kind}")
    return ParameterSpace(tuple(specs))


class ResearchApplicationService:
    """Build existing research objects, orchestrate runs, and own API transaction."""

    def __init__(self, session):
        self.session = session
        self.repository = MarketDataRepository(session)

    def available_strategies(self) -> list[dict[str, Any]]:
        return [{"id": factory.strategy_id, "version": factory.strategy_version,
                 "parameters": [
                     {"name": "fast_period", "kind": "integer", "minimum": 2, "maximum": 100},
                     {"name": "slow_period", "kind": "integer", "minimum": 5, "maximum": 300},
                 ]} for factory in STRATEGY_FACTORIES.values()]

    def market_data_catalog(self, symbol: str) -> dict[str, Any]:
        instrument = self.session.query(Instrument).filter(
            Instrument.symbol == symbol.upper()).first()
        if instrument is None:
            return {"symbol": symbol.upper(), "count": 0, "start": None, "end": None}
        row = self.session.query(func.count(MarketData.id), func.min(MarketData.timestamp),
                                 func.max(MarketData.timestamp)).filter(
                                     MarketData.instrument_id == instrument.id).one()
        return {"symbol": instrument.symbol, "count": row[0],
                "start": row[1].isoformat() if row[1] else None,
                "end": row[2].isoformat() if row[2] else None}

    def run(self, request: dict[str, Any]):
        factory = get_strategy_factory(request["strategy_id"])
        period = DateRange(_datetime(request["start"]), _datetime(request["end"]))
        config = BacktestRunConfig(
            symbol=request["symbol"], timeframe=request["timeframe"],
            start=period.start, end=period.end,
            initial_cash=request.get("initial_cash", 100_000),
            position_size=request.get("position_size", 1),
            commission_per_unit=request.get("commission_per_unit", 0),
            commission_rate=request.get("commission_rate", 0),
            spread_scale=request.get("spread_scale", 1),
            slippage=request.get("slippage", 0),
            final_liquidation=request.get("final_liquidation", True),
        )
        parameter_space = _parameter_space(request.get("parameter_space", []))
        engine = ResearchEngine(BacktestRunner(self.repository), result_store=ResearchResultRepository(self.session))
        analysis_settings = AnalysisSettings(
            periods_per_year=request.get("periods_per_year"),
            risk_free_rate=request.get("risk_free_rate", 0),
            target_return=request.get("target_return", 0),
        )
        method = request["mode"]
        if method == "single":
            values = ParameterSet.from_mapping(request.get("parameters", {}))
            factory.validate(values.as_dict())
            return engine.run_batch((values,), strategy_factory=factory, config=config,
                                    search_method="manual",
                                    parameter_space=parameter_space if parameter_space.parameters else None,
                                    analysis_settings=analysis_settings)
        if method == "batch":
            search_method = request.get("search_method", "grid")
            if search_method == "grid":
                candidates = _bounded(parameter_space.grid())
            elif search_method == "random":
                candidates = parameter_space.random(request["count"], seed=request["seed"])
            else:
                raise ValueError("Batch search method must be grid or random")
            return engine.run_batch(candidates, strategy_factory=factory, config=config,
                                    search_method=search_method, seed=request.get("seed"),
                                    parameter_space=parameter_space, analysis_settings=analysis_settings)
        split = TrainTestSplit(
            DateRange(_datetime(request["training_start"]), _datetime(request["training_end"])),
            DateRange(_datetime(request["testing_start"]), _datetime(request["testing_end"])),
        )
        if split.training.start < period.start or split.testing.end > period.end:
            raise ValueError("Training and testing periods must be inside the configured overall period")
        search_method = request.get("search_method", "grid")
        if search_method == "random":
            candidates = tuple(parameter_space.random(request["count"], seed=request["seed"]))
        elif search_method == "grid":
            candidates = tuple(_bounded(parameter_space.grid()))
        else:
            raise ValueError("Search method must be grid or random")
        rule = SelectionRule(request.get("selection_metric", "total_return"),
                             request.get("maximize", True), request.get("top_n", 1))
        if method == "oos":
            result = engine.run_out_of_sample(candidates, strategy_factory=factory, config=config,
                split=split, selection=rule, parameter_space=parameter_space,
                search_method=search_method, seed=request.get("seed"),
                analysis_settings=analysis_settings)
        elif method == "walk_forward":
            wf = WalkForwardConfig(
                period=DateRange(_datetime(request["start"]), _datetime(request["end"])),
                training_duration=timedelta(days=request["training_days"]),
                testing_duration=timedelta(days=request["testing_days"]),
                step=timedelta(days=request["step_days"]),
                anchored=request.get("anchored", True),
            )
            result = engine.run_walk_forward(candidates, strategy_factory=factory, config=config,
                walk_forward=wf, selection=rule, parameter_space=parameter_space,
                search_method=search_method, seed=request.get("seed"),
                analysis_settings=analysis_settings)
        else:
            raise ValueError(f"Unsupported research mode: {method}")
        return result


def _datetime(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _bounded(values):
    candidates = []
    for value in values:
        if len(candidates) >= MAX_RESEARCH_CANDIDATES:
            raise ValueError(f"Research batch exceeds the {MAX_RESEARCH_CANDIDATES} candidate UI limit")
        candidates.append(value)
    return tuple(candidates)
