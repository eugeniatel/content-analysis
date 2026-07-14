# Statool

Statool is an open-source local CLI for researching social profiles, collecting
short-form social references, importing platform metrics, and turning all of it
into a structured corpus for content analysis.

The core user flow is link-first: share a social profile or website URL, let
Statool find the relevant social profiles/content, then choose whether to use
official APIs, analytics exports, best-effort scraping, or manual metric input.

The preferred CLI command is `statool`; `content-reference` remains as a
compatibility alias. Statool now produces evidence-backed profile reports as well
as a clean, queryable corpus.

## What Is Statool?

Statool is a local, cross-platform social content intelligence tool. Its complete
profile workflow is Instagram-first: inventory a profile, select one exact
performance objective, compare winners with the profile baseline, measure how the
creator writes/speaks/builds carousels, and render every conclusion with samples,
confidence, limitations, and post evidence.

## Analyze a profile

Quick analysis uses profile metadata, captions, formats, dates, durations, and
available metrics:

```bash
statool analyze-profile \
  "https://www.instagram.com/example/" \
  --posts 100 \
  --goal views \
  --top 10 \
  --mode quick \
  --output-root references/example
```

Deep analysis additionally downloads the winner cohort plus matched/stratified
controls, then runs transcription, word timing, OCR blocks, and scene analysis:

```bash
statool analyze-profile \
  "https://www.instagram.com/example/" \
  --posts 100 \
  --goal views \
  --top 10 \
  --mode deep \
  --deep-limit 30 \
  --cookies cookies.txt \
  --output-root references/example
```

Use `--deep-all` only when every inventoried post should be downloaded and
processed. Cookies are always explicit; Statool never reads browser cookies.

The command writes:

```text
references/example/
  corpus.sqlite       versioned profiles, runs, metrics, features, findings, evidence
  findings.json       canonical machine-readable report
  report.md           readable evidence-backed analysis
  report.html         escaped, navigable report
  raw/                local media cache (deep mode)
  processed/          transcripts, OCR, frames (deep mode)
```

Each report covers:

- exact-objective coverage and ranked winning posts;
- winner versus eligible-profile format share and enrichment;
- duration and density bands derived from the profile distribution;
- topics, intent, hook archetype, CTA, narrative structure, and tone;
- caption length, sentence length, questions, imperatives, pronouns, language,
  lexical diversity, repeated words/phrases, and winner-enriched vocabulary;
- Reel spoken words, WPM, first phrase, word gaps, measured pauses, speech ratio,
  on-screen text, and visual cadence;
- carousel slide count, ordered OCR blocks, cover/final slide, per-slide words,
  density, bullets, numbering, questions, caption overlap, roles, and progression;
- what to repeat, what to test, and what the evidence cannot establish.

Findings are associative, not causal. Statool never blends `views` with
`retention_rate`, counts with percentages, or one objective with a fallback signal
inside a profile ranking.

### Owned/local analytics export

Public Instagram extraction is best-effort and may require cookies. Private reach,
retention, saves, and shares must come from an owned-account API or export. An
offline CSV also makes runs reproducible:

```bash
statool analyze-profile \
  "https://www.instagram.com/example/" \
  --inventory-input exports/instagram-posts.csv \
  --evidence-mode owned \
  --posts 100 \
  --goal shares \
  --mode quick \
  --output-root references/example
```

The CSV uses the same flexible headers as `import-metrics`, including `Post URL`,
`Views`, `Reach`, `Share Count`, `Save Count`, `Completion Rate`, and
`Average Percentage Watched`. Include `creator`/`handle` or `profile_url` on
each row so Statool can verify that the export belongs to the requested profile.
For a deliberately pre-filtered file without either field, opt in explicitly with
`--allow-unverified-inventory`.

Metric comparability can be declared per column with fields such as
`views_basis`, `views_unit`, and `views_denominator` (or the generic
`metric_basis`, `metric_unit`, and `metric_denominator`). Statool preserves this
signature and excludes incompatible observations instead of blending, for example,
lifetime views with rolling-seven-day views.

## Platform Coverage

| Plataforma | Formatos | Estado |
| --- | --- | --- |
| Instagram | Reels, carousels, posts estaticos | Media cubierto; metricas via Graph API con media ID |
| TikTok | Videos | Cubierto |
| Local files | Videos, audios, imagenes | Cubierto como fallback/manual |
| X/Twitter | Posts | Metricas via API; media no cubierto |
| LinkedIn | Posts | Metricas via API; media no cubierto |
| YouTube Shorts | Shorts/video | Media via `yt-dlp`; metricas via API |
| Facebook/Threads | Posts/video | Metricas via Graph API con object ID |

## Platform Metric Priorities

