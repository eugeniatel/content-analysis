"""Schema helpers for profile-intelligence reports.

This is intentionally a small, stdlib-only contract rather than a dependency on a
validation framework.  A report is a plain JSON-compatible mapping, making it easy
for the CLI, tests, and future API surfaces to share without translation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

REPORT_SCHEMA_VERSION = "1.0"

SECTION_SPECS: tuple[tuple[str, str], ...] = (
    ("formats", "Where the profile wins by format"),
    ("duration_density", "Duration and density relationships"),
    ("topics_intents", "Topics and content intents"),
    ("writing_vocabulary", "Writing and vocabulary fingerprint"),
    ("reel_rhythm", "Reel language, rhythm, pauses, and structure"),
    ("carousel_anatomy", "Carousel anatomy and slide-by-slide patterns"),
)

_SECTION_TITLES = dict(SECTION_SPECS)
_SECTION_ALIASES = {
    "format": "formats",
    "formats": "formats",
    "media_type": "formats",
    "duration": "duration_density",
    "density": "duration_density",
    "duration_density": "duration_density",
    "topic": "topics_intents",
    "topics": "topics_intents",
    "intent": "topics_intents",
    "content_intent": "topics_intents",
    "topics_intents": "topics_intents",
    "writing": "writing_vocabulary",
    "vocabulary": "writing_vocabulary",
    "lexical": "writing_vocabulary",
    "writing_vocabulary": "writing_vocabulary",
    "reel": "reel_rhythm",
    "reels": "reel_rhythm",
    "reel_rhythm": "reel_rhythm",
    "carousel": "carousel_anatomy",
    "carousels": "carousel_anatomy",
    "carousel_anatomy": "carousel_anatomy",
}

_CAUSAL_CLAIM = re.compile(
    r"(?:\bcauses?\b|\bguarantees?\b|\bmakes?\b.{0,40}\bgo viral\b|"
    r"\bwill\b.{0,40}\b(?:go viral|guarantee|always increase|always improve)\b|"
    r"\bcausa(?:n)?\b|\bgarantiza(?:n)?\b|\bhar[aá](?:n)?\b.{0,40}\bviral\b)",
    re.IGNORECASE,
)


class ReportValidationError(ValueError):
    """Raised when a canonical report would be incomplete or misleading."""


def _plain_copy(value: Any, path: str) -> Any:
    """Return a defensive copy and fail early for non-JSON report values."""

    result = copy.deepcopy(value)
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReportValidationError(f"{path} must contain JSON-compatible values: {exc}") from exc
    return result


def _as_mapping(value: Any, path: str, *, nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReportValidationError(f"{path} must be an object")
    result = _plain_copy(dict(value), path)
    if nonempty and not result:
        raise ReportValidationError(f"{path} must not be empty")
    return result


def _as_list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReportValidationError(f"{path} must be a list")
    return _plain_copy(list(value), path)


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{path} must be a non-empty string")
    return value.strip()


def anchor_token(value: Any) -> str:
    """Create a readable, collision-resistant HTML/Markdown anchor token."""

    raw = str(value)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-") or "item"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:64]}-{digest}"


def safe_http_url(value: Any) -> str | None:
    """Return a renderable HTTP(S) URL, otherwise ``None``.

    URLs are untrusted source data.  Invalid URLs stay in JSON evidence for audit
    purposes but never become active links in Markdown or HTML.
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(ord(char) <= 32 for char in candidate):
        return None
    if any(char in candidate for char in "\"'<>`"):
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    return candidate


