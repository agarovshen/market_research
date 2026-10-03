"""Reproducible strategy research built on the Backtest and Analysis layers."""

from app.research.engine import ResearchEngine, ResearchResultStore, StrategyFactory
from app.research.models import (
    BatchResearchResult,
    CandidateSelection,
    DateRange,
    ExperimentDefinition,
    ExperimentPhase,
    ExperimentStatus,
    FailureInfo,
    OutOfSampleResult,
    ResearchResult,
    SelectionRule,
    TrainTestSplit,
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardWindowResult,
)
from app.research.parameters import (
    ChoiceParameter,
    FixedParameter,
    FloatRange,
    IntegerRange,
    ParameterSet,
    ParameterSpace,
)

__all__ = [
    "BatchResearchResult", "CandidateSelection", "ChoiceParameter", "DateRange",
    "ExperimentDefinition", "ExperimentPhase", "ExperimentStatus", "FailureInfo",
    "FixedParameter", "FloatRange", "IntegerRange", "OutOfSampleResult",
    "ParameterSet", "ParameterSpace", "ResearchEngine", "ResearchResult",
    "ResearchResultStore", "SelectionRule", "StrategyFactory", "TrainTestSplit", "WalkForwardConfig",
    "WalkForwardResult", "WalkForwardWindowResult", "ResearchResultRepository",
]


def __getattr__(name: str):
    # Do not require DATABASE_URL simply to use the in-memory research engine.
    if name == "ResearchResultRepository":
        from app.research.storage import ResearchResultRepository
        return ResearchResultRepository
    raise AttributeError(name)
