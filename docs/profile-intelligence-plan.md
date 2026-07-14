# Statool Profile Intelligence Improvement Plan

**Status:** Implemented; live Instagram acquisition validation pending
**Created:** 2026-07-13
**Repository:** `eugeniatel/content-analysis`
**Primary product scope:** Instagram profiles, Reels, and carousels
**Secondary scope:** TikTok video reuse after the Instagram workflow is proven

### Implementation checkpoint — 2026-07-13

Implemented in the repository:

- `statool analyze-profile` quick/deep orchestration, direct/multiple profiles,
  website confirmation, and offline owned/local inventory CSVs;
- additive numbered SQLite migrations, profile/run state, historical metric
  observations, evidence, versioned features/labels, findings, and failure linkage;
- deterministic common-writing, lexical, Reel timing/pause/cadence, and ordered
  carousel OCR-block feature engines;
- local rule-based and Ollama semantic analyzers with validated schemas and evidence;
- exact-objective winner/baseline/matched-control analytics, quantile bands,
  deterministic bootstrap intervals, and explicit insufficient-evidence findings;
- one canonical report rendered to Markdown, escaped HTML, and JSON;
- compatibility coverage for the original staged commands and `content-reference`
  executable.

Still required before calling public Instagram acquisition production-proven:

- run the acquisition fixture against user-approved real profiles, with an explicit
  cookies file if Instagram requires it;
- inspect the resulting live coverage, content URL canonicalization, carousel order,
  and public metric availability;
- tune the human-labeled semantic evaluation set with real Spanish/English examples.

## 1. Outcome

Turn Statool from a corpus collector into a profile-intelligence tool that can:

1. Accept one or more social profile URLs.
2. Inventory a meaningful sample of their published content and available metrics.
3. Identify the content that performs best for a selected objective.
4. Compare winners with the creator's normal content and matched controls.
5. Explain which observable properties are associated with stronger performance.
6. Describe how the creator writes, speaks, structures Reels, and constructs carousels.
7. Produce a readable report whose claims link back to posts, slides, transcript spans,
   metrics, and extraction confidence.

The core user-facing question is:

> What content works best for this profile, where does it outperform, and what
> repeatable properties distinguish it from the rest of the profile?

The tool must report associations, not claim causation. A finding such as “long
Reels win” is valid only when long Reels are compared with the creator's relevant
baseline and the supporting sample is shown.

## 2. Product principles

### 2.1 Depth before platform breadth

The first complete workflow should deeply support Instagram Reels and carousels.
Existing connectors remain available, but adding more shallow platform connectors is
not part of this milestone. TikTok can reuse the Reel feature engine after the
Instagram workflow passes real-profile validation.

### 2.2 Profile-relative analysis

Raw metrics from unrelated creators, platforms, objectives, or metric types must not
be averaged together. Statool should first compare a post with other eligible posts
from the same profile and platform.

### 2.3 Winners require a baseline

The top ten posts alone cannot explain why they win. Every analysis must include:

- the winning cohort;
- the eligible corpus baseline;
- matched controls where enough data exists;
- feature prevalence in winners and in the baseline;
- sample size and confidence;
- exact supporting examples.

For example, ten winning Reels are weak evidence if 95% of the account is Reels,
but strong evidence if only 35% of the account is Reels.

### 2.4 Deterministic facts before semantic interpretation

Counts, durations, word rates, pause gaps, slide counts, text density, metric ranks,
and lift calculations should be deterministic. A local semantic model may classify
topics, tone, hook archetypes, and narrative roles, but every semantic label must be
versioned and tied to evidence.

### 2.5 Missing evidence is a first-class result

Statool must distinguish among captions, transcript text, on-screen text, slide text,
and inferred semantic labels. It must not describe how somebody speaks when transcript
coverage is missing. Reports should contain a visible coverage and limitations section.

### 2.6 Local-first and explicit authentication

Preserve the current local/OSS-first pipeline. Cookies remain an explicit user input;
Statool must never probe or extract browser cookies automatically. Tokens and cookie
contents must never be written to reports, SQLite, logs, or failure messages.

## 3. Scope

### 3.1 Version 1 scope

- Direct Instagram profile URLs and website-discovered Instagram profiles.
- A configurable inventory of recent posts, defaulting to 50 and supporting 100+.
- Reels, carousels, and static posts when they appear in the same inventory.
- Public metrics where available.
- Owned-account insights imported through authenticated APIs or analytics exports.
- Quick and deep analysis modes.
- Markdown, JSON, and HTML report outputs.
- Spanish and English text/transcript analysis.
- Profile-relative winner analysis with sample and confidence reporting.

### 3.2 Explicit non-goals for Version 1

- Claiming that an observed feature caused performance.
- Predicting that a future post will go viral.
- Inventing private retention, reach, save, or share data for competitor profiles.
- Full brand-design reconstruction, font recognition, or pixel-perfect template cloning.
- Automatic login, browser-cookie extraction, or bypassing platform access controls.
- Cross-platform raw-metric leaderboards.
- An autonomous content generator. Reports may expose reusable patterns, but content
  generation remains a downstream workflow.

## 4. Product requirements

### 4.1 Profile intake and inventory

- **PROF-01:** Accept one or more direct profile URLs.
- **PROF-02:** Reuse website social-profile discovery and require confirmation before
  running all discovered profiles.
