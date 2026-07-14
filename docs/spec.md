# Statool Profile Intelligence Specification

This document describes the implemented product contract. The detailed requirement
matrix and rollout rationale live in
[`profile-intelligence-plan.md`](profile-intelligence-plan.md).

## 1. Product outcome

Given one or more social profile URLs, Statool inventories recent content, selects one
exact performance objective, identifies a winning cohort, compares it with the
profile's eligible baseline and matched controls, and explains the observable
properties associated with stronger performance.

The initial complete surface is Instagram Reels and carousels. Existing TikTok,
YouTube, X, LinkedIn, Facebook, Threads, and local-file connectors remain available.

Statool reports associations, not causation. Every finding carries its comparison,
effect, sample, confidence, limitation, and evidence references.

## 2. Primary command

```bash
statool analyze-profile PROFILE_URL \
  --posts 100 \
  --goal views \
  --top 10 \
  --mode quick \
  --output-root references/profile
```

`content-reference` remains an executable alias for compatibility.

Multiple direct profiles are accepted. A website URL requires `--all` before its
discovered social profiles are analyzed.

### Quick mode

- Inventory recent post URLs and available metadata/metrics.
- Preserve captions, formats, publication dates, and durations.
- Enforce exact-objective eligibility.
- Compute caption and metadata features for the full eligible corpus.
- Label topic/style with the selected local semantic provider.
- Generate the report bundle with explicit missing transcript/OCR coverage.

### Deep mode

- Run quick inventory first.
- Select winners plus format/publication-window controls by default.
- Download and extract only that cohort, up to `--deep-limit`.
- Use `--deep-all` to process the full inventory explicitly.
- Produce word-timed transcripts, OCR blocks, and visual-cadence evidence.
- Resume completed collect/extract/normalize work on later runs.

## 3. Evidence modes

| Mode | Intended source | Evidence contract |
| --- | --- | --- |
| `public` | Competitor/public profile | Public metadata and metrics available to local connectors |
| `owned` | User-controlled account | API/export insights, including private metrics when provided |
| `local` | Supplied files/screenshots/exports | Only operator-supplied media, text, and metrics |

An owned/local CSV can be analyzed without profile scraping:

```bash
statool analyze-profile PROFILE_URL \
  --inventory-input analytics.csv \
  --evidence-mode owned \
  --goal shares
```

Statool never invents unavailable reach, retention, shares, saves, or completion data.

## 4. Objective and cohort contract

- Rank exactly one user-selected objective.
- Require metric name, numeric value, basis, unit, source, and capture time.
- Select one comparable metric signature; exclude unit/basis mismatches visibly.
- Never substitute interaction count for retention or mix counts with ratios.
- Keep the latest snapshot per post/signature for a run while retaining history.
- Winners are top `k`; baseline is the eligible profile corpus.
- Controls match format and publication-age band when samples support it.
- Categorical results report winner/corpus prevalence and enrichment.
- Continuous results use profile-derived quantile bands and matched median lift.
- Bootstrap intervals are deterministic for a configured seed.
- Groups below the minimum sample emit `insufficient_evidence`.

Confidence labels describe evidence strength, never causal certainty.

## 5. Feature contract

### Common writing

Caption, transcript, on-screen, and slide sources remain separate. Deterministic
measurements include words, characters, sentences, sentence length, lines,
paragraphs, questions, exclamations, numbers, bullets, emojis, imperatives,
first/second-person signals, certainty/hedging, lexical diversity, language, n-grams,
and exact source spans.

The report includes profile vocabulary and winner-versus-baseline enriched words and
phrases with example post IDs and excerpts.

### Reels

- Word-level timestamps and segment-level transcript evidence.
- Spoken word count, WPM, speech duration, and speech/video ratio.
- First-phrase words and seconds.
- Median word gap and pause counts at 300, 600, and 1,000 ms.
- Longest uninterrupted spoken run.
- On-screen text frequency/duration when timed evidence exists.
- Scene changes per minute kept as visual cadence, not writing evidence.
- Duration and rhythm comparisons across profile-derived bands.

Segment boundaries are never presented as measured pauses.

### Carousels

- Exact ordered slide count.
- OCR blocks with text, confidence, bounding box, and image dimensions when available.
- Total/cover/final/per-slide words, median/max density, block count, bullets,
  numbering, questions, headings, and caption/slide overlap.
- Versioned cover, slide-role, progression, and CTA labels.
- Per-slide evidence retained for report navigation.

### Semantic labels

The provider-independent analyzer returns structured, validated labels for topics,
intent, hook archetype, narrative structure, CTA, tone, slide roles, and Reel
structure. The default `rules` provider is deterministic and low-confidence. The
`ollama` provider is restricted to a local HTTP endpoint and records model/prompt
versions. Social content is delimited as untrusted data.

## 6. Durable pipeline

```text
profile intake
  -> profile + run record
  -> inventory + metric snapshots
  -> exact objective eligibility
  -> winner/control selection
  -> optional deep extraction
  -> deterministic features
  -> versioned semantic labels
  -> winner/baseline/control comparisons
  -> canonical report
  -> Markdown + HTML + JSON
```

The legacy staged commands remain compatible:

```text
collect -> extract -> normalize -> export
```

## 7. SQLite contract

Additive numbered migrations create or evolve:

- `profiles`
- `profile_runs`
- `pieces` profile/run/content/cadence fields
- `metric_observations`
- `text_artifacts`
- `visual_text_blocks`
- `piece_features`
- `semantic_labels`
- `analysis_runs`
- `findings`
- `finding_evidence`
- run/profile/piece-aware `failures`

Existing corpora migrate in place. Raw media stays local and is not embedded in report
exports.

## 8. Report contract

`findings.json`, `report.md`, and `report.html` are rendered from the same validated
object. Required sections are:

1. Profile/run configuration.
2. Coverage, metric signatures, capture range, and failures.
3. Ranked winners.
4. Format enrichment.
5. Duration/density bands.
6. Topics, intent, hooks, CTA, and tone.
7. Writing and vocabulary fingerprint.
8. Reel language/rhythm/pauses/cadence.
9. Carousel anatomy and slide patterns.
10. What to repeat and test.
11. What cannot be concluded.
12. Evidence appendix.

HTML escapes all untrusted text and activates only validated HTTP(S) links.

## 9. Authentication and trust

- Cookies are accepted only through an explicit `--cookies` path.
- Browser cookie stores are never inspected.
- Token/cookie contents are never written to SQLite, reports, or failures.
- API credentials remain environment variables or CLI inputs for the existing fetchers.
- Captions, transcripts, OCR, and comments are data, never executable/model instructions.

## 10. Outputs

```text
output-root/
  corpus.sqlite
  findings.json
  report.md
  report.html
  raw/         # deep/local only
  processed/   # deep/local only
```

The report remains useful when evidence is incomplete: unavailable channels appear as
coverage/limitations and are never converted into measured zeroes.
