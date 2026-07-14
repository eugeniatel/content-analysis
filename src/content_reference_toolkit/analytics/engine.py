"""Pure orchestration for objective-safe profile winner analytics."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .cohorts import add_comparable_percentiles, content_format
from .findings import (
    DEFAULT_CATEGORICAL_FIELDS,
    DEFAULT_CONTINUOUS_FIELDS,
    categorical_findings,
    continuous_findings,
)
from .objectives import objective_eligibility, rank_objective


def _scope_values(pieces: Sequence[Mapping[str, Any]], key: str) -> set[str]:
    return {
        str(row[key]).strip().casefold()
        for row in pieces
        if row.get(key) is not None and str(row[key]).strip()
    }


def _validate_profile_scope(pieces: Sequence[Mapping[str, Any]]) -> None:
    profile_ids = _scope_values(pieces, "profile_id")
    platforms = _scope_values(pieces, "platform")
    if len(profile_ids) > 1:
        raise ValueError("winner analysis must contain exactly one profile")
    if len(platforms) > 1:
        raise ValueError("winner analysis must contain exactly one platform")


def _format_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    counts = Counter(content_format(row) for row in rows)
    total = len(rows)
    return {
        name: {"count": count, "prevalence": count / total if total else 0.0}
        for name, count in sorted(counts.items())
    }


def _coverage_finding(objective: str, coverage: Mapping[str, Any]) -> dict[str, Any]:
    eligible = int(coverage["eligible_pieces"])
    total = int(coverage["total_pieces"])
    identity = {
        "dimension": "objective_coverage",
        "feature": objective,
        "objective": objective,
        "comparison": "eligible objective observations vs inventoried pieces",
    }
    finding_id = "finding-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "id": finding_id,
        **identity,
        "claim": (
            f"There is insufficient evidence to rank posts for {objective}: "
            f"{eligible} of {total} pieces have a comparable eligible observation."
        ),
        "effect": {
            "value": coverage["objective_coverage"],
            "unit": "eligible_fraction",
        },
        "sample": {"feature": eligible, "control": max(0, total - eligible)},
        "confidence": "insufficient",
        "status": "insufficient_evidence",
        "interval": None,
        "evidence": [],
        "evidence_refs": [],
        "caveat": "No cross-unit or cross-objective fallback was used",
    }


def analyze_profile_winners(
    pieces: Sequence[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
    *,
    objective: str,
    features: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    top_k: int = 10,
    metric_basis: str | None = None,
    metric_unit: str | None = None,
    categorical_fields: Sequence[str] = DEFAULT_CATEGORICAL_FIELDS,
    continuous_fields: Sequence[str] = DEFAULT_CONTINUOUS_FIELDS,
    continuous_band_count: int = 3,
    minimum_sample: int = 5,
    bootstrap_iterations: int = 1_000,
    seed: int = 0,
) -> dict[str, Any]:
    """Return a canonical, report-ready winner analysis object.

    Inputs are plain dictionaries, making the function independent from SQLite and
    connector details.  The returned ``ranked`` rows always retain raw objective
    values as well as corpus and comparable-window relative percentiles.
    """

    _validate_profile_scope(pieces)
    eligibility = objective_eligibility(
        pieces,
        observations,
        objective=objective,
        metric_basis=metric_basis,
        metric_unit=metric_unit,
    )
    comparable_rows = add_comparable_percentiles(eligibility["eligible"])
    cohorts = rank_objective(comparable_rows, top_k=top_k)
    coverage = eligibility["coverage"]

    if not cohorts["ranked"]:
        findings = [_coverage_finding(str(objective).casefold(), coverage)]
    else:
        categorical = categorical_findings(
            cohorts["ranked"],
            cohorts["winners"],
            objective=str(objective).casefold(),
            features=features,
            fields=categorical_fields,
            minimum_sample=minimum_sample,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed,
        )
        continuous = continuous_findings(
            cohorts["ranked"],
            cohorts["winners"],
            objective=str(objective).casefold(),
            features=features,
            fields=continuous_fields,
            band_count=continuous_band_count,
            minimum_sample=minimum_sample,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed,
        )
        findings = [*categorical, *continuous]

    scope = {
        "profile_id": next(iter(_scope_values(pieces, "profile_id")), None),
        "platform": next(iter(_scope_values(pieces, "platform")), None),
    }
    analysis_key = {
        **scope,
        "objective": str(objective).casefold(),
        "metric_basis": coverage["selected_metric_basis"],
        "metric_unit": coverage["selected_metric_unit"],
        "eligible_piece_ids": sorted(row["piece_id"] for row in cohorts["ranked"]),
        "top_k": top_k,
        "seed": seed,
    }
    analysis_id = "analysis-" + hashlib.sha256(
        json.dumps(analysis_key, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    insufficient = sum(item["status"] == "insufficient_evidence" for item in findings)
    return {
        "analysis_id": analysis_id,
        "scope": scope,
        "objective": {
            "name": str(objective).casefold(),
            "metric_basis": coverage["selected_metric_basis"],
            "metric_unit": coverage["selected_metric_unit"],
        },
        "coverage": coverage,
        "cohorts": {
            "winner_count": len(cohorts["winners"]),
            "eligible_count": len(cohorts["ranked"]),
            "non_winner_count": len(cohorts["non_winners"]),
            "winner_piece_ids": [row["piece_id"] for row in cohorts["winners"]],
            "non_winner_piece_ids": [row["piece_id"] for row in cohorts["non_winners"]],
        },
        "format_distribution": {
            "winners": _format_distribution(cohorts["winners"]),
            "eligible_corpus": _format_distribution(cohorts["ranked"]),
        },
        "ranked": cohorts["ranked"],
        "winners": cohorts["winners"],
        "findings": findings,
        "finding_summary": {
            "total": len(findings),
            "supported": len(findings) - insufficient,
            "insufficient_evidence": insufficient,
        },
        "method": {
            "ranking": "exact selected objective and metric signature",
            "baseline": "eligible profile corpus",
            "controls": "topic/format/publication-age where supported, with disclosed fallback",
            "continuous_bands": "profile-derived quantiles",
            "confidence": "deterministic bootstrap evidence-strength labels",
            "bootstrap_iterations": bootstrap_iterations,
            "seed": seed,
            "association_only": True,
        },
    }


# Friendly short alias for callers that already operate at profile scope.
analyze_winners = analyze_profile_winners

