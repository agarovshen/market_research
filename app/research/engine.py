"""Sequential research orchestration over the established Backtest and Analysis layers."""

from dataclasses import replace
from hashlib import sha256
import json
from math import isfinite
from platform import python_version
from typing import Iterable, Mapping, Protocol

from app.analysis import AnalysisEngine, AnalysisSettings
from app.backtest import BacktestRunConfig, BacktestRunner, Strategy
from app.backtest.models import Bar
from app.research.canonical import json_value
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
from app.research.parameters import ParameterSet, ParameterSpace, ParameterValue


class StrategyFactory(Protocol):
    """Versioned, user-owned strategy construction and parameter validation."""

    strategy_id: str
    strategy_version: str

    def validate(self, parameters: Mapping[str, ParameterValue]) -> None: ...

    def create(self, parameters: Mapping[str, ParameterValue]) -> Strategy: ...


class ResearchResultStore(Protocol):
    def save_many(self, results: tuple[ResearchResult, ...]) -> None: ...


class _CachedRepository:
    """One immutable phase dataset shared by every run in a batch."""

    def __init__(self, symbol: str, timeframe: str, period: DateRange, bars: tuple[Bar, ...]):
        self.symbol = symbol
        self.timeframe = timeframe
        self.period = period
        self.bars = bars

    def load(
        self,
        instrument: str,
        start=None,
        end=None,
        *,
        timeframe: str = "M1",
    ) -> tuple[Bar, ...]:
        if str(instrument).upper() != self.symbol or timeframe.upper() != self.timeframe:
            raise ValueError("Research runner requested data outside its cached phase dataset")
        if start == self.period.start and end == self.period.end:
            return self.bars
        return tuple(
            bar for bar in self.bars
            if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp < end)
        )