- **PROF-03:** Canonicalize platform, handle, profile URL, and content URLs.
- **PROF-04:** Persist a profile as a first-class database entity.
- **PROF-05:** Inventory a configurable number of recent posts with publication time,
  format, caption, duration, media pointers, and available public metrics.
- **PROF-06:** Make repeated inventory runs idempotent and resumable.
- **PROF-07:** Record every skipped or failed post without aborting the profile run.
- **PROF-08:** Support `quick` and `deep` modes without changing the result schema.

### 4.2 Metric acquisition and eligibility

- **MET-01:** Store metric observations historically rather than overwriting the only
  copy of a metric.
- **MET-02:** Preserve metric name, value, basis, source, confidence, and capture time.
- **MET-03:** Separate public, authenticated API, analytics export, scrape, and manual
  metric sources.
- **MET-04:** Let the user select an objective such as `views`, `reach`, `retention`,
  `completion_rate`, `shares`, `saves`, `comments`, or `engagement_rate`.
- **MET-05:** Only rank rows with a comparable value for the selected objective.
- **MET-06:** Never silently substitute interactions for retention, or a percentage
  for an absolute count, inside one ranking.
- **MET-07:** Report objective coverage before winner analysis begins.
- **MET-08:** Preserve raw metric values even when relative scores are calculated.

### 4.3 Common writing features

- **TEXT-01:** Count caption, transcript, on-screen, and slide words separately.
- **TEXT-02:** Calculate character count, sentence count, average sentence length,
  paragraph/line count, question count, imperative markers, and lexical diversity.
- **TEXT-03:** Extract repeated unigrams, bigrams, and trigrams for the full profile.
- **TEXT-04:** Identify words and phrases enriched in winners versus the baseline.
- **TEXT-05:** Measure first-person, second-person, certainty, hedging, and direct-address
  signals using deterministic rules where possible.
- **TEXT-06:** Keep exact source spans for every quoted phrase.
- **TEXT-07:** Detect the primary language per item instead of assuming all content is
  Spanish.

### 4.4 Carousel anatomy

- **CAR-01:** Preserve ordered images and exact slide count.
- **CAR-02:** Preserve OCR blocks with bounding boxes, confidence, and image dimensions;
  do not flatten a slide into one string before analysis.
- **CAR-03:** Calculate words per slide, total words, cover words, median and maximum
  density, block count, bullet count, numbering, and question usage.
- **CAR-04:** Classify slide roles such as cover/hook, context, problem, explanation,
  step, example, proof, summary, and CTA.
- **CAR-05:** Classify carousel progression such as list, tutorial, narrative,
  problem-solution, argument, before/after, or reference guide.
- **CAR-06:** Compare caption content with slide content and label it as repeating,
  summarizing, or complementing.
- **CAR-07:** Analyze cover formulas, final-slide formulas, and winning length/density
  bands.
- **CAR-08:** Preserve a per-slide evidence view in the final report.

### 4.5 Reel language and rhythm

- **REEL-01:** Produce word-level timestamps in addition to transcript segments.
- **REEL-02:** Calculate total spoken words, words per minute, speech duration, and
  speech-to-video ratio.
- **REEL-03:** Calculate first-phrase words and seconds, median word gap, pause counts
  above 300 ms, 600 ms, and 1,000 ms, and the longest uninterrupted spoken run.
- **REEL-04:** Preserve sentence/segment lengths and hook/CTA timestamps.
- **REEL-05:** Calculate on-screen text frequency and text-card duration when evidence
  supports it.
- **REEL-06:** Calculate scene-change rate without treating it as writing evidence.
- **REEL-07:** Analyze performance across continuous duration bands and profile-derived
  quantiles, not only the current short/mid/long labels.
- **REEL-08:** Keep transcript, on-screen text, and visual cadence as separate evidence
  channels.

### 4.6 Semantic content and style features

- **SEM-01:** Apply multi-label topics to each post.
- **SEM-02:** Classify content intent: educational, opinion, story, tutorial, list,
  news, prediction, case study, promotion, or other versioned labels.
- **SEM-03:** Classify hook archetype: question, direct claim, promise, problem,
  contrarian statement, number/list, curiosity gap, warning, or other.
- **SEM-04:** Classify narrative structure and CTA type.
- **SEM-05:** Describe tone and delivery through versioned dimensions such as formal,
  conversational, technical, emotional, humorous, urgent, reassuring, direct, and
  hedged.
- **SEM-06:** Require valid structured output, model/prompt version, confidence, and
  evidence spans for every semantic label.
- **SEM-07:** Mark an item `not_analyzable` for a channel when the required source text
  is absent.
- **SEM-08:** Support a local model provider first and keep the analyzer interface
  provider-independent.

### 4.7 Winner and pattern analysis

- **WIN-01:** Rank the top `k` eligible posts for the selected objective.
- **WIN-02:** Show format distribution in winners and in the full eligible corpus.
- **WIN-03:** Calculate feature enrichment: winner prevalence divided by baseline
  prevalence.
- **WIN-04:** Calculate metric lift and relative percentile for categorical and
  continuous feature bands.
- **WIN-05:** Create matched controls by format and publication-age band; add topic
  matching when the sample supports it.
