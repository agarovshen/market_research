"""Deterministic, serializable parameter specifications and generators."""

from dataclasses import dataclass
from itertools import product
from math import isclose, isfinite
from random import Random
from typing import Iterator, Mapping, TypeAlias

ParameterValue: TypeAlias = str | int | float | bool | None


def _check_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and isfinite(value):
        return
    raise ValueError("Parameter values must be finite JSON scalar values")


@dataclass(frozen=True, slots=True)
class ParameterSet:
    """Immutable parameter mapping stored as name-sorted scalar pairs."""

    values: tuple[tuple[str, ParameterValue], ...]

    def __post_init__(self) -> None:
        pairs = tuple(tuple(pair) for pair in self.values)
        if any(len(pair) != 2 for pair in pairs):
            raise ValueError("Parameters must be name/value pairs")
        if any(not isinstance(name, str) or not name.strip() for name, _ in pairs):
            raise ValueError("Parameter names must be nonempty strings")
        normalized = tuple(sorted(pairs, key=lambda pair: pair[0]))
        names: set[str] = set()
        for name, value in normalized:
            if name in names:
                raise ValueError("Parameter names must be nonempty and unique")
            _check_value(value)
            names.add(name)
        object.__setattr__(self, "values", normalized)

    @classmethod
    def from_mapping(cls, values: Mapping[str, ParameterValue] | "ParameterSet") -> "ParameterSet":
        if isinstance(values, cls):
            return values
        if not isinstance(values, Mapping):
            raise TypeError("parameters must be a mapping or ParameterSet")
        return cls(tuple(values.items()))

    def as_dict(self) -> dict[str, ParameterValue]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class FixedParameter:
    name: str
    value: ParameterValue

    def __post_init__(self) -> None:
        _check_value(self.value)
        if not self.name.strip():
            raise ValueError("Parameter name cannot be empty")
        object.__setattr__(self, "name", self.name.strip())

    def grid_values(self) -> tuple[ParameterValue, ...]:
        return (self.value,)

    def sample(self, rng: Random) -> ParameterValue:
        return self.value

    @property
    def finite_cardinality(self) -> int:
        return 1


@dataclass(frozen=True, slots=True)
class IntegerRange:
    name: str
    minimum: int
    maximum: int
    step: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip() or any(isinstance(x, bool) or not isinstance(x, int)
                                        for x in (self.minimum, self.maximum, self.step)):
            raise ValueError("Integer range requires a name and integer bounds/step")
        if self.step <= 0 or self.maximum < self.minimum:
            raise ValueError("Integer range requires maximum >= minimum and positive step")
        object.__setattr__(self, "name", self.name.strip())

    def grid_values(self) -> tuple[int, ...]:
        return tuple(range(self.minimum, self.maximum + 1, self.step))

    def sample(self, rng: Random) -> int:
        count = (self.maximum - self.minimum) // self.step
        return self.minimum + self.step * rng.randrange(count + 1)

    @property
    def finite_cardinality(self) -> int:
        return len(self.grid_values())


