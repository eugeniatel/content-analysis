"""Objective eligibility and profile-relative ranking.

The functions in this module deliberately require an exact metric signature.  A
``views`` count and a ``views`` rate can coexist in the input, but they can never
enter the same ranking.  Callers may select a basis/unit explicitly; otherwise the
largest compatible signature is selected deterministically and every excluded item
is surfaced in the coverage result.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping, Sequence


def piece_id(row: Mapping[str, Any]) -> str:
    """Return the stable piece identifier used by the analytics package."""

    value = row.get("piece_id", row.get("id"))
    return "" if value is None else str(value)


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_text(value: Any) -> str:
    return _text(value).casefold()


def _metric_value(row: Mapping[str, Any]) -> float | None:
    value = _first(row, "metric_value", "value")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _observation_id(row: Mapping[str, Any], *, fallback: str) -> str:
    value = _first(row, "observation_id", "id")
    return _text(value) or fallback


def _signature(row: Mapping[str, Any]) -> tuple[str, str] | None:
    basis = _normalized_text(_first(row, "metric_basis", "basis"))
    if not basis:
        return None
    unit = _normalized_text(_first(row, "metric_unit", "unit"))
    return basis, unit


def objective_eligibility(
    pieces: Sequence[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
    *,
    objective: str,
    metric_basis: str | None = None,
    metric_unit: str | None = None,
) -> dict[str, Any]:
    """Select comparable observations and return eligibility plus coverage.

    Normalized observations may use either ``metric_name`` or ``objective`` and
    either ``metric_value`` or ``value``.  ``metric_basis``, ``source``, and a
    parseable ``captured_at`` are required.  ``metric_unit`` is optional, but an
    empty unit only compares with another empty unit.
    """

    objective_key = _normalized_text(objective)
    if not objective_key:
        raise ValueError("objective must not be empty")

    piece_map = {piece_id(row): dict(row) for row in pieces if piece_id(row)}
    raw_by_piece: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    valid_by_signature: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    invalid_reasons: dict[str, set[str]] = defaultdict(set)
    unknown_piece_observations = 0

    for position, observation in enumerate(observations):
        metric_name = _normalized_text(
            _first(observation, "metric_name", "objective", "name")
        )
        if metric_name != objective_key:
            continue

        observation_piece_id = _text(
            _first(observation, "piece_id", "content_id", "post_id")
        )
        if not observation_piece_id or observation_piece_id not in piece_map:
            unknown_piece_observations += 1
            continue
        raw_by_piece[observation_piece_id].append(observation)

        value = _metric_value(observation)
        source = _text(_first(observation, "source", "metric_source"))
        captured_at_raw = _first(observation, "captured_at", "observed_at")
        captured_at = _parse_datetime(captured_at_raw)
        signature = _signature(observation)

        if value is None:
            invalid_reasons[observation_piece_id].add("invalid_metric_value")
        if not source:
            invalid_reasons[observation_piece_id].add("missing_metric_source")
        if captured_at is None:
            invalid_reasons[observation_piece_id].add("missing_capture_time")
        if signature is None:
            invalid_reasons[observation_piece_id].add("missing_metric_basis")
        if value is None or not source or captured_at is None or signature is None:
            continue

        observation_ref = _observation_id(
            observation,
            fallback=f"{observation_piece_id}:{objective_key}:{position}",
        )
        valid_by_signature[signature].append(
            {
                "piece_id": observation_piece_id,
                "piece": piece_map[observation_piece_id],
                "objective": objective_key,
                "value": value,
                "metric_basis": signature[0],
                "metric_unit": signature[1],
                "source": source,
                "captured_at": captured_at.isoformat(),
                "_captured_at": captured_at,
                "observation_ref": observation_ref,
                "raw_observation": dict(observation),
            }
        )

    requested_basis = _normalized_text(metric_basis)
    requested_unit = _normalized_text(metric_unit) if metric_unit is not None else None

    candidate_signatures = list(valid_by_signature)
    if requested_basis:
        candidate_signatures = [
            signature
            for signature in candidate_signatures
            if signature[0] == requested_basis
            and (requested_unit is None or signature[1] == requested_unit)
        ]

    def signature_score(signature: tuple[str, str]) -> tuple[int, str, str]:
        unique_pieces = {row["piece_id"] for row in valid_by_signature[signature]}
        # ``max`` uses the first value, then deterministic reverse lexical ties.
        return len(unique_pieces), signature[0], signature[1]

    selected_signature = (
        max(sorted(candidate_signatures), key=signature_score)
        if candidate_signatures
        else None
    )

    eligible: list[dict[str, Any]] = []
    if selected_signature is not None:
        latest_by_piece: dict[str, dict[str, Any]] = {}
        for row in valid_by_signature[selected_signature]:
            current = latest_by_piece.get(row["piece_id"])
            if current is None or (
                row["_captured_at"], row["observation_ref"]
            ) > (current["_captured_at"], current["observation_ref"]):
                latest_by_piece[row["piece_id"]] = row
        for row in latest_by_piece.values():
            normalized = dict(row)
            normalized.pop("_captured_at", None)
            eligible.append(normalized)

    eligible_ids = {row["piece_id"] for row in eligible}
    exclusion_ids: dict[str, set[str]] = defaultdict(set)
    valid_piece_signatures: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for signature, rows in valid_by_signature.items():
        for row in rows:
            valid_piece_signatures[row["piece_id"]].add(signature)

    reason_priority = (
        "invalid_metric_value",
        "missing_metric_source",
        "missing_capture_time",
        "missing_metric_basis",
    )
    for identifier in piece_map:
        if identifier in eligible_ids:
            continue
        if valid_piece_signatures.get(identifier):
            exclusion_ids["unit_mismatch"].add(identifier)
            continue
        reasons = invalid_reasons.get(identifier, set())
        if reasons:
            reason = next(item for item in reason_priority if item in reasons)
            exclusion_ids[reason].add(identifier)
        elif identifier not in raw_by_piece:
            exclusion_ids["no_objective_observation"].add(identifier)
        else:
            exclusion_ids["invalid_objective_observation"].add(identifier)

    signature_rows = []
    for signature in sorted(valid_by_signature):
        ids = {row["piece_id"] for row in valid_by_signature[signature]}
        signature_rows.append(
            {
                "metric_basis": signature[0],
                "metric_unit": signature[1],
                "piece_count": len(ids),
                "selected": signature == selected_signature,
            }
        )

    total = len(piece_map)
    coverage = {
        "objective": objective_key,
        "requested_metric_basis": requested_basis or None,
        "requested_metric_unit": requested_unit,
        "selected_metric_basis": selected_signature[0] if selected_signature else None,
        "selected_metric_unit": selected_signature[1] if selected_signature else None,
        "total_pieces": total,
        "observed_pieces": len(raw_by_piece),
        "eligible_pieces": len(eligible),
        "objective_coverage": (len(eligible) / total) if total else 0.0,
        "excluded": {key: len(value) for key, value in sorted(exclusion_ids.items())},
        "excluded_piece_ids": {
            key: sorted(value) for key, value in sorted(exclusion_ids.items())
        },
        "available_metric_signatures": signature_rows,
        "unknown_piece_observations": unknown_piece_observations,
    }
    return {"coverage": coverage, "eligible": eligible}


def relative_percentiles(values: Sequence[float]) -> list[float]:
    """Return average-rank percentiles in ``[0, 1]`` with deterministic ties."""

    if not values:
        return []
    if len(values) == 1:
        return [1.0]

    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = (position + end - 1) / 2
        percentile = average_rank / (len(values) - 1)
        for original_index, _ in ordered[position:end]:
            ranks[original_index] = percentile
        position = end
    return ranks


def rank_objective(
    eligible: Sequence[Mapping[str, Any]], *, top_k: int = 10
) -> dict[str, list[dict[str, Any]]]:
    """Rank eligible posts and return winner/non-winner cohorts."""

    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    copied = [dict(row) for row in eligible]
    percentiles = relative_percentiles([float(row["value"]) for row in copied])
    for row, percentile in zip(copied, percentiles, strict=True):
        row["relative_percentile"] = percentile

    ranked = sorted(
        copied,
        key=lambda row: (
            -float(row["value"]),
            _text(row.get("piece", {}).get("published_at")),
            row["piece_id"],
        ),
    )
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    winner_count = min(top_k, len(ranked))
    return {
        "ranked": ranked,
        "winners": ranked[:winner_count],
        "non_winners": ranked[winner_count:],
    }

