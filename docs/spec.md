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
- Import manually exported platform metrics from CSV.
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

Manual metric CSVs are also valid through `import-metrics`. Supported columns
include:

- `url` or `source_url`, required.
- `platform`, optional when the URL is recognizable.
- `format` or `format_hint`, optional.
- `views`, `impressions`, `reach`, `plays`.
- `likes`, `reactions`, `comments`, `replies`, `shares`, `reposts`.
- `saves`, `bookmarks`, `clicks`, `profile_clicks`.
- `avg_watch_time`, `watched_full_video_pct`, `completion_rate`,
  `retention_rate`, `engagement_rate`.
- `primary_metric_name`, `primary_metric_value` for explicit overrides.
- `metric_source`, `metrics_captured_at`, `notes`.
- `hook_onscreen`, `hook_spoken`, `cta_spoken` for manual creative analysis.

Common export headers are normalized to these names. Examples:

- `Post URL`, `Permalink`, `Link` -> `url`
- `Average Percentage Watched`, `Avg % Watched` -> `retention_rate`
- `Watched Full Video`, `Full Video Watch Rate` -> `watched_full_video_pct`
- `Share Count`, `Reposts`, `Retweets` -> `shares` or `reposts`
- `Visual Hook`, `On Screen Hook` -> `hook_onscreen`
- `Spoken CTA`, `CTA` -> `cta_spoken`

`metric-template <platform>` prints a CSV header plus example row for supported
manual metric platforms.

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
- OCR each frame. With EasyOCR, requested languages (`es,pt,en`) are used
  directly. With the tesseract fallback, requested languages are mapped to
  installed tesseract packs (`es->spa`, `pt->por`, `en->eng`) and per-line
  confidence is read from tesseract TSV output. Install packs with
  `brew install tesseract-lang`; EasyOCR remains the higher-accuracy path for
  stylized overlays.
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
- `interaction_count`: sum of available `likes`, `comments`, `shares`. Real
  counts only, never an estimate. Null when none are present.
- `engagement_rate`: `(likes + comments + shares) / views` when views exist.
- `engagement_basis`: `views` when `engagement_rate` is computed, `interactions`
  when only raw counts exist (e.g. Instagram, which never exposes views via
  yt-dlp), else null. A later LLM weights by `engagement_rate` when present and
  falls back to `interaction_count`.

No qualitative interpretation is done here. Hook type, tone, astrology topic,
and CTA style are left for a later LLM.

## 9. Stage 4: Export

Export `pieces` to `corpus.jsonl`, one object per line.

## 9.1 Manual Metrics Import

`import-metrics` writes or updates `pieces` rows directly from platform exports.
This allows Statool to track X/Twitter, LinkedIn, TikTok, Instagram, YouTube, or
other platform performance before full media collection exists for each
platform.

`metric-template` supports `x`, `twitter`, `linkedin`, `tiktok`, `instagram`,
and `youtube`.

Primary platform metric defaults:

```text
X/Twitter       comments/replies
LinkedIn        engagement_rate
TikTok          completion_rate or watched_full_video_pct
Instagram       retention_rate
```

If the primary metric is unavailable, Statool falls back to derived
`engagement_rate` when possible, then `interaction_count`.

## 9.2 Metrics Report

`report` ranks normalized rows by the most actionable available signal:

1. `primary_metric_value`.
2. `engagement_rate`.
3. `interaction_count`.

It emits one JSON object with the ranked items, optional platform filter, row
counts, and the metric basis used for each piece.

## 9.3 Coverage Report

`coverage` summarizes the current corpus by platform. It reports row counts,
formats, metric sources, rows with any primary metric, rows with native primary
metrics, and rows still missing a primary metric. Use this to keep the README
coverage table aligned with the actual data surface as new platforms are added.

## 9.4 Creative Analytics

`analyze` groups rows by:

- `format`
- `hook_onscreen`
- `hook_spoken`
- `cta_spoken`

Each group is ranked by average primary metric, falling back to engagement rate
or interaction count when no native primary metric exists. `--min-count` should
be used to avoid over-reading single examples.

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
interaction_count INTEGER
engagement_rate   REAL
engagement_basis  TEXT
primary_metric_name TEXT
primary_metric_value REAL
primary_metric_basis TEXT
secondary_metrics TEXT
metric_source TEXT
metric_confidence TEXT
metrics_captured_at TEXT
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
