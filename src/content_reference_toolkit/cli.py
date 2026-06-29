from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


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


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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
        return float(value)
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
    host = urlparse(source).netloc.lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "x.com" in host or "twitter.com" in host:
        return "x"
    if "linkedin.com" in host:
        return "linkedin"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "threads.net" in host:
        return "threads"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    return "local" if not is_url(source) else "unknown"


def infer_format(source: str, format_hint: str | None, media: Iterable[Path] = ()) -> str:
    if format_hint:
        return "tiktok" if format_hint == "tiktok" else format_hint
    if is_url(source):
        parsed = urlparse(source)
        if "tiktok.com" in parsed.netloc:
            return "tiktok"
        if "youtube.com" in parsed.netloc and "/shorts/" in parsed.path:
            return "short"
        if "x.com" in parsed.netloc or "twitter.com" in parsed.netloc:
            return "post"
        if "linkedin.com" in parsed.netloc:
            return "post"
        if "threads.net" in parsed.netloc:
            return "post"
        if "facebook.com" in parsed.netloc or "fb.watch" in parsed.netloc:
            return "post"
        if "/reel/" in parsed.path:
            return "reel"
        if "/p/" in parsed.path:
            return "carousel"
    media = list(media)
    if any(p.suffix.lower() in VIDEO_EXTENSIONS for p in media):
        return "reel"
    if any(p.suffix.lower() in IMAGE_EXTENSIONS for p in media):
        return "carousel"
    return "unknown"


def db_path(output_root: Path) -> Path:
    return output_root / "corpus.sqlite"


def connect(output_root: Path) -> sqlite3.Connection:
    output_root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(output_root))
    con.row_factory = sqlite3.Row
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


