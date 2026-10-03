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
from app.research.advanced import (
    AdvancedResearchResult, CorrelationMatrix, MonteCarloResult, ParameterSensitivity, PortfolioResearchResult,
    RegimeDefinition, RegimeResult, RobustnessResult, RobustnessScenario,
    WalkForwardAggregate, WalkForwardWindowAggregate, aggregate_walk_forward,
    analyze_regimes, correlate_equity_returns, evaluate_robustness, monte_carlo_trades,
    parameter_sensitivity, weighted_rebalanced_portfolio,
)

__all__ = [
    "BatchResearchResult", "CandidateSelection", "ChoiceParameter", "DateRange",
    "ExperimentDefinition", "ExperimentPhase", "ExperimentStatus", "FailureInfo",
    "FixedParameter", "FloatRange", "IntegerRange", "OutOfSampleResult",
    "ParameterSet", "ParameterSpace", "ResearchEngine", "ResearchResult",
    "ResearchResultStore", "SelectionRule", "StrategyFactory", "TrainTestSplit", "WalkForwardConfig",
    "WalkForwardResult", "WalkForwardWindowResult", "ResearchResultRepository",
    "ResearchAnalysisRepository", "AdvancedResearchResult",
    "CorrelationMatrix", "MonteCarloResult", "ParameterSensitivity", "PortfolioResearchResult",
    "RegimeDefinition", "RegimeResult", "RobustnessResult", "RobustnessScenario",
    "WalkForwardAggregate", "WalkForwardWindowAggregate", "aggregate_walk_forward",
    "analyze_regimes", "correlate_equity_returns", "evaluate_robustness",
    "monte_carlo_trades", "parameter_sensitivity", "weighted_rebalanced_portfolio",
]


def __getattr__(name: str):
    # Do not require DATABASE_URL simply to use the in-memory research engine.
    if name == "ResearchResultRepository":
        from app.research.storage import ResearchResultRepository
        return ResearchResultRepository
    if name == "ResearchAnalysisRepository":
        from app.research.storage import ResearchAnalysisRepository
        return ResearchAnalysisRepository
    raise AttributeError(name)
