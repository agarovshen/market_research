"""Portable comparison helpers for engine output and external reference exports.

The reference is data only; this module has no MT5 or terminal dependency.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


TRADE_FIELDS = (
    "side", "entry_timestamp", "entry_price", "exit_timestamp", "exit_price",
    "quantity", "commission", "fees", "slippage", "pnl",
)


@dataclass(frozen=True)
class Difference:
    location: str
    field: str
    ours: Any
    reference: Any
    delta: float | None

    def __str__(self) -> str:
        suffix = "" if self.delta is None else f" ({self.delta:+g})"
        return (f"{self.location}: {self.field}: ours={self.ours!r}, "
                f"reference={self.reference!r}{suffix}")


def first_difference(ours: Mapping[str, Any], reference: Mapping[str, Any], *,
                     absolute_tolerance: float = 0.0,
                     relative_tolerance: float = 0.0) -> Difference | None:
    """Return first mismatch in trade order, then equity, drawdown, statistics."""
    for section in ("trades", "equity", "drawdown", "statistics"):
        left, right = ours.get(section), reference.get(section)
        if isinstance(left, Sequence) and not isinstance(left, (str, bytes)):
            if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
                return Difference(section, "shape", len(left), type(right).__name__, None)
            if len(left) != len(right):
                return Difference(section, "count", len(left), len(right), None)
            for index, (a, b) in enumerate(zip(left, right), 1):
                for key in TRADE_FIELDS if section == "trades" else ("timestamp", "value"):
                    if key not in a or key not in b:
                        continue
                    mismatch = _difference(f"{section[:-1].capitalize()} #{index}", key, a[key], b[key],
                                           absolute_tolerance, relative_tolerance)
                    if mismatch:
                        return mismatch
        elif left != right:
            return Difference(section, "value", left, right, _delta(left, right))
    return None


def _delta(left, right):
    return left - right if isinstance(left, (int, float)) and isinstance(right, (int, float)) else None


def _difference(location, field, left, right, absolute, relative):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        delta = left - right
        if abs(delta) <= absolute + relative * abs(right):
            return None
    else:
        delta = None
        if left == right:
            return None
    return Difference(location, field, left, right, delta)
