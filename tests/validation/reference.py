"""Portable comparison helpers for engine output and external reference exports.

The reference is data only; this module has no MT5 or terminal dependency.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


TRADE_FIELDS = (
    "side", "entry_time", "entry_price", "exit_time", "exit_price",
    "quantity", "gross_pnl", "commission", "fees", "slippage", "net_pnl",
)
ORDER_FIELDS = ("action", "side", "signal_time", "time", "reference_price",
                "price", "quantity", "commission", "fees", "slippage")


@dataclass(frozen=True)
class Difference:
    location: str
    field: str
    ours: Any
    reference: Any
    delta: float | None
    context: tuple[tuple[str, Any, Any, str], ...] = ()

    def __str__(self) -> str:
        lines = [self.location]
        for field, actual, expected, status in self.context:
            if status == "equal":
                lines.append(f"  {field}: {actual!r} == {expected!r}")
            elif status == "different":
                lines.extend((f"  {field}: DIFFERENT",
                              f"    expected: {expected!r}",
                              f"    actual:   {actual!r}"))
                if self.delta is not None:
                    lines.append(f"    actual - expected: {self.delta:+g}")
            else:
                lines.append(f"  {field}: not comparable")
        if not self.context:
            suffix = "" if self.delta is None else f" ({self.delta:+g})"
            lines.append(f"  {self.field}: actual={self.ours!r}, expected={self.reference!r}{suffix}")
        return "\n".join(lines)


def first_difference(ours: Mapping[str, Any], reference: Mapping[str, Any], *,
                     absolute_tolerance: float = 0.0,
                     relative_tolerance: float = 0.0) -> Difference | None:
    """Return the first mismatch, with useful context and downstream fields masked."""
    for section in ("orders", "trades", "equity", "drawdown", "statistics"):
        left, right = ours.get(section), reference.get(section)
        if isinstance(left, Sequence) and not isinstance(left, (str, bytes)):
            if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
                return Difference(section, "shape", len(left), type(right).__name__, None)
            if len(left) != len(right):
                return Difference(section, "count", len(left), len(right), None)
            for index, (a, b) in enumerate(zip(left, right), 1):
                fields = (ORDER_FIELDS if section == "orders" else TRADE_FIELDS
                          if section == "trades" else tuple(dict.fromkeys((*a, *b))))
                context = []
                for key in fields:
                    if key not in a or key not in b:
                        continue
                    mismatch = _difference(f"{section[:-1].capitalize()} #{index}", key, a[key], b[key],
                                           absolute_tolerance, relative_tolerance)
                    if mismatch:
                        context.append((key, a[key], b[key], "different"))
                        context.extend((later, None, None, "unavailable") for later in fields[fields.index(key) + 1:]
                                       if later in a or later in b)
                        return Difference(mismatch.location, mismatch.field, mismatch.ours,
                                          mismatch.reference, mismatch.delta, tuple(context))
                    context.append((key, a[key], b[key], "equal"))
        elif isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in dict.fromkeys((*left, *right)):
                if key not in left or key not in right:
                    return Difference(section, key, left.get(key), right.get(key), None)
                mismatch = _difference(section, key, left[key], right[key],
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
