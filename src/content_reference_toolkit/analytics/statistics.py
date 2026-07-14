"""Small, deterministic statistics helpers for profile winner analysis."""

from __future__ import annotations

import math
import random
from statistics import median
from typing import Any, Callable, Sequence


def finite_values(values: Sequence[float | int]) -> list[float]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def quantile(values: Sequence[float | int], probability: float) -> float:
    """Return a linearly interpolated quantile using the inclusive endpoints."""

    if not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    ordered = sorted(finite_values(values))
    if not ordered:
        raise ValueError("quantile requires at least one finite value")
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _percentile_from_sorted(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    if len(values) == 1:
        return values[0]
    position = probability * (len(values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def median_ratio(feature_values: Sequence[float], control_values: Sequence[float]) -> float | None:
    feature = finite_values(feature_values)
    control = finite_values(control_values)
    if not feature or not control:
        return None
    control_median = median(control)
    if control_median == 0:
        return None
    return median(feature) / control_median


def median_difference(
    feature_values: Sequence[float], control_values: Sequence[float]
) -> float | None:
    feature = finite_values(feature_values)
    control = finite_values(control_values)
    if not feature or not control:
        return None
    return median(feature) - median(control)


def _bootstrap_two_groups(
    feature_values: Sequence[float | int],
    control_values: Sequence[float | int],
    *,
    statistic: Callable[[Sequence[float], Sequence[float]], float | None],
    statistic_name: str,
    neutral: float,
    iterations: int,
    seed: int,
    confidence_level: float = 0.95,
) -> dict[str, Any] | None:
    feature = finite_values(feature_values)
    control = finite_values(control_values)
    if not feature or not control:
        return None
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between 0 and 1")

    estimate = statistic(feature, control)
    if estimate is None or not math.isfinite(estimate):
        return None

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        feature_sample = [feature[rng.randrange(len(feature))] for _ in feature]
        control_sample = [control[rng.randrange(len(control))] for _ in control]
        value = statistic(feature_sample, control_sample)
        if value is not None and math.isfinite(value):
            samples.append(value)
    if not samples:
        return None

    samples.sort()
    tail = (1 - confidence_level) / 2
    low = _percentile_from_sorted(samples, tail)
    high = _percentile_from_sorted(samples, 1 - tail)
    if estimate > neutral:
        directional = sum(value > neutral for value in samples) / len(samples)
    elif estimate < neutral:
        directional = sum(value < neutral for value in samples) / len(samples)
    else:
        directional = 0.5

    return {
        "statistic": statistic_name,
        "estimate": estimate,
        "low": low,
        "high": high,
        "neutral": neutral,
        "confidence_level": confidence_level,
        "iterations": iterations,
        "valid_iterations": len(samples),
        "seed": seed,
        "directional_stability": directional,
    }


def bootstrap_median_ratio(
    feature_values: Sequence[float | int],
    control_values: Sequence[float | int],
    *,
    iterations: int = 1_000,
    seed: int = 0,
) -> dict[str, Any] | None:
    """Bootstrap the ratio of two medians (neutral value: ``1``)."""

    return _bootstrap_two_groups(
        feature_values,
        control_values,
        statistic=median_ratio,
        statistic_name="median_ratio",
        neutral=1.0,
        iterations=iterations,
        seed=seed,
    )


def bootstrap_median_difference(
    feature_values: Sequence[float | int],
    control_values: Sequence[float | int],
    *,
    iterations: int = 1_000,
    seed: int = 0,
) -> dict[str, Any] | None:
    """Bootstrap a difference of medians (neutral value: ``0``)."""

    return _bootstrap_two_groups(
        feature_values,
        control_values,
        statistic=median_difference,
        statistic_name="median_difference",
        neutral=0.0,
        iterations=iterations,
        seed=seed,
    )


def bootstrap_prevalence_difference(
    feature_flags: Sequence[bool | int],
    control_flags: Sequence[bool | int],
    *,
    iterations: int = 1_000,
    seed: int = 0,
) -> dict[str, Any] | None:
    """Bootstrap the prevalence difference between two cohorts."""

    return _bootstrap_two_groups(
        [1.0 if bool(value) else 0.0 for value in feature_flags],
        [1.0 if bool(value) else 0.0 for value in control_flags],
        statistic=lambda left, right: (sum(left) / len(left)) - (sum(right) / len(right)),
        statistic_name="prevalence_difference",
        neutral=0.0,
        iterations=iterations,
        seed=seed,
    )


def confidence_label(
    *,
    sample_feature: int,
    sample_control: int,
    interval: dict[str, Any] | None,
    minimum_sample: int = 5,
) -> str:
    """Apply the evidence-strength policy from the profile intelligence plan."""

    minimum_group = min(sample_feature, sample_control)
    if minimum_group < minimum_sample or interval is None:
        return "insufficient"

    low = float(interval["low"])
    high = float(interval["high"])
    neutral = float(interval["neutral"])
    excludes_neutral = low > neutral or high < neutral
    stability = float(interval.get("directional_stability", 0.0))

    if minimum_group >= 15 and excludes_neutral:
        return "high"
    if minimum_group >= 8 and stability >= 0.8:
        return "medium"
    return "low"

