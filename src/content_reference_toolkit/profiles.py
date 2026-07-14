from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


RATE_METRICS = {
    "engagement_rate",
    "completion_rate",
    "retention_rate",
    "watched_full_video_pct",
}
TIME_METRICS = {"avg_watch_time"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def profile_identity(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"Could not determine a safe profile URL from {url}")
    host = parsed.hostname.lower().rstrip(".").removeprefix("www.")
    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]

    def matches(domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    if matches("instagram.com"):
        platform = "instagram"
        handle = (parts[0] if parts else "").lstrip("@").lower()
        canonical = f"https://www.instagram.com/{handle}"
    elif matches("tiktok.com"):
        platform = "tiktok"
        handle = (parts[0] if parts else "").lstrip("@").lower()
        canonical = f"https://www.tiktok.com/@{handle}"
    elif host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
        platform = "x"
        handle = (parts[0] if parts else "").lstrip("@").lower()
        canonical = f"https://x.com/{handle}"
    elif matches("threads.net"):
        platform = "threads"
        handle = (parts[0] if parts else "").lstrip("@").lower()
        canonical = f"https://www.threads.net/@{handle}"
    elif matches("youtube.com") or host == "youtu.be":
        platform = "youtube"
        handle = (parts[-1] if parts else host).lstrip("@").lower()
        prefix = "@" if parts and parts[0].startswith("@") else ""
        canonical = f"https://www.youtube.com/{prefix}{handle}"
    elif matches("linkedin.com"):
        platform = "linkedin"
        handle = "/".join(parts[:2]).lower()
        canonical = f"https://www.linkedin.com/{handle}"
    elif matches("facebook.com"):
        platform = "facebook"
        handle = (parts[0] if parts else "").lower()
        canonical = f"https://www.facebook.com/{handle}"
    else:
        platform = "unknown"
        handle = (parts[0] if parts else host).lstrip("@").lower()
        canonical = f"{parsed.scheme or 'https'}://{host}/{path}".rstrip("/")

    if not handle or not re.fullmatch(r"[a-z0-9_.\-/]+", handle):
        raise ValueError(f"Could not determine a safe profile handle from {url}")
    return {
        "id": _stable_id("profile", f"{platform}:{handle}"),
        "platform": platform,
        "handle": handle,
        "canonical_url": canonical,
    }


def upsert_profile(
    con: sqlite3.Connection,
    url: str,
    *,
    display_name: str | None = None,
    primary_language: str | None = None,
) -> dict[str, str]:
    identity = profile_identity(url)
    con.execute(
        """
        INSERT INTO profiles(
            id, platform, canonical_url, handle, display_name, primary_language, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, handle) DO UPDATE SET
            canonical_url=excluded.canonical_url,
            display_name=COALESCE(excluded.display_name, profiles.display_name),
            primary_language=COALESCE(excluded.primary_language, profiles.primary_language)
        """,
        (
            identity["id"],
            identity["platform"],
            identity["canonical_url"],
            identity["handle"],
            display_name,
            primary_language,
            utc_now(),
        ),
    )
    con.commit()
    return identity


def start_profile_run(
    con: sqlite3.Connection,
    *,
    profile_id: str,
    mode: str,
    evidence_mode: str,
    requested_posts: int,
    objective: str,
    top_k: int,
    config: dict[str, Any],
) -> str:
    if mode not in {"quick", "deep"}:
        raise ValueError("mode must be 'quick' or 'deep'")
    if evidence_mode not in {"public", "owned", "local"}:
        raise ValueError("evidence_mode must be public, owned, or local")
    run_id = f"run_{uuid.uuid4().hex}"
    con.execute(
        """
        INSERT INTO profile_runs(
            id, profile_id, mode, evidence_mode, requested_posts, selected_objective,
            top_k, started_at, status, config_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
        """,
        (
            run_id,
            profile_id,
            mode,
            evidence_mode,
            requested_posts,
            objective,
            top_k,
            utc_now(),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
        ),
    )
    con.commit()
    return run_id


def finish_profile_run(
    con: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    coverage: dict[str, Any],
) -> None:
    con.execute(
        """
        UPDATE profile_runs
        SET status=?, completed_at=?, coverage_json=?
        WHERE id=?
        """,
        (status, utc_now(), json.dumps(coverage, ensure_ascii=False, sort_keys=True), run_id),
    )
    con.execute(
        """
        UPDATE profiles
        SET last_inventoried_at=?
        WHERE id=(SELECT profile_id FROM profile_runs WHERE id=?)
        """,
        (utc_now(), run_id),
    )
    con.commit()


def associate_piece(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    profile_id: str,
    run_id: str,
    canonical_url: str,
    media_type: str | None,
    platform_content_id: str | None = None,
    inventory_position: int | None = None,
) -> None:
    con.execute(
        """
        UPDATE pieces
        SET profile_id=?, inventory_run_id=?, canonical_url=?, media_type=?,
            platform_content_id=COALESCE(?, platform_content_id)
        WHERE id=?
        """,
        (profile_id, run_id, canonical_url, media_type, platform_content_id, piece_id),
    )
    piece = con.execute("SELECT * FROM pieces WHERE id=?", (piece_id,)).fetchone()
    if not piece:
        raise ValueError(f"piece does not exist: {piece_id}")
    snapshot = {key: piece[key] for key in piece.keys()}
    snapshot["_text_artifacts"] = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM text_artifacts WHERE piece_id=? ORDER BY id",
            (piece_id,),
        ).fetchall()
    ]
    snapshot["_visual_text_blocks"] = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM visual_text_blocks WHERE piece_id=? ORDER BY id",
            (piece_id,),
        ).fetchall()
    ]
    con.execute(
        """
        INSERT INTO profile_run_pieces(
            profile_run_id, piece_id, inventory_position, observed_at, snapshot_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(profile_run_id, piece_id) DO UPDATE SET
            inventory_position=COALESCE(excluded.inventory_position, profile_run_pieces.inventory_position),
            observed_at=excluded.observed_at,
            snapshot_json=excluded.snapshot_json
        """,
        (
            run_id,
            piece_id,
            inventory_position,
            utc_now(),
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        ),
    )
    con.commit()


