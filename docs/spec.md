# Spec: Short-Form Content Analysis Pipeline for Writing Guidelines

## 1. Objective

Given Instagram links (reels and carousels) and TikTok links (videos), build a
structured corpus of examples that can later be used to generate writing
guidelines: voice, hooks, script structure, CTAs, and secondarily design cues.

Primary niche: astrology in Spanish for LATAM/Argentina.

Hard constraint: open-source tools only. No paid vision APIs and no paid models
for extraction.

## 2. Scope

In scope:

- Download video, carousel images, captions, and available metrics.
- Transcribe spoken audio.
- Extract on-screen video text: overlays, title cards, burned-in subtitles.
- Extract text from each carousel slide.
- Normalize all examples to a common queryable schema.

Out of scope:

- Generating guidelines. A later LLM reads the prepared corpus.
- Advanced visual design analysis. Optional minimal cues only.

## 3. Inputs

TXT or CSV. CSV columns:

- `url` required.
- `format_hint`: `reel | tiktok | carousel`, optional.
- `notes`: operator note explaining why the example was selected.

Local media paths are also valid for manually exported screenshots/videos.

## 4. OSS Stack

- `yt-dlp`: video and metadata for reels/TikToks.
- `gallery-dl`: Instagram carousel images and metadata.
- `ffmpeg`: audio extraction and frame sampling.
- `faster-whisper`: multilingual timestamped transcription.
- `PySceneDetect`: smart video frame timestamps.
- `EasyOCR`: OCR for frames and slides, languages `es`, `pt`, `en`.
- `rapidfuzz`: fuzzy deduplication of repeated on-screen text.
- `SQLite`: corpus storage.

Fallbacks:

- `whisper` CLI when `faster-whisper` is unavailable.
- `tesseract` when `EasyOCR` is unavailable.
- fixed-interval frame sampling when `PySceneDetect` is unavailable.

## 5. Architecture

```text
collect -> extract -> normalize -> export
```

Each stage reads disk/DB and writes disk/DB so stages can be rerun without
repeating prior work. Idempotency is by source hash. Use `--force` to rerun.

Folder layout:

```text
references/
  raw/<url_hash>/        video, audio, frames, slides, raw metadata
  processed/<url_hash>/  transcript.json, onscreen.json, slides.json
  corpus.sqlite
  corpus.jsonl
```

## 6. Stage 1: Collect

For each URL:

- Detect platform and format from URL plus optional `format_hint`.
- Video: run `yt-dlp -J` for metadata, then download raw media.
- Carousel: run `gallery-dl` to download ordered images, caption, metadata.
- Store creator, handle, publication date, duration, caption, and metrics when
  present. Missing values remain null.
- Auth: support explicit `cookies.txt`; do not require auth.
- Delay between downloads, default 4 seconds.

## 7. Stage 2: Extract

For videos:

- Extract 16kHz mono WAV with `ffmpeg`.
- Transcribe with timestamped segments.
- Detect scene/title-card changes with PySceneDetect. Fallback to fixed sampling.
- Extract frames with `ffmpeg`.
- OCR each frame.
- Deduplicate near-identical consecutive frame text with rapidfuzz threshold
  around 85.
- Drop low-confidence OCR rows, default threshold 0.4.

For carousels:

- OCR each image.
- Preserve `slide_index`, with slide 1 treated as cover/hook.
- Keep caption separate from slide text.

## 8. Stage 3: Normalize

Create one `pieces` row per source.

Derived fields:

- `hook_spoken`: transcript text from first 3 seconds.
- `hook_onscreen`: first on-screen text card or carousel slide 1.
- `cta_spoken`: final transcript segment.
- `duration_bucket`: `short <=30s`, `mid 30-60s`, `long >60s`, or `carousel`.
- `engagement_rate`: `(likes + comments + shares) / views` when views exist.

No qualitative interpretation is done here. Hook type, tone, astrology topic,
and CTA style are left for a later LLM.

## 9. Stage 4: Export

Export `pieces` to `corpus.jsonl`, one object per line.

## 10. SQLite Schema

Table `pieces`:

```text
id                TEXT
source_url        TEXT
platform          TEXT
format            TEXT
creator           TEXT
published_at      TEXT
duration_sec      REAL
duration_bucket   TEXT
caption           TEXT
transcript        JSON
onscreen_text     JSON
slides            JSON
hook_spoken       TEXT
hook_onscreen     TEXT
cta_spoken        TEXT
views             INTEGER
likes             INTEGER
comments          INTEGER
shares            INTEGER
engagement_rate   REAL
notes             TEXT
collected_at      TEXT
```

Table `failures`:

```text
url/source id, stage, message, occurred_at
```

## 11. Later Guideline Generation

A later LLM should read `corpus.jsonl` and produce:

- `voice-reference.md`
- `short-reel-guidelines.md`
- `long-reel-guidelines.md`
- `carousel-guidelines.md`
- `ascendant-format-guidelines.md`

When metrics exist, weight examples by engagement rate.

## 12. Errors and Edge Cases

- Missing metadata: store null, never estimate.
- Auth/download failure: record failure and continue batch.
- EasyOCR detects no text: empty list, not error.
- Video without audio/speech: empty transcript, valid.
- Single-slide carousel: valid.
- Animated letter-by-letter text: fuzzy dedup should collapse repeated frames.

## 13. Notes

Downloading social content can violate platform terms. Use low scale, local
analysis only, and do not redistribute raw media.
