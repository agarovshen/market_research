"""FastAPI boundary for interactive research runs; calculations remain in Python services."""

from dataclasses import replace
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.analysis import AnalysisEngine
from app.backtest import BacktestRunConfig, BacktestRunner
from app.backtest.data import MarketDataRepository
from app.database import SessionLocal
from app.research.advanced import (
    RobustnessScenario, aggregate_walk_forward, analyze_regimes, correlate_equity_returns,
    AdvancedResearchResult, evaluate_robustness, monte_carlo_trades, parameter_sensitivity,
    weighted_rebalanced_portfolio,
)
from app.research.engine import ResearchEngine
from app.research.models import ExperimentDefinition
from app.research.service import ResearchApplicationService
from app.research.storage import ResearchAnalysisRepository, ResearchResultRepository
from app.strategies import get_strategy_factory


router = APIRouter(prefix="/api/research", tags=["research"])


def _encode_research(value):
    """Encode dataclasses and expose canonical IDs omitted from dataclass fields."""
    encoded = jsonable_encoder(value)

    def attach(original, target):
        if isinstance(original, ExperimentDefinition) and isinstance(target, dict):
            target["experiment_id"] = original.experiment_id
        if is_dataclass(original) and isinstance(target, dict):
            for field in fields(original):
                attach(getattr(original, field.name), target.get(field.name))
        elif isinstance(original, (tuple, list)) and isinstance(target, list):
            for source, item in zip(original, target):
                attach(source, item)

    attach(value, encoded)
    return encoded


def _persist_advanced(db, method, inputs, configuration, output):
    result = AdvancedResearchResult(method, tuple(sorted(set(inputs))),
                                    tuple(sorted(configuration.items())), output)
    ResearchAnalysisRepository(db).save(result)
    db.commit()
    encoded = _encode_research(result)
    encoded["analysis_id"] = result.analysis_id
    return encoded


class ResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["single", "batch", "oos", "walk_forward"]
    strategy_id: str
    symbol: str
    timeframe: Literal["M1", "M5", "M15", "H1", "H4", "D1"]
    start: str
    end: str
    training_start: str | None = None
    training_end: str | None = None
    testing_start: str | None = None
    testing_end: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    parameter_space: list[dict[str, Any]] = Field(default_factory=list)
    search_method: Literal["grid", "random"] = "grid"
    count: int = Field(default=100, ge=1, le=10000)
    seed: int | None = None
    selection_metric: str = "total_return"
    maximize: bool = True
    top_n: int = Field(default=1, ge=1, le=100)
    training_days: int | None = Field(default=None, ge=1, le=10000)
    testing_days: int | None = Field(default=None, ge=1, le=10000)
    step_days: int | None = Field(default=None, ge=1, le=10000)
    anchored: bool = True
    initial_cash: float = Field(default=100000, gt=0)
    position_size: float = Field(default=1, gt=0)
    commission_per_unit: float = Field(default=0, ge=0)
    commission_rate: float = Field(default=0, ge=0)
    spread_scale: float = Field(default=1, ge=0)
    slippage: float = Field(default=0, ge=0)
    periods_per_year: float | None = Field(default=None, gt=0)
    risk_free_rate: float = Field(default=0, gt=-1)
    target_return: float = Field(default=0, gt=-1)


class SensitivityRequest(BaseModel):
    experiment_ids: list[str] = Field(min_length=1, max_length=10000)
    metric: str
    parameters: list[str] = Field(min_length=1, max_length=8)


class MonteCarloRequest(BaseModel):
    experiment_id: str
    simulations: int = Field(ge=1, le=100000)
    seed: int
    method: Literal["bootstrap", "shuffle"] = "bootstrap"


class CorrelationRequest(BaseModel):
    experiment_ids: list[str] = Field(min_length=2, max_length=1000)


class PortfolioRequest(BaseModel):
    experiment_ids: list[str] = Field(min_length=1, max_length=1000)
    weights: dict[str, float]
    initial_capital: float = Field(gt=0)


class RegimeRequest(BaseModel):
    experiment_id: str
    lookback: int = Field(ge=2, le=10000)
    volatility_threshold: float = Field(ge=0)
    trend_threshold: float = Field(default=0, ge=0)


class RobustnessScenarioRequest(BaseModel):
    name: str
    commission_multiplier: float = Field(default=1, ge=0)
    spread_multiplier: float = Field(default=1, ge=0)
    slippage_multiplier: float = Field(default=1, ge=0)
    commission_per_unit_addition: float = Field(default=0, ge=0)
    slippage_addition: float = Field(default=0, ge=0)
    position_size_multiplier: float = Field(default=1, ge=0)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None


