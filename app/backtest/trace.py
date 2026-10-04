"""Trace comparison helpers for diagnostics; they do not execute or account."""

from dataclasses import dataclass

from app.backtest.models import ExecutionEvent


@dataclass(frozen=True, slots=True)
class EventDifference:
    event_number: int
    field: str
    expected: object
    actual: object

    def __str__(self) -> str:
        return (f"FIRST DIVERGENCE\nevent #{self.event_number}\nfield: {self.field}\n"
                f"expected: {self.expected!r}\nactual: {self.actual!r}")


_EVENT_FIELDS = (
    "sequence", "timestamp", "event_type", "bar_index", "action", "side", "order_id",
    "trade_id", "price", "quantity", "trigger_price", "stop_loss",
    "order_type", "reason", "details",
)


def first_event_difference(expected: tuple[ExecutionEvent, ...],
                           actual: tuple[ExecutionEvent, ...], *,
                           absolute_tolerance: float = 0.0) -> EventDifference | None:
    """Find the first event/field mismatch in two ordered traces."""
    common = min(len(expected), len(actual))
    for index in range(common):
        left, right = expected[index], actual[index]
        for field in _EVENT_FIELDS:
            wanted, got = getattr(left, field), getattr(right, field)
            if isinstance(wanted, (int, float)) and isinstance(got, (int, float)):
                equal = abs(wanted - got) <= absolute_tolerance
            else:
                equal = wanted == got
            if not equal:
                return EventDifference(index + 1, field, wanted, got)
    if len(expected) != len(actual):
        return EventDifference(common + 1, "event", expected[common] if common < len(expected) else None,
                               actual[common] if common < len(actual) else None)
    return None
