from dataclasses import dataclass
from typing import Protocol, Sequence, overload

from app.backtest.models import Bar, Order, Position, Signal


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Completed-bar input plus a read-only snapshot of canonical engine state."""

    index: int
    history: Sequence[Bar]
    bar: Bar
    position: Position | None = None
    pending_order: Order | None = None


class BarHistory(Sequence[Bar]):
    """Read-only prefix view; avoids copying all prior bars for every callback."""

    def __init__(self, bars: tuple[Bar, ...], stop: int):
        self._bars = bars
        self._stop = stop

    def __len__(self) -> int:
        return self._stop

    @overload
    def __getitem__(self, index: int) -> Bar: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Bar, ...]: ...

    def __getitem__(self, index: int | slice) -> Bar | tuple[Bar, ...]:
        if isinstance(index, slice):
            return self._bars[:self._stop][index]
        if index < 0:
            index += self._stop
        if index < 0 or index >= self._stop:
            raise IndexError(index)
        return self._bars[index]


class Strategy(Protocol):
    """Return an instruction after observing this completed bar and its history.

    A strategy object is supplied directly to a backtest run. Implementations
    may keep state while that run is in progress, but the engine does not reset
    or clone strategy objects. Callers must create a fresh instance for every
    independent run. ResearchEngine follows this contract by calling its
    strategy factory separately for each candidate and phase run.
    """

    def on_bar(self, context: StrategyContext) -> Signal | None: ...
