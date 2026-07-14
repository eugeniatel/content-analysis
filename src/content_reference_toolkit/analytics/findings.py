"""Canonical categorical and continuous findings.

All generated claims use associative wording.  A weak comparison is retained as an
``insufficient_evidence`` finding instead of disappearing from the report.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .cohorts import content_format, matched_controls
from .statistics import (
    bootstrap_median_ratio,
    bootstrap_prevalence_difference,
    confidence_label,
    quantile,
)


DEFAULT_CATEGORICAL_FIELDS = (
    "format",
    "topics",
    "content_intent",
    "hook_archetype",
    "narrative_structure",
    "carousel_progression",
    "cta_type",
    "tone_dimensions",
)

DEFAULT_CONTINUOUS_FIELDS = (
    "duration_sec",
    "caption_word_count",
    "transcript_word_count",
    "slide_count",
    "words_per_slide_median",
    "text_density",
    "words_per_minute",
    "pause_count_300ms",
    "pause_count_600ms",
    "pause_count_1000ms",
)


def _seed_for(seed: int, *parts: Any) -> int:
    payload = "\x1f".join([str(seed), *(str(part) for part in parts)])
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _stable_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "finding-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{\"":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def normalize_features(
    features: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Normalize either wide feature dicts or long ``piece_features`` rows."""

    if features is None:
        return {}
    if isinstance(features, Mapping):
        result: dict[str, dict[str, Any]] = {}
        for identifier, values in features.items():
            if isinstance(values, Mapping):
                result[str(identifier)] = dict(values)
        return result

    result: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in features:
        identifier = row.get("piece_id", row.get("content_id"))
        if identifier is None:
            continue
        identifier_text = str(identifier)
        feature_name = row.get("feature_name", row.get("name"))
        if feature_name:
            value = None
            for key in ("numeric_value", "text_value", "json_value", "value"):
                if key in row and row[key] is not None:
                    value = _decode_json(row[key])
                    break
            namespace = str(row.get("namespace", "")).strip()
            name = str(feature_name)
            result[identifier_text][name] = value
            if namespace:
                result[identifier_text][f"{namespace}.{name}"] = value
        else:
            result[identifier_text].update(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"piece_id", "content_id", "id"}
                }
            )
    return dict(result)


def _row_value(
    row: Mapping[str, Any], feature_index: Mapping[str, Mapping[str, Any]], field: str
) -> Any:
    if field == "format":
        return content_format(row)
    identifier = str(row["piece_id"])
    piece = row.get("piece") if isinstance(row.get("piece"), Mapping) else {}
    if field in feature_index.get(identifier, {}):
        return feature_index[identifier][field]
    if field in piece:
        return piece[field]
    if field in row:
        return row[field]
    return None