- **WIN-06:** Analyze continuous relationships for duration, word count, slide count,
  density, WPM, and pause behavior.
- **WIN-07:** Analyze selected interactions such as format × topic and duration × topic
  only when every compared group meets the minimum sample threshold.
- **WIN-08:** Attach sample size, interval, and confidence label to every finding.
- **WIN-09:** Explicitly emit `insufficient_evidence` instead of suppressing weak or
  unsupported comparisons.
- **WIN-10:** Use associative wording in generated prose.

### 4.8 Reports and reusable output

- **REP-01:** Generate `report.md`, `report.html`, and `findings.json` from the same
  canonical report object.
- **REP-02:** Include data coverage, extraction failures, and unavailable private
  metrics before conclusions.
- **REP-03:** Include ranked winners with URLs, objective values, and relative scores.
- **REP-04:** Include sections for formats, duration, topics, writing fingerprint,
  words/phrases, Reel rhythm, and carousel anatomy.
- **REP-05:** Every finding must contain claim, objective, comparison, effect/lift,
  sample size, confidence, and evidence references.
- **REP-06:** Make every post, transcript timestamp, and carousel slide evidence item
  navigable from HTML and identifiable in Markdown/JSON.
- **REP-07:** Include “what to repeat,” “what to test,” and “what cannot be concluded”
  without converting associations into guarantees.
- **REP-08:** Escape all untrusted source text in HTML output.

### 4.9 Compatibility and operations

- **OPS-01:** Preserve existing `collect`, `extract`, `normalize`, `export`, `report`,
  `coverage`, and `analyze` behavior until migration is documented.
- **OPS-02:** Add the `statool` executable while retaining `content-reference` as a
  compatibility alias for at least one release.
- **OPS-03:** Apply additive, versioned SQLite migrations to existing corpora.
- **OPS-04:** Cache raw media, extracted artifacts, features, and semantic labels by
  source hash plus extractor version.
- **OPS-05:** Allow interrupted deep runs to resume without downloading or transcribing
  completed items again.
- **OPS-06:** Keep failure records queryable by run, profile, post, and stage.
- **OPS-07:** Keep raw media local and out of exports unless explicitly requested.

### 4.10 Safety and trust

- **SAFE-01:** Never persist or render cookie contents, API tokens, or authorization
  headers.
- **SAFE-02:** Treat captions, transcripts, OCR, and comments as untrusted data rather
  than model or tool instructions.
- **SAFE-03:** Escape every untrusted string and validate every URL rendered in HTML.
- **SAFE-04:** Block release while any unresolved High-severity threat remains.
- **SAFE-05:** Keep raw media local by default and require an explicit option before
  embedding or redistributing it.

## 5. Target user experience

### 5.1 Single-profile analysis

```bash
statool analyze-profile \
  "https://www.instagram.com/example/" \
  --posts 100 \
  --goal views \
  --top 10 \
  --mode deep \
  --output-root references/example
```

Expected outputs:

```text
references/example/
  corpus.sqlite
  report.md
  report.html
  findings.json
  raw/
  processed/
```

### 5.2 Multiple profiles

```bash
statool analyze-profile \
  "https://www.instagram.com/example-a/" \
  "https://www.instagram.com/example-b/" \
  --posts 75 \
  --goal shares \
  --mode quick
```

The initial result remains one report per profile. A later comparison report may use
relative scores, but must not compare raw views or rates with different denominators.

### 5.3 Quick versus deep mode

`quick` mode:

- inventories the requested post count;
- captures captions, formats, dates, durations, and available metrics;
- calculates metadata/caption features;
- chooses the winning cohort and a stratified control cohort;
- clearly marks unavailable transcript, slide, and visual features.

`deep` mode:

- performs media download, transcription, OCR, word timing, and deeper feature
  extraction;
- analyzes all eligible posts when explicitly requested;
- otherwise deep-processes winners plus matched and stratified controls while keeping
  the full inventory as the metric baseline.

## 6. End-to-end pipeline

```text
profile intake
  -> canonical profile + run record
  -> shallow inventory + metric snapshot
  -> coverage and objective eligibility gate
  -> winning cohort + matched/stratified controls
  -> collect media for the requested deep cohort
  -> transcript / word timing / OCR blocks / scenes
  -> deterministic feature extraction
  -> semantic labeling with evidence
  -> profile-relative comparisons and confidence
  -> canonical findings object
  -> Markdown / HTML / JSON reports
```

Each stage writes durable state so it can be rerun independently. The existing
`collect -> extract -> normalize -> export` contract remains valid; the new orchestration
adds `inventory -> featurize -> classify -> compare -> report` around it.

## 7. Acquisition and coverage contract

Statool should expose three explicit evidence modes:

| Mode | Intended use | Available evidence |
| --- | --- | --- |
| Public | Competitor or public profile | Publicly accessible formats, captions, media, views/likes/comments where available |
| Owned | User controls the account | Public evidence plus API/export reach, retention, saves, shares, clicks, and other available insights |
| Local | User supplies media/screenshots/exports | Supplied media, OCR/transcript, and imported metrics |

Before analysis, produce a coverage object containing:

- requested and inventoried post counts;
- count by format;
- target-metric eligible count and percentage;
- transcript, word-timing, on-screen text, and carousel OCR coverage;
- semantic-label coverage;
- metric sources and capture-time range;
- failures by stage;
- public/owned/local mode.

