from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
DEFAULT_OUTPUT_ROOT = Path("references")


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


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value is None:
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
    return "local" if not is_url(source) else "unknown"


def infer_format(source: str, format_hint: str | None, media: Iterable[Path] = ()) -> str:
    if format_hint:
        return "tiktok" if format_hint == "tiktok" else format_hint
    if is_url(source):
        parsed = urlparse(source)
        if "tiktok.com" in parsed.netloc:
            return "tiktok"
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
    for column, decl in (("interaction_count", "INTEGER"), ("engagement_basis", "TEXT")):
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

    con.execute(
        """
        INSERT INTO pieces(
            id, source_url, platform, format, creator, published_at, duration_sec,
            duration_bucket, caption, transcript, onscreen_text, slides, hook_spoken,
            hook_onscreen, cta_spoken, views, likes, comments, shares, interaction_count,
            engagement_rate, engagement_basis, notes, collected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            row["notes"],
            row["collected_at"] or utc_now(),
        ),
    )
    con.execute("UPDATE sources SET status_normalize='done' WHERE id=?", (id_,))
    con.commit()
    return {"id": id_, "source": source, "status": "done"}


def piece_to_json(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("transcript", "onscreen_text", "slides"):
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