def _normalize_evidence(items: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items, 1):
        item = _as_mapping(raw, f"evidence[{index - 1}]", nonempty=True)
        evidence_id = _required_text(item.get("id"), f"evidence[{index - 1}].id")
        piece_id = _required_text(
            item.get("piece_id", evidence_id), f"evidence[{index - 1}].piece_id"
        )
        item["id"] = evidence_id
        item["piece_id"] = piece_id
        item["kind"] = str(item.get("kind") or "post")
        evidence_token = anchor_token(evidence_id)
        piece_token = anchor_token(piece_id)
        navigation: dict[str, str] = {
            "evidence_anchor": f"evidence-{evidence_token}",
            "post_anchor": f"post-{piece_token}",
        }
        if item.get("start_sec") is not None:
            try:
                start_ms = round(float(item["start_sec"]) * 1000)
            except (TypeError, ValueError) as exc:
                raise ReportValidationError(
                    f"evidence[{index - 1}].start_sec must be numeric"
                ) from exc
            navigation["timestamp_anchor"] = (
                f"timestamp-{piece_token}-{start_ms}-{evidence_token}"
            )
        if item.get("slide_index") is not None:
            try:
                slide_index = int(item["slide_index"])
            except (TypeError, ValueError) as exc:
                raise ReportValidationError(
                    f"evidence[{index - 1}].slide_index must be an integer"
                ) from exc
            if slide_index < 0:
                raise ReportValidationError(
                    f"evidence[{index - 1}].slide_index must be non-negative"
                )
            item["slide_index"] = slide_index
            navigation["slide_anchor"] = (
                f"slide-{piece_token}-{slide_index}-{evidence_token}"
            )
        item["navigation"] = navigation
        normalized.append(item)
    return normalized


def _normalize_references(value: Any, path: str) -> list[str]:
    references = _as_list(value, path)
    normalized: list[str] = []
    for index, raw in enumerate(references):
        if isinstance(raw, Mapping):
            raw = raw.get("id") or raw.get("evidence_id") or raw.get("piece_id")
        normalized.append(_required_text(raw, f"{path}[{index}]"))
    return normalized


def _normalize_finding(raw: Any, index: int) -> dict[str, Any]:
    path = f"findings[{index}]"
    item = _as_mapping(raw, path, nonempty=True)
    item["id"] = _required_text(item.get("id"), f"{path}.id")
    for key in ("claim", "objective", "comparison", "confidence", "caveat"):
        item[key] = _required_text(item.get(key), f"{path}.{key}")
    status = str(item.get("status") or "supported").strip().casefold()
    if status not in {"supported", "insufficient_evidence"}:
        raise ReportValidationError(
            f"{path}.status must be 'supported' or 'insufficient_evidence'"
        )
    item["status"] = status
    if status == "supported" and _CAUSAL_CLAIM.search(item["claim"]):
        raise ReportValidationError(
            f"{path}.claim uses causal or guaranteed wording; describe an association instead"
        )
    effect = _as_mapping(item.get("effect"), f"{path}.effect")
    if "value" not in effect or "unit" not in effect:
        raise ReportValidationError(f"{path}.effect requires value and unit")
    item["effect"] = effect
    sample = _as_mapping(item.get("sample"), f"{path}.sample")
    if "feature" not in sample or "control" not in sample:
        raise ReportValidationError(f"{path}.sample requires feature and control")
    item["sample"] = sample
    for key in ("winner_prevalence", "baseline_prevalence"):
        if key not in item:
            raise ReportValidationError(f"{path}.{key} is required")
    item["evidence"] = _normalize_references(item.get("evidence"), f"{path}.evidence")
    if not item["evidence"]:
        raise ReportValidationError(f"{path}.evidence must contain at least one reference")

    section_value = str(item.get("section") or item.get("dimension") or "additional")
    section_key = re.sub(r"[^a-z0-9]+", "_", section_value.casefold()).strip("_")
    item["section"] = _SECTION_ALIASES.get(section_key, section_key or "additional")
    return item


def _normalize_action_items(value: Any, kind: str) -> list[dict[str, Any]]:
    items = _as_list(value, kind)
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items, 1):
        if isinstance(raw, str):
            item: dict[str, Any] = {"text": raw}
        else:
            item = _as_mapping(raw, f"{kind}[{index - 1}]", nonempty=True)
        item["id"] = str(item.get("id") or f"{kind}-{index}")
        item["text"] = _required_text(
            item.get("text") or item.get("claim"), f"{kind}[{index - 1}].text"
        )
        item["evidence"] = _normalize_references(
            item.get("evidence"), f"{kind}[{index - 1}].evidence"
        )
        normalized.append(item)
    return normalized


def _normalize_limitations(value: Any) -> list[dict[str, Any]]:
    items = _as_list(value, "limitations")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items, 1):
        if isinstance(raw, str):
            item: dict[str, Any] = {"text": raw}
        else:
            item = _as_mapping(raw, f"limitations[{index - 1}]", nonempty=True)
        item["id"] = str(item.get("id") or f"limitation-{index}")
        item["text"] = _required_text(item.get("text"), f"limitations[{index - 1}].text")
        normalized.append(item)
    if not normalized:
        raise ReportValidationError(
            "limitations must contain at least one explicit coverage, inference, or data limitation"
        )
    return normalized


