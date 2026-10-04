from dataclasses import replace
from datetime import timedelta

from app.analysis import AnalysisEngine
from app.backtest import BacktestRunConfig, BacktestRunner, Bar, OrderAction, Side, Signal
from app.research import (
    DateRange, ExperimentPhase, ExperimentStatus, ResearchEngine, SelectionRule,
    TrainTestSplit, WalkForwardConfig,
)
from app.research.serialization import decode_result, encode_result
from app.research.storage import ResearchResultRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from tests.validation.support import START


class QtyStrategy:
    def __init__(self, quantity):
        self.quantity = quantity

    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG, self.quantity)
        return None


class QtyFactory:
    strategy_id = "validation.qty"
    strategy_version = "1"

    def validate(self, params):
        if params["quantity"] < 1:
            raise ValueError("positive quantity required")

    def create(self, params):
        return QtyStrategy(params["quantity"])


class MemoryRepository:
    def __init__(self, values):
        self.values = tuple(values)
        self.calls = []

    def load(self, instrument, start=None, end=None, *, timeframe="M1", end_exclusive=None):
        self.calls.append((start, end_exclusive if end_exclusive is not None else end))
        upper = end_exclusive if end_exclusive is not None else end
        return tuple(bar for bar in self.values
                     if (start is None or bar.timestamp >= start)
                     and (upper is None or bar.timestamp < upper))


def sample_bars(train_last=130, test_values=(100, 90)):
    closes = (100, 110, train_last, *test_values)
    return tuple(Bar(START + timedelta(days=index), value, value, value, value)
                 for index, value in enumerate(closes))


def config():
    return BacktestRunConfig("EURUSD", start=START, end=START + timedelta(days=5), initial_cash=1_000)


def split():
    return TrainTestSplit(DateRange(START, START + timedelta(days=3)),
                          DateRange(START + timedelta(days=3), START + timedelta(days=5)))


def research(repository):
    return ResearchEngine(BacktestRunner(repository))


def test_oos_values_cannot_change_training_selection_or_training_fingerprint():
    kwargs = dict(config=config(), split=split(), strategy_factory=QtyFactory(),
                  selection=SelectionRule("total_return"))
    first_repo = MemoryRepository(sample_bars(test_values=(100, 90)))
    second_repo = MemoryRepository(sample_bars(test_values=(500, 900)))
    first = research(first_repo).run_out_of_sample(({"quantity": 1}, {"quantity": 2}), **kwargs)
    second = research(second_repo).run_out_of_sample(({"quantity": 1}, {"quantity": 2}), **kwargs)

    assert first.selected == second.selected
    assert first.training == second.training
    assert first.selected[0].parameters.as_dict()["quantity"] == 2
    # The only inputs changed were held-out bars; each engine loads train before test.
    assert first_repo.calls == [(START, START + timedelta(days=3)),
                                (START + timedelta(days=3), START + timedelta(days=5))]
    assert second_repo.calls == first_repo.calls
    assert first.testing != second.testing


def test_oos_empty_training_and_empty_test_are_structured_and_nonleaking():
    training_empty = research(MemoryRepository(sample_bars())).run_out_of_sample(
        ({"quantity": 1},), strategy_factory=QtyFactory(), config=config(),
        split=TrainTestSplit(DateRange(START + timedelta(days=10), START + timedelta(days=11)),
                             DateRange(START + timedelta(days=11), START + timedelta(days=12))),
        selection=SelectionRule("total_return"),
    )
    assert training_empty.selected == ()
    assert training_empty.training.failed_count == 1
    assert training_empty.testing.results == ()

    test_empty = research(MemoryRepository(sample_bars())).run_out_of_sample(
        ({"quantity": 1},), strategy_factory=QtyFactory(), config=config(),
        split=TrainTestSplit(DateRange(START, START + timedelta(days=3)),
                             DateRange(START + timedelta(days=10), START + timedelta(days=11))),
        selection=SelectionRule("total_return"),
    )
    assert test_empty.selected
    assert test_empty.testing.failed_count == 1
    assert test_empty.testing.results[0].definition.phase is ExperimentPhase.OOS


def test_research_results_repeat_exactly_and_round_trip_structured_fields():
    engine = research(MemoryRepository(sample_bars()))
    first = engine.run_batch(({"quantity": 2},), strategy_factory=QtyFactory(), config=config(), seed=55)
    repeated = engine.run_batch(({"quantity": 2},), strategy_factory=QtyFactory(), config=config(), seed=55)
    assert first == repeated
    result = first.results[0]
    assert result.status is ExperimentStatus.COMPLETED
    assert decode_result(encode_result(result)) == result

    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as session:
        store = ResearchResultRepository(session)
        store.save(result)
        session.commit()
        assert store.get(result.definition.experiment_id) == result
    db_engine.dispose()


def test_walk_forward_windows_have_no_future_train_data_and_are_repeatable():
    specification = WalkForwardConfig(DateRange(START, START + timedelta(days=9)),
                                      timedelta(days=3), timedelta(days=2), timedelta(days=2),
                                      anchored=False)
    repository = MemoryRepository(sample_bars(test_values=(120, 130, 140, 150, 160, 170)))
    output = research(repository).run_walk_forward(
        ({"quantity": 1},), strategy_factory=QtyFactory(), config=config(),
        walk_forward=specification, selection=SelectionRule("total_return"),
    )
    assert len(output.windows) == 3
    for window in output.windows:
        assert window.split.training.end == window.split.testing.start
        assert window.result.training.results[0].definition.period == window.split.training
        assert window.result.testing.results[0].definition.period == window.split.testing
    again = research(MemoryRepository(sample_bars(test_values=(120, 130, 140, 150, 160, 170)))).run_walk_forward(
        ({"quantity": 1},), strategy_factory=QtyFactory(), config=config(),
        walk_forward=specification, selection=SelectionRule("total_return"),
    )
    assert output == again