Statool tracks generic metrics when they are available, but each platform should
also have a primary metric that reflects what the platform tends to reward.

| Plataforma | Metrica principal | Estado |
| --- | --- | --- |
| X/Twitter | comments/replies | Modelado; import manual CSV |
| LinkedIn | engagement rate % | Modelado; import manual CSV |
| TikTok | completion rate % | Modelado; import manual CSV |
| Instagram | retention rate + shares | Modelado; import manual CSV |

## Current Capabilities

- Descarga media y metadata con `yt-dlp` y `gallery-dl`.
- Trae metricas via APIs autenticadas para YouTube, X/Twitter y LinkedIn.
- Trae insights via Meta Graph API para Instagram, Facebook y Threads cuando se
  provee el object/media ID correspondiente.
- Acepta URLs, TXT, CSV y archivos locales.
- Importa CSVs manuales de metricas para plataformas todavia no cubiertas por
  descarga automatica, incluyendo headers comunes de exports como
  `Average Percentage Watched`, `Watched Full Video`, `Share Count`,
  `Engagement Rate` o `Post URL`.
- Extrae audio con `ffmpeg`.
- Transcribe videos con `faster-whisper` o Whisper CLI.
- Extrae texto en pantalla con EasyOCR o Tesseract.
- Detecta frames por escenas o intervalos.
- Hace OCR de slides de carousels.
- Conserva timestamps por palabra y bloques OCR con coordenadas/confianza.
- Normaliza a SQLite y JSONL.
- Guarda caption, creator, fecha, duracion, transcript, texto en pantalla,
  texto de slides, hook hablado, hook visual y CTA hablado.
- Registra metricas si vienen en metadata: `views`, `likes`, `comments`,
  `shares`.
- Calcula `interaction_count = likes + comments + shares`.
- Calcula `engagement_rate = interactions / views` cuando hay views.
- Guarda `primary_metric_name`, `primary_metric_value`,
  `primary_metric_basis`, `secondary_metrics`, `metric_source`,
  `metric_confidence` y `metrics_captured_at`.
- Genera reportes rankeados por metrica primaria, engagement rate o
  interacciones.
- Analiza performance por formato, hook visual, hook hablado y CTA hablado.
- Mantiene perfiles, corridas, snapshots historicos de metricas, artifacts de
  texto, features versionados, labels semanticos, findings y evidencia.
- Compara top-k con baseline y controles por formato/ventana temporal, calcula
  enrichment, lift, percentiles, intervalos bootstrap y confidence labels.
- Genera `report.md`, `report.html` y `findings.json` desde un solo objeto.
- Registra fallas por URL sin cortar todo el batch.

## Pipeline

```text
collect -> extract -> normalize -> export
```

The profile orchestration wraps that compatible pipeline with:

```text
inventory -> metric eligibility -> winner/control cohort -> deep extraction
          -> deterministic features -> semantic labels -> comparisons -> report
```

Each stage reads/writes disk plus SQLite, so stages can be rerun independently.
Items are idempotent by source hash unless `--force` is passed.

Output layout:

```text
references/
  raw/<slug-hash>/        media, metadata
  processed/<slug-hash>/  extracted.json, audio, frames, transcripts
  corpus.sqlite
  corpus.jsonl
```

## Install