Default confidence policy:

- Fewer than 20 eligible posts: descriptive report only.
- 20–29 eligible posts: low-confidence comparisons.
- 30 or more eligible posts: pattern analysis enabled.
- Any feature group with fewer than 5 examples: no comparative claim.

The thresholds should be configurable and included in the analysis-run record.

## 8. Data model evolution

Use additive, numbered migrations rather than extending the current `ALTER TABLE if
missing` block indefinitely.

### 8.1 `profiles`

```text
id, platform, canonical_url, handle, display_name, primary_language,
created_at, last_inventoried_at
```

### 8.2 `profile_runs`

```text
id, profile_id, mode, requested_posts, selected_objective, top_k,
started_at, completed_at, status, config_json, coverage_json
```

### 8.3 `pieces` additions

```text
profile_id, platform_content_id, canonical_url, media_type,
inventory_run_id, latest_metric_snapshot_at
```

Existing rows remain valid with nullable `profile_id` and can be backfilled from creator
and URL when the mapping is unambiguous.

### 8.4 `metric_observations`

```text
id, piece_id, metric_name, metric_value, metric_unit, denominator_name,
metric_basis, source, confidence, captured_at, raw_json
```

The latest values may still be materialized on `pieces` for compatibility, but history
and analysis must use the observation table.

### 8.5 `text_artifacts`

```text
id, piece_id, kind, language, text, start_sec, end_sec,
slide_index, source_path, confidence, extractor_version
```

Kinds include caption, transcript segment, transcript word, on-screen block, slide
block, and operator-provided text.

### 8.6 `visual_text_blocks`

```text
id, piece_id, slide_index, timestamp_sec, text, confidence,
x, y, width, height, image_width, image_height, extractor_version
```

### 8.7 `piece_features`

```text
piece_id, feature_version, computed_at,
common_json, reel_json, carousel_json, lexical_json
```

Frequently queried values such as word count, slide count, duration, WPM, and pause
counts may also be stored as typed columns. The JSON payload allows the feature contract
to evolve without a migration for every experimental feature.

### 8.8 `semantic_labels`

```text
id, piece_id, label_version, dimension, label, confidence,
model_provider, model_name, prompt_version, evidence_json, created_at
```

### 8.9 `analysis_runs`, `findings`, and `finding_evidence`

```text
analysis_runs: id, profile_run_id, objective, cohort_config_json,
               feature_version, label_version, started_at, completed_at

findings: id, analysis_run_id, dimension, feature, claim, comparison,
          effect_value, effect_unit, winner_prevalence, baseline_prevalence,
          sample_winner, sample_baseline, interval_json, confidence, status

finding_evidence: finding_id, piece_id, artifact_id, slide_index,
                  start_sec, end_sec, excerpt, role
```

## 9. Feature contracts

### 9.1 Common deterministic features

- Source-specific word and character counts.
- Sentence, line, and paragraph counts.
- Average, median, and maximum sentence length.
- Questions, exclamations, numbers, bullets, emojis, and imperatives.
- First- and second-person markers.
- Lexical diversity and repeated n-grams.
- Hook length and CTA length when those spans exist.
- Caption-to-media text overlap.
- Language and extraction coverage.

### 9.2 Carousel deterministic features

- Slide count and block count.
- Total, cover, final-slide, and per-slide word counts.
- Median, minimum, maximum, and variance of words per slide.
- Text area and block-area ratios from OCR bounding boxes.
- Bullets, numbering, questions, and headings per slide.
- Cover and final-slide structural features.
- Caption/slide overlap.

Semantic processing adds slide roles, progression, hook type, CTA type, and content
topics without overwriting deterministic measurements.

### 9.3 Reel deterministic features

- Duration and duration percentile within the profile.
- Spoken word count and WPM.
- First phrase and hook timing.
- Word-gap distribution.
- Pause counts at configured thresholds.
- Longest uninterrupted spoken run.
- Speech-to-video ratio.
- On-screen block count and appearance rate.
- Scene-change count/rate as a visual-cadence feature.

Pause analysis requires word timestamps. Segment boundaries must not be presented as
measured pauses.

### 9.4 Lexical distinctiveness

Calculate both:

1. Profile fingerprint: terms disproportionately frequent for the creator compared
   with a configurable background corpus.
2. Winner fingerprint: terms disproportionately frequent in winners compared with the
   creator's eligible baseline.

Apply minimum frequency, language-aware tokenization, and stop-word handling. Every
reported term should link to example posts and source excerpts.

### 9.5 Semantic analyzer contract

The semantic analyzer receives only clearly delimited source data and returns validated
JSON. Social content is untrusted input and cannot issue instructions to the analyzer.

Required output:

```text
topics[]
content_intent[]
hook_archetype
narrative_structure[]
cta_type
tone_dimensions{}
slide_roles[] or reel_structure[]
evidence[]
confidence
not_analyzable[]
```

Before enabling semantic findings in reports, create a small human-labeled evaluation
set covering Spanish/English Reels, carousels, missing transcripts, OCR noise, and
ambiguous topics. Store model, prompt, schema, and evaluation versions.

## 10. Winner-analysis methodology