def record_failure(con: sqlite3.Connection, *, source_id_: str, source_url: str, stage: str, message: str) -> None:
    con.execute(
        "INSERT INTO failures(source_id, source_url, stage, message, occurred_at) VALUES (?, ?, ?, ?, ?)",
        (source_id_, source_url, stage, message, utc_now()),
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
        if format_ == "carousel":
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
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, notes, status_collect, status_extract,
            status_normalize, raw_dir, processed_dir, metadata_json, media_json, collected_at
        )
        VALUES (?, ?, ?, ?, ?, 'done', NULL, NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,
            platform=excluded.platform,
            format_hint=excluded.format_hint,
            notes=excluded.notes,
            status_collect='done',
            raw_dir=excluded.raw_dir,
            processed_dir=excluded.processed_dir,
            metadata_json=excluded.metadata_json,
            media_json=excluded.media_json,
            collected_at=excluded.collected_at
        """,
        (
            id_,
            source,
            platform_for_source(source),
            format_hint,
            notes,
            rel(raw_dir, output_root),
            rel(processed_dir, output_root),
            json_dumps(metadata),
            json_dumps(media_json),
            utc_now(),
        ),
    )
    con.commit()
    if delay:
        time.sleep(delay)
    return {"id": id_, "source": source, "status": "done", "media": len(media)}


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
        "segments, _ = model.transcribe(audio, language=lang, vad_filter=True)\n"
        "rows = [{'start': round(s.start, 3), 'end': round(s.end, 3), 'text': s.text.strip()} for s in segments]\n"
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
        {"start": round(float(s.get("start", 0)), 3), "end": round(float(s.get("end", 0)), 3), "text": str(s.get("text", "")).strip()}
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
        "image = sys.argv[1]\n"
        "langs = sys.argv[2].split(',')\n"
        "reader = easyocr.Reader(langs, gpu=False)\n"
        "rows = []\n"
        "for bbox, text, conf in reader.readtext(image):\n"
        "    rows.append({'text': text, 'confidence': float(conf)})\n"
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
    lines: dict[tuple[str, str, str], dict[str, list[Any]]] = {}
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
            lines[key] = {"words": [], "confs": []}
            order.append(key)
        lines[key]["words"].append(text)
        lines[key]["confs"].append(conf / 100.0)
    rows: list[dict[str, Any]] = []
    for key in order:
        bucket = lines[key]
        if not bucket["words"]:
            continue
        line_conf = sum(bucket["confs"]) / len(bucket["confs"])
        if line_conf < min_confidence:
            continue
        rows.append({"text": " ".join(bucket["words"]), "confidence": round(line_conf, 3)})
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
        kept.append({"timestamp": row["timestamp"], "text": text, "confidence": row.get("confidence")})
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    transcript: list[dict[str, Any]] = []
    onscreen: list[dict[str, Any]] = []
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
                    onscreen.append({"timestamp": stamp, "text": text, "confidence": (sum(confs) / len(confs)) if confs else None})
    return transcript, dedupe_onscreen(sorted(onscreen, key=lambda r: r["timestamp"]), dedupe_threshold), errors


def extract_carousel(*, media: list[Path], processed_dir: Path, languages: list[str], min_confidence: float) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    slides: list[dict[str, Any]] = []
    images = [p for p in sorted(media) if p.suffix.lower() in IMAGE_EXTENSIONS]
    for idx, image in enumerate(images, start=1):
        rows, ocr_error = ocr_image(image, languages=languages, min_confidence=min_confidence)
        if ocr_error:
            errors.append(f"ocr slide {idx} {image.name}: {ocr_error}")
        text = " ".join(row["text"] for row in rows if row.get("text")).strip()
        slides.append({"slide_index": idx, "text": text, "confidence": max([float(r.get("confidence", 0)) for r in rows], default=None)})
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
    processed_dir = output_root / row["processed_dir"]
    format_ = infer_format(source, row["format_hint"], media)
    errors: list[str] = []
    transcript: list[dict[str, Any]] | None = None
    onscreen: list[dict[str, Any]] | None = None
    slides: list[dict[str, Any]] | None = None

    if format_ == "carousel":
        slides, errors = extract_carousel(media=media, processed_dir=processed_dir, languages=languages, min_confidence=min_confidence)
    else:
        transcript, onscreen, errors = extract_video(
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

    artifact = {"format": format_, "transcript": transcript, "onscreen_text": onscreen, "slides": slides, "extracted_at": utc_now()}
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
            value = normalize_metric_rate(value) or value
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
    if views:
        engagement_rate = (interaction_count or 0) / views
        engagement_basis = "views"
    elif interaction_count is not None:
        # Platforms like Instagram never expose views via yt-dlp. Record the raw
        # interaction total as the ranking signal instead of estimating views.
        engagement_basis = "interactions"
    metric_summary = derive_metric_summary(
        platform=row["platform"],
        metrics=numeric_metrics({
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement_rate": engagement_rate,
        }),
        interaction_count=interaction_count,
        engagement_rate=engagement_rate,
        engagement_basis=engagement_basis,
        metric_source="metadata" if any(v is not None for v in (views, likes, comments, shares)) else None,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_url=excluded.source_url,
            platform=excluded.platform,
            format=excluded.format,
            creator=excluded.creator,
            published_at=excluded.published_at,
            duration_sec=excluded.duration_sec,
            duration_bucket=excluded.duration_bucket,
            caption=excluded.caption,
            transcript=excluded.transcript,
            onscreen_text=excluded.onscreen_text,
            slides=excluded.slides,
            hook_spoken=excluded.hook_spoken,
            hook_onscreen=excluded.hook_onscreen,
            cta_spoken=excluded.cta_spoken,
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
            json_dumps(transcript) if transcript else None,
            json_dumps(onscreen) if onscreen else None,
            json_dumps(slides) if slides else None,
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
            utc_now() if metric_summary["metric_source"] else None,
            row["notes"],
            row["collected_at"] or utc_now(),
        ),
    )
    con.execute("UPDATE sources SET status_normalize='done' WHERE id=?", (id_,))
    con.commit()
    return {"id": id_, "source": source, "status": "done"}


def piece_to_json(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("transcript", "onscreen_text", "slides", "secondary_metrics"):
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
        "format": infer_format(source, None),
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

    likes = parse_int(first_present(row, "likes", "reactions"))
    comments = parse_int(first_present(row, "comments", "replies"))
    shares = parse_int(first_present(row, "shares", "reposts"))
    views = parse_int(first_present(row, "views", "plays"))
    metrics = numeric_metrics(row)
    for key, value in (("likes", likes), ("comments", comments), ("shares", shares), ("views", views)):
        if value is not None:
            metrics.setdefault(key, float(value))
    present_interactions = [v for v in (likes, comments, shares) if v is not None]
    interaction_count = sum(present_interactions) if present_interactions else None
    explicit_engagement_rate = normalize_metric_rate(parse_float(first_present(row, "engagement_rate")))
    engagement_rate = explicit_engagement_rate
    engagement_basis = "explicit" if explicit_engagement_rate is not None else None
    denominator = parse_float(first_present(row, "views", "impressions", "reach", "plays"))
    if engagement_rate is None and denominator:
        engagement_rate = (interaction_count or 0) / denominator
        engagement_basis = "derived"
    elif engagement_rate is None and interaction_count is not None:
        engagement_basis = "interactions"

    primary_value = normalize_metric_rate(parse_float(first_present(row, "primary_metric_value")))
    metric_summary = derive_metric_summary(
        platform=platform,
        metrics=metrics,
        interaction_count=interaction_count,
        engagement_rate=engagement_rate,
        engagement_basis=engagement_basis,
        explicit_primary_name=first_present(row, "primary_metric_name"),
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="content-reference")
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
    args = parse_args(argv or sys.argv[1:])
    raise SystemExit(args.func(args))
