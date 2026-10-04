from datetime import timedelta

import pytest

from app.research import DateRange, TrainTestSplit, WalkForwardConfig
from tests.validation.support import START


def test_half_open_range_includes_start_and_excludes_end_and_outside():
    interval = DateRange(START, START + timedelta(days=2))
    assert interval.contains(START)
    assert interval.contains(START + timedelta(days=1))
    assert not interval.contains(START - timedelta(microseconds=1))
    assert not interval.contains(START + timedelta(days=2))
    assert not interval.contains(START + timedelta(days=3))


def test_empty_reversed_and_overlapping_ranges_fail_but_touching_ranges_are_disjoint():
    with pytest.raises(ValueError):
        DateRange(START, START)
    with pytest.raises(ValueError):
        DateRange(START + timedelta(days=1), START)
    train = DateRange(START, START + timedelta(days=2))
    test = DateRange(START + timedelta(days=2), START + timedelta(days=4))
    assert TrainTestSplit(train, test).training.end == test.start
    with pytest.raises(ValueError):
        TrainTestSplit(train, DateRange(START + timedelta(days=1), START + timedelta(days=3)))


def test_walk_forward_boundaries_are_adjacent_half_open_and_disjoint():
    specification = WalkForwardConfig(
        DateRange(START, START + timedelta(days=10)),
        training_duration=timedelta(days=3),
        testing_duration=timedelta(days=2),
        step=timedelta(days=2),
        anchored=True,
    )
    splits = specification.splits()
    assert len(splits) == 3
    assert [(x.training.start, x.training.end, x.testing.start, x.testing.end) for x in splits] == [
        (START, START + timedelta(days=3), START + timedelta(days=3), START + timedelta(days=5)),
        (START, START + timedelta(days=5), START + timedelta(days=5), START + timedelta(days=7)),
        (START, START + timedelta(days=7), START + timedelta(days=7), START + timedelta(days=9)),
    ]
    for split in splits:
        assert split.training.end == split.testing.start
        assert split.training.end <= split.testing.start


def test_walk_forward_rolling_train_starts_advance_and_short_horizon_is_empty():
    base = DateRange(START, START + timedelta(days=9))
    rolling = WalkForwardConfig(base, timedelta(days=3), timedelta(days=2), timedelta(days=2), anchored=False)
    splits = rolling.splits()
    assert [item.training.start for item in splits] == [START, START + timedelta(days=2), START + timedelta(days=4)]
    too_short = WalkForwardConfig(DateRange(START, START + timedelta(days=4)),
                                  timedelta(days=3), timedelta(days=2), timedelta(days=2))
    assert too_short.splits() == ()
