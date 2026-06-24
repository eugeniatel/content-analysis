# Content Reference Toolkit

Local CLI for collecting social-content references into a structured corpus.

This is meant to be called from another project, for example `eugeniatel`, when
you want to analyze examples and turn them into content guidelines.

## What It Does

- Collects URL metadata/video through `yt-dlp` when available.
- Uses `gallery-dl` for image/carousel downloads when available.
- Accepts local media files/screenshots directly.
- Extracts audio with `ffmpeg`.
- Transcribes audio/video with `whisper`.
- OCRs images with `tesseract`.
- Writes normalized records to `references/content-reference-corpus.jsonl`.

The downloaded media and processed text stay local and are ignored by default.

## Install Locally

```bash
cd /Users/euge/content-reference-toolkit
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Optional external tools:

```bash
brew install ffmpeg tesseract
.venv/bin/python -m pip install yt-dlp gallery-dl openai-whisper
```

## Use From Another Repo

From `eugeniatel`:

```bash
cd /Users/euge/eugeniatel
/Users/euge/content-reference-toolkit/.venv/bin/content-reference collect \
  "https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/" \
  --download-media \
  --transcribe \
  --use-for astrology_coverage
```

Local carousel screenshots:

```bash
cd /Users/euge/eugeniatel
/Users/euge/content-reference-toolkit/.venv/bin/content-reference collect \
  ~/Downloads/carousel-slide-*.png \
  --ocr \
  --use-for carousel_density
```

Outputs:

```text
references/raw/<slug>/
references/processed/<slug>/record.json
references/content-reference-corpus.jsonl
```

## Notes

This tool intentionally does not read browser cookies. If a platform blocks a
carousel behind login, export screenshots/media manually and run OCR on local
files instead.
