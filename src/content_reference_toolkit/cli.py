from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from .analytics import analyze_winners
from .evidence import persist_piece_evidence
from .migrations import run_migrations
from .profile_pipeline import build_profile_report, write_profile_report
from .profiles import (
    associate_piece,
    finish_profile_run,
    profile_identity,
    start_profile_run,
    store_metric_observations,
    upsert_profile,
)
from .semantic import OllamaSemanticAnalyzer, RuleBasedSemanticAnalyzer


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
DEFAULT_OUTPUT_ROOT = Path("references")
METRIC_COLUMNS = {
    "views", "impressions", "reach", "plays",
    "likes", "reactions", "comments", "replies", "shares", "reposts",
    "saves", "bookmarks", "clicks", "profile_clicks",
    "avg_watch_time", "watched_full_video_pct", "completion_rate",
    "retention_rate", "engagement_rate",
}
CSV_COLUMN_ALIASES = {
    "post url": "url",
    "permalink": "url",
    "link": "url",
    "content link": "url",
    "date": "published_at",
    "publish date": "published_at",
    "published date": "published_at",
    "published time": "published_at",
    "account": "creator",
    "author": "creator",
    "handle": "creator",
    "profile url": "profile_url",
    "account url": "profile_url",
    "impression": "impressions",
    "impressions": "impressions",
    "views": "views",
    "video views": "views",
    "plays": "plays",
    "reach": "reach",
    "likes": "likes",
    "like count": "likes",
    "reactions": "reactions",
    "reaction count": "reactions",
    "comments": "comments",
    "comment count": "comments",
    "replies": "replies",
    "reply count": "replies",
    "shares": "shares",
    "share count": "shares",
    "reposts": "reposts",
    "repost count": "reposts",
    "retweets": "reposts",
    "saves": "saves",
    "save count": "saves",
    "bookmarks": "bookmarks",
    "bookmark count": "bookmarks",
    "clicks": "clicks",
    "link clicks": "clicks",
    "profile clicks": "profile_clicks",
    "profile visits": "profile_clicks",
    "average watch time": "avg_watch_time",
    "avg watch time": "avg_watch_time",
    "avg_watch_time": "avg_watch_time",
    "watched full video": "watched_full_video_pct",
    "watched full video %": "watched_full_video_pct",
    "full video watch rate": "watched_full_video_pct",
    "completion rate": "completion_rate",
    "completion rate %": "completion_rate",
    "retention rate": "retention_rate",
    "retention rate %": "retention_rate",
    "average percentage watched": "retention_rate",
    "avg % watched": "retention_rate",
    "engagement rate": "engagement_rate",
    "engagement rate %": "engagement_rate",
    "hook": "hook_onscreen",
    "visual hook": "hook_onscreen",
    "onscreen hook": "hook_onscreen",
    "on screen hook": "hook_onscreen",
    "spoken hook": "hook_spoken",
    "cta": "cta_spoken",
    "spoken cta": "cta_spoken",
}
PRIMARY_METRIC_BY_PLATFORM = {
    "x": ("comments", ("comments", "replies")),
    "twitter": ("comments", ("comments", "replies")),
    "linkedin": ("engagement_rate", ("engagement_rate",)),
    "tiktok": ("completion_rate", ("completion_rate", "watched_full_video_pct")),
    "instagram": ("retention_rate", ("retention_rate",)),
}
METRIC_TEMPLATES = {
    "x": [
        "url", "platform", "published_at", "creator", "impressions", "likes",
        "replies", "reposts", "bookmarks", "profile_clicks", "hook_onscreen",
        "cta_spoken", "notes",
    ],
    "twitter": [
        "url", "platform", "published_at", "creator", "impressions", "likes",
        "replies", "reposts", "bookmarks", "profile_clicks", "hook_onscreen",
        "cta_spoken", "notes",
    ],
    "linkedin": [
        "url", "platform", "published_at", "creator", "impressions",
        "reactions", "comments", "reposts", "clicks", "engagement_rate",
        "hook_onscreen", "cta_spoken", "notes",
    ],
    "tiktok": [
        "url", "platform", "published_at", "creator", "views", "likes",
        "comments", "shares", "avg_watch_time", "watched_full_video_pct",
        "completion_rate", "hook_onscreen", "hook_spoken", "cta_spoken", "notes",
    ],
    "instagram": [
        "url", "platform", "published_at", "creator", "reach", "plays",
        "likes", "comments", "shares", "saves", "retention_rate",
        "hook_onscreen", "hook_spoken", "cta_spoken", "notes",
    ],
    "youtube": [
        "url", "platform", "published_at", "creator", "views", "likes",
        "comments", "shares", "avg_watch_time", "retention_rate",
        "hook_onscreen", "hook_spoken", "cta_spoken", "notes",
    ],
}
DEFAULT_GRAPH_INSIGHT_METRICS = {
    "instagram": "reach,plays,saved,shares,total_interactions,likes,comments",
    "facebook": "post_impressions,post_engaged_users,post_clicks",
    "threads": "views,likes,replies,reposts,quotes,shares",
}
CONNECTORS = {
    "youtube": {
        "modes": {
            "api": {"command": "fetch-metrics --platform youtube", "requires": ["YOUTUBE_API_KEY"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform youtube", "requires": ["yt-dlp"], "quality": "best_effort"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["YouTube Studio export"], "quality": "good"},
            "manual": {"command": "metric-template youtube", "requires": [], "quality": "manual"},
        },
    },
    "x": {
        "modes": {
            "api": {"command": "fetch-metrics --platform x", "requires": ["X_BEARER_TOKEN"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform x", "requires": ["yt-dlp", "optional cookies.txt"], "quality": "best_effort"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["X analytics export"], "quality": "good"},
            "manual": {"command": "metric-template x", "requires": [], "quality": "manual"},
        },
    },
    "linkedin": {
        "modes": {
            "api": {"command": "fetch-metrics --platform linkedin", "requires": ["LINKEDIN_ACCESS_TOKEN"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform linkedin", "requires": ["yt-dlp if supported", "optional cookies.txt"], "quality": "fragile"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["LinkedIn analytics export"], "quality": "good"},
            "manual": {"command": "metric-template linkedin", "requires": [], "quality": "manual"},
        },
    },
    "tiktok": {
        "modes": {
            "scrape": {"command": "scrape-metrics --platform tiktok", "requires": ["yt-dlp", "optional cookies.txt"], "quality": "best_effort"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["TikTok analytics export"], "quality": "best"},
            "manual": {"command": "metric-template tiktok", "requires": [], "quality": "manual"},
        },
    },
    "instagram": {
        "modes": {
            "api": {"command": "fetch-metrics --platform instagram", "requires": ["META_ACCESS_TOKEN", "media ID"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform instagram", "requires": ["yt-dlp", "optional cookies.txt"], "quality": "best_effort"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["Instagram insights export"], "quality": "good"},
            "manual": {"command": "metric-template instagram", "requires": [], "quality": "manual"},
        },
    },
    "facebook": {
        "modes": {
            "api": {"command": "fetch-metrics --platform facebook", "requires": ["META_ACCESS_TOKEN", "object ID"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform facebook", "requires": ["yt-dlp if supported", "optional cookies.txt"], "quality": "fragile"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["Meta export"], "quality": "good"},
        },
    },
    "threads": {
        "modes": {
            "api": {"command": "fetch-metrics --platform threads", "requires": ["META_ACCESS_TOKEN", "object ID"], "quality": "best"},
            "scrape": {"command": "scrape-metrics --platform threads", "requires": ["yt-dlp if supported", "optional cookies.txt"], "quality": "fragile"},
            "export": {"command": "import-metrics --input metrics.csv", "requires": ["Threads/Meta export"], "quality": "good"},
        },
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tool(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def http_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc


def http_get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> str:
    request_headers = {"User-Agent": "content-reference-toolkit/0.1"}
    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            charset = "utf-8"
            match = re.search(r"charset=([^;]+)", content_type, re.I)
            if match:
                charset = match.group(1).strip()
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def social_platform_from_host(host: str) -> str | None:
    host = host.casefold().rstrip(".")

    def matches(domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    if matches("instagram.com"):
        return "instagram"
    if matches("tiktok.com"):
        return "tiktok"
    if matches("x.com") or matches("twitter.com"):
        return "x"
    if matches("linkedin.com"):
        return "linkedin"
    if matches("youtube.com") or matches("youtu.be"):
        return "youtube"
    if matches("threads.net"):
        return "threads"
    if matches("facebook.com") or matches("fb.watch"):
        return "facebook"
    return None


def is_social_content_url(source: str) -> bool:
    parsed = urlparse(source)
    path = parsed.path.lower()
    platform = social_platform_from_host(parsed.hostname or "")
    if platform == "instagram":
        return any(part in path for part in ("/p/", "/reel/", "/tv/"))
    if platform == "tiktok":
        return "/video/" in path
    if platform == "x":
        return bool(x_post_id(source))
    if platform == "linkedin":
        return "activity:" in source or "/feed/update/" in path or "/posts/" in path
    if platform == "youtube":
        return bool(youtube_video_id(source))
    if platform == "threads":
        return "/post/" in path or bool(re.search(r"/@[^/]+/post/", path))
    if platform == "facebook":
        return any(part in path for part in ("/posts/", "/videos/", "/reel/", "/watch/"))
    return False


def is_social_profile_url(source: str) -> bool:
    parsed = urlparse(source)
    platform = social_platform_from_host(parsed.hostname or "")
    if not platform or is_social_content_url(source):
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if platform == "instagram":
        return len(path.split("/")) == 1
    if platform == "tiktok":
        return path.startswith("@") and "/video/" not in path
    if platform == "x":
        return len(path.split("/")) == 1 and path not in {"home", "explore", "search", "settings"}
    if platform == "linkedin":
        return path.startswith(("in/", "company/", "school/"))
    if platform == "youtube":
        return path.startswith(("@", "channel/", "c/", "user/"))
    if platform == "threads":
        return path.startswith("@") and "/post/" not in path
    if platform == "facebook":
        return True
    return False


def normalize_url(source: str) -> str:
    parsed = urlparse(source)
    cleaned = parsed._replace(fragment="")
    url = cleaned.geturl()
    return url.rstrip("/")


def discover_social_profiles(html: str, base_url: str) -> list[dict[str, str]]:
    parser = LinkParser()
    parser.feed(html)
    seen: set[str] = set()
    profiles: list[dict[str, str]] = []
    for href in parser.links:
        if href.startswith(("mailto:", "tel:", "#")):
            continue
        url = normalize_url(urljoin(base_url, href))
        if url in seen or not is_url(url) or not is_social_profile_url(url):
            continue
        platform = platform_for_source(url)
        seen.add(url)
        profiles.append({"platform": platform, "url": url})
    return profiles


def profile_prompt(profiles: list[dict[str, str]]) -> str:
    urls = ", ".join(f"{item['platform']}: {item['url']}" for item in profiles)
    return f"I found these profiles [{urls}], would you like to run content-analysis on all of them?"


def link_intake(source: str, *, fetch_html: Any = http_get_text) -> dict[str, Any]:
    if not is_url(source):
        return {"status": "error", "error": "source must be an http(s) URL", "source": source}
    platform = platform_for_source(source)
    if platform != "unknown":
        if is_social_profile_url(source):
            return {
                "status": "done",
                "kind": "social_profile",
                "platform": platform,
                "url": normalize_url(source),
                "suggested_command": f"content-reference research-link {source}",
                "connector_options_command": f"content-reference connector-options {platform}",
            }
        return {
            "status": "done",
            "kind": "social_content",
            "platform": platform,
            "url": normalize_url(source),
            "suggested_command": f"content-reference scrape-metrics --platform {platform} {source}",
            "connector_options_command": f"content-reference connector-options {platform}",
        }

    html = fetch_html(source)
    profiles = discover_social_profiles(html, source)
    result: dict[str, Any] = {
        "status": "done",
        "kind": "website",
        "url": normalize_url(source),
        "profiles": profiles,
    }
    if profiles:
        result["prompt"] = profile_prompt(profiles)
        result["suggested_command"] = f"content-reference research-link {source} --all"
        result["profile_commands"] = [f"content-reference research-link {item['url']}" for item in profiles]
    else:
        result["prompt"] = "No social profiles found on this website."
    return result


def youtube_video_id(source: str) -> str | None:
    parsed = urlparse(source)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0] or None
    if "youtube.com" in host:
        if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            return parsed.path.strip("/").split("/")[1] if len(parsed.path.strip("/").split("/")) > 1 else None
        params = parse_qs(parsed.query)
        return params.get("v", [None])[0]
    return None


def x_post_id(source: str) -> str | None:
    match = re.search(r"/status(?:es)?/(\d+)", source)
    return match.group(1) if match else None


def linkedin_activity_urn(source: str) -> str | None:
    if source.startswith("urn:li:"):
        return source
    match = re.search(r"activity[:/-](\d+)", source)
    if match:
        return f"urn:li:activity:{match.group(1)}"
    return None


def url_hash(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def readable_slug(source: str) -> str:
    if is_url(source):
        parsed = urlparse(source)
        bits = [parsed.netloc.replace("www.", "").split(":")[0]]
        bits.extend(part for part in parsed.path.strip("/").split("/") if part)
        base = "-".join(bits) or "reference"
    else:
        base = Path(source).stem
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in base)
    return "-".join(part for part in cleaned.split("-") if part)[:72] or "reference"


def source_id(source: str) -> str:
    return url_hash(source)


def source_dir_name(source: str) -> str:
    return f"{readable_slug(source)}-{source_id(source)[:8]}"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path | None) -> str:
    return path.read_text(encoding="utf-8").strip() if path and path.exists() else ""


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace(" ", "")
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "")
        if not value:
            return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def canonical_column_name(value: str) -> str:
    normalized = " ".join(value.strip().replace("_", " ").split()).lower()
    return CSV_COLUMN_ALIASES.get(normalized, normalized.replace(" ", "_"))


def canonicalize_row(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        canonical = canonical_column_name(str(key))
        text = "" if value is None else str(value).strip()
        if canonical in out and out[canonical]:
            continue
        out[canonical] = text
    return out


def duration_bucket(format_: str, duration_sec: float | None) -> str:
    if format_ == "carousel":
        return "carousel"
    if duration_sec is None:
        return "unknown"
    if duration_sec <= 30:
        return "short"
    if duration_sec <= 60:
        return "mid"
    return "long"


def published_at_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    timestamp = parse_float(metadata.get("timestamp") or metadata.get("release_timestamp"))
    if timestamp is not None:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    upload_date = metadata.get("upload_date")
    if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return None


def platform_for_source(source: str) -> str:
    parsed = urlparse(source)
    platform = social_platform_from_host(parsed.hostname or "")
    if platform:
        return platform
    # A malformed HTTP(S) URL (for example one with userinfo host confusion)
    # is not a local filesystem path.
    if parsed.scheme in {"http", "https"}:
        return "unknown"
    return "local" if not is_url(source) else "unknown"


def infer_format(source: str, format_hint: str | None, media: Iterable[Path] = ()) -> str:
    if format_hint and format_hint != "unknown":
        return "tiktok" if format_hint == "tiktok" else format_hint
    if is_url(source):
        parsed = urlparse(source)
        platform = platform_for_source(source)
        if platform == "tiktok":
            return "tiktok"
        if platform == "youtube" and "/shorts/" in parsed.path:
            return "short"
        if platform == "x":
            return "post"
        if platform == "linkedin":
            return "post"
        if platform == "threads":
            return "post"
        if platform == "facebook":
            return "post"
        if "/reel/" in parsed.path:
            return "reel"
    media = list(media)
    if any(p.suffix.lower() in VIDEO_EXTENSIONS for p in media):
        return "reel"
    image_count = sum(p.suffix.lower() in IMAGE_EXTENSIONS for p in media)
    if image_count > 1:
        return "carousel"
    if image_count == 1:
        return "static"
    return "unknown"


def infer_format_from_metadata(source: str, metadata: dict[str, Any] | None) -> str:
    direct = infer_format(source, None)
    if direct != "unknown":
        return direct
    metadata = metadata or {}
    media_type = str(
        metadata.get("media_type")
        or metadata.get("post_type")
        or metadata.get("type")
        or ""
    ).casefold()
    child_count = parse_int(
        metadata.get("carousel_media_count")
        or metadata.get("media_count")
        or metadata.get("playlist_count")
    )
    entries = metadata.get("entries")
    if child_count is None and isinstance(entries, list):
        child_count = len(entries)
    if (child_count is not None and child_count > 1) or any(
        token in media_type for token in ("carousel", "sidecar", "album")
    ):
        return "carousel"
    if any(token in media_type for token in ("image", "photo", "static")):
        return "static"
    if metadata.get("duration") is not None or any(token in media_type for token in ("video", "reel")):
        return "reel"
    return "unknown"


def db_path(output_root: Path) -> Path:
    return output_root / "corpus.sqlite"


def connect(output_root: Path) -> sqlite3.Connection:
    output_root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(output_root))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    init_db(con)
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            platform TEXT,
            format_hint TEXT,
            notes TEXT,
            status_collect TEXT,
            status_extract TEXT,
            status_normalize TEXT,
            raw_dir TEXT,
            processed_dir TEXT,
            metadata_json TEXT,
            media_json TEXT,
            collected_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pieces (
            id TEXT PRIMARY KEY,
            source_url TEXT,
            platform TEXT,
            format TEXT,
            creator TEXT,
            published_at TEXT,
            duration_sec REAL,
            duration_bucket TEXT,
            caption TEXT,
            transcript TEXT,
            onscreen_text TEXT,
            slides TEXT,
            hook_spoken TEXT,
            hook_onscreen TEXT,
            cta_spoken TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            interaction_count INTEGER,
            engagement_rate REAL,
            engagement_basis TEXT,
            primary_metric_name TEXT,
            primary_metric_value REAL,
            primary_metric_basis TEXT,
            secondary_metrics TEXT,
            metric_source TEXT,
            metric_confidence TEXT,
            metrics_captured_at TEXT,
            notes TEXT,
            collected_at TEXT
        );

        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            source_url TEXT,
            stage TEXT,
            message TEXT,
            occurred_at TEXT
        );
        """
    )
    # Migrate pre-existing pieces tables that lack the newer metric columns.
    existing = {row["name"] for row in con.execute("PRAGMA table_info(pieces)")}
    for column, decl in (
        ("interaction_count", "INTEGER"),
        ("engagement_basis", "TEXT"),
        ("primary_metric_name", "TEXT"),
        ("primary_metric_value", "REAL"),
        ("primary_metric_basis", "TEXT"),
        ("secondary_metrics", "TEXT"),
        ("metric_source", "TEXT"),
        ("metric_confidence", "TEXT"),
        ("metrics_captured_at", "TEXT"),
    ):
        if column not in existing:
            con.execute(f"ALTER TABLE pieces ADD COLUMN {column} {decl}")
    con.commit()
    run_migrations(con)


def record_failure(
    con: sqlite3.Connection,
    *,
    source_id_: str,
    source_url: str,
    stage: str,
    message: str,
    profile_id: str | None = None,
    run_id: str | None = None,
    piece_id: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO failures(
            source_id, source_url, stage, message, occurred_at, profile_id, run_id, piece_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id_, source_url, stage, message, utc_now(), profile_id, run_id, piece_id),
    )
    con.commit()


def already_done(con: sqlite3.Connection, table: str, id_: str, status_column: str, *, force: bool) -> bool:
    if force:
        return False
    row = con.execute(f"SELECT {status_column} FROM {table} WHERE id = ?", (id_,)).fetchone()
    return bool(row and row[status_column] == "done")


def load_inputs(paths: list[Path], inline_sources: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in inline_sources:
        rows.append({"url": source, "format_hint": "", "notes": ""})
    for path in paths:
        if path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    url = (row.get("url") or "").strip()
                    if url:
                        rows.append({
                            "url": url,
                            "format_hint": (row.get("format_hint") or "").strip(),
                            "notes": (row.get("notes") or "").strip(),
                        })
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                url = line.strip()
                if url and not url.startswith("#"):
                    rows.append({"url": url, "format_hint": "", "notes": ""})
    return rows


def yt_dlp_metadata(source: str, cookies_file: Path | None) -> tuple[dict[str, Any] | None, str]:
    exe = tool("yt-dlp")
    if not exe:
        return None, "yt-dlp not found"
    cmd = [exe, "--skip-download", "--dump-single-json", "--no-warnings"]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(source)
    proc = run(cmd, timeout=90)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()
    try:
        return json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"yt-dlp returned invalid JSON: {exc}"


def yt_dlp_flat_entries(source: str, cookies_file: Path | None, *, limit: int) -> tuple[list[str], str]:
    exe = tool("yt-dlp")
    if not exe:
        return [], "yt-dlp not found"
    cmd = [exe, "--flat-playlist", "--dump-single-json", "--no-warnings", "--playlist-end", str(limit)]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(source)
    proc = run(cmd, timeout=180)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or proc.stdout.strip()
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"yt-dlp returned invalid JSON: {exc}"
    entries = data.get("entries") or []
    urls: list[str] = []
    for entry in entries[:limit]:
        # Flat extractors sometimes put an opaque media ID in ``url`` while
        # retaining the canonical public permalink in ``webpage_url``.
        url = entry.get("webpage_url") or entry.get("url")
        if not url:
            continue
        if is_url(url):
            urls.append(url)
        elif entry.get("ie_key") == "Youtube" or platform_for_source(source) == "youtube":
            urls.append(f"https://www.youtube.com/watch?v={url}")
        else:
            urls.append(str(url))
    return urls, ""


def gallery_dl_download(source: str, outdir: Path, cookies_file: Path | None) -> tuple[list[Path], str]:
    exe = tool("gallery-dl")
    if not exe:
        return [], "gallery-dl not found"
    cmd = [exe, "--write-metadata", "--write-info-json", "--directory", str(outdir)]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(source)
    proc = run(cmd, timeout=300)
    media = [p for p in outdir.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
    return sorted(media), proc.stderr.strip() if proc.returncode != 0 else ""


def yt_dlp_download(source: str, outdir: Path, cookies_file: Path | None) -> tuple[list[Path], str]:
    exe = tool("yt-dlp")
    if not exe:
        return [], "yt-dlp not found"
    before = {p for p in outdir.rglob("*") if p.is_file()}
    cmd = [
        exe,
        "--no-warnings",
        "--write-info-json",
        "--write-thumbnail",
        # Force a format that includes a usable audio track. Some platforms
        # (TikTok) serve an HEVC/h265 muxed stream as "best" whose audio is
        # dropped on download, leaving the transcript empty. Preferring h264
        # picks the muxed variant that actually carries aac audio.
        "-f",
        "bv*+ba/b",
        "-S",
        "vcodec:h264",
        "--merge-output-format",
        "mp4",
        "-o",
        str(outdir / "%(id)s.%(ext)s"),
    ]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(source)
    proc = run(cmd, timeout=420)
    after = {p for p in outdir.rglob("*") if p.is_file()}
    media = [p for p in sorted(after - before) if p.suffix.lower() in MEDIA_EXTENSIONS or p.name.endswith(".image")]
    if not media and proc.returncode == 0:
        media = [p for p in sorted(after) if p.suffix.lower() in MEDIA_EXTENSIONS or p.name.endswith(".image")]
    return media, proc.stderr.strip() if proc.returncode != 0 else ""


def copy_local_media(source: str, outdir: Path) -> tuple[list[Path], str]:
    path = Path(source).expanduser()
    if not path.exists():
        return [], f"local path not found: {path}"
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
    elif path.suffix.lower() in MEDIA_EXTENSIONS:
        files = [path]
    else:
        return [], f"unsupported local file type: {path}"
    outdir.mkdir(parents=True, exist_ok=True)
    copied = []
    for file in sorted(files):
        target = outdir / file.name
        if file.resolve() != target.resolve():
            shutil.copy2(file, target)
        copied.append(target)
    return copied, ""


def collect_source(
    con: sqlite3.Connection,
    *,
    output_root: Path,
    source: str,
    format_hint: str,
    notes: str,
    cookies_file: Path | None,
    delay: float,
    force: bool,
) -> dict[str, Any]:
    id_ = source_id(source)
    if already_done(con, "sources", id_, "status_collect", force=force):
        return {"id": id_, "source": source, "status": "skipped"}

    raw_dir = output_root / "raw" / source_dir_name(source)
    processed_dir = output_root / "processed" / source_dir_name(source)
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] | None = None
    media: list[Path] = []

    if is_url(source):
        metadata, meta_error = yt_dlp_metadata(source, cookies_file)
        if metadata:
            write_json(raw_dir / "metadata.yt-dlp.json", metadata)
        if meta_error:
            record_failure(con, source_id_=id_, source_url=source, stage="collect", message=f"metadata: {meta_error}")

        format_ = infer_format(source, format_hint)
        if format_ in {"carousel", "static"} or (
            platform_for_source(source) == "instagram" and "/p/" in urlparse(source).path
        ):
            media, dl_error = gallery_dl_download(source, raw_dir, cookies_file)
            if dl_error:
                record_failure(con, source_id_=id_, source_url=source, stage="collect", message=f"gallery-dl: {dl_error}")
        else:
            media, dl_error = yt_dlp_download(source, raw_dir, cookies_file)
            if dl_error:
                record_failure(con, source_id_=id_, source_url=source, stage="collect", message=f"yt-dlp: {dl_error}")
    else:
        media, local_error = copy_local_media(source, raw_dir)
        if local_error:
            record_failure(con, source_id_=id_, source_url=source, stage="collect", message=local_error)

    media_json = [rel(p, output_root) for p in sorted(set(media))]
    collection_status = "done" if media_json else "failed"
    if not media_json:
        record_failure(
            con,
            source_id_=id_,
            source_url=source,
            stage="collect",
            message="no media was downloaded or copied; existing evidence was preserved",
        )
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, notes, status_collect, status_extract,
            status_normalize, raw_dir, processed_dir, metadata_json, media_json, collected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,
            platform=excluded.platform,
            format_hint=excluded.format_hint,
            notes=excluded.notes,
            status_collect=CASE
                WHEN excluded.status_collect='done' THEN 'done'
                WHEN sources.status_collect='done' THEN 'done'
                ELSE excluded.status_collect
            END,
            raw_dir=excluded.raw_dir,
            processed_dir=excluded.processed_dir,
            metadata_json=COALESCE(excluded.metadata_json, sources.metadata_json),
            media_json=CASE
                WHEN excluded.media_json IS NOT NULL AND excluded.media_json<>'[]'
                    THEN excluded.media_json
                ELSE sources.media_json
            END,
            collected_at=CASE
                WHEN excluded.status_collect='done' THEN excluded.collected_at
                ELSE sources.collected_at
            END
        """,
        (
            id_,
            source,
            platform_for_source(source),
            format_hint,
            notes,
            collection_status,
            rel(raw_dir, output_root),
            rel(processed_dir, output_root),
            json_dumps(metadata) if metadata is not None else None,
            json_dumps(media_json),
            utc_now(),
        ),
    )
    con.commit()
    if delay:
        time.sleep(delay)
    return {"id": id_, "source": source, "status": collection_status, "media": len(media)}


def extract_audio(video: Path, outdir: Path) -> tuple[Path | None, str]:
    exe = tool("ffmpeg")
    if not exe:
        return None, "ffmpeg not found"
    outdir.mkdir(parents=True, exist_ok=True)
    audio = outdir / f"{video.stem}.wav"
    proc = run([exe, "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)], timeout=240)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return audio, ""


def transcribe_faster_whisper(audio: Path, outdir: Path, *, language: str, model: str) -> tuple[list[dict[str, Any]], str]:
    code = (
        "import json, sys\n"
        "from faster_whisper import WhisperModel\n"
        "audio, out, model_name, lang = sys.argv[1:5]\n"
        "model = WhisperModel(model_name, device='auto', compute_type='auto')\n"
        "segments, _ = model.transcribe(audio, language=lang, vad_filter=True, word_timestamps=True)\n"
        "rows = []\n"
        "for s in segments:\n"
        "    words = [{'start': round(w.start, 3), 'end': round(w.end, 3), 'word': w.word.strip(), 'probability': round(w.probability, 4)} for w in (s.words or []) if w.word.strip()]\n"
        "    rows.append({'start': round(s.start, 3), 'end': round(s.end, 3), 'text': s.text.strip(), 'words': words})\n"
        "open(out, 'w', encoding='utf-8').write(json.dumps(rows, ensure_ascii=False, indent=2) + '\\n')\n"
    )
    out = outdir / f"{audio.stem}.transcript.json"
    proc = run([sys.executable, "-c", code, str(audio), str(out), model, language], timeout=1800)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or proc.stdout.strip()
    return read_json(out), ""


def transcribe_openai_whisper(audio: Path, outdir: Path, *, language: str, model: str) -> tuple[list[dict[str, Any]], str]:
    exe = tool("whisper")
    if not exe:
        return [], "faster-whisper and whisper CLI not found"
    proc = run(
        [
            exe,
            str(audio),
            "--language",
            language,
            "--model",
            model,
            "--output_format",
            "json",
            "--word_timestamps",
            "True",
            "--output_dir",
            str(outdir),
        ],
        timeout=1800,
    )
    raw = outdir / f"{audio.stem}.json"
    if proc.returncode != 0:
        return [], proc.stderr.strip() or proc.stdout.strip()
    if not raw.exists():
        return [], "whisper did not produce json output"
    data = read_json(raw)
    rows = [
        {
            "start": round(float(s.get("start", 0)), 3),
            "end": round(float(s.get("end", 0)), 3),
            "text": str(s.get("text", "")).strip(),
            "words": [
                {
                    "start": round(float(word.get("start", 0)), 3),
                    "end": round(float(word.get("end", 0)), 3),
                    "word": str(word.get("word", "")).strip(),
                    "probability": parse_float(word.get("probability")),
                }
                for word in s.get("words", [])
                if str(word.get("word", "")).strip()
            ],
        }
        for s in data.get("segments", [])
        if str(s.get("text", "")).strip()
    ]
    write_json(outdir / f"{audio.stem}.transcript.json", rows)
    return rows, ""


def transcribe(audio: Path, outdir: Path, *, language: str, model: str) -> tuple[list[dict[str, Any]], str]:
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return transcribe_openai_whisper(audio, outdir, language=language, model=model)
    return transcribe_faster_whisper(audio, outdir, language=language, model=model)


def scene_timestamps(video: Path, *, fallback_every_seconds: int) -> list[float]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        return []
    try:
        video_stream = open_video(str(video))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector())
        scene_manager.detect_scenes(video_stream)
        scenes = scene_manager.get_scene_list()
    except Exception:
        return []
    stamps = [round(scene[0].get_seconds(), 3) for scene in scenes]
    return sorted(set(stamps)) or [0.0]


def fallback_frame_timestamps(video: Path, every_seconds: int) -> list[float]:
    exe = tool("ffprobe")
    duration = None
    if exe:
        proc = run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)], timeout=60)
        duration = parse_float(proc.stdout.strip()) if proc.returncode == 0 else None
    if not duration:
        return [0.0]
    stamps = []
    cur = 0.0
    while cur <= duration:
        stamps.append(round(cur, 3))
        cur += max(1, every_seconds)
    return stamps


def extract_frame_at(video: Path, outdir: Path, timestamp: float, index: int) -> tuple[Path | None, str]:
    exe = tool("ffmpeg")
    if not exe:
        return None, "ffmpeg not found"
    outdir.mkdir(parents=True, exist_ok=True)
    frame = outdir / f"{video.stem}_{index:04d}_{timestamp:.3f}.jpg"
    proc = run([exe, "-y", "-ss", str(timestamp), "-i", str(video), "-frames:v", "1", str(frame)], timeout=90)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return frame if frame.exists() else None, ""


def easyocr_image(image: Path, *, languages: list[str], min_confidence: float) -> tuple[list[dict[str, Any]], str]:
    code = (
        "import json, sys\n"
        "import easyocr\n"
        "from PIL import Image\n"
        "image = sys.argv[1]\n"
        "langs = sys.argv[2].split(',')\n"
        "reader = easyocr.Reader(langs, gpu=False)\n"
        "image_width, image_height = Image.open(image).size\n"
        "rows = []\n"
        "for bbox, text, conf in reader.readtext(image):\n"
        "    xs = [float(point[0]) for point in bbox]\n"
        "    ys = [float(point[1]) for point in bbox]\n"
        "    rows.append({'text': text, 'confidence': float(conf), 'x': min(xs), 'y': min(ys), 'width': max(xs)-min(xs), 'height': max(ys)-min(ys), 'image_width': image_width, 'image_height': image_height})\n"
        "print(json.dumps(rows, ensure_ascii=False))\n"
    )
    proc = run([sys.executable, "-c", code, str(image), ",".join(languages)], timeout=300)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or proc.stdout.strip()
    rows = json.loads(proc.stdout or "[]")
    return [row for row in rows if parse_float(row.get("confidence")) is not None and float(row["confidence"]) >= min_confidence], ""


# ISO 639-1 (and a few common aliases) to tesseract's 639-2/T language codes.
TESSERACT_LANG_MAP = {
    "es": "spa", "spa": "spa",
    "pt": "por", "por": "por",
    "en": "eng", "eng": "eng",
    "fr": "fra", "fra": "fra",
    "it": "ita", "ita": "ita",
    "de": "deu", "deu": "deu",
}


def tesseract_languages(languages: list[str]) -> str:
    """Map requested languages to installed tesseract packs, falling back to eng."""
    exe = tool("tesseract")
    available: set[str] = set()
    if exe:
        proc = run([exe, "--list-langs"], timeout=30)
        if proc.returncode == 0:
            available = {line.strip() for line in proc.stdout.splitlines()[1:] if line.strip()}
    codes: list[str] = []
    for lang in languages:
        code = TESSERACT_LANG_MAP.get(lang.strip().lower(), lang.strip().lower())
        if code and code not in codes and (not available or code in available):
            codes.append(code)
    if not codes:
        codes = ["eng"]
    return "+".join(codes)


def tesseract_image(image: Path, *, languages: list[str], min_confidence: float) -> tuple[list[dict[str, Any]], str]:
    exe = tool("tesseract")
    if not exe:
        return [], "easyocr and tesseract not found"
    langs = tesseract_languages(languages)
    proc = run([exe, str(image), "stdout", "-l", langs, "tsv"], timeout=120)
    if proc.returncode != 0:
        return [], proc.stderr.strip()
    # TSV columns: level page block par line word left top width height conf text
    lines: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for raw in proc.stdout.splitlines()[1:]:
        cols = raw.split("\t")
        if len(cols) < 12:
            continue
        text = cols[11].strip()
        conf = parse_float(cols[10])
        if not text or conf is None or conf < 0:
            continue
        key = (cols[2], cols[3], cols[4])
        if key not in lines:
            lines[key] = {"words": [], "confs": [], "left": [], "top": [], "right": [], "bottom": []}
            order.append(key)
        lines[key]["words"].append(text)
        lines[key]["confs"].append(conf / 100.0)
        left = parse_int(cols[6]) or 0
        top = parse_int(cols[7]) or 0
        width = parse_int(cols[8]) or 0
        height = parse_int(cols[9]) or 0
        lines[key]["left"].append(left)
        lines[key]["top"].append(top)
        lines[key]["right"].append(left + width)
        lines[key]["bottom"].append(top + height)
    rows: list[dict[str, Any]] = []
    for key in order:
        bucket = lines[key]
        if not bucket["words"]:
            continue
        line_conf = sum(bucket["confs"]) / len(bucket["confs"])
        if line_conf < min_confidence:
            continue
        left = min(bucket["left"])
        top = min(bucket["top"])
        rows.append({
            "text": " ".join(bucket["words"]),
            "confidence": round(line_conf, 3),
            "x": left,
            "y": top,
            "width": max(bucket["right"]) - left,
            "height": max(bucket["bottom"]) - top,
            "image_width": None,
            "image_height": None,
        })
    return rows, ""


def ocr_image(image: Path, *, languages: list[str], min_confidence: float) -> tuple[list[dict[str, Any]], str]:
    try:
        import easyocr  # noqa: F401
    except ImportError:
        return tesseract_image(image, languages=languages, min_confidence=min_confidence)
    return easyocr_image(image, languages=languages, min_confidence=min_confidence)


def similarity(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio() * 100
    return float(fuzz.ratio(a, b))


def dedupe_onscreen(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        text = " ".join(str(row.get("text", "")).split())
        if not text:
            continue
        if kept and similarity(kept[-1]["text"], text) >= threshold:
            continue
        kept.append({
            "timestamp": row["timestamp"],
            "text": text,
            "confidence": row.get("confidence"),
            "blocks": row.get("blocks") or [],
        })
    return kept


def extract_video(
    *,
    media: list[Path],
    processed_dir: Path,
    languages: list[str],
    whisper_language: str,
    whisper_model: str,
    frame_every_seconds: int,
    min_confidence: float,
    dedupe_threshold: float,
    use_scene_detect: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float], list[str]]:
    errors: list[str] = []
    transcript: list[dict[str, Any]] = []
    onscreen: list[dict[str, Any]] = []
    scene_changes: list[float] = []
    for file in media:
        if file.suffix.lower() in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            audio = file
            if file.suffix.lower() in VIDEO_EXTENSIONS:
                audio_out, audio_error = extract_audio(file, processed_dir / "audio")
                if audio_error:
                    errors.append(f"audio {file.name}: {audio_error}")
                if audio_out:
                    audio = audio_out
            if audio.exists():
                rows, tr_error = transcribe(audio, processed_dir / "transcript", language=whisper_language, model=whisper_model)
                transcript.extend(rows)
                if tr_error:
                    errors.append(f"transcribe {file.name}: {tr_error}")

        if file.suffix.lower() in VIDEO_EXTENSIONS:
            timestamps = scene_timestamps(file, fallback_every_seconds=frame_every_seconds) if use_scene_detect else []
            if timestamps:
                # SceneDetect returns scene starts. The initial 0s boundary is not
                # a change, and fallback sampling must never be labeled cadence.
                scene_changes.extend(stamp for stamp in timestamps if stamp > 0)
            if not timestamps:
                timestamps = fallback_frame_timestamps(file, frame_every_seconds)
            for idx, stamp in enumerate(timestamps):
                frame, frame_error = extract_frame_at(file, processed_dir / "frames", stamp, idx)
                if frame_error:
                    errors.append(f"frame {file.name}@{stamp}: {frame_error}")
                    continue
                if not frame:
                    continue
                ocr_rows, ocr_error = ocr_image(frame, languages=languages, min_confidence=min_confidence)
                if ocr_error:
                    errors.append(f"ocr {frame.name}: {ocr_error}")
                text = " ".join(row["text"] for row in ocr_rows if row.get("text")).strip()
                if text:
                    confs = [float(row["confidence"]) for row in ocr_rows if row.get("confidence") is not None]
                    onscreen.append({
                        "timestamp": stamp,
                        "text": text,
                        "confidence": (sum(confs) / len(confs)) if confs else None,
                        "blocks": ocr_rows,
                    })
    return (
        transcript,
        dedupe_onscreen(sorted(onscreen, key=lambda r: r["timestamp"]), dedupe_threshold),
        sorted(set(scene_changes)),
        errors,
    )


def extract_carousel(*, media: list[Path], processed_dir: Path, languages: list[str], min_confidence: float) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    slides: list[dict[str, Any]] = []
    images = [p for p in sorted(media) if p.suffix.lower() in IMAGE_EXTENSIONS]
    for idx, image in enumerate(images, start=1):
        rows, ocr_error = ocr_image(image, languages=languages, min_confidence=min_confidence)
        if ocr_error:
            errors.append(f"ocr slide {idx} {image.name}: {ocr_error}")
        text = " ".join(row["text"] for row in rows if row.get("text")).strip()
        slides.append({
            "slide_index": idx,
            "text": text,
            "confidence": max([float(r.get("confidence", 0)) for r in rows], default=None),
            "blocks": rows,
        })
    return slides, errors


def extract_source(
    con: sqlite3.Connection,
    *,
    output_root: Path,
    row: sqlite3.Row,
    languages: list[str],
    whisper_language: str,
    whisper_model: str,
    frame_every_seconds: int,
    min_confidence: float,
    dedupe_threshold: float,
    use_scene_detect: bool,
    force: bool,
) -> dict[str, Any]:
    id_ = row["id"]
    source = row["source_url"]
    if already_done(con, "sources", id_, "status_extract", force=force):
        return {"id": id_, "source": source, "status": "skipped"}
    media = [output_root / p for p in (json_loads(row["media_json"]) or [])]
    media = [path for path in media if path.exists()]
    if not media:
        message = "no collected media available; extraction skipped to preserve existing evidence"
        record_failure(con, source_id_=id_, source_url=source, stage="extract", message=message)
        con.execute("UPDATE sources SET status_extract='failed' WHERE id=?", (id_,))
        con.commit()
        return {"id": id_, "source": source, "status": "failed", "error": message}
    processed_dir = output_root / row["processed_dir"]
    format_ = infer_format(source, row["format_hint"], media)
    errors: list[str] = []
    transcript: list[dict[str, Any]] | None = None
    onscreen: list[dict[str, Any]] | None = None
    slides: list[dict[str, Any]] | None = None
    scene_changes: list[float] | None = None

    if format_ in {"carousel", "static"}:
        slides, errors = extract_carousel(media=media, processed_dir=processed_dir, languages=languages, min_confidence=min_confidence)
    else:
        transcript, onscreen, scene_changes, errors = extract_video(
            media=media,
            processed_dir=processed_dir,
            languages=languages,
            whisper_language=whisper_language,
            whisper_model=whisper_model,
            frame_every_seconds=frame_every_seconds,
            min_confidence=min_confidence,
            dedupe_threshold=dedupe_threshold,
            use_scene_detect=use_scene_detect,
        )

    transcript_words = [
        word
        for segment in (transcript or [])
        for word in (segment.get("words") or [])
    ]
    artifact = {
        "format": format_,
        "transcript": transcript,
        "transcript_words": transcript_words,
        "onscreen_text": onscreen,
        "slides": slides,
        "scene_changes": scene_changes,
        "extracted_at": utc_now(),
    }
    write_json(processed_dir / "extracted.json", artifact)
    for error in errors:
        record_failure(con, source_id_=id_, source_url=source, stage="extract", message=error)
    con.execute("UPDATE sources SET status_extract='done' WHERE id=?", (id_,))
    con.commit()
    return {"id": id_, "source": source, "status": "done", "errors": len(errors)}


def metadata_value(metadata: dict[str, Any] | None, *keys: str) -> Any:
    if not metadata:
        return None
    for key in keys:
        if metadata.get(key) is not None:
            return metadata.get(key)
    return None


def normalize_metric_rate(value: float | None) -> float | None:
    if value is None:
        return None
    # User-facing exports often use either 7.5 or 0.075 for 7.5%.
    return value / 100 if value > 1 else value


def numeric_metrics(values: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key in METRIC_COLUMNS:
        value = parse_float(values.get(key))
        if value is None:
            continue
        if key in {"engagement_rate", "completion_rate", "retention_rate", "watched_full_video_pct"}:
            value = normalize_metric_rate(value)
            if value is None or not 0 <= value <= 1:
                continue
        elif value < 0:
            continue
        metrics[key] = value
    return metrics


def derive_metric_summary(
    *,
    platform: str | None,
    metrics: dict[str, float],
    interaction_count: int | None,
    engagement_rate: float | None,
    engagement_basis: str | None,
    explicit_primary_name: str | None = None,
    explicit_primary_value: float | None = None,
    metric_source: str | None = None,
) -> dict[str, Any]:
    platform_key = (platform or "").lower()
    if engagement_rate is not None and "engagement_rate" not in metrics:
        metrics["engagement_rate"] = engagement_rate
    if interaction_count is not None and "interaction_count" not in metrics:
        metrics["interaction_count"] = float(interaction_count)

    primary_name = explicit_primary_name or None
    primary_value = explicit_primary_value
    primary_basis = None
    confidence = "partial"

    if primary_name and primary_value is not None:
        primary_basis = "explicit"
        confidence = "native"
    else:
        desired = PRIMARY_METRIC_BY_PLATFORM.get(platform_key)
        if desired:
            primary_name = desired[0]
            for candidate in desired[1]:
                if candidate in metrics:
                    primary_value = metrics[candidate]
                    primary_basis = candidate
                    confidence = "native"
                    break

    if primary_value is None:
        if engagement_rate is not None:
            primary_name = primary_name or "engagement_rate"
            primary_value = engagement_rate
            primary_basis = engagement_basis or "derived"
            confidence = "derived"
        elif interaction_count is not None:
            primary_name = primary_name or "interaction_count"
            primary_value = float(interaction_count)
            primary_basis = "interactions"
            confidence = "derived"

    secondary = {key: value for key, value in sorted(metrics.items()) if key != primary_basis}
    return {
        "primary_metric_name": primary_name,
        "primary_metric_value": primary_value,
        "primary_metric_basis": primary_basis,
        "secondary_metrics": secondary or None,
        "metric_source": metric_source,
        "metric_confidence": confidence if primary_value is not None else None,
    }


def normalize_source(con: sqlite3.Connection, *, output_root: Path, row: sqlite3.Row, force: bool) -> dict[str, Any]:
    id_ = row["id"]
    source = row["source_url"]
    if not force and con.execute("SELECT id FROM pieces WHERE id=?", (id_,)).fetchone() and row["status_normalize"] == "done":
        return {"id": id_, "source": source, "status": "skipped"}
    processed_dir = output_root / row["processed_dir"]
    extracted_path = processed_dir / "extracted.json"
    if not extracted_path.exists():
        message = "missing extracted.json; run extract first"
        record_failure(con, source_id_=id_, source_url=source, stage="normalize", message=message)
        return {"id": id_, "source": source, "status": "failed", "error": message}

    metadata = json_loads(row["metadata_json"]) or {}
    extracted = read_json(extracted_path)
    format_ = extracted.get("format") or infer_format(source, row["format_hint"])
    duration_sec = parse_float(metadata_value(metadata, "duration"))
    transcript = extracted.get("transcript") or []
    onscreen = extracted.get("onscreen_text") or []
    slides = extracted.get("slides") or []
    transcript_json = (
        json_dumps(extracted.get("transcript"))
        if extracted.get("transcript") is not None
        else None
    )
    onscreen_json = (
        json_dumps(extracted.get("onscreen_text"))
        if extracted.get("onscreen_text") is not None
        else None
    )
    slides_json = (
        json_dumps(extracted.get("slides"))
        if extracted.get("slides") is not None
        else None
    )
    hook_spoken = " ".join(seg["text"] for seg in transcript if float(seg.get("start", 0)) <= 3).strip() or None
    hook_onscreen = (slides[0].get("text") if slides else onscreen[0].get("text") if onscreen else None)
    cta_spoken = transcript[-1].get("text") if transcript else None
    views = parse_int(metadata_value(metadata, "view_count", "play_count"))
    likes = parse_int(metadata_value(metadata, "like_count"))
    comments = parse_int(metadata_value(metadata, "comment_count"))
    shares = parse_int(metadata_value(metadata, "repost_count", "share_count"))
    present_interactions = [v for v in (likes, comments, shares) if v is not None]
    interaction_count = sum(present_interactions) if present_interactions else None
    engagement_rate = None
    engagement_basis = None
    if views and interaction_count is not None:
        engagement_rate = interaction_count / views
        engagement_basis = "views"
    elif interaction_count is not None:
        # Platforms like Instagram never expose views via yt-dlp. Record the raw
        # interaction total as the ranking signal instead of estimating views.
        engagement_basis = "interactions"
    metrics = numeric_metrics({
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": engagement_rate,
    })
    metric_summary = derive_metric_summary(
        platform=row["platform"],
        metrics=metrics,
        interaction_count=interaction_count,
        engagement_rate=engagement_rate,
        engagement_basis=engagement_basis,
        metric_source="metadata" if any(v is not None for v in (views, likes, comments, shares)) else None,
    )
    metrics_captured_at = utc_now() if metric_summary["metric_source"] else None

    con.execute(
        """
        INSERT INTO pieces(
            id, source_url, platform, format, creator, published_at, duration_sec,
            duration_bucket, caption, transcript, onscreen_text, slides, hook_spoken,
            hook_onscreen, cta_spoken, views, likes, comments, shares, interaction_count,
            engagement_rate, engagement_basis, primary_metric_name, primary_metric_value,
            primary_metric_basis, secondary_metrics, metric_source, metric_confidence,
            metrics_captured_at, notes, collected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,
            platform=excluded.platform,
            format=CASE
                WHEN excluded.format IS NULL OR excluded.format='unknown' THEN pieces.format
                ELSE excluded.format
            END,
            creator=COALESCE(excluded.creator, pieces.creator),
            published_at=COALESCE(excluded.published_at, pieces.published_at),
            duration_sec=COALESCE(excluded.duration_sec, pieces.duration_sec),
            duration_bucket=CASE
                WHEN excluded.duration_sec IS NULL THEN pieces.duration_bucket
                ELSE excluded.duration_bucket
            END,
            caption=COALESCE(excluded.caption, pieces.caption),
            transcript=COALESCE(excluded.transcript, pieces.transcript),
            onscreen_text=COALESCE(excluded.onscreen_text, pieces.onscreen_text),
            slides=COALESCE(excluded.slides, pieces.slides),
            hook_spoken=COALESCE(excluded.hook_spoken, pieces.hook_spoken),
            hook_onscreen=COALESCE(excluded.hook_onscreen, pieces.hook_onscreen),
            cta_spoken=COALESCE(excluded.cta_spoken, pieces.cta_spoken),
            views=COALESCE(excluded.views, pieces.views),
            likes=COALESCE(excluded.likes, pieces.likes),
            comments=COALESCE(excluded.comments, pieces.comments),
            shares=COALESCE(excluded.shares, pieces.shares),
            interaction_count=COALESCE(excluded.interaction_count, pieces.interaction_count),
            engagement_rate=COALESCE(excluded.engagement_rate, pieces.engagement_rate),
            engagement_basis=COALESCE(excluded.engagement_basis, pieces.engagement_basis),
            primary_metric_name=COALESCE(excluded.primary_metric_name, pieces.primary_metric_name),
            primary_metric_value=COALESCE(excluded.primary_metric_value, pieces.primary_metric_value),
            primary_metric_basis=COALESCE(excluded.primary_metric_basis, pieces.primary_metric_basis),
            secondary_metrics=COALESCE(excluded.secondary_metrics, pieces.secondary_metrics),
            metric_source=COALESCE(excluded.metric_source, pieces.metric_source),
            metric_confidence=COALESCE(excluded.metric_confidence, pieces.metric_confidence),
            metrics_captured_at=COALESCE(excluded.metrics_captured_at, pieces.metrics_captured_at),
            notes=excluded.notes,
            collected_at=excluded.collected_at
        """,
        (
            id_,
            source,
            row["platform"],
            format_,
            metadata_value(metadata, "uploader", "channel", "creator"),
            published_at_from_metadata(metadata),
            duration_sec,
            duration_bucket(format_, duration_sec),
            metadata_value(metadata, "description"),
            transcript_json,
            onscreen_json,
            slides_json,
            hook_spoken,
            hook_onscreen,
            cta_spoken,
            views,
            likes,
            comments,
            shares,
            interaction_count,
            engagement_rate,
            engagement_basis,
            metric_summary["primary_metric_name"],
            metric_summary["primary_metric_value"],
            metric_summary["primary_metric_basis"],
            json_dumps(metric_summary["secondary_metrics"]) if metric_summary["secondary_metrics"] else None,
            metric_summary["metric_source"],
            metric_summary["metric_confidence"],
            metrics_captured_at,
            row["notes"],
            row["collected_at"] or utc_now(),
        ),
    )
    con.execute(
        "UPDATE pieces SET scene_changes=COALESCE(?, scene_changes) WHERE id=?",
        (
            json_dumps(extracted.get("scene_changes"))
            if extracted.get("scene_changes") is not None
            else None,
            id_,
        ),
    )
    con.execute("UPDATE sources SET status_normalize='done' WHERE id=?", (id_,))
    con.commit()
    persisted_piece = con.execute("SELECT caption FROM pieces WHERE id=?", (id_,)).fetchone()
    persist_piece_evidence(
        con,
        piece_id=id_,
        caption=persisted_piece["caption"] if persisted_piece else metadata_value(metadata, "description"),
        extracted=extracted,
        source_path=rel(extracted_path, output_root),
    )
    if metric_summary["metric_source"] and metrics_captured_at:
        store_metric_observations(
            con,
            piece_id=id_,
            metrics=metrics,
            source=metric_summary["metric_source"],
            captured_at=metrics_captured_at,
            confidence=metric_summary["metric_confidence"],
            basis_by_metric={
                name: (
                    f"metadata:derived_{engagement_basis or 'unknown'}"
                    if name == "engagement_rate"
                    else "metadata:public_snapshot"
                )
                for name in metrics
            },
            unit_by_metric={
                name: "ratio" if name == "engagement_rate" else "count"
                for name in metrics
            },
            denominator_by_metric={"engagement_rate": engagement_basis},
        )
    return {"id": id_, "source": source, "status": "done"}


def piece_to_json(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("transcript", "onscreen_text", "slides", "scene_changes", "secondary_metrics"):
        out[key] = json_loads(out.get(key))
    return out


def export_jsonl(con: sqlite3.Connection, output_root: Path, out: Path | None) -> int:
    out = out or (output_root / "corpus.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT * FROM pieces ORDER BY collected_at, id").fetchall()
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(piece_to_json(row), ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def metric_sort_value(row: sqlite3.Row) -> float:
    primary = parse_float(row["primary_metric_value"])
    if primary is not None:
        return primary
    engagement = parse_float(row["engagement_rate"])
    if engagement is not None:
        return engagement
    interactions = parse_float(row["interaction_count"])
    if interactions is not None:
        return interactions
    return -1


def metric_report(con: sqlite3.Connection, *, platform: str | None = None, limit: int = 10) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if platform:
        where = "WHERE platform = ?"
        params.append(platform)
    rows = con.execute(
        f"""
        SELECT * FROM pieces
        {where}
        ORDER BY collected_at DESC, id
        """,
        params,
    ).fetchall()
    ranked = sorted(rows, key=metric_sort_value, reverse=True)[:limit]
    items = []
    for row in ranked:
        item = {
            "id": row["id"],
            "source_url": row["source_url"],
            "platform": row["platform"],
            "format": row["format"],
            "creator": row["creator"],
            "published_at": row["published_at"],
            "primary_metric_name": row["primary_metric_name"],
            "primary_metric_value": row["primary_metric_value"],
            "primary_metric_basis": row["primary_metric_basis"],
            "metric_source": row["metric_source"],
            "metric_confidence": row["metric_confidence"],
            "engagement_rate": row["engagement_rate"],
            "interaction_count": row["interaction_count"],
            "views": row["views"],
            "likes": row["likes"],
            "comments": row["comments"],
            "shares": row["shares"],
            "caption": row["caption"],
            "hook_spoken": row["hook_spoken"],
            "hook_onscreen": row["hook_onscreen"],
            "notes": row["notes"],
        }
        items.append(item)
    return {
        "status": "done",
        "platform": platform,
        "rows": len(items),
        "total_rows": len(rows),
        "ranked_by": "primary_metric_value, engagement_rate, interaction_count",
        "items": items,
    }


def coverage_report(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute("SELECT * FROM pieces ORDER BY platform, format, id").fetchall()
    platforms: dict[str, dict[str, Any]] = {}
    for row in rows:
        platform = row["platform"] or "unknown"
        bucket = platforms.setdefault(
            platform,
            {
                "platform": platform,
                "rows": 0,
                "formats": {},
                "with_primary_metric": 0,
                "with_native_primary_metric": 0,
                "missing_primary_metric": 0,
                "metric_sources": {},
            },
        )
        bucket["rows"] += 1
        format_ = row["format"] or "unknown"
        bucket["formats"][format_] = bucket["formats"].get(format_, 0) + 1
        metric_source = row["metric_source"] or "none"
        bucket["metric_sources"][metric_source] = bucket["metric_sources"].get(metric_source, 0) + 1
        if row["primary_metric_value"] is not None:
            bucket["with_primary_metric"] += 1
            if row["metric_confidence"] == "native":
                bucket["with_native_primary_metric"] += 1
        else:
            bucket["missing_primary_metric"] += 1

    platform_rows = sorted(platforms.values(), key=lambda item: (-item["rows"], item["platform"]))
    return {
        "status": "done",
        "total_rows": len(rows),
        "platforms": platform_rows,
    }


def compact_text(value: str | None, *, limit: int = 120) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "unknown"
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def analytics_bucket(rows: list[sqlite3.Row], key_fn: Any, *, min_count: int) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        bucket = buckets.setdefault(key, {"key": key, "count": 0, "values": [], "examples": []})
        bucket["count"] += 1
        value = metric_sort_value(row)
        if value >= 0:
            bucket["values"].append(value)
        if len(bucket["examples"]) < 3:
            bucket["examples"].append({
                "source_url": row["source_url"],
                "platform": row["platform"],
                "primary_metric_name": row["primary_metric_name"],
                "primary_metric_value": row["primary_metric_value"],
            })
    out = []
    for bucket in buckets.values():
        if bucket["count"] < min_count:
            continue
        out.append({
            "key": bucket["key"],
            "count": bucket["count"],
            "avg_metric": avg(bucket["values"]),
            "examples": bucket["examples"],
        })
    return sorted(out, key=lambda item: (item["avg_metric"] is not None, item["avg_metric"] or -1, item["count"]), reverse=True)


def analytics_report(con: sqlite3.Connection, *, platform: str | None = None, min_count: int = 1, limit: int = 10) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if platform:
        where = "WHERE platform = ?"
        params.append(platform)
    rows = con.execute(f"SELECT * FROM pieces {where} ORDER BY collected_at DESC, id", params).fetchall()
    usable = [row for row in rows if metric_sort_value(row) >= 0]
    return {
        "status": "done",
        "platform": platform,
        "total_rows": len(rows),
        "usable_rows": len(usable),
        "min_count": min_count,
        "ranked_by": "average primary metric, falling back to engagement/interactions",
        "formats": analytics_bucket(usable, lambda row: row["format"] or "unknown", min_count=min_count)[:limit],
        "hook_onscreen": analytics_bucket(usable, lambda row: compact_text(row["hook_onscreen"]), min_count=min_count)[:limit],
        "hook_spoken": analytics_bucket(usable, lambda row: compact_text(row["hook_spoken"]), min_count=min_count)[:limit],
        "cta_spoken": analytics_bucket(usable, lambda row: compact_text(row["cta_spoken"]), min_count=min_count)[:limit],
    }


def metric_template(platform: str, *, include_example: bool = True) -> str:
    platform_key = platform.lower()
    columns = METRIC_TEMPLATES.get(platform_key)
    if not columns:
        known = ", ".join(sorted(METRIC_TEMPLATES))
        raise ValueError(f"unknown platform '{platform}'. Known platforms: {known}")
    rows = [columns]
    if include_example:
        example = {column: "" for column in columns}
        example["url"] = f"https://example.com/{platform_key}/post"
        example["platform"] = "x" if platform_key == "twitter" else platform_key
        example["published_at"] = "2026-06-29"
        example["creator"] = "example_creator"
        example["hook_onscreen"] = "example opening hook"
        example["hook_spoken"] = "example spoken hook"
        example["cta_spoken"] = "example CTA"
        example["notes"] = "why this post matters"
        if platform_key in {"x", "twitter"}:
            example.update({"impressions": "1000", "likes": "25", "replies": "8", "reposts": "4", "bookmarks": "12"})
        elif platform_key == "linkedin":
            example.update({"impressions": "1000", "reactions": "40", "comments": "10", "reposts": "5", "clicks": "30", "engagement_rate": "5.5%"})
        elif platform_key == "tiktok":
            example.update({"views": "1000", "likes": "80", "comments": "12", "shares": "7", "avg_watch_time": "8.4", "watched_full_video_pct": "42%", "completion_rate": "42%"})
        elif platform_key == "instagram":
            example.update({"reach": "1000", "plays": "1400", "likes": "90", "comments": "8", "shares": "20", "saves": "15", "retention_rate": "38%"})
        elif platform_key == "youtube":
            example.update({"views": "1000", "likes": "70", "comments": "9", "shares": "4", "avg_watch_time": "12.5", "retention_rate": "45%"})
        rows.append([example[column] for column in columns])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(rows)
    return out.getvalue()


def connector_options(platform: str | None = None) -> dict[str, Any]:
    if platform:
        key = platform.lower()
        if key == "twitter":
            key = "x"
        if key not in CONNECTORS:
            return {"status": "error", "error": f"unknown platform: {platform}", "platforms": sorted(CONNECTORS)}
        return {"status": "done", "platform": key, "modes": CONNECTORS[key]["modes"]}
    return {"status": "done", "platforms": sorted(CONNECTORS), "connectors": CONNECTORS}


def setup_connector(platform: str, mode: str) -> dict[str, Any]:
    options = connector_options(platform)
    if options.get("status") != "done":
        return options
    modes = options["modes"]
    if mode not in modes:
        return {"status": "error", "error": f"unsupported mode '{mode}' for {options['platform']}", "available_modes": sorted(modes)}
    selected = modes[mode]
    return {
        "status": "done",
        "platform": options["platform"],
        "mode": mode,
        "quality": selected["quality"],
        "requires": selected["requires"],
        "command": selected["command"],
        "notes": "Secrets should be passed through env vars or flags; Statool does not store tokens or auto-extract browser cookies.",
    }


def metric_row_from_metadata(source: str, metadata: dict[str, Any], *, platform: str | None = None, metric_source: str = "scrape_yt_dlp") -> dict[str, str]:
    platform = platform or platform_for_source(source)
    row: dict[str, str] = {
        "url": source,
        "platform": platform,
        "format": infer_format_from_metadata(source, metadata),
        "creator": str(metadata_value(metadata, "uploader", "channel", "creator") or ""),
        "published_at": published_at_from_metadata(metadata) or "",
        "duration_sec": str(metadata_value(metadata, "duration") or ""),
        "caption": str(metadata_value(metadata, "description", "title") or ""),
        "views": str(metadata_value(metadata, "view_count", "play_count") or ""),
        "likes": str(metadata_value(metadata, "like_count") or ""),
        "comments": str(metadata_value(metadata, "comment_count") or ""),
        "shares": str(metadata_value(metadata, "repost_count", "share_count") or ""),
        "metric_source": metric_source,
    }
    return row


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def read_metric_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            normalized = canonicalize_row(row)
            source = (first_present(normalized, "source_url", "url") or "").strip()
            if source:
                rows.append(normalized)
        return rows


def import_metric_row(con: sqlite3.Connection, *, output_root: Path, row: dict[str, str]) -> dict[str, Any]:
    row = canonicalize_row(row)
    source = first_present(row, "source_url", "url")
    if not source:
        return {"status": "skipped", "error": "missing source_url/url"}

    id_ = source_id(source)
    platform = (first_present(row, "platform") or platform_for_source(source)).lower()
    format_ = first_present(row, "format", "format_hint") or infer_format(source, None)
    collected_at = first_present(row, "collected_at") or utc_now()
    captured_at = first_present(row, "metrics_captured_at", "captured_at") or collected_at
    metric_source = first_present(row, "metric_source") or "manual_csv"

    def nonnegative_int(value: Any) -> int | None:
        parsed = parse_int(value)
        return parsed if parsed is not None and parsed >= 0 else None

    likes = nonnegative_int(first_present(row, "likes", "reactions"))
    comments = nonnegative_int(first_present(row, "comments", "replies"))
    shares = nonnegative_int(first_present(row, "shares", "reposts"))
    views = nonnegative_int(first_present(row, "views", "plays"))
    metrics = numeric_metrics(row)
    for key, value in (("likes", likes), ("comments", comments), ("shares", shares), ("views", views)):
        if value is not None:
            metrics.setdefault(key, float(value))
    present_interactions = [v for v in (likes, comments, shares) if v is not None]
    interaction_count = sum(present_interactions) if present_interactions else None
    explicit_engagement_rate = normalize_metric_rate(parse_float(first_present(row, "engagement_rate")))
    if explicit_engagement_rate is not None and not 0 <= explicit_engagement_rate <= 1:
        explicit_engagement_rate = None
    engagement_rate = explicit_engagement_rate
    engagement_basis = "explicit" if explicit_engagement_rate is not None else None
    denominator = parse_float(first_present(row, "views", "impressions", "reach", "plays"))
    if engagement_rate is None and denominator and interaction_count is not None:
        engagement_rate = interaction_count / denominator
        engagement_basis = "derived"
    elif engagement_rate is None and interaction_count is not None:
        engagement_basis = "interactions"

    explicit_primary_name = first_present(row, "primary_metric_name")
    primary_value = parse_float(first_present(row, "primary_metric_value"))
    if primary_value is not None:
        if explicit_primary_name in {"engagement_rate", "completion_rate", "retention_rate", "watched_full_video_pct"}:
            primary_value = normalize_metric_rate(primary_value)
            if primary_value is not None and not 0 <= primary_value <= 1:
                primary_value = None
        elif primary_value < 0:
            primary_value = None
    if explicit_primary_name and primary_value is not None:
        metrics[explicit_primary_name] = primary_value
    metric_summary = derive_metric_summary(
        platform=platform,
        metrics=metrics,
        interaction_count=interaction_count,
        engagement_rate=engagement_rate,
        engagement_basis=engagement_basis,
        explicit_primary_name=explicit_primary_name,
        explicit_primary_value=primary_value,
        metric_source=metric_source,
    )

    raw_dir = output_root / "raw" / source_dir_name(source)
    processed_dir = output_root / "processed" / source_dir_name(source)
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, notes, status_collect, status_extract,
            status_normalize, raw_dir, processed_dir, metadata_json, media_json, collected_at
        )
        VALUES (?, ?, ?, ?, ?, 'manual_metrics', NULL, 'done', ?, ?, NULL, '[]', ?)
        ON CONFLICT(id) DO UPDATE SET
            platform=excluded.platform,
            format_hint=excluded.format_hint,
            notes=excluded.notes,
            status_normalize='done',
            collected_at=excluded.collected_at
        """,
        (
            id_,
            source,
            platform,
            format_,
            first_present(row, "notes") or "",
            rel(raw_dir, output_root),
            rel(processed_dir, output_root),
            collected_at,
        ),
    )
    con.execute(
        """
        INSERT INTO pieces(
            id, source_url, platform, format, creator, published_at, duration_sec,
            duration_bucket, caption, transcript, onscreen_text, slides, hook_spoken,
            hook_onscreen, cta_spoken, views, likes, comments, shares, interaction_count,
            engagement_rate, engagement_basis, primary_metric_name, primary_metric_value,
            primary_metric_basis, secondary_metrics, metric_source, metric_confidence,
            metrics_captured_at, notes, collected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            platform=excluded.platform,
            format=excluded.format,
            creator=COALESCE(excluded.creator, pieces.creator),
            published_at=COALESCE(excluded.published_at, pieces.published_at),
            caption=COALESCE(excluded.caption, pieces.caption),
            hook_spoken=COALESCE(excluded.hook_spoken, pieces.hook_spoken),
            hook_onscreen=COALESCE(excluded.hook_onscreen, pieces.hook_onscreen),
            cta_spoken=COALESCE(excluded.cta_spoken, pieces.cta_spoken),
            views=excluded.views,
            likes=excluded.likes,
            comments=excluded.comments,
            shares=excluded.shares,
            interaction_count=excluded.interaction_count,
            engagement_rate=excluded.engagement_rate,
            engagement_basis=excluded.engagement_basis,
            primary_metric_name=excluded.primary_metric_name,
            primary_metric_value=excluded.primary_metric_value,
            primary_metric_basis=excluded.primary_metric_basis,
            secondary_metrics=excluded.secondary_metrics,
            metric_source=excluded.metric_source,
            metric_confidence=excluded.metric_confidence,
            metrics_captured_at=excluded.metrics_captured_at,
            notes=COALESCE(NULLIF(excluded.notes, ''), pieces.notes),
            collected_at=excluded.collected_at
        """,
        (
            id_,
            source,
            platform,
            format_,
            first_present(row, "creator", "author", "handle"),
            first_present(row, "published_at", "date"),
            parse_float(first_present(row, "duration_sec", "duration")),
            duration_bucket(format_, parse_float(first_present(row, "duration_sec", "duration"))),
            first_present(row, "caption", "text"),
            first_present(row, "hook_spoken") or None,
            first_present(row, "hook_onscreen") or None,
            first_present(row, "cta_spoken") or None,
            views,
            likes,
            comments,
            shares,
            interaction_count,
            engagement_rate,
            engagement_basis,
            metric_summary["primary_metric_name"],
            metric_summary["primary_metric_value"],
            metric_summary["primary_metric_basis"],
            json_dumps(metric_summary["secondary_metrics"]) if metric_summary["secondary_metrics"] else None,
            metric_summary["metric_source"],
            metric_summary["metric_confidence"],
            captured_at,
            first_present(row, "notes") or "",
            collected_at,
        ),
    )
    con.commit()
    generic_basis = first_present(row, "metric_basis", "basis")
    generic_unit = first_present(row, "metric_unit", "unit")
    generic_denominator = first_present(row, "denominator_name", "metric_denominator")
    basis_by_metric = {
        name: str(
            first_present(row, f"{name}_basis")
            or generic_basis
            or f"{metric_source}:observed_{'rate' if name in {'engagement_rate', 'completion_rate', 'retention_rate', 'watched_full_video_pct'} else 'total'}"
        )
        for name in metrics
    }
    unit_by_metric = {
        name: str(first_present(row, f"{name}_unit") or generic_unit or (
            "ratio" if name in {"engagement_rate", "completion_rate", "retention_rate", "watched_full_video_pct"}
            else "seconds" if name == "avg_watch_time"
            else "count"
        ))
        for name in metrics
    }
    denominator_by_metric = {
        name: first_present(row, f"{name}_denominator", f"{name}_denominator_name") or generic_denominator
        for name in metrics
    }
    if engagement_basis:
        denominator_by_metric["engagement_rate"] = (
            denominator_by_metric.get("engagement_rate") or engagement_basis
        )
    store_metric_observations(
        con,
        piece_id=id_,
        metrics=metrics,
        source=metric_source,
        captured_at=captured_at,
        confidence=metric_summary["metric_confidence"],
        basis_by_metric=basis_by_metric,
        unit_by_metric=unit_by_metric,
        denominator_by_metric=denominator_by_metric,
    )
    return {
        "id": id_,
        "source": source,
        "platform": platform,
        "status": "done",
        "primary_metric": metric_summary["primary_metric_name"],
    }


def fetch_youtube_metrics(source: str, *, api_key: str, http_get: Any = http_get_json) -> dict[str, str]:
    video_id = youtube_video_id(source)
    if not video_id:
        raise ValueError(f"Could not extract YouTube video id from {source}")
    params = urlencode({"part": "snippet,statistics,contentDetails", "id": video_id, "key": api_key})
    data = http_get(f"https://www.googleapis.com/youtube/v3/videos?{params}")
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"YouTube video not found: {video_id}")
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    return {
        "url": source,
        "platform": "youtube",
        "format": "short" if "/shorts/" in source else "video",
        "creator": str(snippet.get("channelTitle") or ""),
        "published_at": str(snippet.get("publishedAt") or "")[:10],
        "caption": str(snippet.get("title") or ""),
        "views": str(stats.get("viewCount") or ""),
        "likes": str(stats.get("likeCount") or ""),
        "comments": str(stats.get("commentCount") or ""),
        "metric_source": "youtube_api",
    }


def fetch_x_metrics(source: str, *, bearer_token: str, http_get: Any = http_get_json) -> dict[str, str]:
    post_id = x_post_id(source)
    if not post_id:
        raise ValueError(f"Could not extract X post id from {source}")
    params = urlencode({
        "tweet.fields": "created_at,public_metrics,text",
        "expansions": "author_id",
        "user.fields": "username,name",
    })
    headers = {"Authorization": f"Bearer {bearer_token}"}
    data = http_get(f"https://api.x.com/2/tweets/{post_id}?{params}", headers=headers)
    tweet = data.get("data") or {}
    metrics = tweet.get("public_metrics") or {}
    users = ((data.get("includes") or {}).get("users") or [])
    creator = users[0].get("username") if users else ""
    return {
        "url": source,
        "platform": "x",
        "format": "post",
        "creator": str(creator or ""),
        "published_at": str(tweet.get("created_at") or "")[:10],
        "caption": str(tweet.get("text") or ""),
        "likes": str(metrics.get("like_count") or ""),
        "replies": str(metrics.get("reply_count") or ""),
        "reposts": str(metrics.get("retweet_count") or ""),
        "quotes": str(metrics.get("quote_count") or ""),
        "metric_source": "x_api",
    }


def fetch_linkedin_metrics(source: str, *, access_token: str, restli_protocol_version: str, http_get: Any = http_get_json) -> dict[str, str]:
    urn = linkedin_activity_urn(source)
    if not urn:
        raise ValueError(f"Could not extract LinkedIn activity URN from {source}")
    encoded = quote(urn, safe="")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": restli_protocol_version,
    }
    data = http_get(f"https://api.linkedin.com/v2/socialActions/{encoded}", headers=headers)
    likes_summary = data.get("likesSummary") or {}
    comments_summary = data.get("commentsSummary") or {}
    shares_summary = data.get("sharesSummary") or {}
    return {
        "url": source,
        "platform": "linkedin",
        "format": "post",
        "likes": str(likes_summary.get("totalLikes") or likes_summary.get("aggregatedTotalLikes") or ""),
        "comments": str(comments_summary.get("aggregatedTotalComments") or comments_summary.get("totalFirstLevelComments") or ""),
        "reposts": str(shares_summary.get("totalShares") or shares_summary.get("aggregatedTotalShares") or ""),
        "metric_source": "linkedin_api",
    }


def latest_insight_value(item: dict[str, Any]) -> Any:
    values = item.get("values")
    if isinstance(values, list) and values:
        return values[-1].get("value")
    return item.get("value")


def meta_metric_name(name: str) -> str:
    mapping = {
        "post_impressions": "impressions",
        "post_engaged_users": "engaged_users",
        "post_clicks": "clicks",
        "saved": "saves",
        "total_interactions": "interaction_count",
        "replies": "comments",
        "quotes": "quotes",
    }
    return mapping.get(name, canonical_column_name(name))


def fetch_meta_graph_insights(
    source: str,
    *,
    platform: str,
    access_token: str,
    graph_version: str,
    metrics: str,
    http_get: Any = http_get_json,
) -> dict[str, str]:
    object_id = source.strip()
    if is_url(object_id):
        raise ValueError("Meta Graph fetches require an object/media ID, not a public URL.")
    params = urlencode({"metric": metrics, "access_token": access_token})
    data = http_get(f"https://graph.facebook.com/{graph_version}/{quote(object_id, safe='')}/insights?{params}")
    row = {
        "url": object_id,
        "platform": platform,
        "format": "post",
        "metric_source": f"{platform}_graph_api",
    }
    for item in data.get("data") or []:
        name = str(item.get("name") or "")
        value = latest_insight_value(item)
        if not name or value is None:
            continue
        row[meta_metric_name(name)] = str(value)
    return row


def fetch_platform_metric_row(args: argparse.Namespace, source: str) -> dict[str, str]:
    platform = args.platform.lower()
    if platform == "youtube":
        api_key = args.api_key or os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            raise ValueError("Missing YouTube API key. Pass --api-key or set YOUTUBE_API_KEY.")
        return fetch_youtube_metrics(source, api_key=api_key)
    if platform in {"x", "twitter"}:
        bearer_token = args.bearer_token or os.environ.get("X_BEARER_TOKEN")
        if not bearer_token:
            raise ValueError("Missing X bearer token. Pass --bearer-token or set X_BEARER_TOKEN.")
        return fetch_x_metrics(source, bearer_token=bearer_token)
    if platform == "linkedin":
        access_token = args.access_token or os.environ.get("LINKEDIN_ACCESS_TOKEN")
        if not access_token:
            raise ValueError("Missing LinkedIn access token. Pass --access-token or set LINKEDIN_ACCESS_TOKEN.")
        return fetch_linkedin_metrics(source, access_token=access_token, restli_protocol_version=args.restli_protocol_version)
    if platform in {"instagram", "facebook", "threads"}:
        access_token = args.access_token or os.environ.get("META_ACCESS_TOKEN")
        if not access_token:
            raise ValueError("Missing Meta access token. Pass --access-token or set META_ACCESS_TOKEN.")
        metrics = args.metrics or DEFAULT_GRAPH_INSIGHT_METRICS[platform]
        return fetch_meta_graph_insights(
            source,
            platform=platform,
            access_token=access_token,
            graph_version=args.graph_version,
            metrics=metrics,
        )
    raise ValueError(f"Unsupported API platform: {args.platform}")


def selected_sources(con: sqlite3.Connection, ids: list[str] | None = None) -> list[sqlite3.Row]:
    if ids:
        q = ",".join("?" for _ in ids)
        return con.execute(f"SELECT * FROM sources WHERE id IN ({q}) ORDER BY collected_at", ids).fetchall()
    return con.execute("SELECT * FROM sources ORDER BY collected_at").fetchall()


def print_summary(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def cmd_collect(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    rows = load_inputs(args.input or [], args.sources or [])
    if not rows:
        print("No sources provided.", file=sys.stderr)
        return 2
    for item in rows:
        result = collect_source(
            con,
            output_root=output_root,
            source=item["url"],
            format_hint=item.get("format_hint") or "",
            notes=item.get("notes") or args.notes or "",
            cookies_file=args.cookies,
            delay=args.delay,
            force=args.force,
        )
        print_summary(result)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    languages = [lang.strip() for lang in args.ocr_languages.split(",") if lang.strip()]
    for row in selected_sources(con, args.ids):
        result = extract_source(
            con,
            output_root=output_root,
            row=row,
            languages=languages,
            whisper_language=args.whisper_language,
            whisper_model=args.whisper_model,
            frame_every_seconds=args.frame_every_seconds,
            min_confidence=args.min_confidence,
            dedupe_threshold=args.dedupe_threshold,
            use_scene_detect=not args.no_scene_detect,
            force=args.force,
        )
        print_summary(result)
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    for row in selected_sources(con, args.ids):
        print_summary(normalize_source(con, output_root=output_root, row=row, force=args.force))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    count = export_jsonl(con, output_root, args.output)
    print_summary({"status": "done", "rows": count, "output": str(args.output or (output_root / "corpus.jsonl"))})
    return 0


def cmd_import_metrics(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    count = 0
    for input_path in args.input:
        for row in read_metric_rows(input_path):
            result = import_metric_row(con, output_root=output_root, row=row)
            print_summary(result)
            if result.get("status") == "done":
                count += 1
    print_summary({"status": "done", "rows": count})
    return 0


def cmd_fetch_metrics(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    rows = load_inputs(args.input or [], args.sources or [])
    if not rows:
        print("No sources provided.", file=sys.stderr)
        return 2
    count = 0
    for item in rows:
        source = item["url"]
        try:
            metric_row = fetch_platform_metric_row(args, source)
            if item.get("notes"):
                metric_row["notes"] = item["notes"]
            result = import_metric_row(con, output_root=output_root, row=metric_row)
        except Exception as exc:
            record_failure(con, source_id_=source_id(source), source_url=source, stage="fetch-metrics", message=str(exc))
            result = {"source": source, "status": "failed", "error": str(exc)}
        print_summary(result)
        if result.get("status") == "done":
            count += 1
    print_summary({"status": "done", "rows": count})
    return 0


def cmd_scrape_metrics(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    rows = load_inputs(args.input or [], args.sources or [])
    if not rows:
        print("No sources provided.", file=sys.stderr)
        return 2
    count = 0
    for item in rows:
        source = item["url"]
        try:
            metadata, error = yt_dlp_metadata(source, args.cookies)
            if error:
                raise RuntimeError(error)
            if not metadata:
                raise RuntimeError("yt-dlp returned no metadata")
            metric_row = metric_row_from_metadata(source, metadata, platform=args.platform, metric_source="scrape_yt_dlp")
            if item.get("notes"):
                metric_row["notes"] = item["notes"]
            result = import_metric_row(con, output_root=output_root, row=metric_row)
        except Exception as exc:
            record_failure(con, source_id_=source_id(source), source_url=source, stage="scrape-metrics", message=str(exc))
            result = {"source": source, "status": "failed", "error": str(exc)}
        print_summary(result)
        if result.get("status") == "done":
            count += 1
        if args.delay:
            time.sleep(args.delay)
    print_summary({"status": "done", "rows": count})
    return 0


def cmd_link_intake(args: argparse.Namespace) -> int:
    for source in args.sources:
        try:
            result = link_intake(source)
        except Exception as exc:
            result = {"status": "failed", "source": source, "error": str(exc)}
        print_summary(result)
    return 0


def cmd_research_link(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    profile_urls: list[str] = []
    for source in args.sources:
        try:
            intake = link_intake(source)
        except Exception as exc:
            print_summary({"status": "failed", "source": source, "error": str(exc)})
            continue
        if intake.get("kind") == "website":
            profiles = intake.get("profiles") or []
            if not args.all:
                print_summary(intake)
                continue
            profile_urls.extend(item["url"] for item in profiles)
        elif intake.get("kind") == "social_profile":
            profile_urls.append(intake["url"])
        else:
            profile_urls.append(source)

    count = 0
    for profile in profile_urls:
        platform = platform_for_source(profile)
        urls = [profile]
        if is_social_profile_url(profile):
            expanded, error = yt_dlp_flat_entries(profile, args.cookies, limit=args.limit)
            if error:
                record_failure(con, source_id_=source_id(profile), source_url=profile, stage="research-link", message=error)
                print_summary({"status": "failed", "source": profile, "error": error})
                continue
            urls = expanded or [profile]
        for url in urls[:args.limit]:
            metadata, error = yt_dlp_metadata(url, args.cookies)
            if error:
                record_failure(con, source_id_=source_id(url), source_url=url, stage="research-link", message=error)
                print_summary({"status": "failed", "source": url, "error": error})
                continue
            if not metadata:
                continue
            row = metric_row_from_metadata(url, metadata, platform=platform if platform != "unknown" else None, metric_source="research_link_yt_dlp")
            result = import_metric_row(con, output_root=output_root, row=row)
            print_summary(result)
            if result.get("status") == "done":
                count += 1
            if args.delay:
                time.sleep(args.delay)
    print_summary({"status": "done", "rows": count})
    return 0


def cmd_connector_options(args: argparse.Namespace) -> int:
    print_summary(connector_options(args.platform))
    return 0


def cmd_setup_connector(args: argparse.Namespace) -> int:
    print_summary(setup_connector(args.platform, args.mode))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    print_summary(metric_report(con, platform=args.platform, limit=args.limit))
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    print_summary(coverage_report(con))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    output_root = args.output_root.resolve()
    con = connect(output_root)
    print_summary(analytics_report(con, platform=args.platform, min_count=args.min_count, limit=args.limit))
    return 0


def platform_content_id(source: str) -> str | None:
    if not is_url(source):
        return None
    parts = [part for part in urlparse(source).path.strip("/").split("/") if part]
    if not parts:
        return None
    if parts[-1].startswith("@"):  # profile URL, not a content ID
        return None
    return parts[-1]


def _tag_source_failures(
    con: sqlite3.Connection,
    *,
    source: str,
    profile_id: str,
    run_id: str,
    piece_id: str | None,
) -> None:
    con.execute(
        """
        UPDATE failures
        SET profile_id=COALESCE(profile_id, ?),
            run_id=COALESCE(run_id, ?),
            piece_id=COALESCE(piece_id, ?)
        WHERE source_id=?
        """,
        (profile_id, run_id, piece_id, source_id(source)),
    )
    con.commit()


def _deep_cohort_ids(
    con: sqlite3.Connection,
    *,
    profile_id: str,
    run_id: str,
    objective: str,
    top_k: int,
    limit: int,
) -> list[str]:
    pieces = [
        dict(row)
        for row in con.execute(
            """
            SELECT p.* FROM profile_run_pieces membership
            JOIN pieces p ON p.id=membership.piece_id
            WHERE p.profile_id=? AND membership.profile_run_id=?
            ORDER BY COALESCE(membership.inventory_position, 2147483647), p.published_at DESC, p.id
            """,
            (profile_id, run_id),
        ).fetchall()
    ]
    if not pieces:
        return []
    observations = [
        dict(row)
        for row in con.execute(
            """
            SELECT mo.* FROM metric_observations mo
            JOIN pieces p ON p.id=mo.piece_id
            JOIN profile_run_pieces membership
              ON membership.piece_id=p.id AND membership.profile_run_id=?
            WHERE p.profile_id=?
            """,
            (run_id, profile_id),
        ).fetchall()
    ]
    preliminary = analyze_winners(
        pieces,
        observations,
        objective=objective,
        top_k=top_k,
        categorical_fields=[],
        continuous_fields=[],
        bootstrap_iterations=100,
    )
    winners = list(preliminary["cohorts"]["winner_piece_ids"])
    non_winners = list(preliminary["cohorts"]["non_winner_piece_ids"])
    if not winners:
        return [str(row["id"]) for row in pieces[:limit]]
    by_id = {str(row["id"]): row for row in pieces}
    selected = list(winners)
    for winner_id in winners:
        winner_format = by_id[winner_id].get("media_type") or by_id[winner_id].get("format")
        match = next(
            (
                piece_id
                for piece_id in non_winners
                if piece_id not in selected
                and (by_id[piece_id].get("media_type") or by_id[piece_id].get("format")) == winner_format
            ),
            None,
        )
        if match:
            selected.append(match)
    selected.extend(piece_id for piece_id in non_winners if piece_id not in selected)
    return selected[:limit]


def _deep_process_profile(
    con: sqlite3.Connection,
    *,
    output_root: Path,
    profile_id: str,
    run_id: str,
    piece_ids: list[str],
    args: argparse.Namespace,
) -> None:
    languages = [lang.strip() for lang in args.ocr_languages.split(",") if lang.strip()]
    for piece_id in piece_ids:
        piece = con.execute("SELECT * FROM pieces WHERE id=?", (piece_id,)).fetchone()
        if not piece:
            continue
        source = piece["source_url"]
        collect_result = collect_source(
            con,
            output_root=output_root,
            source=source,
            format_hint=piece["format"] or "",
            notes=piece["notes"] or "",
            cookies_file=args.cookies,
            delay=0,
            force=args.force,
        )
        if collect_result.get("status") == "failed":
            _tag_source_failures(
                con,
                source=source,
                profile_id=profile_id,
                run_id=run_id,
                piece_id=piece_id,
            )
            continue
        source_row = con.execute("SELECT * FROM sources WHERE id=?", (source_id(source),)).fetchone()
        if source_row:
            extract_result = extract_source(
                con,
                output_root=output_root,
                row=source_row,
                languages=languages,
                whisper_language=args.whisper_language,
                whisper_model=args.whisper_model,
                frame_every_seconds=args.frame_every_seconds,
                min_confidence=args.min_confidence,
                dedupe_threshold=args.dedupe_threshold,
                use_scene_detect=not args.no_scene_detect,
                force=args.force,
            )
            refreshed = con.execute("SELECT * FROM sources WHERE id=?", (source_id(source),)).fetchone()
            if refreshed and extract_result.get("status") != "failed":
                normalize_source(con, output_root=output_root, row=refreshed, force=args.force)
        refreshed_piece = con.execute("SELECT * FROM pieces WHERE id=?", (piece_id,)).fetchone()
        associate_piece(
            con,
            piece_id=piece_id,
            profile_id=profile_id,
            run_id=run_id,
            canonical_url=normalize_url(source),
            media_type=(refreshed_piece or piece)["format"],
            platform_content_id=platform_content_id(source),
        )
        _tag_source_failures(
            con,
            source=source,
            profile_id=profile_id,
            run_id=run_id,
            piece_id=piece_id,
        )


def _analysis_profile_urls(args: argparse.Namespace) -> list[str]:
    profiles: list[str] = []
    for source in args.sources:
        intake = link_intake(source)
        if intake.get("kind") == "website":
            if not args.all:
                print_summary(intake)
                continue
            profiles.extend(item["url"] for item in intake.get("profiles") or [])
        elif intake.get("kind") == "social_profile":
            profiles.append(intake["url"])
        else:
            raise ValueError(f"analyze-profile requires a profile URL, not a content URL: {source}")
    return list(dict.fromkeys(profiles))


def _inventory_row_profile_error(
    row: dict[str, str],
    profile: dict[str, str],
    *,
    allow_unverified: bool,
) -> str | None:
    normalized = canonicalize_row(row)
    row_platform = str(first_present(normalized, "platform") or platform_for_source(
        str(first_present(normalized, "source_url", "url") or "")
    )).casefold()
    if row_platform not in {"unknown", profile["platform"]}:
        return f"row platform {row_platform!r} does not match profile platform {profile['platform']!r}"

    supplied_profile_url = first_present(normalized, "profile_url")
    if supplied_profile_url:
        try:
            supplied = profile_identity(str(supplied_profile_url))
        except ValueError:
            return "row profile_url is invalid"
        if supplied["id"] != profile["id"]:
            return f"row profile_url belongs to {supplied['handle']!r}, not {profile['handle']!r}"
        return None

    creator = first_present(normalized, "creator", "account", "author", "handle")
    if creator:
        creator_text = str(creator).strip().casefold().lstrip("@").rstrip("/")
        if is_url(creator_text):
            try:
                creator_text = profile_identity(creator_text)["handle"]
            except ValueError:
                return "row creator URL is invalid"
        expected = profile["handle"].casefold().lstrip("@").rstrip("/")
        if creator_text not in {expected, expected.rsplit("/", 1)[-1]}:
            return f"row creator {creator!r} does not match profile handle {profile['handle']!r}"
        return None

    if not allow_unverified:
        return "row has no creator or profile_url; pass --allow-unverified-inventory to attach it explicitly"
    return None


def cmd_analyze_profile(args: argparse.Namespace) -> int:
    for name in ("posts", "top", "deep_limit", "minimum_sample", "bootstrap_iterations"):
        if getattr(args, name) < 1:
            print(f"--{name.replace('_', '-')} must be at least 1", file=sys.stderr)
            return 2
    if args.top > args.posts:
        print("--top cannot be greater than --posts", file=sys.stderr)
        return 2
    try:
        profile_urls = _analysis_profile_urls(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not profile_urls:
        return 0
    if args.semantic_provider == "ollama":
        semantic_analyzer = OllamaSemanticAnalyzer(
            model_name=args.ollama_model,
            endpoint=args.ollama_endpoint,
        )
    else:
        semantic_analyzer = RuleBasedSemanticAnalyzer()

    base_root = args.output_root.resolve()
    multi = len(profile_urls) > 1
    successful = 0
    for profile_url in profile_urls:
        identity = profile_identity(profile_url)
        safe_handle = re.sub(r"[^a-z0-9._-]+", "-", identity["handle"]).strip("-") or "profile"
        output_root = base_root / f"{identity['platform']}-{safe_handle}" if multi else base_root
        con = connect(output_root)
        profile = upsert_profile(con, profile_url)
        config = {
            "posts": args.posts,
            "goal": args.goal,
            "top": args.top,
            "mode": args.mode,
            "evidence_mode": args.evidence_mode,
            "semantic_provider": args.semantic_provider,
            "allow_unverified_inventory": args.allow_unverified_inventory,
            "minimum_sample": args.minimum_sample,
            "bootstrap_iterations": args.bootstrap_iterations,
            "seed": args.seed,
        }
        run_id = start_profile_run(
            con,
            profile_id=profile["id"],
            mode=args.mode,
            evidence_mode=args.evidence_mode,
            requested_posts=args.posts,
            objective=args.goal,
            top_k=args.top,
            config=config,
        )
        inventory_rows: list[dict[str, str]] = []
        if args.inventory_input:
            for path in args.inventory_input:
                inventory_rows.extend(read_metric_rows(path))
            verified_rows: list[dict[str, str]] = []
            for candidate in inventory_rows:
                mismatch = _inventory_row_profile_error(
                    candidate,
                    profile,
                    allow_unverified=args.allow_unverified_inventory,
                )
                if mismatch:
                    source = first_present(candidate, "source_url", "url") or profile_url
                    record_failure(
                        con,
                        source_id_=source_id(source),
                        source_url=source,
                        stage="inventory-profile-validation",
                        message=mismatch,
                        profile_id=profile["id"],
                        run_id=run_id,
                    )
                    continue
                verified_rows.append(candidate)
            inventory_rows = verified_rows[: args.posts]
        else:
            urls, error = yt_dlp_flat_entries(profile_url, args.cookies, limit=args.posts)
            if error:
                record_failure(
                    con,
                    source_id_=source_id(profile_url),
                    source_url=profile_url,
                    stage="inventory",
                    message=error,
                    profile_id=profile["id"],
                    run_id=run_id,
                )
            for url in urls[: args.posts]:
                metadata, metadata_error = yt_dlp_metadata(url, args.cookies)
                if metadata_error or not metadata:
                    record_failure(
                        con,
                        source_id_=source_id(url),
                        source_url=url,
                        stage="inventory-metadata",
                        message=metadata_error or "metadata unavailable",
                        profile_id=profile["id"],
                        run_id=run_id,
                    )
                    continue
                inventory_rows.append(
                    metric_row_from_metadata(
                        url,
                        metadata,
                        platform=profile["platform"],
                        metric_source="profile_inventory_yt_dlp",
                    )
                )
                if args.delay:
                    time.sleep(args.delay)

        for inventory_position, inventory_row in enumerate(inventory_rows, start=1):
            try:
                result = import_metric_row(con, output_root=output_root, row=inventory_row)
                if result.get("status") != "done":
                    continue
                piece = con.execute("SELECT * FROM pieces WHERE id=?", (result["id"],)).fetchone()
                associate_piece(
                    con,
                    piece_id=result["id"],
                    profile_id=profile["id"],
                    run_id=run_id,
                    canonical_url=normalize_url(result["source"]) if is_url(result["source"]) else result["source"],
                    media_type=piece["format"] if piece else None,
                    platform_content_id=platform_content_id(result["source"]),
                    inventory_position=inventory_position,
                )
            except Exception as exc:
                source = first_present(inventory_row, "source_url", "url") or profile_url
                record_failure(
                    con,
                    source_id_=source_id(source),
                    source_url=source,
                    stage="inventory-import",
                    message=str(exc),
                    profile_id=profile["id"],
                    run_id=run_id,
                )

        try:
            if args.mode == "deep":
                if args.deep_all:
                    deep_ids = [
                        row[0]
                        for row in con.execute(
                            """
                            SELECT p.id FROM profile_run_pieces membership
                            JOIN pieces p ON p.id=membership.piece_id
                            WHERE p.profile_id=? AND membership.profile_run_id=?
                            ORDER BY COALESCE(membership.inventory_position, 2147483647), p.published_at DESC, p.id
                            """,
                            (profile["id"], run_id),
                        ).fetchall()
                    ]
                else:
                    deep_ids = _deep_cohort_ids(
                        con,
                        profile_id=profile["id"],
                        run_id=run_id,
                        objective=args.goal,
                        top_k=args.top,
                        limit=args.deep_limit,
                    )
                _deep_process_profile(
                    con,
                    output_root=output_root,
                    profile_id=profile["id"],
                    run_id=run_id,
                    piece_ids=deep_ids,
                    args=args,
                )
            report, analysis = build_profile_report(
                con,
                profile_id=profile["id"],
                profile_run_id=run_id,
                objective=args.goal,
                top_k=args.top,
                semantic_analyzer=semantic_analyzer,
                minimum_sample=args.minimum_sample,
                bootstrap_iterations=args.bootstrap_iterations,
                seed=args.seed,
            )
            coverage = report["coverage"]
            finish_profile_run(con, run_id, status="done", coverage=coverage)
            finished_run = con.execute(
                "SELECT * FROM profile_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if finished_run:
                report["run"] = dict(finished_run)
                report["run"]["config_json"] = json_loads(report["run"].get("config_json"))
                report["run"]["coverage_json"] = json_loads(report["run"].get("coverage_json"))
            paths = write_profile_report(report, output_root)
            print_summary({
                "status": "done",
                "profile": profile,
                "run_id": run_id,
                "analysis_id": analysis["analysis_id"],
                "coverage": coverage,
                "outputs": {key: str(path) for key, path in paths.items()},
            })
            successful += 1
        except Exception as exc:
            coverage = {
                "inventoried_posts": con.execute(
                    """
                    SELECT COUNT(*) FROM profile_run_pieces membership
                    JOIN pieces p ON p.id=membership.piece_id
                    WHERE p.profile_id=? AND membership.profile_run_id=?
                    """,
                    (profile["id"], run_id),
                ).fetchone()[0],
                "error": str(exc),
            }
            finish_profile_run(con, run_id, status="failed", coverage=coverage)
            record_failure(
                con,
                source_id_=source_id(profile_url),
                source_url=profile_url,
                stage="analyze-profile",
                message=str(exc),
                profile_id=profile["id"],
                run_id=run_id,
            )
            print_summary({"status": "failed", "profile": profile_url, "run_id": run_id, "error": str(exc)})
    return 0 if successful == len(profile_urls) else 1


def cmd_metric_template(args: argparse.Namespace) -> int:
    try:
        sys.stdout.write(metric_template(args.platform, include_example=not args.header_only))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    collect_args = argparse.Namespace(**vars(args))
    cmd_collect(collect_args)
    extract_args = argparse.Namespace(**vars(args))
    extract_args.ids = None
    cmd_extract(extract_args)
    normalize_args = argparse.Namespace(**vars(args))
    normalize_args.ids = None
    cmd_normalize(normalize_args)
    if args.export:
        export_args = argparse.Namespace(output_root=args.output_root, output=None)
        cmd_export(export_args)
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")


def add_collect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("sources", nargs="*", help="URLs or local media paths.")
    parser.add_argument("--input", type=Path, action="append", help="TXT or CSV with url,format_hint,notes columns.")
    parser.add_argument("--cookies", type=Path, help="Optional cookies.txt file.")
    parser.add_argument("--delay", type=float, default=4.0)
    parser.add_argument("--notes", default="")


def add_extract_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--whisper-language", default="es")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--ocr-languages", default="es,pt,en")
    parser.add_argument("--frame-every-seconds", type=int, default=2)
    parser.add_argument("--min-confidence", type=float, default=0.4)
    parser.add_argument("--dedupe-threshold", type=float, default=85.0)
    parser.add_argument("--no-scene-detect", action="store_true")


def parse_args(
    argv: list[str],
    *,
    prog: str = "content-reference",
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Stage 1: download raw media and metadata.")
    add_common(collect)
    add_collect_args(collect)
    collect.set_defaults(func=cmd_collect)

    extract = sub.add_parser("extract", help="Stage 2: transcript + OCR into processed artifacts.")
    add_common(extract)
    add_extract_args(extract)
    extract.set_defaults(func=cmd_extract)

    normalize = sub.add_parser("normalize", help="Stage 3: write normalized pieces to SQLite.")
    add_common(normalize)
    normalize.add_argument("--ids", nargs="*")
    normalize.set_defaults(func=cmd_normalize)

    export = sub.add_parser("export", help="Stage 4: export pieces to JSONL.")
    add_common(export)
    export.add_argument("--output", type=Path)
    export.set_defaults(func=cmd_export)

    import_metrics = sub.add_parser("import-metrics", help="Import manual platform metrics from CSV.")
    add_common(import_metrics)
    import_metrics.add_argument("--input", type=Path, action="append", required=True, help="CSV with source_url/url plus metric columns.")
    import_metrics.set_defaults(func=cmd_import_metrics)

    fetch_metrics = sub.add_parser("fetch-metrics", help="Fetch metrics from an authenticated platform API.")
    add_common(fetch_metrics)
    fetch_metrics.add_argument("sources", nargs="*", help="URLs or platform IDs/URNs.")
    fetch_metrics.add_argument("--input", type=Path, action="append", help="TXT or CSV with url,format_hint,notes columns.")
    fetch_metrics.add_argument("--platform", required=True, choices=["youtube", "x", "twitter", "linkedin", "instagram", "facebook", "threads"])
    fetch_metrics.add_argument("--api-key", help="YouTube API key. Defaults to YOUTUBE_API_KEY.")
    fetch_metrics.add_argument("--bearer-token", help="X bearer token. Defaults to X_BEARER_TOKEN.")
    fetch_metrics.add_argument("--access-token", help="LinkedIn or Meta access token. Defaults to LINKEDIN_ACCESS_TOKEN or META_ACCESS_TOKEN.")
    fetch_metrics.add_argument("--restli-protocol-version", default="2.0.0")
    fetch_metrics.add_argument("--graph-version", default="v24.0")
    fetch_metrics.add_argument("--metrics", help="Comma-separated Meta Graph insight metrics override.")
    fetch_metrics.set_defaults(func=cmd_fetch_metrics)

    scrape_metrics = sub.add_parser("scrape-metrics", help="Best-effort low-cost metric fetch using public metadata tools.")
    add_common(scrape_metrics)
    scrape_metrics.add_argument("sources", nargs="*", help="URLs to inspect.")
    scrape_metrics.add_argument("--input", type=Path, action="append", help="TXT or CSV with url,format_hint,notes columns.")
    scrape_metrics.add_argument("--platform", help="Override detected platform.")
    scrape_metrics.add_argument("--cookies", type=Path, help="Optional explicit cookies.txt. Browser cookies are never auto-extracted.")
    scrape_metrics.add_argument("--delay", type=float, default=4.0)
    scrape_metrics.set_defaults(func=cmd_scrape_metrics)

    link_intake_parser = sub.add_parser("link-intake", help="Classify a shared link and discover social profiles on websites.")
    link_intake_parser.add_argument("sources", nargs="+")
    link_intake_parser.set_defaults(func=cmd_link_intake)

    research_link_parser = sub.add_parser("research-link", help="Research social profile links or confirmed website profile discoveries.")
    add_common(research_link_parser)
    research_link_parser.add_argument("sources", nargs="+")
    research_link_parser.add_argument("--all", action="store_true", help="When a website is passed, run all discovered profiles.")
    research_link_parser.add_argument("--limit", type=int, default=12, help="Maximum recent URLs per profile.")
    research_link_parser.add_argument("--cookies", type=Path, help="Optional explicit cookies.txt. Browser cookies are never auto-extracted.")
    research_link_parser.add_argument("--delay", type=float, default=4.0)
    research_link_parser.set_defaults(func=cmd_research_link)

    connector_options_parser = sub.add_parser("connector-options", help="Show acquisition modes for one or all platforms.")
    connector_options_parser.add_argument("platform", nargs="?")
    connector_options_parser.set_defaults(func=cmd_connector_options)

    setup_connector_parser = sub.add_parser("setup-connector", help="Show setup steps for a platform acquisition mode.")
    setup_connector_parser.add_argument("platform")
    setup_connector_parser.add_argument("--mode", required=True, choices=["api", "export", "scrape", "manual"])
    setup_connector_parser.set_defaults(func=cmd_setup_connector)

    report = sub.add_parser("report", help="Rank pieces by platform-native metrics.")
    add_common(report)
    report.add_argument("--platform", help="Optional platform filter, e.g. instagram, tiktok, linkedin, x.")
    report.add_argument("--limit", type=int, default=10)
    report.set_defaults(func=cmd_report)

    coverage = sub.add_parser("coverage", help="Summarize corpus platform and metric coverage.")
    add_common(coverage)
    coverage.set_defaults(func=cmd_coverage)

    analyze = sub.add_parser("analyze", help="Analyze performance by format, hooks, and CTA.")
    add_common(analyze)
    analyze.add_argument("--platform", help="Optional platform filter, e.g. instagram, tiktok, linkedin, x.")
    analyze.add_argument("--min-count", type=int, default=1)
    analyze.add_argument("--limit", type=int, default=10)
    analyze.set_defaults(func=cmd_analyze)

    analyze_profile = sub.add_parser(
        "analyze-profile",
        help="Inventory one or more profiles and explain which content properties are associated with performance.",
    )
    add_common(analyze_profile)
    analyze_profile.add_argument("sources", nargs="+", help="Direct social profile URLs, or a website with --all.")
    analyze_profile.add_argument("--all", action="store_true", help="Analyze every social profile discovered on a website.")
    analyze_profile.add_argument("--posts", type=int, default=50, help="Maximum recent posts to inventory per profile.")
    analyze_profile.add_argument("--goal", default="views", help="Exact objective metric to rank, e.g. views, reach, shares, saves, or retention_rate.")
    analyze_profile.add_argument("--top", type=int, default=10, help="Size of the winning cohort.")
    analyze_profile.add_argument("--mode", choices=["quick", "deep"], default="quick")
    analyze_profile.add_argument("--evidence-mode", choices=["public", "owned", "local"], default="public")
    analyze_profile.add_argument(
        "--inventory-input",
        type=Path,
        action="append",
        help="Offline/owned metrics CSV. When supplied, skip public profile inventory and analyze these rows.",
    )
    analyze_profile.add_argument(
        "--allow-unverified-inventory",
        action="store_true",
        help="Explicitly attach CSV rows lacking creator/profile_url fields to this profile.",
    )
    analyze_profile.add_argument("--cookies", type=Path, help="Optional explicit cookies.txt. Browser cookies are never auto-extracted.")
    analyze_profile.add_argument("--delay", type=float, default=2.0)
    analyze_profile.add_argument("--deep-limit", type=int, default=30, help="Maximum winners plus matched/stratified controls to deep-process.")
    analyze_profile.add_argument("--deep-all", action="store_true", help="Deep-process every inventoried post.")
    analyze_profile.add_argument("--minimum-sample", type=int, default=5)
    analyze_profile.add_argument("--bootstrap-iterations", type=int, default=1000)
    analyze_profile.add_argument("--seed", type=int, default=0)
    analyze_profile.add_argument("--semantic-provider", choices=["rules", "ollama"], default="rules")
    analyze_profile.add_argument("--ollama-model", default="llama3.2")
    analyze_profile.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434/api/generate")
    add_extract_args(analyze_profile)
    analyze_profile.set_defaults(func=cmd_analyze_profile)

    template = sub.add_parser("metric-template", help="Print a platform-specific manual metrics CSV template.")
    template.add_argument("platform", help="x, linkedin, tiktok, instagram, youtube.")
    template.add_argument("--header-only", action="store_true")
    template.set_defaults(func=cmd_metric_template)

    run_all = sub.add_parser("run", help="Run collect -> extract -> normalize, optionally export.")
    add_common(run_all)
    add_collect_args(run_all)
    add_extract_args(run_all)
    run_all.add_argument("--export", action="store_true")
    run_all.set_defaults(func=cmd_run)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    prog = Path(sys.argv[0]).name if argv is None else "content-reference"
    args = parse_args(argv or sys.argv[1:], prog=prog)
    raise SystemExit(args.func(args))