### 10.1 Objective selection

The user chooses one objective. Statool reports secondary metrics alongside it but does
not blend them into the ranking unless a separately documented composite is explicitly
selected.

### 10.2 Eligibility

A post is eligible when it has:

- a comparable observation for the selected objective;
- a known metric source and capture time;
- the minimum fields needed for the requested comparison.

The analysis never ranks a fallback interaction count against a completion rate.

### 10.3 Cohorts

- **Winners:** top `k` or top percentile by the selected objective.
- **Baseline:** every other eligible post from the profile and platform.
- **Matched controls:** non-winners matched by format and publication-age band; add topic
  matching only with sufficient samples.
- **Format controls:** within-format comparisons for duration, wording, and structure.

### 10.4 Time and audience effects

Always expose raw values. Also calculate relative ranks within comparable publication
windows or age bands. Do not claim that `views / days` perfectly removes time effects.
When historical metric snapshots exist, prefer comparable post-age snapshots.

### 10.5 Categorical properties

For format, topic, hook type, carousel progression, CTA type, and tone labels, calculate:

- winner count and prevalence;
- baseline count and prevalence;
- enrichment ratio;
- median objective value and relative percentile in each group;
- effect/lift versus baseline or matched controls;
- interval and confidence.

### 10.6 Continuous properties

For duration, words, WPM, pauses, slide count, and density:

- show the observed distribution;
- compare profile-derived quantiles or data-supported bands;
- avoid declaring a precise “sweet spot” from a tiny group;
- report sample counts for each band;
- preserve the continuous values in `findings.json`.

### 10.7 Confidence

Use bootstrap intervals or another documented resampling method for median lift and
prevalence differences. Initial labels:

- **High:** minimum group sample is at least 15 and the interval excludes the neutral
  value.
- **Medium:** minimum group sample is at least 8 with a stable directional result.
- **Low:** minimum group sample is 5–7 or the interval crosses neutral.
- **Insufficient:** minimum group sample is below 5 or required evidence is missing.

These labels describe evidence strength, not causal certainty.

## 11. Report contract

### 11.1 Required sections

1. Profile and run configuration.
2. Data and metric coverage.
3. Top posts for the selected objective.
4. Where the profile wins by format.
5. Duration and density relationships.
6. Topics and content intents associated with performance.
7. Writing and vocabulary fingerprint.
8. Reel language, rhythm, pauses, and structure.
9. Carousel anatomy and slide-by-slide patterns.
10. Repeatable patterns and suggested tests.
11. Unsupported conclusions and extraction failures.
12. Evidence appendix.

### 11.2 Finding schema

Every prose finding is rendered from a structured object containing:

```json
{
  "claim": "Reels between 45 and 70 seconds outperform the profile's other Reels",
  "objective": "views",
  "comparison": "45-70s Reels vs other eligible Reels",
  "effect": {"value": 1.7, "unit": "median_ratio"},
  "winner_prevalence": 0.6,
  "baseline_prevalence": 0.18,
  "sample": {"feature": 19, "control": 24},
  "confidence": "medium",
  "evidence": ["piece-id-1", "piece-id-2"],
  "caveat": "Association within this profile; not a causal estimate"
}
```

### 11.3 Example report language

Preferred:

> Eight of the ten most-viewed posts are Reels, while Reels are 46% of the eligible
> corpus (1.74× enrichment). Reels in the 45–70 second band have 1.7× the median views
> of matched Reels (`n=19` versus `n=24`, medium confidence).

Not allowed:

> Long Reels cause this creator to go viral.

## 12. Implementation architecture

The current single CLI module should be separated incrementally while features are
added. Avoid a large refactor with no user-visible slice.

Target package layout:

```text
src/content_reference_toolkit/
  cli.py
  db.py
  models.py
  migrations/
  connectors/
    base.py
    instagram.py
    tiktok.py
    exports.py
  pipeline/
    inventory.py
    collect.py
    extract.py
    normalize.py
    orchestrate.py
  extractors/
    transcript.py
    ocr.py
    scenes.py
  features/
    common.py
    lexical.py
    carousel.py
    reel.py
    semantic.py
  analytics/
    eligibility.py
    cohorts.py
    comparisons.py
    confidence.py
  reporting/
    schema.py
    markdown.py
    html.py
    templates/
```

Compatibility wrappers may remain in `cli.py` while implementation moves behind stable
function signatures.

## 13. Execution roadmap

| Plan | Wave | Deliverable | Depends on |
| --- | --- | --- | --- |
| 00 | 0 | Instagram acquisition feasibility and evidence fixture | None |
| 01 | 1 | Contracts, migrations, and compatibility foundation | 00 |
| 02 | 2 | Profile inventory, run tracking, and metric history | 01 |
| 03 | 2 | Common text and lexical feature engine | 01 |
| 04 | 3 | Carousel block extraction and anatomy | 02, 03 |
| 05 | 3 | Reel word timing, pauses, and rhythm | 02, 03 |
| 06 | 3 | Versioned semantic analyzer and evaluation set | 03 |
| 07 | 4 | Winner cohorts, matched controls, lift, and confidence | 02, 04, 05, 06 |
| 08 | 5 | Canonical findings and Markdown/HTML/JSON reports | 07 |
| 09 | 6 | Real-profile validation, hardening, docs, and release | 08 |

