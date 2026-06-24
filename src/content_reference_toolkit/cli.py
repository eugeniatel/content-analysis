from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


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


def slug_for_source(source: str) -> str:
    if is_url(source):
        parsed = urlparse(source)
        bits = [parsed.netloc.replace("www.", "").split(":")[0]]
        bits.extend(part for part in parsed.path.strip("/").split("/") if part)
        base = "-".join(bits) or "reference"
    else:
        base = Path(source).stem
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in base)
    cleaned = "-".join(part for part in cleaned.split("-") if part) or "reference"
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[:72]}-{digest}"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def read_text(path: Path | None) -> str:
    return path.read_text(encoding="utf-8").strip() if path and path.exists() else ""


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def yt_dlp_metadata(url: str, cookies_file: Path | None) -> tuple[dict[str, Any] | None, str]:
    exe = tool("yt-dlp")
    if not exe:
        return None, "yt-dlp not found"
    cmd = [exe, "--skip-download", "--dump-single-json", "--no-warnings"]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(url)
    proc = run(cmd, timeout=90)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()
    try:
        return json.loads(proc.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"yt-dlp returned invalid JSON: {exc}"


def gallery_dl_download(url: str, outdir: Path, cookies_file: Path | None) -> tuple[list[Path], str]:
    exe = tool("gallery-dl")
    if not exe:
        return [], "gallery-dl not found"
    cmd = [exe, "--write-metadata", "--write-info-json", "--directory", str(outdir)]
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd.append(url)
    proc = run(cmd, timeout=240)
    media = [p for p in outdir.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
    return sorted(media), proc.stderr.strip() if proc.returncode != 0 else ""


def yt_dlp_download(url: str, outdir: Path, cookies_file: Path | None) -> tuple[list[Path], str]:
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
    cmd.append(url)
    proc = run(cmd, timeout=300)
    after = {p for p in outdir.rglob("*") if p.is_file()}
    media = [p for p in sorted(after - before) if p.suffix.lower() in MEDIA_EXTENSIONS or p.name.endswith(".image")]
    return media, proc.stderr.strip() if proc.returncode != 0 else ""


def collect_local_file(source: Path, outdir: Path) -> tuple[list[Path], str]:
    if not source.exists():
        return [], f"local file not found: {source}"
    if source.is_dir():
        files = [p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
    elif source.suffix.lower() in MEDIA_EXTENSIONS:
        files = [source]
    else:
        return [], f"unsupported local file type: {source}"
    copied: list[Path] = []
    outdir.mkdir(parents=True, exist_ok=True)
    for file in files:
        target = outdir / file.name
        if file.resolve() != target.resolve():
            shutil.copy2(file, target)
        copied.append(target)
    return sorted(copied), ""


def extract_audio(video: Path, outdir: Path) -> tuple[Path | None, str]:
    exe = tool("ffmpeg")
    if not exe:
        return None, "ffmpeg not found"
    audio = outdir / f"{video.stem}.wav"
    cmd = [exe, "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)]
    proc = run(cmd, timeout=180)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return audio, ""


def transcribe(audio: Path, outdir: Path, *, language: str, model: str) -> tuple[Path | None, str]:
    exe = tool("whisper")
    if not exe:
        return None, "whisper not found"
    cmd = [
        exe,
        str(audio),
        "--language",
        language,
        "--model",
        model,
        "--output_format",
        "txt",
        "--output_dir",
        str(outdir),
    ]
    proc = run(cmd, timeout=900)
    transcript = outdir / f"{audio.stem}.txt"
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()
    return transcript if transcript.exists() else None, ""


def extract_frames(video: Path, outdir: Path, every_seconds: int) -> tuple[list[Path], str]:
    exe = tool("ffmpeg")
    if not exe:
        return [], "ffmpeg not found"
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = outdir / f"{video.stem}_%04d.jpg"
    cmd = [exe, "-y", "-i", str(video), "-vf", f"fps=1/{every_seconds}", str(pattern)]
    proc = run(cmd, timeout=240)
    frames = sorted(outdir.glob(f"{video.stem}_*.jpg"))
    return frames, proc.stderr.strip() if proc.returncode != 0 else ""


def ocr_image(image: Path, outdir: Path, *, language: str) -> tuple[Path | None, str]:
    exe = tool("tesseract")
    if not exe:
        return None, "tesseract not found"
    outdir.mkdir(parents=True, exist_ok=True)
    outbase = outdir / image.stem
    proc = run([exe, str(image), str(outbase), "-l", language], timeout=90)
    txt = outbase.with_suffix(".txt")
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return txt if txt.exists() else None, ""


def detect_format(source: str, metadata: dict[str, Any] | None, media: list[Path]) -> str:
    if is_url(source):
        parsed = urlparse(source)
        if "tiktok.com" in parsed.netloc:
            return "tiktok"
        if "/reel/" in parsed.path:
            return "reel"
        if "/p/" in parsed.path:
            return "carousel_or_post"
    if any(p.suffix.lower() in VIDEO_EXTENSIONS for p in media):
        return "video"
    if any(p.suffix.lower() in IMAGE_EXTENSIONS for p in media):
        return "image_or_carousel"
    if metadata and metadata.get("duration"):
        return "video"
    return "unknown"


def collect_one(args: argparse.Namespace, source: str) -> dict[str, Any]:
    slug = slug_for_source(source)
    item_dir = args.output_root / "raw" / slug
    processed_dir = args.output_root / "processed" / slug
    item_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] | None = None
    errors: list[str] = []
    media: list[Path] = []

    if is_url(source):
        metadata, meta_error = yt_dlp_metadata(source, args.cookies_file)
        if metadata:
            write_json(item_dir / "metadata.yt-dlp.json", metadata)
        if meta_error:
            errors.append(f"metadata: {meta_error}")

        if args.download_media:
            if "/p/" in urlparse(source).path:
                g_media, g_error = gallery_dl_download(source, item_dir, args.cookies_file)
                media.extend(g_media)
                if g_error:
                    errors.append(f"gallery-dl: {g_error}")
            if not media:
                y_media, y_error = yt_dlp_download(source, item_dir, args.cookies_file)
                media.extend(y_media)
                if y_error:
                    errors.append(f"yt-dlp download: {y_error}")
    else:
        local_media, local_error = collect_local_file(Path(source).expanduser(), item_dir)
        media.extend(local_media)
        if local_error:
            errors.append(local_error)

    transcripts = build_transcripts(args, media, processed_dir, errors)
    ocr_outputs = build_ocr(args, media, processed_dir, errors)

    transcript_text = "\n\n".join(read_text(args.output_root / t["text_file"]) for t in transcripts).strip()
    ocr_text = "\n\n".join(read_text(args.output_root / o["text_file"]) for o in ocr_outputs).strip()

    record = {
        "source": source,
        "slug": slug,
        "project": args.project,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "format_guess": detect_format(source, metadata, media),
        "title": metadata.get("title") if metadata else None,
        "creator": metadata.get("uploader") if metadata else None,
        "duration": (metadata.get("duration_string") or metadata.get("duration")) if metadata else None,
        "caption": metadata.get("description") if metadata else None,
        "media_files": [relative_or_absolute(p, args.output_root) for p in sorted(set(media))],
        "transcript_files": transcripts,
        "ocr_files": ocr_outputs,
        "transcript_text": transcript_text,
        "ocr_text": ocr_text,
        "notes": {
            "use_for": args.use_for,
            "what_we_like": args.what_we_like,
            "what_not_to_copy": args.what_not_to_copy,
        },
        "errors": errors,
    }
    write_json(processed_dir / "record.json", record)
    append_jsonl(args.output_root / "content-reference-corpus.jsonl", record)
    return record


def build_transcripts(args: argparse.Namespace, media: list[Path], processed_dir: Path, errors: list[str]) -> list[dict[str, str]]:
    if not args.transcribe:
        return []
    transcripts: list[dict[str, str]] = []
    for file in sorted(set(media)):
        if file.suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            continue
        audio = file
        if file.suffix.lower() in VIDEO_EXTENSIONS:
            audio_out, audio_error = extract_audio(file, processed_dir)
            if audio_error:
                errors.append(f"audio {file.name}: {audio_error}")
                continue
            if audio_out:
                audio = audio_out
        transcript, tr_error = transcribe(audio, processed_dir, language=args.whisper_language, model=args.whisper_model)
        if tr_error:
            errors.append(f"transcribe {file.name}: {tr_error}")
        if transcript:
            transcripts.append({
                "media": relative_or_absolute(file, args.output_root),
                "text_file": relative_or_absolute(transcript, args.output_root),
            })
    return transcripts


def build_ocr(args: argparse.Namespace, media: list[Path], processed_dir: Path, errors: list[str]) -> list[dict[str, str]]:
    if not args.ocr:
        return []
    images = [p for p in sorted(set(media)) if p.suffix.lower() in IMAGE_EXTENSIONS]
    if args.ocr_video_frames:
        for video in [p for p in sorted(set(media)) if p.suffix.lower() in VIDEO_EXTENSIONS]:
            frames, frame_error = extract_frames(video, processed_dir / "frames", args.frame_every_seconds)
            images.extend(frames)
            if frame_error:
                errors.append(f"frames {video.name}: {frame_error}")
    outputs: list[dict[str, str]] = []
    for image in sorted(set(images)):
        txt, ocr_error = ocr_image(image, processed_dir / "ocr", language=args.tesseract_language)
        if ocr_error:
            errors.append(f"ocr {image.name}: {ocr_error}")
        if txt:
            outputs.append({
                "image": relative_or_absolute(image, args.output_root),
                "text_file": relative_or_absolute(txt, args.output_root),
            })
    return outputs


def print_summary(record: dict[str, Any]) -> None:
    print(json.dumps({
        "source": record["source"],
        "slug": record["slug"],
        "format": record["format_guess"],
        "title": record["title"],
        "duration": record["duration"],
        "media": len(record["media_files"]),
        "transcripts": len(record["transcript_files"]),
        "ocr": len(record["ocr_files"]),
        "errors": record["errors"],
    }, ensure_ascii=False))


def add_collect_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("collect", help="Collect reference URLs or local media files.")
    parser.add_argument("sources", nargs="+", help="URLs or local media paths.")
    parser.add_argument("--output-root", type=Path, default=Path("references"))
    parser.add_argument("--project", default="", help="Project/context label, e.g. eugeniatel.")
    parser.add_argument("--cookies-file", type=Path, help="Optional cookies.txt file. Browser-cookie extraction is intentionally not used.")
    parser.add_argument("--download-media", action="store_true", help="Download URL media when possible.")
    parser.add_argument("--transcribe", action="store_true", help="Transcribe audio/video with Whisper.")
    parser.add_argument("--ocr", action="store_true", help="OCR images with Tesseract.")
    parser.add_argument("--ocr-video-frames", action="store_true", help="Extract video frames and OCR on-screen text.")
    parser.add_argument("--frame-every-seconds", type=int, default=2)
    parser.add_argument("--whisper-language", default="Spanish")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--tesseract-language", default="eng")
    parser.add_argument("--use-for", default="", help="Reference role, e.g. voice, carousel_density, astrology_coverage.")
    parser.add_argument("--what-we-like", default="")
    parser.add_argument("--what-not-to-copy", default="")
    parser.set_defaults(func=cmd_collect)


def cmd_collect(args: argparse.Namespace) -> int:
    args.output_root = args.output_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for source in args.sources:
        print(f"collecting {source}", file=sys.stderr)
        print_summary(collect_one(args, source))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="content-reference")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_collect_parser(subparsers)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    raise SystemExit(args.func(args))
