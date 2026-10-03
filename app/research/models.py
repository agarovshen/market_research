"""Immutable definitions and outputs for reproducible research runs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite
import re

from app.analysis.engine import AnalysisSettings
from app.analysis.models import AnalysisResult
from app.backtest.models import BacktestResult
from app.backtest.runner import BacktestRunConfig
from app.research.canonical import canonical_json, sha256_json
from app.research.parameters import ParameterSet, ParameterSpace


@dataclass(frozen=True, slots=True)
class DateRange:
    """Chronological half-open interval [start, end), aligned with OOS boundaries."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise TypeError("Date range boundaries must be datetimes")
        try:
            valid = self.start < self.end
        except TypeError as error:
            raise ValueError("Date range endpoints must use compatible timezone awareness") from error
        if not valid:
            raise ValueError("Date range must have start earlier than end")

    def contains(self, timestamp: datetime) -> bool:
        try:
            return self.start <= timestamp < self.end
        except TypeError as error:
            raise ValueError("Timestamp and date range must use compatible timezone awareness") from error


@dataclass(frozen=True, slots=True)
class TrainTestSplit:
    training: DateRange
    testing: DateRange

    def __post_init__(self) -> None:
        try:
            disjoint = self.training.end <= self.testing.start
        except TypeError as error:
            raise ValueError("Training and test ranges must use compatible timezone awareness") from error
        if not disjoint:
            raise ValueError("Training and test ranges overlap or are out of chronological order")


@dataclass(frozen=True, slots=True)
class SelectionRule:
    metric: str
    maximize: bool = True
    top_n: int = 1

    ALLOWED_METRICS = frozenset({
        "total_return", "total_profit_loss", "trades.net_profit", "trades.win_rate",
        "trades.profit_factor", "trades.expectancy", "trades.average_trade",
        "risk.period_volatility", "risk.annualized_volatility", "risk.annualized_return",
        "risk.sharpe_ratio", "risk.sortino_ratio", "risk.calmar_ratio",
        "drawdown.max_drawdown", "drawdown.max_drawdown_pct",
    })

    def __post_init__(self) -> None:
        if not isinstance(self.maximize, bool):
            raise ValueError("maximize must be boolean")
        if not isinstance(self.metric, str) or self.metric not in self.ALLOWED_METRICS:
            raise ValueError(f"Unsupported analysis selection metric: {self.metric}")
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int) or self.top_n <= 0:
            raise ValueError("top_n must be a positive integer")


class ExperimentPhase(str, Enum):
    BATCH = "batch"
    TRAIN = "train"
    OOS = "oos"


class ExperimentStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    period: DateRange
    parameters: ParameterSet
    parameter_space: ParameterSpace | None
    backtest_config: BacktestRunConfig
    analysis_config: AnalysisSettings
    dataset_fingerprint: str
    candidate_set_fingerprint: str
    python_version: str
    research_engine_version: str
    seed: int | None
    search_method: str
    tested_configurations: int
    phase: ExperimentPhase = ExperimentPhase.BATCH
    training_period: DateRange | None = None
    testing_period: DateRange | None = None
    selection_rule: SelectionRule | None = None
    selected_rank: int | None = None
    source_training_experiment_id: str | None = None
    walk_forward_window: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ExperimentPhase):
            raise ValueError("phase must be an ExperimentPhase")
        if (not isinstance(self.strategy_id, str) or not self.strategy_id.strip()
                or not isinstance(self.strategy_version, str) or not self.strategy_version.strip()):
            raise ValueError("Strategy identity and version are required")
        if (not re.fullmatch(r"[0-9a-f]{64}", self.dataset_fingerprint)
                or not re.fullmatch(r"[0-9a-f]{64}", self.candidate_set_fingerprint)
                or not self.search_method.strip() or not self.python_version
                or not self.research_engine_version):
            raise ValueError("Dataset, candidate set, search method, and software identities are required")
        if (isinstance(self.tested_configurations, bool)
                or not isinstance(self.tested_configurations, int) or self.tested_configurations < 1):
            raise ValueError("tested_configurations must be positive")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be an integer or None")
        if self.selected_rank is not None and (
            isinstance(self.selected_rank, bool) or not isinstance(self.selected_rank, int)
            or self.selected_rank < 1
        ):
            raise ValueError("selected_rank must be positive")
        if self.walk_forward_window is not None and (
            isinstance(self.walk_forward_window, bool)
            or not isinstance(self.walk_forward_window, int)
            or self.walk_forward_window < 1
        ):
            raise ValueError("walk_forward_window must be positive")
        if self.phase is ExperimentPhase.OOS:
            if (self.training_period is None or self.testing_period is None
                    or self.period != self.testing_period or self.selection_rule is None
                    or self.selected_rank is None or not self.source_training_experiment_id):
                raise ValueError("OOS definitions require both periods, selection rule, rank, and training source")
            TrainTestSplit(self.training_period, self.testing_period)
        elif self.phase is ExperimentPhase.TRAIN:
            if (self.training_period is None or self.testing_period is None
                    or self.period != self.training_period
                    or self.source_training_experiment_id is not None or self.selected_rank is not None):
                raise ValueError("Training definitions require the explicit train/test split")
            TrainTestSplit(self.training_period, self.testing_period)
        elif self.source_training_experiment_id is not None or self.selected_rank is not None:
            raise ValueError("Only OOS definitions may reference selected training candidates")
        elif self.training_period is not None or self.testing_period is not None:
            raise ValueError("Only train/test experiments may contain split periods")

    @property
    def experiment_id(self) -> str:
        return sha256_json(("research-experiment-v1", self))

    @property
    def canonical_representation(self) -> str:
        return canonical_json(("research-experiment-v1", self))