### Plan 00 — Instagram acquisition feasibility and evidence fixture

**Requirements covered:** PROF-01, PROF-05, PROF-07, SAFE-01, SAFE-05

Tasks:

1. Use one user-approved Reel-heavy profile and one carousel-heavy profile to test
   recent-post enumeration through the currently supported public and explicit-cookie
   paths.
2. Record which profile, Reel, carousel, caption, publication-date, duration, and public
   metric fields are actually available through each connector mode.
3. Test the failure and recovery path when a profile or carousel requires authentication.
4. Define the Version 1 Instagram connector contract and its ordered fallbacks: public
   extraction, explicit cookies, owned-account API/export, explicit URL manifest, and
   local media.
5. Save a redacted fixture manifest that represents the confirmed data shapes without
   committing cookies, tokens, or copyrighted media.

Acceptance criteria:

- Each pilot profile produces either a bounded URL inventory with format metadata or a
  documented, usable fallback path.
- Reel and carousel enumeration limitations are recorded before schema and orchestration
  work begins.
- No test reads browser cookies automatically or persists secret contents.
- The fixture manifest is sufficient to implement offline inventory tests.
- Plan 01 does not begin until the direct-profile acquisition path or fallback contract
  is explicitly selected.

### Plan 01 — Contracts, migrations, and compatibility foundation

**Requirements covered:** OPS-01, OPS-02, OPS-03, OPS-07, SAFE-01, SAFE-04,
SAFE-05

Tasks:

1. Add typed contracts for profiles, runs, metric observations, artifacts, features,
   semantic labels, and findings.
2. Introduce numbered, transactional SQLite migrations.
3. Add the new tables without removing current `pieces` columns.
4. Add the `statool` script alias while preserving `content-reference`.
5. Extract database and model helpers from `cli.py` behind compatibility functions.

Acceptance criteria:

- A database created by the current release opens and migrates without data loss.
- Running migrations twice produces no additional schema changes.
- Both CLI executable names display the same command set.
- All existing tests pass unchanged or with compatibility-only import updates.
- New migration rollback guidance is documented, even if automatic down-migrations are
  intentionally unsupported.

### Plan 02 — Profile inventory, run tracking, and metric history

**Requirements covered:** PROF-01–PROF-08, MET-01–MET-03, MET-07, MET-08,
OPS-04–OPS-06

Tasks:

1. Implement profile canonicalization and `profiles` persistence.
2. Implement shallow inventory with configurable limits.
3. Store profile runs and per-post inventory membership.
4. Write every metric capture to `metric_observations` and materialize latest compatible
   values for existing commands.
5. Implement coverage calculation and objective eligibility gates.
6. Implement quick/deep cohort selection and resumable run state.

Acceptance criteria:

- A fixture profile inventory creates one profile, one run, and the expected unique
  pieces.
- Re-running the same inventory does not duplicate profiles or pieces.
- Two metric captures for one piece produce two observations and one latest value.
- A requested objective with no eligible posts returns a coverage result and no winner
  claims.
- Failures are associated with both profile run and source URL.

### Plan 03 — Common text and lexical feature engine

**Requirements covered:** TEXT-01–TEXT-07, OPS-04

Tasks:

1. Normalize caption, transcript, on-screen, and slide artifacts without merging them.
2. Implement language-aware deterministic counts.
3. Implement n-grams and winner-versus-baseline lexical comparison inputs.
4. Add feature versioning, caching, and explicit missing-source flags.
5. Build fixtures for Spanish, English, punctuation-heavy, and empty text.

Acceptance criteria:

- Counts match hand-calculated fixture values.
- Reprocessing with the same feature version is idempotent.
- Changing the feature version creates a new feature result rather than silently
  mutating provenance.
- Empty transcript data cannot produce spoken-language features.

### Plan 04 — Carousel block extraction and anatomy

**Requirements covered:** CAR-01–CAR-08, OPS-04

Tasks:

1. Preserve EasyOCR/Tesseract block coordinates and image dimensions.
2. Create ordered slide/block artifacts.
3. Calculate cover, body, final-slide, density, bullet, numbering, and overlap features.
4. Add deterministic heuristics for cover/final-slide identification.
5. Prepare semantic slide-role inputs without assigning unsupported roles.
6. Add a slide evidence renderer used by reports.

Acceptance criteria:

- An eight-slide fixture remains eight ordered slides.
- Known cover and per-slide word counts match fixture expectations.
- OCR boxes survive normalization and export.
- A missing or unreadable slide is visible in coverage and does not shift later slide
  indices.
- Carousel analysis never uses Reel duration labels.

### Plan 05 — Reel word timing, pauses, and rhythm

**Requirements covered:** REEL-01–REEL-08, OPS-04

Tasks:

1. Enable word-level timestamps in supported transcription backends.
2. Normalize backend-specific word outputs into one contract.
3. Implement WPM, first-phrase, word-gap, pause, speech-ratio, and uninterrupted-run
   features.
4. Keep scene and on-screen text features in separate namespaces.
5. Add a segment-only compatibility path that marks pause features unavailable.

Acceptance criteria:

- A synthetic word-timing fixture produces exact pause counts for all thresholds.
- Segment-only transcripts do not claim measured pauses.
- Word timestamps remain linked to source media and transcript segments.
- Existing segment transcript exports continue to work.