def _categories(value: Any) -> list[str]:
    value = _decode_json(value)
    if value is None:
        return []
    if isinstance(value, Mapping):
        categories = []
        for key, item in value.items():
            if isinstance(item, bool):
                if item:
                    categories.append(str(key).strip().casefold())
            elif item is not None:
                categories.append(f"{str(key).strip().casefold()}={str(item).strip().casefold()}")
        return sorted(category for category in categories if category)
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted(
            {
                str(item).strip().casefold()
                for item in value
                if item is not None and str(item).strip()
            }
        )
    text = str(value).strip().casefold()
    return [text] if text else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _evidence_refs(rows: Sequence[Mapping[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (-float(row["value"]), str(row["piece_id"])))
    return [
        {
            "piece_id": str(row["piece_id"]),
            "observation_ref": row.get("observation_ref"),
            "objective_value": float(row["value"]),
            "relative_percentile": row.get("relative_percentile"),
            "comparable_percentile": row.get("comparable_percentile"),
        }
        for row in ordered[:limit]
    ]


def _canonical_finding(
    *,
    dimension: str,
    feature: str,
    claim: str,
    objective: str,
    comparison: str,
    effect: Mapping[str, Any],
    sample: Mapping[str, int],
    confidence: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    interval: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = "insufficient_evidence" if confidence == "insufficient" else "supported"
    identity = {
        "dimension": dimension,
        "feature": feature,
        "objective": objective,
        "comparison": comparison,
    }
    evidence_refs = _evidence_refs(evidence_rows)
    finding = {
        "id": _stable_id(identity),
        **identity,
        "claim": claim,
        "effect": dict(effect),
        "sample": dict(sample),
        "confidence": confidence,
        "status": status,
        "interval": dict(interval) if interval else None,
        "evidence": [row["piece_id"] for row in evidence_refs],
        "evidence_refs": evidence_refs,
        "caveat": "Association within this profile; not a causal estimate",
    }
    if extra:
        finding.update(extra)
    return finding


def categorical_findings(
    eligible: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    *,
    objective: str,
    features: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    fields: Sequence[str] = DEFAULT_CATEGORICAL_FIELDS,
    minimum_sample: int = 5,
    bootstrap_iterations: int = 1_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Compare winner prevalence with the eligible-corpus prevalence."""

    feature_index = normalize_features(features)
    winner_ids = {str(row["piece_id"]) for row in winners}
    findings: list[dict[str, Any]] = []

    for field in fields:
        categories_by_piece = {
            str(row["piece_id"]): set(_categories(_row_value(row, feature_index, field)))
            for row in eligible
        }
        all_categories = sorted(set().union(*categories_by_piece.values())) if categories_by_piece else []
        for category in all_categories:
            category_rows = [
                row
                for row in eligible
                if category in categories_by_piece[str(row["piece_id"])]
            ]
            control_rows = [
                row
                for row in eligible
                if category not in categories_by_piece[str(row["piece_id"])]
            ]
            winner_category_rows = [
                row for row in category_rows if str(row["piece_id"]) in winner_ids
            ]
            winner_flags = [
                category in categories_by_piece[str(row["piece_id"])] for row in winners
            ]
            corpus_flags = [
                category in categories_by_piece[str(row["piece_id"])] for row in eligible
            ]
            winner_prevalence = sum(winner_flags) / len(winner_flags) if winner_flags else 0.0
            baseline_prevalence = sum(corpus_flags) / len(corpus_flags) if corpus_flags else 0.0
            enrichment = (
                winner_prevalence / baseline_prevalence if baseline_prevalence > 0 else None
            )

            interval_seed = _seed_for(seed, "categorical", field, category)
            prevalence_interval = bootstrap_prevalence_difference(
                winner_flags,
                corpus_flags,
                iterations=bootstrap_iterations,
                seed=interval_seed,
            )
            confidence = confidence_label(
                sample_feature=len(category_rows),
                sample_control=len(control_rows),
                interval=prevalence_interval,
                minimum_sample=minimum_sample,
            )

            controls = matched_controls(
                category_rows,
                control_rows,
                minimum_sample=minimum_sample,
            )
            selected_controls = controls["rows"]
            lift_interval = bootstrap_median_ratio(
                [float(row["value"]) for row in category_rows],
                [float(row["value"]) for row in selected_controls],
                iterations=bootstrap_iterations,
                seed=_seed_for(seed, "categorical-lift", field, category),
            )
            metric_lift = lift_interval["estimate"] if lift_interval else None

            if confidence == "insufficient":
                claim = (
                    f"There is insufficient evidence to associate {field}={category} "
                    f"with stronger {objective} performance."
                )
            elif enrichment is not None:
                claim = (
                    f"{field}={category} is associated with {enrichment:.2f}x winner "
                    f"enrichment for {objective} within this profile."
                )
            else:
                claim = (
                    f"{field}={category} has no estimable winner enrichment for {objective}."
                )

            findings.append(
                _canonical_finding(
                    dimension="categorical",
                    feature=f"{field}={category}",
                    claim=claim,
                    objective=objective,
                    comparison=f"top {len(winners)} winners vs eligible profile corpus",
                    effect={"value": enrichment, "unit": "winner_enrichment_ratio"},
                    sample={
                        "winner_cohort": len(winners),
                        "eligible_corpus": len(eligible),
                        "feature": len(category_rows),
                        "control": len(control_rows),
                    },
                    confidence=confidence,
                    evidence_rows=winner_category_rows or category_rows,
                    interval=prevalence_interval,
                    extra={
                        "winner_count": len(winner_category_rows),
                        "winner_prevalence": winner_prevalence,
                        "baseline_count": len(category_rows),
                        "baseline_prevalence": baseline_prevalence,
                        "metric_lift": (
                            {"value": metric_lift, "unit": "median_ratio"}
                            if metric_lift is not None
                            else None
                        ),
                        "metric_lift_interval": lift_interval,
                        "control_match": controls["method"],
                        "raw_values": {
                            "feature_objective": [float(row["value"]) for row in category_rows],
                            "control_objective": [
                                float(row["value"]) for row in selected_controls
                            ],
                        },
                        "relative_percentiles": {
                            "feature": [row.get("relative_percentile") for row in category_rows],
                            "control": [
                                row.get("relative_percentile") for row in selected_controls
                            ],
                        },
                    },
                )
            )

    return findings


def _band_definitions(values: Sequence[float], band_count: int) -> list[tuple[float, float]]:
    if band_count < 2:
        raise ValueError("band_count must be at least 2")
    unique = sorted(set(values))
    if not unique:
        return []
    if len(unique) == 1:
        return [(unique[0], unique[0])]

    cuts = sorted(
        {
            quantile(values, index / band_count)
            for index in range(1, band_count)
        }
    )
    boundaries = [min(values), *cuts, max(values)]
    bands: list[tuple[float, float]] = []
    for low, high in zip(boundaries, boundaries[1:]):
        if bands and low == high == bands[-1][1]:
            continue
        bands.append((low, high))
    return bands or [(min(values), max(values))]


def _in_band(value: float, band: tuple[float, float], index: int, total: int) -> bool:
    low, high = band
    if total == 1:
        return low <= value <= high
    if index == 0:
        return low <= value <= high
    return low < value <= high


def continuous_findings(
    eligible: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    *,
    objective: str,
    features: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    fields: Sequence[str] = DEFAULT_CONTINUOUS_FIELDS,
    band_count: int = 3,
    minimum_sample: int = 5,
    bootstrap_iterations: int = 1_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Compare data-supported quantile bands with matched controls."""

    feature_index = normalize_features(features)
    winner_ids = {str(row["piece_id"]) for row in winners}
    findings: list[dict[str, Any]] = []

    for field in fields:
        values_by_piece: dict[str, float] = {}
        analyzable_rows: list[Mapping[str, Any]] = []
        for row in eligible:
            value = _number(_row_value(row, feature_index, field))
            if value is None:
                continue
            values_by_piece[str(row["piece_id"])] = value
            analyzable_rows.append(row)
        if not analyzable_rows:
            continue

        values = list(values_by_piece.values())
        bands = _band_definitions(values, band_count)
        for band_index, band in enumerate(bands):
            feature_rows = [
                row
                for row in analyzable_rows
                if _in_band(
                    values_by_piece[str(row["piece_id"])],
                    band,
                    band_index,
                    len(bands),
                )
            ]
            feature_ids = {str(row["piece_id"]) for row in feature_rows}
            outside_rows = [
                row for row in analyzable_rows if str(row["piece_id"]) not in feature_ids
            ]
            controls = matched_controls(
                feature_rows,
                outside_rows,
                minimum_sample=minimum_sample,
            )
            control_rows = controls["rows"]
            interval = bootstrap_median_ratio(
                [float(row["value"]) for row in feature_rows],
                [float(row["value"]) for row in control_rows],
                iterations=bootstrap_iterations,
                seed=_seed_for(seed, "continuous", field, band_index, *band),
            )
            confidence = confidence_label(
                sample_feature=len(feature_rows),
                sample_control=len(control_rows),
                interval=interval,
                minimum_sample=minimum_sample,
            )
            ratio = interval["estimate"] if interval else None
            label = f"{band[0]:g}-{band[1]:g}"

            winner_feature_rows = [
                row for row in feature_rows if str(row["piece_id"]) in winner_ids
            ]
            winner_prevalence = (
                len(winner_feature_rows) / len(winners) if winners else 0.0
            )
            baseline_prevalence = len(feature_rows) / len(analyzable_rows)
            enrichment = (
                winner_prevalence / baseline_prevalence if baseline_prevalence else None
            )

            if confidence == "insufficient":
                claim = (
                    f"There is insufficient evidence to identify the {field} band "
                    f"{label} as associated with stronger {objective} performance."
                )
            else:
                claim = (
                    f"{field} values in the {label} band are associated with "
                    f"{ratio:.2f}x the median {objective} of {controls['method']} controls."
                )

            findings.append(
                _canonical_finding(
                    dimension="continuous_band",
                    feature=f"{field}:{label}",
                    claim=claim,
                    objective=objective,
                    comparison=f"{field} {label} vs {controls['method']} controls",
                    effect={"value": ratio, "unit": "median_ratio"},
                    sample={
                        "feature": len(feature_rows),
                        "control": len(control_rows),
                        "analyzable": len(analyzable_rows),
                    },
                    confidence=confidence,
                    evidence_rows=feature_rows,
                    interval=interval,
                    extra={
                        "band": {
                            "field": field,
                            "index": band_index,
                            "count": len(bands),
                            "low": band[0],
                            "high": band[1],
                            "profile_derived": True,
                        },
                        "control_match": controls["method"],
                        "winner_count": len(winner_feature_rows),
                        "winner_prevalence": winner_prevalence,
                        "baseline_count": len(feature_rows),
                        "baseline_prevalence": baseline_prevalence,
                        "winner_enrichment": enrichment,
                        "distribution": {
                            "count": len(values),
                            "min": min(values),
                            "median": median(values),
                            "max": max(values),
                            "q25": quantile(values, 0.25),
                            "q75": quantile(values, 0.75),
                        },
                        "raw_values": {
                            "feature": [
                                values_by_piece[str(row["piece_id"])] for row in feature_rows
                            ],
                            "control": [
                                values_by_piece[str(row["piece_id"])] for row in control_rows
                            ],
                            "feature_objective": [float(row["value"]) for row in feature_rows],
                            "control_objective": [float(row["value"]) for row in control_rows],
                        },
                        "relative_percentiles": {
                            "feature": [row.get("relative_percentile") for row in feature_rows],
                            "control": [row.get("relative_percentile") for row in control_rows],
                        },
                    },
                )
            )

    return findings
