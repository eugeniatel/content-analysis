"""Profile-relative, objective-safe winner analytics."""

from .cohorts import (
    add_comparable_percentiles,
    content_format,
    matched_controls,
    publication_age_band,
    publication_age_days,
)
from .engine import analyze_profile_winners, analyze_winners
from .findings import (
    DEFAULT_CATEGORICAL_FIELDS,
    DEFAULT_CONTINUOUS_FIELDS,
    categorical_findings,
    continuous_findings,
    normalize_features,
)
from .objectives import objective_eligibility, rank_objective, relative_percentiles
from .statistics import (
    bootstrap_median_difference,
    bootstrap_median_ratio,
    bootstrap_prevalence_difference,
    confidence_label,
    median_difference,
    median_ratio,
    quantile,
)

__all__ = [
    "DEFAULT_CATEGORICAL_FIELDS",
    "DEFAULT_CONTINUOUS_FIELDS",
    "add_comparable_percentiles",
    "analyze_profile_winners",
    "analyze_winners",
    "bootstrap_median_difference",
    "bootstrap_median_ratio",
    "bootstrap_prevalence_difference",
    "categorical_findings",
    "confidence_label",
    "content_format",
    "continuous_findings",
    "matched_controls",
    "median_difference",
    "median_ratio",
    "normalize_features",
    "objective_eligibility",
    "publication_age_band",
    "publication_age_days",
    "quantile",
    "rank_objective",
    "relative_percentiles",
]