### Plan 06 — Versioned semantic analyzer and evaluation set

**Requirements covered:** SEM-01–SEM-08, SAFE-02, OPS-04

Tasks:

1. Define the semantic JSON schema and provider interface.
2. Implement a local-model adapter and a deterministic `disabled` adapter.
3. Delimit social text as untrusted evidence and validate every response.
4. Store labels, confidence, evidence, prompt, schema, and model versions.
5. Build and hand-label a small bilingual evaluation set.
6. Add evaluation commands for topic, hook, intent, CTA, tone, narrative, and slide-role
   agreement.

Acceptance criteria:

- Invalid model JSON is rejected and recorded as a failure.
- Every accepted label contains at least one valid evidence reference or is marked as a
  whole-item classification under the documented policy.
- Missing transcript prevents spoken-style labels but still permits caption/slide labels.
- The evaluation report is reproducible from versioned fixtures.
- No cloud model or credential is required for deterministic and local test runs.

### Plan 07 — Winner cohorts, matched controls, lift, and confidence

**Requirements covered:** MET-04–MET-08, WIN-01–WIN-10

Tasks:

1. Implement objective eligibility and top-`k` ranking without cross-unit fallback.
2. Implement baseline, format control, and matched control cohorts.
3. Implement categorical enrichment and continuous band comparisons.
4. Implement relative ranks, metric lift, resampling intervals, and confidence labels.
5. Implement minimum-sample and insufficient-evidence gates.
6. Produce structured findings with evidence IDs.

Acceptance criteria:

- A mixed-metric fixture never compares a rate with an absolute count.
- A 10/10 Reel winner cohort is reported differently when Reels are 95% versus 35% of
  the corpus.
- A group with fewer than five examples produces `insufficient_evidence`.
- Identical input and random seed produce identical intervals and findings.
- Every finding names its objective, cohorts, sample sizes, and evidence strength.

### Plan 08 — Canonical findings and reports

**Requirements covered:** REP-01–REP-08, SAFE-03

Tasks:

1. Define the canonical report object.
2. Render Markdown, escaped HTML, and JSON from that object.
3. Implement all required report sections and evidence navigation.
4. Add wording rules that prevent causal overstatement.
5. Add `analyze-profile` orchestration and output summaries.
6. Add golden report fixtures.

Acceptance criteria:

- All three output formats contain the same finding IDs and core values.
- HTML escapes captions, OCR, and transcript text containing markup or scripts.
- No report describes spoken style when spoken coverage is zero.
- Every rendered finding links to or identifies its evidence.
- A completed CLI run prints output paths, coverage, and the number of supported versus
  insufficient findings.

### Plan 09 — Real-profile validation, hardening, docs, and release

**Requirements covered:** OPS-01–OPS-07, SAFE-01–SAFE-05; release verification for
all PROF, MET, TEXT, CAR, REEL, SEM, WIN, and REP requirements

Tasks:

1. Run one real Reel-heavy and one carousel-heavy profile through quick mode.
2. Deep-process a bounded winner/control sample from each profile.
3. Manually review transcript accuracy, OCR ordering, semantic labels, and report claims.
4. Record extraction and analysis precision issues as fixtures or tests.
5. Document public versus owned limitations, auth boundaries, runtime expectations,
   recovery, and cleanup.
6. Update README/spec only after the actual implemented surface is verified.

Acceptance criteria:

- At least one real Reel and one real carousel are manually inspected end to end.
- Every discovered overclaim or extraction failure becomes a regression test or an
  explicit documented limitation.
- Existing 29-test baseline and all new tests pass.
- Quick and interrupted-deep runs can be resumed.
- The README's capability table matches observed behavior, not planned behavior.

## 14. Walking-skeleton release slice

Before completing every plan, ship the smallest end-to-end proof behind an experimental
command:

```text
one profile URL
  -> 30-post shallow inventory
  -> top 10 by one comparable public metric
  -> baseline format/duration/caption word features
  -> Markdown report with coverage and evidence links
```

This proves the profile, metric, cohort, feature, and reporting contracts. It must not be
marketed as full language, pause, or carousel analysis until the corresponding plans are
complete.

## 15. Verification strategy

### 15.1 Unit tests

- URL/profile canonicalization.
- Database migrations and idempotency.
- Metric observation selection and eligibility.
- Text, lexical, carousel, Reel, and pause features.
- Cohort selection, enrichment, lift, intervals, and confidence.
- Structured semantic validation.
- HTML escaping and report schema parity.

### 15.2 Integration tests

- Fixture profile inventory through quick report.
- Local Reel through collect, word timing, features, findings, and report.
- Local carousel through OCR blocks, features, findings, and report.
- Missing transcript and partial OCR runs.
- Repeated and interrupted runs.
- Migration from a copy of the current schema.

### 15.3 Golden fixtures

Create versioned fixtures for:

- mixed-format profile inventory;
- mixed metric names and sources;
- eight-slide Spanish carousel;
- English and Spanish word-timed Reels;
- segment-only transcript;
- OCR failure and missing slide;
- semantic labels with evidence;
- full canonical report.

### 15.4 Real-profile UAT

Use user-provided URLs and explicit authentication only. For each pilot:

