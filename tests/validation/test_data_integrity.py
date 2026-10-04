from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from app.backtest import BacktestEngine
from tests.validation.support import START, bars


class NoSignals:
    def on_bar(self, context):
        return None


def test_empty_single_bar_and_missing_time_intervals_are_supported():
    engine = BacktestEngine()
    assert engine.run((), NoSignals()).equity_curve == ()
    assert len(engine.run(bars(10), NoSignals()).equity_curve) == 1
    # The engine checks ordering, not exchange-calendar continuity.
    gap_data = (bars(10)[0], replace(bars(11)[0], timestamp=START + timedelta(days=4)))
    assert len(engine.run(gap_data, NoSignals()).equity_curve) == 2


@pytest.mark.parametrize("mutate", [
    lambda data: tuple(reversed(data)),
    lambda data: (data[0], data[0]),
    lambda data: (replace(data[0], high=float("nan")),),
    lambda data: (replace(data[0], high=9.0),),
    lambda data: (replace(data[0], spread=-1.0),),
])
def test_unordered_duplicate_nonfinite_invalid_ohlc_and_negative_spread_fail(mutate):
    data = mutate(bars(10, 11))
    with pytest.raises(ValueError):
        BacktestEngine().run(data, NoSignals())


def test_mixed_naive_and_aware_timestamps_are_rejected_by_order_comparison():
    data = (bars(10)[0], replace(bars(11)[0], timestamp=(START + timedelta(minutes=1)).replace(tzinfo=timezone.utc)))
    with pytest.raises(TypeError):
        BacktestEngine().run(data, NoSignals())


def test_consistently_aware_timestamps_are_accepted():
    aware = tuple(replace(bar, timestamp=bar.timestamp.replace(tzinfo=timezone.utc)) for bar in bars(10, 11))
    assert len(BacktestEngine().run(aware, NoSignals()).equity_curve) == 2