@dataclass(frozen=True, slots=True)
class FloatRange:
    name: str
    minimum: float
    maximum: float
    grid_step: float | None = None

    def __post_init__(self) -> None:
        values = (self.minimum, self.maximum)
        if (not self.name.strip() or any(isinstance(x, bool) for x in values)
                or not all(isfinite(x) for x in values)
                or (self.grid_step is not None and (not isfinite(self.grid_step) or self.grid_step <= 0))):
            raise ValueError("Float range requires finite bounds and a positive optional grid step")
        if not isfinite(self.maximum - self.minimum):
            raise ValueError("Float range span must be finite")
        if self.maximum < self.minimum:
            raise ValueError("Float range maximum must be >= minimum")
        if self.grid_step is not None:
            steps = (self.maximum - self.minimum) / self.grid_step
            if not isclose(steps, round(steps), rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("Float range span must be divisible by grid_step")
        object.__setattr__(self, "name", self.name.strip())

    def grid_values(self) -> tuple[float, ...]:
        if self.grid_step is None:
            if self.minimum == self.maximum:
                return (self.minimum,)
            raise ValueError(f"Float parameter {self.name!r} needs grid_step for grid generation")
        count = round((self.maximum - self.minimum) / self.grid_step)
        return tuple(self.maximum if i == count else self.minimum + i * self.grid_step
                     for i in range(count + 1))

    def sample(self, rng: Random) -> float:
        return rng.uniform(self.minimum, self.maximum)

    @property
    def finite_cardinality(self) -> int | None:
        return 1 if self.minimum == self.maximum else None


@dataclass(frozen=True, slots=True)
class ChoiceParameter:
    name: str
    choices: tuple[ParameterValue, ...]

    def __post_init__(self) -> None:
        choices = tuple(self.choices)
        if not self.name.strip() or not choices:
            raise ValueError("Choice parameter requires a name and at least one choice")
        for choice in choices:
            _check_value(choice)
        if len(set(choices)) != len(choices):
            raise ValueError("Parameter choices must be unique")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "choices", choices)

    def grid_values(self) -> tuple[ParameterValue, ...]:
        return self.choices

    def sample(self, rng: Random) -> ParameterValue:
        return rng.choice(self.choices)

    @property
    def finite_cardinality(self) -> int:
        return len(self.choices)


ParameterSpec: TypeAlias = FixedParameter | IntegerRange | FloatRange | ChoiceParameter


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    parameters: tuple[ParameterSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", tuple(self.parameters))
        names = [parameter.name for parameter in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("Parameter specification names must be unique")

    def grid(self) -> Iterator[ParameterSet]:
        """Yield a Cartesian product in specification order, lazily."""
        value_sets = tuple(parameter.grid_values() for parameter in self.parameters)
        names = tuple(parameter.name for parameter in self.parameters)
        for values in product(*value_sets):
            yield ParameterSet(tuple(zip(names, values)))

    def validate(self, values: ParameterSet) -> None:
        """Check that a candidate is described by this parameter specification."""
        candidate = values.as_dict()
        if set(candidate) != {parameter.name for parameter in self.parameters}:
            raise ValueError("Candidate parameter names do not match the parameter space")
        for spec in self.parameters:
            value = candidate[spec.name]
            if isinstance(spec, FixedParameter):
                if type(value) is not type(spec.value) or value != spec.value:
                    raise ValueError(f"{spec.name} must equal its fixed value")
            elif isinstance(spec, IntegerRange):
                if (isinstance(value, bool) or not isinstance(value, int)
                        or not spec.minimum <= value <= spec.maximum
                        or (value - spec.minimum) % spec.step):
                    raise ValueError(f"{spec.name} is outside its integer range")
            elif isinstance(spec, FloatRange):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not isfinite(value) or not spec.minimum <= value <= spec.maximum):
                    raise ValueError(f"{spec.name} is outside its floating-point range")
            elif not any(type(value) is type(choice) and value == choice for choice in spec.choices):
                raise ValueError(f"{spec.name} is not one of its discrete choices")

    def random(self, count: int, *, seed: int) -> Iterator[ParameterSet]:
        """Yield uniform samples from a private seeded RNG; global RNG is untouched."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("count must be a nonnegative integer")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        cardinalities = tuple(parameter.finite_cardinality for parameter in self.parameters)
        if all(cardinality is not None for cardinality in cardinalities):
            capacity = 1
            for cardinality in cardinalities:
                capacity *= cardinality
            if count > capacity:
                raise ValueError("Could not generate the requested number of unique configurations")
        rng = Random(seed)
        seen: set[ParameterSet] = set()
        attempts = 0
        max_attempts = max(100, count * 100)
        while len(seen) < count:
            candidate = ParameterSet(tuple((parameter.name, parameter.sample(rng))
                                           for parameter in self.parameters))
            attempts += 1
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
            elif attempts >= max_attempts:
                raise ValueError("Could not generate the requested number of unique configurations")
