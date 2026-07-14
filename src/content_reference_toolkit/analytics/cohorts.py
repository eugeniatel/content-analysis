"""Winner, baseline, and matched-control cohort construction."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .objectives import relative_percentiles


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


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


def content_format(row: Mapping[str, Any]) -> str:
    piece = row.get("piece") if isinstance(row.get("piece"), Mapping) else row
    for key in ("media_type", "format", "content_type", "kind"):
        value = _text(piece.get(key))
        if value:
            normalized = value.casefold()
            aliases = {
                "video": "reel",
                "short_video": "reel",
                "sidecar": "carousel",
                "album": "carousel",
                "image": "static",
                "photo": "static",
            }
            return aliases.get(normalized, normalized)
    return "unknown"


def publication_age_days(row: Mapping[str, Any]) -> float | None:
    piece = row.get("piece") if isinstance(row.get("piece"), Mapping) else row
    for key in ("publication_age_days", "age_days"):
        value = piece.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number

    published_at = _parse_datetime(
        piece.get("published_at", piece.get("publication_time"))
    )
    captured_at = _parse_datetime(row.get("captured_at"))
    if published_at is None or captured_at is None:
        return None
    age = (captured_at - published_at).total_seconds() / 86_400
    return max(0.0, age)


def publication_age_band(age_days: float | None) -> str:
    if age_days is None:
        return "unknown"
    if age_days < 3:
        return "0-2d"
    if age_days < 7:
        return "3-6d"
    if age_days < 14:
        return "7-13d"
    if age_days < 30:
        return "14-29d"
    if age_days < 90:
        return "30-89d"
    return "90d+"


def add_comparable_percentiles(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Add a relative percentile inside format/publication-age strata."""

    copied = [dict(row) for row in rows]
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(copied):
        age_band = publication_age_band(publication_age_days(row))
        row["publication_age_days"] = publication_age_days(row)
        row["publication_age_band"] = age_band
        row["format"] = content_format(row)
        grouped[(row["format"], age_band)].append(index)

    for indices in grouped.values():
        percentiles = relative_percentiles([float(copied[index]["value"]) for index in indices])
        for index, percentile in zip(indices, percentiles, strict=True):
            copied[index]["comparable_percentile"] = percentile
            copied[index]["comparable_group_size"] = len(indices)
    return copied


def matched_controls(
    feature_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    minimum_sample: int = 5,
    topics_by_piece: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Choose the strongest available non-overlapping control cohort.

    Topic + format + age matching is used only when it preserves the minimum sample.
    The function then falls back to format + age, format only, and finally the
    eligible non-feature corpus.  The selected method is always returned.
    """

    feature_ids = {str(row["piece_id"]) for row in feature_rows}
    candidates = [dict(row) for row in candidate_rows if str(row["piece_id"]) not in feature_ids]
    if not feature_rows:
        return {"rows": [], "method": "none", "matched": False}

    formats = {content_format(row) for row in feature_rows}
    format_age_keys = {
        (content_format(row), publication_age_band(publication_age_days(row)))
        for row in feature_rows
    }

    format_age = [
        row
        for row in candidates
        if (content_format(row), publication_age_band(publication_age_days(row)))
        in format_age_keys
    ]

    if topics_by_piece:
        topic_keys: set[tuple[str, str, str]] = set()
        for row in feature_rows:
            identifier = str(row["piece_id"])
            for topic in topics_by_piece.get(identifier, ()):
                topic_keys.add(
                    (
                        content_format(row),
                        publication_age_band(publication_age_days(row)),
                        str(topic).casefold(),
                    )
                )
        topic_matched = []
        if topic_keys:
            for row in format_age:
                identifier = str(row["piece_id"])
                row_topics = {str(topic).casefold() for topic in topics_by_piece.get(identifier, ())}
                row_prefix = (
                    content_format(row),
                    publication_age_band(publication_age_days(row)),
                )
                if any((*row_prefix, topic) in topic_keys for topic in row_topics):
                    topic_matched.append(row)
            if len(topic_matched) >= minimum_sample:
                return {
                    "rows": topic_matched,
                    "method": "topic_format_publication_age",
                    "matched": True,
                }

    if len(format_age) >= minimum_sample:
        return {
            "rows": format_age,
            "method": "format_publication_age",
            "matched": True,
        }

    format_only = [row for row in candidates if content_format(row) in formats]
    if len(format_only) >= minimum_sample:
        return {"rows": format_only, "method": "format", "matched": True}
    if len(candidates) >= minimum_sample:
        return {"rows": candidates, "method": "eligible_corpus", "matched": False}
    return {"rows": candidates, "method": "insufficient", "matched": False}

