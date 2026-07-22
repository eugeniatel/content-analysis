from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode


SOURCE_VERSION = "instagram-business-discovery-v1"
WATCHLIST_SCHEMA_VERSION = "mara-1171-instagram-watchlist-v1"

RECOMMENDATION_FIELDS = [
    "marta_native_content",
    "framing",
    "script_structure",
    "copy_angle",
    "design_treatment",
    "suggested_format_package",
    "primary_metric",
    "what_not_to_copy",
    "guardrails",
    "evidence_confidence",
    "decision_needed",
]

TOPIC_KEYWORDS = {
    "mercury_retrograde": ("mercurio retrogrado", "mercury retrograde", "retrogrado"),
    "moon": ("luna", "full moon", "new moon", "lunacion", "lunar"),
    "venus": ("venus",),
    "relationships": ("relacion", "pareja", "amor", "vinculo", "mensajes"),
    "tarot": ("tarot", "carta", "cartas"),
    "horoscope": ("horoscopo", "signo", "signos", "zodiaco"),
    "transits": ("transito", "transitos", "eclipse", "portal", "temporada"),
    "birth_chart": ("carta natal", "ascendente", "casas", "sinastria"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_handle(handle: str | None) -> str:
    cleaned = (handle or "").strip().lstrip("@").casefold()
    return re.sub(r"[^a-z0-9._-]+", "-", cleaned).strip("-")


def profile_key(row: dict[str, Any]) -> str:
    explicit = str(row.get("profile_key") or "").strip()
    if explicit:
        return explicit
    handle = safe_handle(str(row.get("instagram_handle") or row.get("handle") or ""))
    if handle:
        return f"instagram:{handle}"
    entity = re.sub(r"[^a-z0-9]+", "-", str(row.get("entity") or "unknown").casefold()).strip("-")
    return f"instagram:{entity or 'unknown'}"


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("profiles") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("watchlist must be a JSON list or an object with profiles[]")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("watchlist rows must be objects")
        row = dict(raw)
        row.setdefault("profile_key", profile_key(row))
        if row["profile_key"] in seen:
            raise ValueError(f"duplicate watchlist profile_key: {row['profile_key']}")
        seen.add(row["profile_key"])
        row.setdefault("schema_version", WATCHLIST_SCHEMA_VERSION)
        out.append(row)
    return out


def is_es_ar(row: dict[str, Any]) -> bool:
    tags = {str(item).casefold() for item in row.get("tags") or []}
    market = str(row.get("market") or "").casefold()
    language = str(row.get("language") or "").casefold()
    return "ar" in {market, language} or "es_ar" in {language, *tags} or "market:ar" in tags


def is_watchlist_supported(row: dict[str, Any]) -> bool:
    status = str(row.get("monitor_status") or row.get("status") or "").casefold()
    if status in {"unsupported_or_unresolved", "unresolved", "unsupported", "private", "blocked"}:
        return False
    return bool(safe_handle(str(row.get("instagram_handle") or row.get("handle") or "")))


def business_discovery_url(*, ig_user_id: str, username: str, access_token: str, graph_version: str, limit: int) -> str:
    media_fields = ",".join(
        [
            "id",
            "caption",
            "media_type",
            "media_product_type",
            "permalink",
            "timestamp",
            "like_count",
            "comments_count",
            "children{id,media_type,media_url,permalink,timestamp,thumbnail_url}",
            "thumbnail_url",
            "media_url",
        ]
    )
    fields = (
        f"business_discovery.username({username})"
        f"{{id,username,name,biography,followers_count,media_count,website,media.limit({limit}){{{media_fields}}}}}"
    )
    params = urlencode({"fields": fields, "access_token": access_token})
    return f"https://graph.facebook.com/{graph_version}/{quote(ig_user_id, safe='')}?{params}"


def fixture_path(fixture_response_dir: Path, handle: str) -> Path:
    return fixture_response_dir / f"{safe_handle(handle)}.json"


def load_fixture_response(fixture_response_dir: Path, handle: str) -> dict[str, Any]:
    path = fixture_path(fixture_response_dir, handle)
    if not path.exists():
        raise FileNotFoundError(f"fixture response not found for @{handle}: {path}")
    payload = read_json(path)
    if "business_discovery" in payload:
        return payload
    return {"business_discovery": payload}


def fetch_business_discovery(
    *,
    row: dict[str, Any],
    ig_user_id: str,
    access_token: str,
    graph_version: str,
    limit: int,
    http_get: Any,
    fixture_response_dir: Path | None = None,
) -> dict[str, Any]:
    username = safe_handle(str(row.get("instagram_handle") or row.get("handle") or ""))
    if fixture_response_dir:
        return load_fixture_response(fixture_response_dir, username)
    url = business_discovery_url(
        ig_user_id=ig_user_id,
        username=username,
        access_token=access_token,
        graph_version=graph_version,
        limit=limit,
    )
    return http_get(url)


def classify_meta_error(message: str) -> tuple[str, str]:
    lower = message.casefold()
    if "fixture response not found" in lower:
        return "blocked", "missing_fixture_response"
    if any(token in lower for token in ("rate", "limit", "too many", "429", "code\":4", "code\":17")):
        return "blocked", "rate_limited"
    if any(token in lower for token in ("unsupported", "permission", "not found", "cannot be loaded")):
        return "unsupported_or_unresolved", "not_business_creator_or_no_permission"
    return "blocked", "api_error"


def redact_meta_error(message: str) -> str:
    message = re.sub(r"(access_token=)[^&\s]+", r"\1<redacted>", message, flags=re.I)
    message = re.sub(r"(Authorization:\s*Bearer\s+)[^\s]+", r"\1<redacted>", message, flags=re.I)
    return message


def media_items_from_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    profile = payload.get("business_discovery") or {}
    media = profile.get("media") or {}
    data = media.get("data") if isinstance(media, dict) else media
    return [dict(item) for item in data or [] if isinstance(item, dict)]


def profile_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("business_discovery") or {}
    return dict(profile) if isinstance(profile, dict) else {}


def public_format(media: dict[str, Any]) -> str:
    media_type = str(media.get("media_type") or "").upper()
    product = str(media.get("media_product_type") or "").upper()
    if media_type == "CAROUSEL_ALBUM":
        return "carousel"
    if product == "REELS":
        return "reel"
    if media_type == "VIDEO":
        return "reel"
    if media_type == "IMAGE":
        return "static"
    return media_type.casefold() or "unknown"


def published_date(timestamp: str | None) -> str:
    return str(timestamp or "")[:10]


def child_rows(media: dict[str, Any]) -> list[dict[str, Any]]:
    children = ((media.get("children") or {}).get("data") or []) if isinstance(media.get("children"), dict) else []
    rows = []
    for index, child in enumerate(children, start=1):
        if not isinstance(child, dict):
            continue
        rows.append(
            {
                "slide_index": index,
                "media_id": str(child.get("id") or ""),
                "media_type": str(child.get("media_type") or ""),
                "permalink": child.get("permalink"),
                "timestamp": child.get("timestamp"),
                "media_url_present": bool(child.get("media_url")),
                "thumbnail_url_present": bool(child.get("thumbnail_url")),
                "missing_field_reasons": missing_field_reasons(child, required=("id", "media_type")),
            }
        )
    return rows


def missing_field_reasons(media: dict[str, Any], *, required: tuple[str, ...] = ("id", "media_type", "timestamp", "permalink")) -> list[str]:
    reasons: list[str] = []
    for field in required:
        if media.get(field) in (None, ""):
            reasons.append(f"missing_{field}")
    for metric in ("like_count", "comments_count"):
        if media.get(metric) is None:
            reasons.append(f"metric_not_exposed:{metric}")
    return reasons


def normalize_media(row: dict[str, Any], media: dict[str, Any], *, retrieved_at: str) -> dict[str, Any]:
    handle = safe_handle(str(row.get("instagram_handle") or row.get("handle") or ""))
    media_id = str(media.get("id") or "")
    children = child_rows(media)
    normalized = {
        "source_version": SOURCE_VERSION,
        "retrieved_at": retrieved_at,
        "profile_key": row["profile_key"],
        "entity": row.get("entity"),
        "market": row.get("market"),
        "language": row.get("language"),
        "tier": row.get("tier"),
        "watchlist_sources": row.get("source_refs") or row.get("sources") or [],
        "instagram_handle": handle,
        "account_id": str(row.get("account_id") or ""),
        "media_id": media_id,
        "canonical_permalink": media.get("permalink"),
        "media_type": str(media.get("media_type") or ""),
        "media_product_type": str(media.get("media_product_type") or ""),
        "format": public_format(media),
        "caption": media.get("caption"),
        "timestamp": media.get("timestamp"),
        "published_at": published_date(media.get("timestamp")),
        "public_counts": {
            "likes": media.get("like_count"),
            "comments": media.get("comments_count"),
        },
        "children": children,
        "provenance": {
            "connector": SOURCE_VERSION,
            "api": "instagram_business_discovery",
            "profile_url": f"https://www.instagram.com/{handle}/" if handle else None,
            "registry_profile_key": row["profile_key"],
        },
        "confidence": "api_reported",
        "missing_field_reasons": missing_field_reasons(media),
    }
    if children:
        normalized["carousel_slide_count"] = len(children)
    return normalized


def metric_row_from_media(media: dict[str, Any]) -> dict[str, str]:
    source = str(media.get("canonical_permalink") or f"instagram://media/{media['media_id']}")
    counts = media.get("public_counts") or {}
    return {
        "url": source,
        "platform": "instagram",
        "format": str(media.get("format") or ""),
        "creator": str(media.get("instagram_handle") or ""),
        "profile_url": str((media.get("provenance") or {}).get("profile_url") or ""),
        "published_at": str(media.get("published_at") or ""),
        "caption": str(media.get("caption") or ""),
        "likes": "" if counts.get("likes") is None else str(counts.get("likes")),
        "comments": "" if counts.get("comments") is None else str(counts.get("comments")),
        "metric_source": SOURCE_VERSION,
        "metrics_captured_at": str(media.get("retrieved_at") or ""),
        "notes": f"MARA-1171 Business Discovery profile_key={media.get('profile_key')}; media_id={media.get('media_id')}",
    }


def raw_profile_dir(output_root: Path, handle: str) -> Path:
    return output_root / "instagram" / "raw" / safe_handle(handle)


def normalized_jsonl_path(output_root: Path) -> Path:
    return output_root / "instagram" / "normalized" / "media.jsonl"


def coverage_report_path(output_root: Path) -> Path:
    return output_root / "instagram" / "reports" / "coverage.json"


def deltas_path(output_root: Path) -> Path:
    return output_root / "instagram" / "reports" / "daily-deltas.json"


def signal_brief_path(output_root: Path) -> Path:
    return output_root / "instagram" / "reports" / "signal-brief.json"


def cursor_path(output_root: Path, profile_key_: str) -> Path:
    safe_key = re.sub(r"[^a-z0-9._-]+", "-", profile_key_.casefold()).strip("-")
    return output_root / "instagram" / "cursors" / f"{safe_key}.json"


def read_cursor(output_root: Path, profile_key_: str) -> dict[str, Any]:
    path = cursor_path(output_root, profile_key_)
    if not path.exists():
        return {}
    return read_json(path)


def write_cursor(output_root: Path, profile_key_: str, media_rows: list[dict[str, Any]]) -> None:
    if not media_rows:
        return
    timestamps = [str(item.get("timestamp") or "") for item in media_rows if item.get("timestamp")]
    latest = max(timestamps) if timestamps else None
    latest_ids = sorted(str(item.get("media_id")) for item in media_rows if item.get("timestamp") == latest)
    write_json(
        cursor_path(output_root, profile_key_),
        {
            "profile_key": profile_key_,
            "latest_timestamp": latest,
            "latest_media_ids": latest_ids,
            "updated_at": utc_now(),
            "source_version": SOURCE_VERSION,
        },
    )


def load_existing_media(output_root: Path) -> dict[str, dict[str, Any]]:
    path = normalized_jsonl_path(output_root)
    existing: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return existing
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        media_id = str(item.get("media_id") or "")
        if media_id:
            existing[media_id] = item
    return existing


def write_normalized_media(output_root: Path, rows: list[dict[str, Any]]) -> None:
    path = normalized_jsonl_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda item: (str(item.get("timestamp") or ""), str(item.get("media_id") or "")))
    with path.open("w", encoding="utf-8") as handle:
        for row in ordered:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def is_new_since_cursor(media: dict[str, Any], cursor: dict[str, Any], existing_ids: set[str]) -> bool:
    media_id = str(media.get("media_id") or "")
    if media_id in existing_ids:
        return False
    latest = cursor.get("latest_timestamp")
    timestamp = media.get("timestamp")
    if not latest or not timestamp:
        return True
    if str(timestamp) > str(latest):
        return True
    if str(timestamp) == str(latest):
        return media_id not in set(cursor.get("latest_media_ids") or [])
    return False


def write_raw_media(output_root: Path, media: dict[str, Any], raw: dict[str, Any]) -> str:
    handle = str(media.get("instagram_handle") or "unknown")
    media_id = str(media.get("media_id") or "unknown")
    path = raw_profile_dir(output_root, handle) / f"{media_id}.json"
    write_json(path, raw)
    return str(path.relative_to(output_root))


def topic_for_caption(caption: str | None) -> str:
    text = " ".join(str(caption or "").casefold().split())
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return topic
    return "uncategorized"


def first_hook(caption: str | None) -> str | None:
    text = " ".join(str(caption or "").split())
    if not text:
        return None
    first_line = str(caption or "").strip().splitlines()[0].strip()
    return (first_line or text)[:160]


def build_coverage_report(
    *,
    watchlist: list[dict[str, Any]],
    coverage_rows: list[dict[str, Any]],
    retrieved_at: str,
    dry_run: bool,
) -> dict[str, Any]:
    status_counts = Counter(row["coverage_status"] for row in coverage_rows)
    es_ar_rows = [row for row in coverage_rows if row.get("es_AR")]
    return {
        "status": "done",
        "source_version": SOURCE_VERSION,
        "retrieved_at": retrieved_at,
        "dry_run_local_safe": dry_run,
        "watchlist_rows": len(watchlist),
        "coverage_summary": dict(sorted(status_counts.items())),
        "profiles": coverage_rows,
        "es_AR": {
            "rows": len(es_ar_rows),
            "coverage_summary": dict(sorted(Counter(row["coverage_status"] for row in es_ar_rows).items())),
            "profiles": es_ar_rows,
        },
        "notes": [
            "No personal cookies, private sessions, publishing, credential mutation, or runtime scheduling are used.",
            "Only public counts exposed by Instagram Business Discovery are recorded.",
        ],
    }


def build_daily_deltas(new_media: list[dict[str, Any]], coverage_rows: list[dict[str, Any]], *, retrieved_at: str) -> dict[str, Any]:
    format_counts = Counter(str(item.get("format") or "unknown") for item in new_media)
    topic_counts = Counter(topic_for_caption(item.get("caption")) for item in new_media if item.get("caption"))
    cadence = Counter(f"{item.get('instagram_handle')}:{item.get('published_at')}" for item in new_media)
    carousels = [
        {
            "profile_key": item.get("profile_key"),
            "media_id": item.get("media_id"),
            "permalink": item.get("canonical_permalink"),
            "slide_count": len(item.get("children") or []),
            "child_media_types": [child.get("media_type") for child in item.get("children") or []],
        }
        for item in new_media
        if item.get("children")
    ]
    engagements = []
    for item in new_media:
        counts = item.get("public_counts") or {}
        likes = counts.get("likes")
        comments = counts.get("comments")
        interaction_count = None
        if likes is not None or comments is not None:
            interaction_count = int(likes or 0) + int(comments or 0)
        engagements.append(
            {
                "profile_key": item.get("profile_key"),
                "media_id": item.get("media_id"),
                "permalink": item.get("canonical_permalink"),
                "likes": likes,
                "comments": comments,
                "interaction_count": interaction_count,
                "confidence": "public_counts_only" if interaction_count is not None else "missing_public_counts",
            }
        )
    return {
        "status": "done",
        "source_version": SOURCE_VERSION,
        "retrieved_at": retrieved_at,
        "new_media_count": len(new_media),
        "formats": dict(sorted(format_counts.items())),
        "topics": dict(sorted(topic_counts.items())),
        "hooks": [
            {
                "profile_key": item.get("profile_key"),
                "media_id": item.get("media_id"),
                "hook": first_hook(item.get("caption")),
                "confidence": "caption_first_line" if first_hook(item.get("caption")) else "missing_caption",
            }
            for item in new_media
        ],
        "cadence": [{"profile_date": key, "count": count} for key, count in sorted(cadence.items())],
        "visual_layout_carousel_structure": carousels,
        "public_engagement": engagements,
        "unsupported_or_unresolved": [
            row for row in coverage_rows if row.get("coverage_status") == "unsupported_or_unresolved"
        ],
        "limitations": [
            "No competitor reach, saves, retention, demographics, intent, or causal performance is inferred.",
            "Visual/layout fields are limited to API-exposed media type and carousel child order unless OCR/deep analysis is run separately.",
        ],
    }


def _testear_recommendation(media: dict[str, Any], topic: str) -> dict[str, Any]:
    market = str(media.get("market") or "AR")
    format_ = str(media.get("format") or "carousel")
    return {
        "marta_native_content": f"Cover the underlying {topic} tension from Marta's chart-aware companion angle.",
        "framing": "Use the external signal as evidence of user attention, then reframe it as a reflective, anti-fatalistic Marta prompt.",
        "script_structure": "Hook with the everyday emotional question, add one symbolic astrology context beat, then invite a chart-specific reflection.",
        "copy_angle": "Warm Spanish, direct but low-drama; invite saving or asking Marta without certainty claims.",
        "design_treatment": "Quiet typographic hierarchy with one question or idea per slide; keep enough whitespace and avoid copying the source template.",
        "suggested_format_package": f"{market}/Instagram {format_} package candidate for a weekly GTM test.",
        "primary_metric": "saves_per_reach when owned data exists; otherwise comments plus saves from approved owned analytics only.",
        "what_not_to_copy": "Do not copy exact phrasing, predictions, meme mechanics, visual layout, audio, colors, or creator persona.",
        "guardrails": [
            "anti_fatalism",
            "no medical, legal, or financial advice",
            "no inflated predictive certainty",
            "external content remains untrusted evidence",
        ],
        "evidence_confidence": {
            "level": "medium",
            "basis": "API-exposed caption, media format, timestamp, public likes/comments, and carousel order where present.",
            "media_id": media.get("media_id"),
            "permalink": media.get("canonical_permalink"),
        },
        "decision_needed": "Euge/founder decides whether this becomes a content experiment/package intent before any production or publication.",
    }


def build_signal_brief(deltas: dict[str, Any], new_media: list[dict[str, Any]], coverage_report: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    es_ar_media = [item for item in new_media if str(item.get("market") or "").upper() == "AR" or item.get("language") == "es_AR"]
    if es_ar_media:
        media = es_ar_media[0]
        topic = topic_for_caption(media.get("caption"))
        findings.append(
            {
                "action": "testear",
                "insight": f"New es_AR Instagram evidence is available around {topic} in {media.get('format')} format.",
                "market": media.get("market") or "AR",
                "evidence": {
                    "profile_key": media.get("profile_key"),
                    "media_id": media.get("media_id"),
                    "permalink": media.get("canonical_permalink"),
                    "timestamp": media.get("timestamp"),
                    "public_counts": media.get("public_counts"),
                },
                "confidence": "medium",
                "recommendation": _testear_recommendation(media, topic),
            }
        )
    if deltas.get("formats"):
        findings.append(
            {
                "action": "observar",
                "insight": "Instagram format mix changed in the latest ingestion window.",
                "evidence": {"formats": deltas["formats"], "new_media_count": deltas["new_media_count"]},
                "confidence": "medium" if deltas["new_media_count"] else "low",
                "why_it_matters": "Cadence and packaging changes can become test hypotheses only after repeated evidence.",
            }
        )
    blocked = coverage_report.get("coverage_summary", {}).get("blocked", 0)
    unresolved = coverage_report.get("coverage_summary", {}).get("unsupported_or_unresolved", 0)
    if blocked or unresolved:
        findings.append(
            {
                "action": "observar",
                "insight": "Coverage gaps remain visible in the frozen watchlist.",
                "evidence": {"blocked": blocked, "unsupported_or_unresolved": unresolved},
                "confidence": "high",
                "why_it_matters": "The monitor should not silently omit profiles when Meta permissions or handle verification are incomplete.",
            }
        )
    if not findings:
        findings.append(
            {
                "action": "observar",
                "insight": "No new Instagram media was ingested from the current watchlist window.",
                "evidence": {"new_media_count": 0, "coverage_summary": coverage_report.get("coverage_summary", {})},
                "confidence": "high",
                "why_it_matters": "An empty brief is valid when evidence is missing or unchanged.",
            }
        )
    return {
        "status": "done",
        "source_version": SOURCE_VERSION,
        "retrieved_at": deltas.get("retrieved_at"),
        "max_findings": 5,
        "findings": findings[:5],
        "testear_contract_fields": RECOMMENDATION_FIELDS,
        "guardrails": [
            "No publishing, replies, DMs, public communication, guide changes, vocabulary adoption, or runtime activation.",
            "No competitor reach, saves, retention, demographics, intent, or causal performance is inferred.",
        ],
    }


def run_watchlist_ingestion(
    *,
    watchlist: list[dict[str, Any]],
    output_root: Path,
    ig_user_id: str | None,
    access_token: str | None,
    graph_version: str,
    limit: int,
    dry_run: bool,
    http_get: Any,
    fixture_response_dir: Path | None = None,
) -> dict[str, Any]:
    retrieved_at = utc_now()
    existing = load_existing_media(output_root)
    new_media: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []

    for row in watchlist:
        handle = safe_handle(str(row.get("instagram_handle") or row.get("handle") or ""))
        base_coverage = {
            "profile_key": row["profile_key"],
            "entity": row.get("entity"),
            "market": row.get("market"),
            "language": row.get("language"),
            "tier": row.get("tier"),
            "instagram_handle": handle or None,
            "profile_url": f"https://www.instagram.com/{handle}/" if handle else None,
            "es_AR": is_es_ar(row),
            "owner": row.get("owner") or "GTM Lead",
            "next_verification_action": row.get("next_verification_action"),
            "source_refs": row.get("source_refs") or row.get("sources") or [],
        }
        if not is_watchlist_supported(row):
            coverage_rows.append(
                {
                    **base_coverage,
                    "coverage_status": "unsupported_or_unresolved",
                    "reason": row.get("unsupported_reason") or row.get("verification_status") or "missing_or_unverified_business_creator_handle",
                }
            )
            continue
        if not fixture_response_dir and (not ig_user_id or not access_token):
            coverage_rows.append(
                {
                    **base_coverage,
                    "coverage_status": "blocked",
                    "reason": "missing_meta_access_token_or_ig_user_id",
                }
            )
            continue
        try:
            payload = fetch_business_discovery(
                row=row,
                ig_user_id=ig_user_id or "fixture_ig_user",
                access_token=access_token or "fixture_access_token",
                graph_version=graph_version,
                limit=limit,
                http_get=http_get,
                fixture_response_dir=fixture_response_dir,
            )
            profile = profile_from_response(payload)
            raw_dir = raw_profile_dir(output_root, handle)
            write_json(raw_dir / "business_discovery.profile.json", {"retrieved_at": retrieved_at, "payload": payload})
            media_rows = [
                normalize_media({**row, "account_id": profile.get("id")}, item, retrieved_at=retrieved_at)
                for item in media_items_from_response(payload)
            ]
            cursor = read_cursor(output_root, row["profile_key"])
            existing_ids = set(existing)
            profile_new = [item for item in media_rows if is_new_since_cursor(item, cursor, existing_ids)]
            for item in profile_new:
                raw = next((raw_item for raw_item in media_items_from_response(payload) if str(raw_item.get("id")) == item["media_id"]), {})
                item["raw_evidence_path"] = write_raw_media(output_root, item, raw)
            if media_rows:
                write_cursor(output_root, row["profile_key"], media_rows)
            new_media.extend(profile_new)
            existing.update({item["media_id"]: item for item in profile_new})
            coverage_rows.append(
                {
                    **base_coverage,
                    "coverage_status": "ingestable",
                    "account_id": profile.get("id"),
                    "media_returned": len(media_rows),
                    "new_media": len(profile_new),
                    "cursor_latest_timestamp": (read_cursor(output_root, row["profile_key"]) or {}).get("latest_timestamp"),
                }
            )
        except Exception as exc:
            error = redact_meta_error(str(exc))
            status, reason = classify_meta_error(error)
            coverage_rows.append({**base_coverage, "coverage_status": status, "reason": reason, "error": error})

    merged = {**load_existing_media(output_root), **{item["media_id"]: item for item in new_media if item.get("media_id")}}
    write_normalized_media(output_root, list(merged.values()))
    coverage = build_coverage_report(watchlist=watchlist, coverage_rows=coverage_rows, retrieved_at=retrieved_at, dry_run=dry_run)
    deltas = build_daily_deltas(new_media, coverage_rows, retrieved_at=retrieved_at)
    brief = build_signal_brief(deltas, new_media, coverage)
    write_json(coverage_report_path(output_root), coverage)
    write_json(deltas_path(output_root), deltas)
    write_json(signal_brief_path(output_root), brief)
    return {
        "status": "done",
        "retrieved_at": retrieved_at,
        "new_media": new_media,
        "coverage": coverage,
        "deltas": deltas,
        "brief": brief,
        "outputs": {
            "normalized_media": str(normalized_jsonl_path(output_root)),
            "coverage": str(coverage_report_path(output_root)),
            "daily_deltas": str(deltas_path(output_root)),
            "signal_brief": str(signal_brief_path(output_root)),
        },
    }