class ResearchEngine:
    """Run reproducible batches, train/test selection, and walk-forward workflows."""

    def __init__(
        self,
        backtest_runner: BacktestRunner,
        analysis_engine: AnalysisEngine | None = None,
        result_store: ResearchResultStore | None = None,
    ):
        self.backtest_runner = backtest_runner
        self.analysis_engine = analysis_engine or AnalysisEngine()
        self.result_store = result_store

    def run_batch(
        self,
        configurations: Iterable[Mapping[str, ParameterValue] | ParameterSet],
        *,
        strategy_factory: StrategyFactory,
        config: BacktestRunConfig,
        analysis_settings: AnalysisSettings | None = None,
        seed: int | None = None,
        search_method: str = "manual",
        parameter_space: ParameterSpace | None = None,
    ) -> BatchResearchResult:
        period = self._config_range(config)
        return self._run_phase(
            configurations,
            strategy_factory=strategy_factory,
            config=config,
            period=period,
            analysis_settings=analysis_settings or self.analysis_engine.settings,
            seed=seed,
            search_method=search_method,
            phase=ExperimentPhase.BATCH,
            parameter_space=parameter_space,
        )

    def run_out_of_sample(
        self,
        configurations: Iterable[Mapping[str, ParameterValue] | ParameterSet],
        *,
        strategy_factory: StrategyFactory,
        config: BacktestRunConfig,
        split: TrainTestSplit,
        selection: SelectionRule,
        analysis_settings: AnalysisSettings | None = None,
        seed: int | None = None,
        search_method: str = "manual",
        parameter_space: ParameterSpace | None = None,
        _walk_forward_window: int | None = None,
    ) -> OutOfSampleResult:
        """Evaluate on train, select only those results, and only then run held-out data."""
        self._validate_factory(strategy_factory)
        candidates = self._unique_configurations(configurations)
        analysis_config = analysis_settings or self.analysis_engine.settings
        training = self._run_phase(
            candidates,
            strategy_factory=strategy_factory,
            config=config,
            period=split.training,
            analysis_settings=analysis_config,
            seed=seed,
            search_method=search_method,
            phase=ExperimentPhase.TRAIN,
            parameter_space=parameter_space,
            selection=selection,
            walk_forward_window=_walk_forward_window,
            total_candidates=len(candidates),
            candidate_set_fingerprint=self._candidate_fingerprint(candidates),
            train_test_split=split,
        )
        selected = self.select_training_candidates(training, selection)
        if not selected:
            testing = BatchResearchResult((), 0)
            return OutOfSampleResult(training, selection, (), testing)

        # Test data is queried only after training selection is complete.
        test_candidates = tuple((candidate.parameters, candidate) for candidate in selected)
        testing = self._run_phase(
            (parameters for parameters, _ in test_candidates),
            strategy_factory=strategy_factory,
            config=config,
            period=split.testing,
            analysis_settings=analysis_config,
            seed=seed,
            search_method=search_method,
            phase=ExperimentPhase.OOS,
            parameter_space=parameter_space,
            selection=selection,
            candidate_sources={candidate.parameters: candidate for _, candidate in test_candidates},
            walk_forward_window=_walk_forward_window,
            total_candidates=len(candidates),
            candidate_set_fingerprint=self._candidate_fingerprint(candidates),
            train_test_split=split,
        )
        return OutOfSampleResult(training, selection, selected, testing)

    def select_training_candidates(
        self,
        training: BatchResearchResult,
        selection: SelectionRule,
    ) -> tuple[CandidateSelection, ...]:
        """Select exclusively from a batch explicitly tagged as training data."""
        if any(result.definition.phase is not ExperimentPhase.TRAIN for result in training.results):
            raise ValueError("Candidate selection accepts training-phase results only")
        ranked = []
        for result in training.results:
            if result.status is not ExperimentStatus.COMPLETED:
                continue
            value = self._metric(result, selection.metric)
            if value is not None:
                ranked.append((value, result))
        ranked.sort(key=lambda pair: ((-pair[0] if selection.maximize else pair[0]),
                                      pair[1].definition.experiment_id))
        return tuple(
            CandidateSelection(result.definition.experiment_id, result.definition.parameters,
                               value, index)
            for index, (value, result) in enumerate(ranked[:selection.top_n], start=1)
        )

    def run_walk_forward(
        self,
        configurations: Iterable[Mapping[str, ParameterValue] | ParameterSet],
        *,
        strategy_factory: StrategyFactory,
        config: BacktestRunConfig,
        walk_forward: WalkForwardConfig,
        selection: SelectionRule,
        analysis_settings: AnalysisSettings | None = None,
        seed: int | None = None,
        search_method: str = "manual",
        parameter_space: ParameterSpace | None = None,
    ) -> WalkForwardResult:
        candidates = self._unique_configurations(configurations)
        windows = tuple(
            WalkForwardWindowResult(
                index,
                split,
                self.run_out_of_sample(
                    candidates,
                    strategy_factory=strategy_factory,
                    config=config,
                    split=split,
                    selection=selection,
                    analysis_settings=analysis_settings,
                    seed=seed,
                    search_method=search_method,
                    parameter_space=parameter_space,
                    _walk_forward_window=index,
                ),
            )
            for index, split in enumerate(walk_forward.splits(), start=1)
        )
        return WalkForwardResult(walk_forward, windows)

    def _run_phase(
        self,
        configurations: Iterable[Mapping[str, ParameterValue] | ParameterSet],
        *,
        strategy_factory: StrategyFactory,
        config: BacktestRunConfig,
        period: DateRange,
        analysis_settings: AnalysisSettings,
        seed: int | None,
        search_method: str,
        phase: ExperimentPhase,
        selection: SelectionRule | None = None,
        candidate_sources: Mapping[ParameterSet, CandidateSelection] | None = None,
        walk_forward_window: int | None = None,
        parameter_space: ParameterSpace | None = None,
        total_candidates: int | None = None,
        candidate_set_fingerprint: str | None = None,
        train_test_split: TrainTestSplit | None = None,
    ) -> BatchResearchResult:
        self._validate_factory(strategy_factory)
        if not search_method or not search_method.strip():
            raise ValueError("search_method cannot be empty")
        if search_method in {"grid", "random"} and parameter_space is None:
            raise ValueError(f"{search_method} research must record its parameter_space")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("seed must be an integer or None")
        if search_method == "random" and seed is None:
            raise ValueError("Random search requires an explicit seed")
        parameters = self._unique_configurations(configurations)
        if not parameters:
            return BatchResearchResult((), 0)

        validation_errors: dict[ParameterSet, FailureInfo] = {}
        for parameter_set in parameters:
            try:
                if parameter_space is not None:
                    parameter_space.validate(parameter_set)
                verdict = strategy_factory.validate(parameter_set.as_dict())
                if verdict is False:
                    raise ValueError("Strategy factory rejected parameters")
            except Exception as error:
                validation_errors[parameter_set] = FailureInfo(type(error).__name__, str(error))

        bars = self._load_phase(config.symbol, config.timeframe, period)
        dataset_id = self._fingerprint(config.symbol, config.timeframe, period, bars)
        candidate_set_fingerprint = candidate_set_fingerprint or self._candidate_fingerprint(parameters)
        cached_runner = BacktestRunner(_CachedRepository(config.symbol, config.timeframe, period, bars))
        analyzer = (self.analysis_engine if analysis_settings == self.analysis_engine.settings
                    else AnalysisEngine(analysis_settings))
        phase_config = replace(config, start=period.start, end=period.end)
        outcomes = []
        for parameter_set in parameters:
            source = (candidate_sources or {}).get(parameter_set)
            definition = ExperimentDefinition(
                strategy_id=strategy_factory.strategy_id,
                strategy_version=strategy_factory.strategy_version,
                symbol=config.symbol,
                timeframe=config.timeframe,
                period=period,
                parameters=parameter_set,
                parameter_space=parameter_space,
                backtest_config=phase_config,
                analysis_config=analysis_settings,
                dataset_fingerprint=dataset_id,
                candidate_set_fingerprint=candidate_set_fingerprint,
                python_version=python_version(),
                research_engine_version="1",
                seed=seed,
                search_method=search_method,
                tested_configurations=total_candidates if total_candidates is not None else len(parameters),
                phase=phase,
                training_period=train_test_split.training if train_test_split else None,
                testing_period=train_test_split.testing if train_test_split else None,
                selection_rule=selection,
                selected_rank=source.rank if source else None,
                source_training_experiment_id=source.training_experiment_id if source else None,
                walk_forward_window=walk_forward_window,
            )
            if parameter_set in validation_errors:
                outcomes.append(ResearchResult(definition, ExperimentStatus.FAILED, None, None,
                                               validation_errors[parameter_set]))
                continue
            if not bars:
                failure = FailureInfo("InsufficientDataError", "No market bars exist in this research period")
                outcomes.append(ResearchResult(definition, ExperimentStatus.FAILED, None, None, failure))
                continue
            try:
                strategy = strategy_factory.create(parameter_set.as_dict())
                backtest = cached_runner.run(strategy=strategy, config=phase_config)
                analysis = analyzer.analyze(backtest)
                outcomes.append(ResearchResult(definition, ExperimentStatus.COMPLETED,
                                               backtest, analysis))
            except Exception as error:
                failure = FailureInfo(type(error).__name__, str(error))
                outcomes.append(ResearchResult(definition, ExperimentStatus.FAILED, None, None, failure))
        batch = BatchResearchResult(tuple(outcomes), len(outcomes))
        if self.result_store is not None:
            self.result_store.save_many(batch.results)
        return batch

    def _load_phase(self, symbol: str, timeframe: str, period: DateRange) -> tuple[Bar, ...]:
        bars = self.backtest_runner.repository.load(
            symbol,
            start=period.start,
            timeframe=timeframe,
            end_exclusive=period.end,
        )
        result = tuple(bar for bar in bars if period.contains(bar.timestamp))
        previous = None
        for bar in result:
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("Market data must be strictly chronological for reproducible research")
            previous = bar.timestamp
        return result

    @staticmethod
    def _fingerprint(symbol: str, timeframe: str, period: DateRange, bars: tuple[Bar, ...]) -> str:
        digest = sha256()
        header = json.dumps(json_value((symbol, timeframe, period)), sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
        digest.update(header.encode("utf-8"))
        for bar in bars:
            line = json.dumps(json_value(bar), sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest.update(b"\n")
            digest.update(line.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _candidate_fingerprint(parameters: tuple[ParameterSet, ...]) -> str:
        return sha256(json.dumps(json_value(parameters), sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _unique_configurations(
        configurations: Iterable[Mapping[str, ParameterValue] | ParameterSet],
    ) -> tuple[ParameterSet, ...]:
        normalized = tuple(ParameterSet.from_mapping(values) for values in configurations)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate parameter configurations are not allowed in one research batch")
        return normalized

    @staticmethod
    def _config_range(config: BacktestRunConfig) -> DateRange:
        if config.start is None or config.end is None:
            raise ValueError("Research backtests require explicit start and end boundaries")
        return DateRange(config.start, config.end)

    @staticmethod
    def _validate_factory(factory: StrategyFactory) -> None:
        if not isinstance(getattr(factory, "strategy_id", None), str) or not factory.strategy_id.strip():
            raise ValueError("Strategy factory must define a nonempty strategy_id")
        if not isinstance(getattr(factory, "strategy_version", None), str) or not factory.strategy_version.strip():
            raise ValueError("Strategy factory must define a nonempty strategy_version")
        if not callable(getattr(factory, "create", None)) or not callable(getattr(factory, "validate", None)):
            raise TypeError("Strategy factory must implement validate(parameters) and create(parameters)")

    @staticmethod
    def _metric(result: ResearchResult, path: str) -> float | None:
        analysis = result.analysis_result
        if analysis is None:
            return None
        value = analysis
        for part in path.split("."):
            value = getattr(value, part)
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            raise ValueError(f"Selection metric {path} is not a finite numeric value")
        return float(value)