def _normalize_section_summaries(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    raw_summaries = _as_mapping(value, "section_summaries")
    result: dict[str, dict[str, Any]] = {}
    for raw_key, raw in raw_summaries.items():
        key_token = re.sub(r"[^a-z0-9]+", "_", str(raw_key).casefold()).strip("_")
        key = _SECTION_ALIASES.get(key_token, key_token)
        if isinstance(raw, str):
            result[key] = {"summary": raw, "details": {}}
        else:
            section = _as_mapping(raw, f"section_summaries.{raw_key}")
            summary = section.get("summary", "")
            if not isinstance(summary, str):
                raise ReportValidationError(
                    f"section_summaries.{raw_key}.summary must be a string"
                )
            result[key] = {
                "summary": summary,
                "details": _plain_copy(section.get("details", {}), f"section_summaries.{raw_key}.details"),
            }
    return result


def build_report(
    *,
    profile: Mapping[str, Any],
    run: Mapping[str, Any],
    coverage: Mapping[str, Any],
    winners: Sequence[Mapping[str, Any]] = (),
    findings: Sequence[Mapping[str, Any]] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    repeat: Sequence[str | Mapping[str, Any]] = (),
    tests: Sequence[str | Mapping[str, Any]] = (),
    cannot_conclude: Sequence[str | Mapping[str, Any]] = (),
    failures: Sequence[Mapping[str, Any]] = (),
    limitations: Sequence[str | Mapping[str, Any]],
    section_summaries: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build and validate the canonical object used by every report renderer.

    Findings must carry the fields in the report contract, including one or more
    evidence references.  A reference may identify an evidence row or a piece/post.
    Explicit limitations are mandatory: an empty list would make an apparently
    polished report easy to misread as complete.
    """

    normalized_findings = [_normalize_finding(item, i) for i, item in enumerate(findings)]
    normalized_evidence = _normalize_evidence(evidence)
    summaries = _normalize_section_summaries(section_summaries)

    section_keys = [key for key, _ in SECTION_SPECS]
    for finding in normalized_findings:
        if finding["section"] not in section_keys:
            section_keys.append(finding["section"])

    sections: dict[str, dict[str, Any]] = {}
    for key in section_keys:
        supplied = summaries.get(key, {})
        sections[key] = {
            "title": _SECTION_TITLES.get(key, key.replace("_", " ").title()),
            "summary": supplied.get("summary", ""),
            "details": supplied.get("details", {}),
            "finding_ids": [
                finding["id"] for finding in normalized_findings if finding["section"] == key
            ],
        }

    normalized_limitations = _normalize_limitations(limitations)
    normalized_cannot = _normalize_action_items(cannot_conclude, "cannot_conclude")
    if not normalized_cannot:
        normalized_cannot = [
            {"id": item["id"], "text": item["text"], "evidence": []}
            for item in normalized_limitations
        ]

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": _as_mapping(profile, "profile", nonempty=True),
        "run": _as_mapping(run, "run", nonempty=True),
        "coverage": _as_mapping(coverage, "coverage", nonempty=True),
        "winners": _as_list(winners, "winners"),
        "sections": sections,
        "findings": normalized_findings,
        "recommendations": {
            "repeat": _normalize_action_items(repeat, "repeat"),
            "test": _normalize_action_items(tests, "test"),
            "cannot_conclude": normalized_cannot,
        },
        "limitations": normalized_limitations,
        "failures": _as_list(failures, "failures"),
        "evidence": normalized_evidence,
    }
    validate_report(report)
    return report


def _validate_references(
    references: Sequence[Any],
    *,
    path: str,
    evidence_ids: set[str],
    piece_ids: set[str],
) -> None:
    for index, reference in enumerate(references):
        if reference not in evidence_ids and reference not in piece_ids:
            raise ReportValidationError(
                f"{path}[{index}] references missing evidence or piece {reference!r}"
            )


def validate_report(report: Mapping[str, Any]) -> None:
    """Validate a canonical report without mutating it."""

    if not isinstance(report, Mapping):
        raise ReportValidationError("report must be an object")
    required = {
        "schema_version",
        "generated_at",
        "profile",
        "run",
        "coverage",
        "winners",
        "sections",
        "findings",
        "recommendations",
        "limitations",
        "failures",
        "evidence",
    }
    missing = sorted(required.difference(report))
    if missing:
        raise ReportValidationError(f"report is missing required fields: {', '.join(missing)}")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise ReportValidationError(
            f"unsupported report schema version {report['schema_version']!r}"
        )
    _as_mapping(report["profile"], "profile", nonempty=True)
    _as_mapping(report["run"], "run", nonempty=True)
    _as_mapping(report["coverage"], "coverage", nonempty=True)
    sections = _as_mapping(report["sections"], "sections")
    for key, _ in SECTION_SPECS:
        if key not in sections:
            raise ReportValidationError(f"sections.{key} is required")

    evidence = _as_list(report["evidence"], "evidence")
    evidence_ids: set[str] = set()
    piece_ids: set[str] = set()
    anchors: set[str] = set()
    for index, item in enumerate(evidence):
        mapping = _as_mapping(item, f"evidence[{index}]", nonempty=True)
        evidence_id = _required_text(mapping.get("id"), f"evidence[{index}].id")
        piece_id = _required_text(mapping.get("piece_id"), f"evidence[{index}].piece_id")
        if evidence_id in evidence_ids:
            raise ReportValidationError(f"duplicate evidence id {evidence_id!r}")
        evidence_ids.add(evidence_id)
        piece_ids.add(piece_id)
        navigation = _as_mapping(mapping.get("navigation"), f"evidence[{index}].navigation")
        for anchor_name, anchor in navigation.items():
            _required_text(anchor, f"evidence[{index}].navigation.{anchor_name}")
            if anchor_name != "post_anchor" and anchor in anchors:
                raise ReportValidationError(f"duplicate evidence navigation anchor {anchor!r}")
            anchors.add(anchor)

    findings = _as_list(report["findings"], "findings")
    finding_ids: set[str] = set()
    memberships: dict[str, int] = {}
    for section_key, raw_section in sections.items():
        section = _as_mapping(raw_section, f"sections.{section_key}")
        for finding_id in _as_list(section.get("finding_ids"), f"sections.{section_key}.finding_ids"):
            memberships[str(finding_id)] = memberships.get(str(finding_id), 0) + 1
    for index, raw in enumerate(findings):
        finding = _normalize_finding(raw, index)
        finding_id = finding["id"]
        if finding_id in finding_ids:
            raise ReportValidationError(f"duplicate finding id {finding_id!r}")
        finding_ids.add(finding_id)
        if memberships.get(finding_id) != 1:
            raise ReportValidationError(
                f"finding {finding_id!r} must appear in exactly one report section"
            )
        _validate_references(
            finding["evidence"],
            path=f"findings[{index}].evidence",
            evidence_ids=evidence_ids,
            piece_ids=piece_ids,
        )
    unknown_memberships = sorted(set(memberships).difference(finding_ids))
    if unknown_memberships:
        raise ReportValidationError(
            "sections reference unknown findings: " + ", ".join(unknown_memberships)
        )

    recommendations = _as_mapping(report["recommendations"], "recommendations")
    for kind in ("repeat", "test", "cannot_conclude"):
        if kind not in recommendations:
            raise ReportValidationError(f"recommendations.{kind} is required")
        for index, raw in enumerate(_as_list(recommendations[kind], f"recommendations.{kind}")):
            item = _as_mapping(raw, f"recommendations.{kind}[{index}]", nonempty=True)
            _required_text(item.get("text"), f"recommendations.{kind}[{index}].text")
            references = _as_list(
                item.get("evidence", []), f"recommendations.{kind}[{index}].evidence"
            )
            _validate_references(
                references,
                path=f"recommendations.{kind}[{index}].evidence",
                evidence_ids=evidence_ids,
                piece_ids=piece_ids,
            )
    if not _as_list(report["limitations"], "limitations"):
        raise ReportValidationError("limitations must not be empty")

    # A final serialization check catches accidental Paths, Decimal values, NaN, etc.
    _plain_copy(dict(report), "report")

