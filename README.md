# Content Reference Toolkit

Open-source local pipeline for collecting short-form social references and
turning them into a structured corpus for later writing-guideline generation.

Primary use case: astrology content in Spanish for LATAM/Argentina, with
Instagram Reels, Instagram carousels, and TikTok videos as inputs.

The toolkit does **not** generate guidelines itself. It prepares clean,
queryable data that an LLM can read later.

## What Is Statool?

Statool is the working product direction for this repository: a local,
cross-platform social content analysis tool. Today it is implemented as the
`content-reference` CLI, focused on collecting content references and turning
them into a clean corpus for later analysis. As the project expands, this README
should be updated to reflect the current platform coverage, metrics, and
analysis features.

## Platform Coverage

| Plataforma | Formatos | Estado |
| --- | --- | --- |
| Instagram | Reels, carousels | Cubierto |
| TikTok | Videos | Cubierto |
| Local files | Videos, audios, imagenes | Cubierto como fallback/manual |
| X/Twitter | Posts, threads, videos | No cubierto |
| LinkedIn | Posts, carousels/docs, video | No cubierto |
| YouTube Shorts | Shorts/video | No explicito |
| Facebook/Threads | Posts/video | No cubierto |

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
- Registra fallas por URL sin cortar todo el batch.

## Pipeline

```text
collect -> extract -> normalize -> export
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
cd /Users/euge/content-reference-toolkit
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
/Users/euge/content-reference-toolkit/.venv/bin/content-reference collect \
  --input references/examples.csv \
  --output-root references

/Users/euge/content-reference-toolkit/.venv/bin/content-reference extract \
  --output-root references \
  --whisper-language es \
  --ocr-languages es,pt,en

/Users/euge/content-reference-toolkit/.venv/bin/content-reference normalize \
  --output-root references

/Users/euge/content-reference-toolkit/.venv/bin/content-reference export \
  --output-root references
```

All stages at once:

```bash
/Users/euge/content-reference-toolkit/.venv/bin/content-reference run \
  --input references/examples.csv \
  --output-root references \
  --export
```

Local carousel screenshots:

```bash
/Users/euge/content-reference-toolkit/.venv/bin/content-reference collect \
  ~/Downloads/carousel-slides/ \
  --output-root references \
  --notes "Lu Gaitan carousel reference"

/Users/euge/content-reference-toolkit/.venv/bin/content-reference extract \
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

Normalized rows live in SQLite table `pieces` and export to JSONL with:

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