class RobustnessRequest(BaseModel):
    experiment_id: str
    metric: str
    scenarios: list[RobustnessScenarioRequest] = Field(min_length=1, max_length=200)


def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get("/strategies")
def strategies(db: Session = Depends(get_db)):
    return ResearchApplicationService(db).available_strategies()


@router.get("/latest-test")
def latest_test(db: Session = Depends(get_db)):
    """Expose the latest successful canonical single-test result to Research."""
    result = ResearchResultRepository(db).latest_single_run()
    return None if result is None else _encode_research(result)


@router.get("/market-data/{symbol}")
def market_data_catalog(symbol: str, db: Session = Depends(get_db)):
    return ResearchApplicationService(db).market_data_catalog(symbol)


@router.post("/run")
def run_research(request: ResearchRunRequest, db: Session = Depends(get_db)):
    try:
        result = ResearchApplicationService(db).run(request.model_dump(exclude_none=True))
        if request.mode == "walk_forward":
            aggregate = aggregate_walk_forward(result)
            records = tuple(item for window in result.windows for item in
                            (*window.result.training.results, *window.result.testing.results))
            if records:
                inputs = tuple(item.definition.experiment_id for item in records)
                persisted = _persist_advanced(db, "walk_forward_aggregate", inputs,
                                              {"candidate_rank": 1, "config": result.config}, aggregate)
            else:
                persisted = None
                db.commit()
            return {"walk_forward": _encode_research(result),
                    "aggregate": _encode_research(aggregate),
                    "advanced_analysis": persisted}
        db.commit()
        return _encode_research(result)
    except (ValueError, KeyError, TypeError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        db.rollback()
        raise


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    result = ResearchResultRepository(db).get(experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return _encode_research(result)


@router.post("/advanced/sensitivity")
def sensitivity(request: SensitivityRequest, db: Session = Depends(get_db)):
    store = ResearchResultRepository(db)
    results = [store.get(identity) for identity in request.experiment_ids]
    if any(result is None for result in results):
        raise HTTPException(status_code=404, detail="One or more experiments were not found")
    try:
        output = parameter_sensitivity(results, request.metric, request.parameters)
        return _persist_advanced(db, "sensitivity", request.experiment_ids,
                                 {"metric": request.metric, "parameters": tuple(request.parameters)}, output)
    except (ValueError, AttributeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advanced/monte-carlo")
def monte_carlo(request: MonteCarloRequest, db: Session = Depends(get_db)):
    result = ResearchResultRepository(db).get(request.experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if result.backtest_result is None:
        raise HTTPException(status_code=422, detail="Experiment has no completed backtest")
    try:
        output = monte_carlo_trades(
            result.backtest_result, simulations=request.simulations,
            seed=request.seed, method=request.method)
        return _persist_advanced(db, "monte_carlo", (request.experiment_id,),
                                 {"simulations": request.simulations, "seed": request.seed,
                                  "method": request.method}, output)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advanced/correlation")
def correlation(request: CorrelationRequest, db: Session = Depends(get_db)):
    store = ResearchResultRepository(db)
    results = [store.get(identity) for identity in request.experiment_ids]
    if any(result is None for result in results):
        raise HTTPException(status_code=404, detail="One or more experiments were not found")
    complete = [result for result in results if result.analysis_result is not None]
    if len(complete) != len(results):
        raise HTTPException(status_code=422, detail="Correlation requires completed analysis results")
    try:
        series = {result.definition.experiment_id: result.analysis_result for result in complete}
        output = correlate_equity_returns(series)
        return _persist_advanced(db, "correlation", request.experiment_ids,
                                 {"frequency": "equity_observation", "method": "pearson"}, output)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advanced/portfolio")
def portfolio(request: PortfolioRequest, db: Session = Depends(get_db)):
    store = ResearchResultRepository(db)
    records = [store.get(identity) for identity in request.experiment_ids]
    if any(result is None for result in records):
        raise HTTPException(status_code=404, detail="One or more experiments were not found")
    if any(result.analysis_result is None for result in records):
        raise HTTPException(status_code=422, detail="Portfolio requires completed analysis results")
    try:
        series = {record.definition.experiment_id: record.analysis_result for record in records}
        output = weighted_rebalanced_portfolio(series, request.weights, request.initial_capital)
        return _persist_advanced(db, "weighted_portfolio", request.experiment_ids,
                                 {"weights": tuple(sorted(request.weights.items())),
                                  "initial_capital": request.initial_capital,
                                  "rebalancing": "each_common_equity_observation"}, output)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advanced/regimes")
def regimes(request: RegimeRequest, db: Session = Depends(get_db)):
    from app.research.advanced import RegimeDefinition

    result = ResearchResultRepository(db).get(request.experiment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if result.backtest_result is None:
        raise HTTPException(status_code=422, detail="Experiment has no completed backtest")
    definition = result.definition
    bars = MarketDataRepository(db).load(
        definition.symbol, start=definition.period.start, end_exclusive=definition.period.end,
        timeframe=definition.timeframe)
    bars = tuple(bar for bar in bars if definition.period.contains(bar.timestamp))
    dataset_fingerprint = ResearchEngine.fingerprint_dataset(
        definition.symbol, definition.timeframe, definition.period, bars)
    if dataset_fingerprint != definition.dataset_fingerprint:
        raise HTTPException(status_code=409, detail="Stored market data changed since this experiment ran")
    try:
        regime = RegimeDefinition(request.lookback, request.volatility_threshold,
                                  request.trend_threshold)
        output = analyze_regimes(result.backtest_result, bars, regime)
        return _persist_advanced(db, "regime", (request.experiment_id,),
                                 {"definition": regime, "dataset_fingerprint": dataset_fingerprint}, output)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/advanced/robustness")
def robustness(request: RobustnessRequest, db: Session = Depends(get_db)):
    baseline = ResearchResultRepository(db).get(request.experiment_id)
    if baseline is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    definition = baseline.definition
    factory = get_strategy_factory(definition.strategy_id)
    repository = MarketDataRepository(db)
    research = ResearchEngine(BacktestRunner(repository), AnalysisEngine(definition.analysis_config),
                              ResearchResultRepository(db))
    base = definition.backtest_config
    params = definition.parameters.as_dict()
    from app.research.models import DateRange
    base_period = definition.period
    scenarios = []
    for item in request.scenarios:
        period = None
        if item.start is not None or item.end is not None:
            if item.start is None or item.end is None:
                raise HTTPException(status_code=422, detail="Scenario date perturbation needs both start and end")
            period = DateRange(datetime.fromisoformat(item.start.replace("Z", "+00:00")),
                               datetime.fromisoformat(item.end.replace("Z", "+00:00")))
            try:
                outside = period.start < base_period.start or period.end > base_period.end
            except TypeError as error:
                raise HTTPException(status_code=422,
                                    detail="Robustness and baseline dates must share timezone awareness") from error
            if outside:
                raise HTTPException(status_code=422, detail="Robustness date windows must be within the baseline period")
        scenarios.append(RobustnessScenario(
            item.name, item.commission_multiplier, item.spread_multiplier,
            item.slippage_multiplier, item.commission_per_unit_addition,
            item.slippage_addition, item.position_size_multiplier,
            tuple(sorted(item.parameter_overrides.items())), period))
    scenarios = tuple(scenarios)

    def run_scenario(scenario):
        changed = dict(params)
        changed.update(dict(scenario.parameter_overrides))
        config = replace(base,
            start=scenario.period.start if scenario.period else base.start,
            end=scenario.period.end if scenario.period else base.end,
            commission_per_unit=(base.commission_per_unit * scenario.commission_multiplier
                                 + scenario.commission_per_unit_addition),
            commission_rate=base.commission_rate * scenario.commission_multiplier,
            spread_scale=base.spread_scale * scenario.spread_multiplier,
            slippage=base.slippage * scenario.slippage_multiplier + scenario.slippage_addition)
        config = replace(config, position_size=base.position_size * scenario.position_size_multiplier)
        return research.run_batch((changed,), strategy_factory=factory, config=config,
                                  analysis_settings=definition.analysis_config,
                                  search_method=f"robustness:{scenario.name}",
                                  parameter_space=None).results[0]

    output = evaluate_robustness(baseline, scenarios, run_scenario, request.metric)
    config = {"metric": request.metric, "scenarios": scenarios}
    return _persist_advanced(db, "robustness", (request.experiment_id,), config, output)


@router.get("/advanced/results/{analysis_id}")
def get_advanced_result(analysis_id: str, db: Session = Depends(get_db)):
    result = ResearchAnalysisRepository(db).get(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Advanced analysis not found")
    encoded = _encode_research(result)
    encoded["analysis_id"] = result.analysis_id
    return encoded