```bash
cd /Users/euge/content-analysis
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Optional extraction stack:

```bash
brew install ffmpeg tesseract
.venv/bin/python -m pip install yt-dlp gallery-dl openai-whisper
.venv/bin/python -m pip install ".[extract]"
```

`faster-whisper`, `easyocr`, and `scenedetect` are optional because they are
large. The CLI falls back where possible:

- If `faster-whisper` is missing, it tries the `whisper` CLI.
- If `easyocr` is missing, it tries `tesseract`, mapping the requested OCR
  languages to installed tesseract packs (`es->spa`, `pt->por`, `en->eng`).
  Install the packs with `brew install tesseract-lang`. EasyOCR is still the
  more accurate engine for stylized social overlays.
- If `scenedetect` is missing, it samples frames every N seconds.

## Inputs

Inline URLs/files:

```bash
content-reference collect "https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/"
```

Link-first intake:

```bash
content-reference link-intake "https://example.com"
```

When a website link is shared, Statool scans the page for social profiles and
returns a confirmation prompt:

```text
I found these profiles [instagram: https://www.instagram.com/example, tiktok: https://www.tiktok.com/@example], would you like to run content-analysis on all of them?
```

After confirmation, run all discovered profiles:

```bash
content-reference research-link "https://example.com" --all --output-root references
```

For a direct social profile link, run:

```bash
content-reference research-link "https://www.tiktok.com/@example" --output-root references
```

TXT:

```text
https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/
https://www.tiktok.com/@marenaltman/video/7635083954688724254
```

CSV:

```csv
url,format_hint,notes
https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/,reel,astrology coverage
https://www.instagram.com/lu.gaitan/p/DXwWqZLDg27/,carousel,carousel writing density
```

## Usage From Another Repo

From `eugeniatel`:

```bash
cd /Users/euge/eugeniatel
/Users/euge/content-analysis/.venv/bin/content-reference collect \
  --input references/examples.csv \
  --output-root references

/Users/euge/content-analysis/.venv/bin/content-reference extract \
  --output-root references \
  --whisper-language es \
  --ocr-languages es,pt,en

/Users/euge/content-analysis/.venv/bin/content-reference normalize \
  --output-root references

/Users/euge/content-analysis/.venv/bin/content-reference export \
  --output-root references
```

All stages at once:

```bash
/Users/euge/content-analysis/.venv/bin/content-reference run \
  --input references/examples.csv \
  --output-root references \
  --export
```

Local carousel screenshots:

```bash
/Users/euge/content-analysis/.venv/bin/content-reference collect \
  ~/Downloads/carousel-slides/ \
  --output-root references \
  --notes "Lu Gaitan carousel reference"

/Users/euge/content-analysis/.venv/bin/content-reference extract \
  --output-root references
```

Manual platform metrics:

```bash
content-reference metric-template linkedin > references/linkedin-metrics.csv
```

```csv
url,platform,impressions,reactions,comments,reposts,engagement_rate
https://www.linkedin.com/feed/update/urn:li:activity:123/,linkedin,1000,40,10,5,5.5%
```

```bash
content-reference import-metrics \
  --input references/manual-metrics.csv \
  --output-root references
```

Authenticated platform metrics:

```bash
YOUTUBE_API_KEY=... content-reference fetch-metrics \
  --platform youtube \
  "https://www.youtube.com/shorts/VIDEO_ID" \
  --output-root references

X_BEARER_TOKEN=... content-reference fetch-metrics \
  --platform x \
  "https://x.com/user/status/1234567890" \
  --output-root references

LINKEDIN_ACCESS_TOKEN=... content-reference fetch-metrics \
  --platform linkedin \
  "https://www.linkedin.com/feed/update/urn:li:activity:1234567890/" \
  --output-root references

META_ACCESS_TOKEN=... content-reference fetch-metrics \
  --platform instagram \
  17895695668004550 \
  --metrics reach,plays,saved,shares \
  --output-root references
```

Choose an acquisition mode:

```bash
content-reference connector-options linkedin
content-reference setup-connector linkedin --mode api
content-reference setup-connector linkedin --mode scrape
```

Low-cost best-effort scraping:

```bash
content-reference scrape-metrics \
  --platform tiktok \
  "https://www.tiktok.com/@user/video/1234567890" \
  --output-root references
```

`scrape-metrics` uses public metadata tooling and only uses cookies when an
explicit `--cookies cookies.txt` file is supplied. It marks rows as
`metric_source=scrape_yt_dlp`, so scraped metrics stay separate from API and
export data.

Rank content by platform-native metrics:

```bash
content-reference report \
  --platform linkedin \
  --limit 10 \
  --output-root references
```

Check corpus coverage:

```bash
content-reference coverage --output-root references
```

Analyze creative patterns:

```bash
content-reference analyze \
  --platform tiktok \
  --min-count 2 \
  --output-root references
```

## Data Model

Normalized rows still live in `pieces` and export to JSONL. Additive SQLite
migrations also create:

- `profiles` and `profile_runs`
- `profile_run_pieces` (historical run membership and inventory snapshots)
- `metric_observations` (historical snapshots; analysis source of truth)
- `text_artifacts` and `visual_text_blocks`
- `piece_features` and `semantic_labels`
- `analysis_runs`, `findings`, and `finding_evidence`

Existing `pieces` rows remain compatible and gain nullable profile/run fields.
The corpus includes:

- source URL and platform
- format
- creator and publish date
- duration and duration bucket
- caption
- transcript segments
- onscreen text sequence
- slide text sequence
- spoken/on-screen hooks
- spoken CTA
- metrics when available
- interaction count (raw likes + comments + shares) as a ranking signal when
  views are unavailable
- engagement rate when computable, plus the basis (`views` or `interactions`)
- primary platform metric and secondary metrics when imported or computable
- metric source, confidence, and capture timestamp
- operator notes

Failures are recorded in SQLite table `failures`; batch runs continue after
per-URL errors.

## Auth

The CLI supports a manually supplied `cookies.txt` file via `--cookies`.
It intentionally does not auto-extract browser session cookies.

## Full Spec

See [docs/spec.md](docs/spec.md).