@dataclass(frozen=True, slots=True)
class FailureInfo:
    exception_type: str
    message: str


@dataclass(frozen=True, slots=True)
class ResearchResult:
    definition: ExperimentDefinition
    status: ExperimentStatus
    backtest_result: BacktestResult | None
    analysis_result: AnalysisResult | None
    failure: FailureInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExperimentStatus):
            raise ValueError("status must be an ExperimentStatus")
        completed = self.status is ExperimentStatus.COMPLETED
        if completed:
            if self.backtest_result is None or self.analysis_result is None or self.failure is not None:
                raise ValueError("Completed results require backtest and analysis and cannot contain failure details")
        elif self.backtest_result is not None or self.analysis_result is not None or self.failure is None:
            raise ValueError("Failed results require failure details and cannot contain partial result data")


@dataclass(frozen=True, slots=True)
class BatchResearchResult:
    results: tuple[ResearchResult, ...]
    tested_configurations: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        if (isinstance(self.tested_configurations, bool)
                or self.tested_configurations != len(self.results)):
            raise ValueError("tested_configurations must match batch result count")

    @property
    def completed_count(self) -> int:
        return sum(result.status is ExperimentStatus.COMPLETED for result in self.results)

    @property
    def failed_count(self) -> int:
        return self.tested_configurations - self.completed_count


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    training_experiment_id: str
    parameters: ParameterSet
    metric_value: float
    rank: int

    def __post_init__(self) -> None:
        if not self.training_experiment_id or not isfinite(self.metric_value):
            raise ValueError("Candidate selection requires training identity and finite metric")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("Candidate rank must be positive")


@dataclass(frozen=True, slots=True)
class OutOfSampleResult:
    training: BatchResearchResult
    selection_rule: SelectionRule
    selected: tuple[CandidateSelection, ...]
    testing: BatchResearchResult

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected", tuple(self.selected))
        if any(item.definition.phase is not ExperimentPhase.TRAIN for item in self.training.results):
            raise ValueError("Out-of-sample training batch contains non-training results")
        training = {item.definition.experiment_id: item for item in self.training.results}
        selected_ids = set()
        for candidate in self.selected:
            source = training.get(candidate.training_experiment_id)
            if (source is None or source.status is not ExperimentStatus.COMPLETED
                    or source.definition.parameters != candidate.parameters):
                raise ValueError("Selected candidates must reference completed training experiments")
            selected_ids.add(candidate.training_experiment_id)
        if len(selected_ids) != len(self.selected):
            raise ValueError("A training experiment may be selected only once")
        if tuple(candidate.rank for candidate in self.selected) != tuple(range(1, len(self.selected) + 1)):
            raise ValueError("Selected candidate ranks must be contiguous and ordered")
        if len(self.testing.results) != len(self.selected):
            raise ValueError("Every selected candidate must have exactly one OOS result")
        testing_sources = set()
        for outcome in self.testing.results:
            definition = outcome.definition
            source_id = definition.source_training_experiment_id
            if definition.phase is not ExperimentPhase.OOS or source_id not in selected_ids:
                raise ValueError("OOS results must reference the selected training candidate")
            if source_id in testing_sources:
                raise ValueError("A selected training candidate may have only one OOS result")
            if definition.parameters != training[source_id].definition.parameters:
                raise ValueError("OOS parameter values must match the source training result")
            train_definition = training[source_id].definition
            if (definition.training_period != train_definition.training_period
                    or definition.testing_period != train_definition.testing_period
                    or definition.selection_rule != self.selection_rule
                    or definition.candidate_set_fingerprint != train_definition.candidate_set_fingerprint):
                raise ValueError("OOS metadata must match its training split and selection context")
            testing_sources.add(source_id)
        if testing_sources != selected_ids:
            raise ValueError("OOS results must cover exactly the selected candidates")

    @property
    def tested_configurations(self) -> int:
        return self.training.tested_configurations


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    period: DateRange
    training_duration: timedelta
    testing_duration: timedelta
    step: timedelta
    anchored: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.anchored, bool):
            raise ValueError("anchored must be boolean")
        if min(self.training_duration, self.testing_duration, self.step) <= timedelta(0):
            raise ValueError("Walk-forward durations and step must be positive")
        if self.step < self.testing_duration:
            raise ValueError("step must be at least testing_duration to keep OOS windows disjoint")

    def splits(self) -> tuple[TrainTestSplit, ...]:
        splits = []
        cursor = self.period.start
        while True:
            train_start = self.period.start if self.anchored else cursor
            train_end = cursor + self.training_duration
            test_end = train_end + self.testing_duration
            if test_end > self.period.end:
                break
            splits.append(TrainTestSplit(
                DateRange(train_start, train_end),
                DateRange(train_end, test_end),
            ))
            cursor += self.step
        return tuple(splits)


@dataclass(frozen=True, slots=True)
class WalkForwardWindowResult:
    index: int
    split: TrainTestSplit
    result: OutOfSampleResult

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 1:
            raise ValueError("Walk-forward window index must be positive")
        if any(item.definition.period != self.split.training for item in self.result.training.results):
            raise ValueError("Walk-forward training period does not match the window split")
        if any(item.definition.period != self.split.testing for item in self.result.testing.results):
            raise ValueError("Walk-forward test period does not match the window split")


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    config: WalkForwardConfig
    windows: tuple[WalkForwardWindowResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "windows", tuple(self.windows))
        expected = self.config.splits()
        if tuple(window.index for window in self.windows) != tuple(range(1, len(expected) + 1)):
            raise ValueError("Walk-forward windows must be complete and in stable order")
        if tuple(window.split for window in self.windows) != expected:
            raise ValueError("Walk-forward result splits do not match configuration")