1. Confirm inventory completeness.
2. Spot-check metrics against the visible or exported source.
3. Spot-check winning and control media.
4. Compare transcript words and pauses with playback.
5. Compare OCR blocks and order with carousel images.
6. Review every high-confidence finding manually.
7. Confirm limitations are visible before sharing the report.

### 15.5 Required verification commands

```bash
pytest -q
python3 -m compileall src
git diff --check
```

Add an offline end-to-end fixture command once `analyze-profile` exists. Network-backed
or authenticated tests remain opt-in and must never be required for the default suite.

## 16. Threat model and safety requirements

| Threat | Severity | Mitigation |
| --- | --- | --- |
| Cookie or token leakage | High | Accept paths/env vars only; redact commands/errors; never persist secret contents |
| Social text prompt injection | High | Treat content as delimited data; fixed schema; validate output; disallow tool instructions from content |
| HTML injection in reports | High | Escape all untrusted text and URLs; test script/markup fixtures |
| Unauthorized scraping | High | Explicit cookies only; no bypass or automatic browser-session extraction; document platform terms |
| Misleading causal claims | Medium | Association-only templates, confidence, sample, baseline, and caveat required |
| Private metric invention | Medium | Coverage gate; unavailable metrics remain unavailable |
| Raw media redistribution | Medium | Local storage by default; reports contain evidence references, not embedded raw media |
| Model/version drift | Medium | Version prompts, schemas, models, features, fixtures, and evaluations |
| Destructive schema migration | Medium | Additive transactional migrations; backup guidance; migration tests with current schema |

No plan may pass verification with an unresolved High-severity threat.

## 17. Risks and mitigations

### Profile extraction is platform-fragile

Mitigation: connector contract, explicit auth modes, recorded failures, local/manual
fallbacks, and real-profile smoke tests before batch runs.

### Public metrics cannot answer every performance question

Mitigation: public versus owned mode, objective coverage, no private-metric inference,
and reports that explain exactly which questions the evidence can answer.

### Small samples create seductive but weak patterns

Mitigation: baselines, matched controls, minimum samples, intervals, confidence labels,
and explicit insufficient-evidence findings.

### OCR and transcripts can distort style analysis

Mitigation: source confidence, separate evidence channels, manual spot checks, preserved
raw artifacts, and no high-confidence semantic claims from low-coverage inputs.

### Semantic labels may drift

Mitigation: versioned schema/model/prompt, human-labeled evaluation fixtures, and
reproducible evaluation reports.

### The current CLI module is large

Mitigation: incremental extraction behind compatibility wrappers; no standalone rewrite
before the vertical profile slice works.

## 18. Requirement-to-request coverage

| Requested capability | Requirements | Primary plan |
| --- | --- | --- |
| Send profiles directly | PROF-01–PROF-08 | 02 |
| Identify the best content | MET-04–MET-08, WIN-01 | 02, 07 |
| Explain whether winners are Reels/carousels | WIN-02–WIN-05 | 07 |
| Long versus short Reels | REEL-07, WIN-04–WIN-06 | 05, 07 |
| Topics in winning content | SEM-01–SEM-02, WIN-03–WIN-08 | 06, 07 |
| How they write and talk | TEXT-01–TEXT-07, SEM-04–SEM-06 | 03, 06 |
| Which words and phrases they use | TEXT-03–TEXT-06 | 03, 07 |
| Pauses and speech rhythm | REEL-01–REEL-04 | 05 |
| How long their spoken phrases are | REEL-02–REEL-04 | 05 |
| How they construct carousels | CAR-01–CAR-08 | 04, 06 |
| Which carousel lengths/densities win | CAR-07, WIN-04–WIN-06 | 04, 07 |
| Evidence-backed explanation of where they win | WIN-08–WIN-10, REP-01–REP-08 | 07, 08 |
| Avoid claims when transcripts are missing | SEM-07, REP-02, REP-07 | 03, 06, 08 |

## 19. Recommended defaults and decisions

These defaults let implementation begin without blocking product questions:

- Start with Instagram, then reuse Reel logic for TikTok.
- Default inventory: 50 posts; allow 100+.
- Default top cohort: 10 posts.
- Default mode: `quick`; deep processing is explicit.
- Default objective: `views` only when coverage is sufficient; otherwise require the
  user to choose from available comparable objectives.
- Default semantic mode: disabled unless a configured local provider is available.
- Default reports: Markdown + JSON first in the walking skeleton; add HTML in Plan 08.
- Keep current command behavior and add `statool` as an alias before deprecating names.
- Preserve the explicit cookie boundary.

## 20. Definition of done

This improvement is complete when a user can provide an Instagram profile and receive a
report that, with traceable evidence:

1. Identifies the top content for a clearly named comparable objective.
2. Shows whether formats, durations, topics, and structural properties are enriched in
   winners relative to the profile baseline.
3. Describes writing, vocabulary, Reel speech/rhythm, and carousel construction only
   where the source evidence exists.
4. Reports sample size, lift/effect, confidence, and limitations for every conclusion.
5. Links every conclusion to posts, transcript timestamps, or carousel slides.
6. Never invents private metrics, spoken evidence, or causal explanations.
7. Runs locally, resumes safely, preserves current corpora, and passes the full offline
   verification suite.