def metric_unit(metric_name: str) -> str:
    if metric_name in RATE_METRICS:
        return "ratio"
    if metric_name in TIME_METRICS:
        return "seconds"
    return "count"


def store_metric_observations(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    metrics: dict[str, float | int],
    source: str,
    captured_at: str,
    confidence: str | None,
    basis_by_metric: dict[str, str] | None = None,
    unit_by_metric: dict[str, str] | None = None,
    denominator_by_metric: dict[str, str | None] | None = None,
    raw: dict[str, Any] | None = None,
) -> int:
    basis_by_metric = basis_by_metric or {}
    unit_by_metric = unit_by_metric or {}
    denominator_by_metric = denominator_by_metric or {}
    inserted = 0
    for name, value in sorted(metrics.items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        before = con.total_changes
        con.execute(
            """
            INSERT OR IGNORE INTO metric_observations(
                piece_id, metric_name, metric_value, metric_unit, denominator_name,
                metric_basis, source, confidence, captured_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                piece_id,
                name,
                float(value),
                unit_by_metric.get(name) or metric_unit(name),
                denominator_by_metric.get(name),
                basis_by_metric.get(name) or name,
                source,
                confidence,
                captured_at,
                json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw is not None else None,
            ),
        )
        if con.total_changes > before:
            inserted += 1
    con.execute(
        "UPDATE pieces SET latest_metric_snapshot_at=? WHERE id=?",
        (captured_at, piece_id),
    )
    con.commit()
    return inserted


def latest_objective_rows(
    con: sqlite3.Connection,
    *,
    profile_id: str,
    objective: str,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT mo.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY mo.piece_id, mo.metric_name, mo.metric_unit,
                                    COALESCE(mo.denominator_name, ''), mo.metric_basis
                       ORDER BY mo.captured_at DESC, mo.id DESC
                   ) AS recency_rank
            FROM metric_observations mo
            JOIN pieces p ON p.id=mo.piece_id
            WHERE p.profile_id=? AND mo.metric_name=?
        )
        SELECT p.*, ranked.metric_name AS objective_name,
               ranked.metric_value AS objective_value,
               ranked.metric_unit AS objective_unit,
               ranked.denominator_name AS objective_denominator,
               ranked.metric_basis AS objective_basis,
               ranked.source AS objective_source,
               ranked.confidence AS objective_confidence,
               ranked.captured_at AS objective_captured_at
        FROM ranked
        JOIN pieces p ON p.id=ranked.piece_id
        WHERE ranked.recency_rank=1
        ORDER BY ranked.metric_value DESC, p.id
        """,
        (profile_id, objective),
    ).fetchall()
    return [dict(row) for row in rows]
