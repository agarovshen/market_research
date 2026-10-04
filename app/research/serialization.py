"""Versionable JSON codec for durable research results; never pickles Python objects."""

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any

from app.analysis.engine import AnalysisSettings
from app.analysis.models import (
    AnalysisResult, DrawdownPoint, DrawdownSummary, EquityAnalysisPoint,
    ReturnPoint, RiskStatistics, TradeStatistics,
)
from app.backtest.models import (
    BacktestResult, EquityPoint, ExecutionEvent, ExecutionEventType, Order,
    OrderAction, Position, Side, Trade,
)
from app.backtest.runner import BacktestRunConfig
from app.research.models import (
    BatchResearchResult, CandidateSelection, DateRange, ExperimentDefinition,
    ExperimentPhase, ExperimentStatus, FailureInfo, OutOfSampleResult,
    ResearchResult, SelectionRule, TrainTestSplit, WalkForwardConfig,
    WalkForwardResult, WalkForwardWindowResult,
)
from app.research.advanced import (
    AdvancedResearchResult, CorrelationMatrix, MonteCarloResult, ParameterSensitivity,
    PortfolioPoint, PortfolioResearchResult, RegimeDefinition, RegimeObservation,
    RegimeResult, RegimeStatistics, RobustnessPoint, RobustnessResult, RobustnessScenario,
    SensitivityCell, WalkForwardAggregate, WalkForwardWindowAggregate,
)
from app.research.parameters import (
    ChoiceParameter, FixedParameter, FloatRange, IntegerRange, ParameterSet, ParameterSpace,
)


_DATACLASSES = {
    cls.__name__: cls for cls in (
        AnalysisSettings, AnalysisResult, DrawdownPoint, DrawdownSummary,
        EquityAnalysisPoint, ReturnPoint, RiskStatistics, TradeStatistics,
        BacktestResult, EquityPoint, ExecutionEvent, Order, Position, Trade, BacktestRunConfig,
        BatchResearchResult, CandidateSelection, DateRange, ExperimentDefinition,
        FailureInfo, OutOfSampleResult, ResearchResult, SelectionRule,
        TrainTestSplit, WalkForwardConfig, WalkForwardResult, WalkForwardWindowResult,
        ChoiceParameter, FixedParameter, FloatRange, IntegerRange, ParameterSet, ParameterSpace,
        AdvancedResearchResult, CorrelationMatrix, MonteCarloResult, ParameterSensitivity,
        PortfolioPoint, PortfolioResearchResult, RegimeDefinition, RegimeObservation,
        RegimeResult, RegimeStatistics, RobustnessPoint, RobustnessResult, RobustnessScenario,
        SensitivityCell, WalkForwardAggregate, WalkForwardWindowAggregate,
    )
}
_ENUMS = {cls.__name__: cls for cls in (
    OrderAction, Side, ExecutionEventType, ExperimentPhase, ExperimentStatus,
)}


def encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if is_dataclass(value):
        name = type(value).__name__
        if name not in _DATACLASSES:
            raise TypeError(f"Unsupported research persistence type: {name}")
        return {"$type": name, "fields": {field.name: encode(getattr(value, field.name))
                                            for field in fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Research persistence mapping keys must be strings")
        return {"$map": {key: encode(value[key]) for key in sorted(value)}}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise TypeError(f"Unsupported research persistence value: {type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if "$datetime" in value:
            return datetime.fromisoformat(value["$datetime"])
        if "$enum" in value:
            try:
                return _ENUMS[value["$enum"]](value["value"])
            except KeyError as error:
                raise ValueError(f"Unknown stored enum: {value.get('$enum')}") from error
        if "$tuple" in value:
            return tuple(decode(item) for item in value["$tuple"])
        if "$map" in value:
            return {key: decode(item) for key, item in value["$map"].items()}
        if "$type" in value:
            try:
                cls = _DATACLASSES[value["$type"]]
            except KeyError as error:
                raise ValueError(f"Unknown stored research type: {value.get('$type')}") from error
            fields_value = value.get("fields")
            if not isinstance(fields_value, dict):
                raise ValueError("Malformed stored dataclass fields")
            return cls(**{key: decode(item) for key, item in fields_value.items()})
        raise ValueError("Malformed tagged research payload")
    return value


def encode_result(result: ResearchResult) -> dict[str, Any]:
    return {"schema_version": 1, "result": encode(result)}


def decode_result(payload: dict[str, Any]) -> ResearchResult:
    if payload.get("schema_version") != 1 or "result" not in payload:
        raise ValueError("Unsupported or malformed research result schema")
    result = decode(payload["result"])
    if not isinstance(result, ResearchResult):
        raise ValueError("Stored payload is not a ResearchResult")
    return result


def encode_advanced_result(result: AdvancedResearchResult) -> dict[str, Any]:
    return {"schema_version": 1, "result": encode(result)}


def decode_advanced_result(payload: dict[str, Any]) -> AdvancedResearchResult:
    if payload.get("schema_version") != 1 or "result" not in payload:
        raise ValueError("Unsupported or malformed advanced research payload")
    result = decode(payload["result"])
    if not isinstance(result, AdvancedResearchResult):
        raise ValueError("Stored payload is not an AdvancedResearchResult")
    return result
