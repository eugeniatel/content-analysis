from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any

from .analytics import analyze_winners
from .evidence import FEATURE_VERSION, persist_piece_features
from .features import (
    analyze_carousel,
    analyze_reel,
    analyze_text_sources,
    aggregate_ngrams,
    winner_baseline_inputs,
)
from .reporting import build_report, write_report_bundle
from .semantic import LABEL_VERSION, RuleBasedSemanticAnalyzer, SemanticAnalyzer, persist_semantic_result


def _decode(value: Any) -> Any:
    if not value:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _format(row: dict[str, Any]) -> str:
    media_type = str(row.get("media_type") or "").casefold()
    if media_type and media_type != "unknown":
        return media_type
    return str(row.get("format") or "unknown").casefold()


def _flatten_words(transcript: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if transcript is None:
        return None
    words: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(transcript):
        for raw in segment.get("words") or []:
            item = dict(raw)
            item.setdefault("segment_index", segment_index)
            if "confidence" not in item and item.get("probability") is not None:
                item["confidence"] = item.get("probability")
            words.append(item)
    return words or None


def _onscreen_blocks(frames: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if frames is None:
        return None
    out: list[dict[str, Any]] = []
    for frame in frames:
        start = frame.get("timestamp", frame.get("timestamp_sec"))
        blocks = frame.get("blocks") or [frame]
        for raw in blocks:
            block = dict(raw)
            block.setdefault("start_sec", start)
            out.append(block)
    return out


def _has_transcript_text(value: Any) -> bool:
    transcript = _decode(value)
    if not isinstance(transcript, list):
        return False
    return any(
        str(segment.get("text") or "").strip()
        or any(str(word.get("word") or word.get("text") or "").strip() for word in segment.get("words") or [])
        for segment in transcript
        if isinstance(segment, dict)
    )


def _has_slide_ocr(value: Any) -> bool:
    slides = _decode(value)
    if not isinstance(slides, list):
        return False
    return any(
        str(slide.get("text") or "").strip()
        or any(str(block.get("text") or "").strip() for block in slide.get("blocks") or [])
        for slide in slides
        if isinstance(slide, dict)
    )


def _common_number(common: dict[str, Any], source: str, key: str) -> Any:
    return (common.get("sources", {}).get(source) or {}).get(key)


def _pause_count(reel: dict[str, Any] | None, threshold: str) -> int | None:
    if not reel:
        return None
    value = (((reel.get("pauses") or {}).get("thresholds") or {}).get(threshold) or {}).get("count")
    return int(value) if isinstance(value, (int, float)) else None


def compute_features(
    con: sqlite3.Connection,
    pieces: list[dict[str, Any]],
    *,
    semantic_analyzer: SemanticAnalyzer | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    """Compute and persist all available facts without converting missing sources to zero."""
    semantic_analyzer = semantic_analyzer or RuleBasedSemanticAnalyzer()
    durations = [float(row["duration_sec"]) for row in pieces if row.get("duration_sec")]
    flat: dict[str, dict[str, Any]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    captions: dict[str, str] = {}
    for row in pieces:
        piece_id = str(row["id"])
        caption = row.get("caption")
        transcript = _decode(row.get("transcript"))
        onscreen = _decode(row.get("onscreen_text"))
        slides = _decode(row.get("slides"))
        if isinstance(caption, str) and caption.strip():
            captions[piece_id] = caption

        common = analyze_text_sources(
            {
                "caption": caption if row.get("caption") is not None else None,
                "transcript": transcript if row.get("transcript") is not None else None,
                "onscreen_text": onscreen if row.get("onscreen_text") is not None else None,
                "slides": slides if row.get("slides") is not None else None,
            },
            hook=row.get("hook_spoken") or row.get("hook_onscreen"),
            cta=row.get("cta_spoken"),
        )
        carousel = analyze_carousel(slides, caption=caption) if _format(row) == "carousel" else None
        reel = None
        if _format(row) in {"reel", "tiktok", "short", "video"}:
            reel = analyze_reel(
                word_timestamps=_flatten_words(transcript),
                segments=transcript,
                duration_seconds=row.get("duration_sec"),
                on_screen_blocks=_onscreen_blocks(onscreen),
                scene_changes=_decode(row.get("scene_changes")),
                profile_durations=durations,
            )

        semantic_sources = {
            "caption": caption,
            "transcript": transcript,
            "onscreen_text": onscreen,
            "slides": slides,
            "hook_spoken": row.get("hook_spoken"),
            "hook_onscreen": row.get("hook_onscreen"),
            "cta_spoken": row.get("cta_spoken"),
        }
        semantic = semantic_analyzer.analyze(semantic_sources)
        persist_semantic_result(con, piece_id=piece_id, result=semantic)
        unavailable = set(semantic.get("not_analyzable") or [])
        evidence_pairs = {
            (str(item.get("dimension")), str(item.get("label")))
            for item in semantic.get("evidence") or []
        }

        lexical = {
            str(n): aggregate_ngrams(
                [caption] if isinstance(caption, str) else [],
                n=n,
                minimum_count=1,
                remove_stopwords=n == 1,
            )
            for n in (1, 2, 3)
        }
        persist_piece_features(
            con,
            piece_id=piece_id,
            common=common,
            reel=reel,
            carousel=carousel,
            lexical=lexical,
            duration_sec=row.get("duration_sec"),
        )

        speech = (reel or {}).get("speech") or {}
        pauses = (reel or {}).get("pauses") or {}
        words_per_slide = (carousel or {}).get("words_per_slide") or {}
        caption_features = (common.get("sources", {}).get("caption") or {})
        sentence_words = caption_features.get("sentence_words") or {}
        pronouns = caption_features.get("pronouns") or {}
        imperatives = caption_features.get("imperatives") or {}
        lexical_diversity = caption_features.get("lexical_diversity") or {}
        language = (caption_features.get("language") or {}).get("language")
        topic_labels = [
            label for label in semantic["topics"]
            if "topics" not in unavailable and ("topic", str(label)) in evidence_pairs
        ]
        tone_labels = [
            label for label, score in semantic["tone_dimensions"].items()
            if isinstance(score, (int, float)) and score >= 0.34
        ]
        flat[piece_id] = {
            "format": _format(row),
            "topics": topic_labels,
            "content_intent": [
                label for label in semantic["content_intent"]
                if "content_intent" not in unavailable
                and ("content_intent", str(label)) in evidence_pairs
            ],
            "hook_archetype": (
                semantic["hook_archetype"]
                if "hook_archetype" not in unavailable
                and any(pair in evidence_pairs for pair in {
                    ("hook", str(semantic["hook_archetype"])),
                    ("hook_archetype", str(semantic["hook_archetype"])),
                })
                else None
            ),
            "narrative_structure": [
                label for label in semantic["narrative_structure"]
                if "narrative_structure" not in unavailable
                and ("narrative_structure", str(label)) in evidence_pairs
            ],
            "carousel_progression": (
                [
                    label for label in semantic["narrative_structure"]
                    if ("narrative_structure", str(label)) in evidence_pairs
                ]
                if carousel and "narrative_structure" not in unavailable
                else None
            ),
            "cta_type": (
                semantic["cta_type"]
                if "cta_type" not in unavailable
                and semantic["cta_type"] != "none"
                and ("cta_type", str(semantic["cta_type"])) in evidence_pairs
                else None
            ),
            "tone_dimensions": [
                label for label in tone_labels
                if "tone" not in unavailable and ("tone", str(label)) in evidence_pairs
            ],
            "language": language,
            "slide_roles": [
                label for label in semantic["slide_roles"]
                if "slide_roles" not in unavailable and ("slide_role", str(label)) in evidence_pairs
            ],
            "reel_structure": [
                label for label in semantic["reel_structure"]
                if "spoken_structure" not in unavailable and ("reel_structure", str(label)) in evidence_pairs
            ],
            "format_topic": [f"{_format(row)}:{topic}" for topic in topic_labels],
            "duration_sec": row.get("duration_sec"),
            "caption_word_count": caption_features.get("word_count"),
            "caption_sentence_count": caption_features.get("sentence_count"),
            "caption_average_sentence_words": sentence_words.get("average"),
            "caption_question_count": caption_features.get("question_count"),
            "caption_imperative_count": imperatives.get("count"),
            "caption_second_person_count": pronouns.get("second_person_count"),
            "caption_lexical_diversity": lexical_diversity.get("type_token_ratio"),
            "transcript_word_count": _common_number(common, "transcript", "word_count"),
            "slide_count": (carousel or {}).get("slide_count"),
            "carousel_total_word_count": (carousel or {}).get("total_word_count"),
            "carousel_cover_word_count": (carousel or {}).get("cover_word_count"),
            "words_per_slide_median": words_per_slide.get("median"),
            "words_per_slide_maximum": words_per_slide.get("maximum"),
            "text_density": (carousel or {}).get("median_density"),
            "carousel_bullet_count": (carousel or {}).get("bullet_count"),
            "carousel_question_count": (carousel or {}).get("question_count"),
            "caption_slide_overlap": ((carousel or {}).get("caption_slide_overlap") or {}).get("jaccard"),
            "words_per_minute": speech.get("words_per_minute"),
            "spoken_segment_words_median": _median_present([
                segment.get("word_count")
                for segment in ((speech.get("segment_lengths") or {}).get("values") or [])
            ]),
            "first_phrase_word_count": (speech.get("first_phrase") or {}).get("word_count"),
            "first_phrase_seconds": (speech.get("first_phrase") or {}).get("duration_seconds"),
            "median_word_gap_seconds": pauses.get("median_word_gap_seconds"),
            "speech_to_video_ratio": speech.get("speech_to_video_ratio"),
            "longest_uninterrupted_seconds": ((reel or {}).get("longest_uninterrupted_run") or {}).get("duration_seconds"),
            "pause_count_300ms": _pause_count(reel, "0.3"),
            "pause_count_600ms": _pause_count(reel, "0.6"),
            "pause_count_1000ms": _pause_count(reel, "1.0"),
            "scene_changes_per_minute": ((reel or {}).get("visual_cadence") or {}).get("changes_per_minute"),
        }
        payloads[piece_id] = {
            "common": common,
            "carousel": carousel,
            "reel": reel,
            "semantic": semantic,
            "lexical": lexical,
        }
    return flat, payloads, captions


def _section_for_finding(finding: dict[str, Any]) -> str:
    feature = str(finding.get("feature") or "").casefold()
    if feature.startswith("format="):
        return "formats"
    if any(term in feature for term in ("topic", "content_intent", "narrative", "hook_archetype", "cta_type", "tone")):
        return "topics_intents"
    if any(term in feature for term in (
        "words_per_minute", "pause_count", "transcript_word", "spoken_", "first_phrase",
        "word_gap", "speech_to_video", "uninterrupted", "scene_changes", "reel_structure",
    )):
        return "reel_rhythm"
    if any(term in feature for term in (
        "slide_count", "words_per_slide", "text_density", "carousel", "caption_slide_overlap", "slide_roles",
    )):
        return "carousel_anatomy"
    if any(term in feature for term in (
        "caption_", "lexical", "word", "phrase", "language",
    )):
        return "writing_vocabulary"
    if "duration" in feature:
        return "duration_density"
    return "formats"


def _canonical_findings(
    findings: list[dict[str, Any]],
    *,
    coverage_evidence_id: str,
    report_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_by_piece: dict[str, list[dict[str, Any]]] = {}
    for evidence in report_evidence:
        evidence_by_piece.setdefault(str(evidence.get("piece_id")), []).append(evidence)

    def evidence_score(section: str, evidence: dict[str, Any]) -> int:
        kind = str(evidence.get("kind") or "")
        if section == "reel_rhythm" and kind in {"pause", "first_phrase", "spoken_run", "transcript_span", "transcript_word"}:
            return 0
        if section == "carousel_anatomy" and kind in {"carousel_slide", "slide_block"}:
            return 0
        if section == "topics_intents" and kind in {"semantic_span", "caption"}:
            return 0
        if section == "writing_vocabulary" and kind in {"caption", "transcript_span", "slide_block", "onscreen_block"}:
            return 0
        return 2 if kind == "post" else 1

    out: list[dict[str, Any]] = []
    for raw in findings:
        item = dict(raw)
        item["section"] = _section_for_finding(item)
        item.setdefault("winner_prevalence", None)
        item.setdefault("baseline_prevalence", None)
        item.setdefault("caveat", "Association within this profile; not a causal estimate")
        references = list(item.get("evidence") or [])
        enriched: list[str] = []
        for reference in references:
            reference_text = str(reference)
            candidates = sorted(
                evidence_by_piece.get(reference_text, []),
                key=lambda evidence: (evidence_score(item["section"], evidence), str(evidence["id"])),
            )
            enriched.extend(str(evidence["id"]) for evidence in candidates[:3])
            enriched.append(reference_text)
        item["evidence"] = list(dict.fromkeys(enriched))
        if not item["evidence"]:
            item["evidence"] = [coverage_evidence_id]
        out.append(item)
    return out


def _report_evidence(
    con: sqlite3.Connection,
    pieces: list[dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    piece_by_id = {str(row["id"]): row for row in pieces}

    def add(item: dict[str, Any]) -> None:
        identifier = str(item["id"])
        if identifier in seen:
            return
        seen.add(identifier)
        evidence.append(item)

    for row in pieces:
        excerpt = str(row.get("caption") or row.get("hook_spoken") or row.get("hook_onscreen") or "")
        add({
            "id": f"post-evidence-{row['id']}",
            "piece_id": str(row["id"]),
            "kind": "post",
            "url": row.get("canonical_url") or row.get("source_url"),
            "excerpt": excerpt[:500],
            "format": _format(row),
        })

    piece_ids = list(piece_by_id)
    if piece_ids:
        placeholders = ",".join("?" for _ in piece_ids)
        current_artifacts = [
            dict(row)
            for row in con.execute(
            f"""
            SELECT * FROM text_artifacts
            WHERE piece_id IN ({placeholders})
              AND kind IN ('caption', 'transcript_segment', 'transcript_word', 'onscreen_block', 'slide_block')
            ORDER BY piece_id, COALESCE(slide_index, 0), COALESCE(start_sec, 0), id
            """,
            piece_ids,
            ).fetchall()
        ]
        current_artifacts_by_piece: dict[str, list[dict[str, Any]]] = {}
        for artifact in current_artifacts:
            current_artifacts_by_piece.setdefault(str(artifact["piece_id"]), []).append(artifact)
        artifacts: list[dict[str, Any]] = []
        for piece_id in piece_ids:
            piece = piece_by_id[piece_id]
            if "_text_artifacts" in piece:
                artifacts.extend(dict(item) for item in piece.get("_text_artifacts") or [])
            else:
                artifacts.extend(current_artifacts_by_piece.get(piece_id, []))
        artifacts.sort(key=lambda row: (
            str(row.get("piece_id")),
            int(row.get("slide_index") or 0),
            float(row.get("start_sec") or 0),
            int(row.get("id") or 0),
        ))
        word_counts: Counter[str] = Counter()
        for row in artifacts:
            kind = row["kind"]
            if kind not in {
                "caption", "transcript_segment", "transcript_word",
                "onscreen_block", "slide_block",
            }:
                continue
            # Include a bounded word-level timing sample; segment and pause spans
            # carry the rest without making long reports enormous.
            if kind == "transcript_word":
                word_counts[str(row["piece_id"])] += 1
                if word_counts[str(row["piece_id"])] > 20:
                    continue
            report_kind = {
                "transcript_segment": "transcript_span",
                "transcript_word": "transcript_word",
                "slide_block": "slide_block",
                "onscreen_block": "onscreen_block",
                "caption": "caption",
            }[kind]
            piece = piece_by_id[str(row["piece_id"])]
            add({
                "id": f"artifact-{row['id']}",
                "piece_id": str(row["piece_id"]),
                "artifact_id": row["id"],
                "kind": report_kind,
                "start_sec": row["start_sec"],
                "end_sec": row["end_sec"],
                "slide_index": row["slide_index"],
                "excerpt": row["text"],
                "confidence": row["confidence"],
                "url": piece.get("canonical_url") or piece.get("source_url"),
            })

        current_visual_blocks = [
            dict(row)
            for row in con.execute(
            f"""
            SELECT * FROM visual_text_blocks
            WHERE piece_id IN ({placeholders})
            ORDER BY piece_id, COALESCE(slide_index, 0), COALESCE(timestamp_sec, 0), id
            """,
            piece_ids,
            ).fetchall()
        ]
        current_visual_by_piece: dict[str, list[dict[str, Any]]] = {}
        for block in current_visual_blocks:
            current_visual_by_piece.setdefault(str(block["piece_id"]), []).append(block)
        visual_blocks: list[dict[str, Any]] = []
        for piece_id in piece_ids:
            piece = piece_by_id[piece_id]
            if "_visual_text_blocks" in piece:
                visual_blocks.extend(dict(item) for item in piece.get("_visual_text_blocks") or [])
            else:
                visual_blocks.extend(current_visual_by_piece.get(piece_id, []))
        visual_blocks.sort(key=lambda row: (
            str(row.get("piece_id")),
            int(row.get("slide_index") or 0),
            float(row.get("timestamp_sec") or 0),
            int(row.get("id") or 0),
        ))
        for row in visual_blocks:
            piece = piece_by_id[str(row["piece_id"])]
            add({
                "id": f"visual-block-{row['id']}",
                "piece_id": str(row["piece_id"]),
                "kind": "carousel_slide" if row["slide_index"] is not None else "onscreen_block",
                "start_sec": row["timestamp_sec"],
                "slide_index": row["slide_index"],
                "excerpt": row["text"],
                "confidence": row["confidence"],
                "bbox": {
                    "x": row["x"], "y": row["y"], "width": row["width"], "height": row["height"],
                    "image_width": row["image_width"], "image_height": row["image_height"],
                },
                "url": piece.get("canonical_url") or piece.get("source_url"),
            })

    for piece_id, payload in payloads.items():
        piece = piece_by_id[piece_id]
        url = piece.get("canonical_url") or piece.get("source_url")
        reel = payload.get("reel") or {}
        speech = reel.get("speech") or {}
        first_phrase = speech.get("first_phrase") or {}
        if first_phrase:
            add({
                "id": f"first-phrase-{piece_id}",
                "piece_id": piece_id,
                "kind": "first_phrase",
                "start_sec": first_phrase.get("start_sec"),
                "end_sec": first_phrase.get("end_sec"),
                "excerpt": first_phrase.get("text") or "",
                "url": url,
            })
        spoken_run = reel.get("longest_uninterrupted_run") or {}
        if spoken_run:
            add({
                "id": f"spoken-run-{piece_id}",
                "piece_id": piece_id,
                "kind": "spoken_run",
                "start_sec": spoken_run.get("start_sec"),
                "end_sec": spoken_run.get("end_sec"),
                "excerpt": spoken_run.get("text") or "",
                "url": url,
            })
        gaps_seen: set[tuple[Any, Any]] = set()
        for gap in (reel.get("pauses") or {}).get("gaps") or []:
            if float(gap.get("duration_seconds") or 0) < 0.3:
                continue
            key = (gap.get("start_sec"), gap.get("end_sec"))
            if key in gaps_seen or len(gaps_seen) >= 10:
                continue
            gaps_seen.add(key)
            add({
                "id": f"pause-{piece_id}-{round(float(gap['start_sec']) * 1000)}-{round(float(gap['end_sec']) * 1000)}",
                "piece_id": piece_id,
                "kind": "pause",
                "start_sec": gap.get("start_sec"),
                "end_sec": gap.get("end_sec"),
                "excerpt": f"{gap.get('after_text', '')} … {gap.get('before_text', '')}".strip(),
                "duration_seconds": gap.get("duration_seconds"),
                "url": url,
            })
        for index, item in enumerate((payload.get("semantic") or {}).get("evidence") or []):
            add({
                "id": f"semantic-{piece_id}-{index}",
                "piece_id": piece_id,
                "kind": "semantic_span",
                "slide_index": item.get("slide_index"),
                "excerpt": item.get("excerpt") or "",
                "start_char": item.get("start_char"),
                "end_char": item.get("end_char"),
                "dimension": item.get("dimension"),
                "label": item.get("label"),
                "source_kind": item.get("source_kind"),
                "url": url,
            })
    return evidence


def _lexical_summary(
    captions: dict[str, str],
    winner_ids: list[str],
) -> dict[str, Any]:
    profile_docs = list(captions.items())
    winner_docs = [(piece_id, captions[piece_id]) for piece_id in winner_ids if piece_id in captions]
    winner_set = set(winner_ids)
    baseline_docs = [(piece_id, text) for piece_id, text in captions.items() if piece_id not in winner_set]

    def attach(rows: list[dict[str, Any]], docs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            row["examples"] = [
                {**example, "piece_id": docs[example["document_index"]][0]}
                for example in raw.get("examples") or []
                if 0 <= int(example["document_index"]) < len(docs)
            ]
            enriched.append(row)
        return enriched

    def comparison(n: int, limit: int) -> dict[str, Any]:
        if not winner_docs or not baseline_docs:
            return {
                "feature_version": "lexical-v1",
                "n": n,
                "winner_document_count": len(winner_docs),
                "baseline_document_count": len(baseline_docs),
                "terms": [],
            }
        result = winner_baseline_inputs(
            [text for _, text in winner_docs],
            [text for _, text in baseline_docs],
            n=n,
            minimum_total_count=2,
        )
        for term in result["terms"]:
            term["winner_examples"] = [
                {**example, "piece_id": winner_docs[example["document_index"]][0]}
                for example in term.get("winner_examples") or []
                if 0 <= int(example["document_index"]) < len(winner_docs)
            ]
            term["baseline_examples"] = [
                {**example, "piece_id": baseline_docs[example["document_index"]][0]}
                for example in term.get("baseline_examples") or []
                if 0 <= int(example["document_index"]) < len(baseline_docs)
            ]
        result["terms"] = result["terms"][:limit]
        return result

    profile_unigrams = aggregate_ngrams(
        [text for _, text in profile_docs], n=1, minimum_count=2, remove_stopwords=True
    )
    profile_bigrams = aggregate_ngrams(
        [text for _, text in profile_docs], n=2, minimum_count=2
    )
    profile_trigrams = aggregate_ngrams(
        [text for _, text in profile_docs], n=3, minimum_count=2
    )
    return {
        "profile_document_count": len(profile_docs),
        "profile_unigrams": attach(profile_unigrams, profile_docs)[:25],
        "profile_bigrams": attach(profile_bigrams, profile_docs)[:20],
        "profile_trigrams": attach(profile_trigrams, profile_docs)[:15],
        "winner_unigrams": comparison(1, 25),
        "winner_bigrams": comparison(2, 20),
    }


def _lexical_findings(lexical: dict[str, Any], *, objective: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key, label in (("winner_unigrams", "word"), ("winner_bigrams", "phrase")):
        comparison = lexical.get(key) or {}
        winner_docs = int(comparison.get("winner_document_count") or 0)
        baseline_docs = int(comparison.get("baseline_document_count") or 0)
        for term in comparison.get("terms") or []:
            enrichment = float(term.get("log2_enrichment_preview") or 0)
            winner_frequency = int(term.get("winner_document_frequency") or 0)
            baseline_frequency = int(term.get("baseline_document_frequency") or 0)
            if enrichment <= 0 or winner_frequency < 2:
                continue
            minimum_group = min(winner_docs, baseline_docs)
            confidence = "medium" if minimum_group >= 8 and winner_frequency >= 5 else "low" if minimum_group >= 5 else "insufficient"
            evidence = list(dict.fromkeys(
                example["piece_id"] for example in term.get("winner_examples") or [] if example.get("piece_id")
            ))
            findings.append({
                "id": "lexical-" + label + "-" + hashlib.sha256(
                    f"{objective}\x1f{term['ngram']}".encode("utf-8")
                ).hexdigest()[:12],
                "dimension": "lexical_enrichment",
                "feature": f"{label}={term['ngram']}",
                "claim": (
                    f"The {label} '{term['ngram']}' is associated with greater representation "
                    f"in winning captions for {objective} within this profile."
                    if confidence != "insufficient"
                    else f"There is insufficient evidence to associate the {label} '{term['ngram']}' with stronger {objective} performance."
                ),
                "objective": objective,
                "comparison": "winning captions vs other eligible captions",
                "effect": {"value": enrichment, "unit": "log2_smoothed_term_enrichment"},
                "winner_prevalence": winner_frequency / winner_docs if winner_docs else 0.0,
                "baseline_prevalence": baseline_frequency / baseline_docs if baseline_docs else 0.0,
                "sample": {"feature": winner_docs, "control": baseline_docs},
                "interval": None,
                "confidence": confidence,
                "status": "insufficient_evidence" if confidence == "insufficient" else "supported",
                "evidence": evidence,
                "caveat": "Lexical association within caption text; topic and format can confound the comparison.",
            })
    return findings[:12]


def _median_present(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return median(numbers) if numbers else None


def _writing_summary(
    features: dict[str, dict[str, Any]],
    winner_ids: list[str],
) -> dict[str, Any]:
    winner_set = set(winner_ids)
    fields = (
        "caption_word_count",
        "caption_sentence_count",
        "caption_average_sentence_words",
        "caption_question_count",
        "caption_imperative_count",
        "caption_second_person_count",
        "caption_lexical_diversity",
    )
    result: dict[str, Any] = {}
    for field in fields:
        result[field] = {
            "profile_median": _median_present([row.get(field) for row in features.values()]),
            "winner_median": _median_present([
                row.get(field) for piece_id, row in features.items() if piece_id in winner_set
            ]),
            "baseline_median": _median_present([
                row.get(field) for piece_id, row in features.items() if piece_id not in winner_set
            ]),
        }
    result["language_counts"] = dict(sorted(Counter(
        row.get("language") or "undetermined" for row in features.values()
    ).items()))
    return result


def build_profile_report(
    con: sqlite3.Connection,
    *,
    profile_id: str,
    profile_run_id: str,
    objective: str,
    top_k: int,
    semantic_analyzer: SemanticAnalyzer | None = None,
    minimum_sample: int = 5,
    bootstrap_iterations: int = 1_000,
    seed: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_row = con.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    run_row = con.execute("SELECT * FROM profile_runs WHERE id=?", (profile_run_id,)).fetchone()
    if not profile_row or not run_row:
        raise ValueError("profile and profile run must exist before analysis")
    pieces = []
    for row in con.execute(
        """
        SELECT p.*, membership.snapshot_json, membership.inventory_position
        FROM profile_run_pieces membership
        JOIN pieces p ON p.id=membership.piece_id
        WHERE membership.profile_run_id=? AND p.profile_id=?
        ORDER BY COALESCE(membership.inventory_position, 2147483647), p.published_at DESC, p.id
        """,
        (profile_run_id, profile_id),
    ).fetchall():
        current = dict(row)
        snapshot = _decode(current.pop("snapshot_json", None)) or {}
        # Run-specific source facts remain stable even if the canonical piece is
        # refreshed during a later inventory.
        current.update({key: value for key, value in snapshot.items() if key in current})
        for evidence_key in ("_text_artifacts", "_visual_text_blocks"):
            if evidence_key in snapshot:
                current[evidence_key] = snapshot[evidence_key]
        pieces.append(current)
    observations = [
        dict(row)
        for row in con.execute(
            """
            SELECT mo.* FROM metric_observations mo
            JOIN pieces p ON p.id=mo.piece_id
            JOIN profile_run_pieces membership
              ON membership.piece_id=p.id AND membership.profile_run_id=?
            JOIN profile_runs run ON run.id=membership.profile_run_id
            WHERE p.profile_id=?
              AND mo.captured_at <= COALESCE(run.completed_at, ?)
            ORDER BY mo.captured_at, mo.id
            """,
            (profile_run_id, profile_id, datetime.now(timezone.utc).isoformat()),
        ).fetchall()
    ]
    features, payloads, captions = compute_features(
        con,
        pieces,
        semantic_analyzer=semantic_analyzer,
    )
    analysis = analyze_winners(
        pieces,
        observations,
        objective=objective,
        features=features,
        top_k=top_k,
        categorical_fields=(
            "format",
            "topics",
            "content_intent",
            "hook_archetype",
            "narrative_structure",
            "carousel_progression",
            "cta_type",
            "tone_dimensions",
            "language",
            "slide_roles",
            "reel_structure",
            "format_topic",
        ),
        continuous_fields=(
            "duration_sec",
            "caption_word_count",
            "caption_sentence_count",
            "caption_average_sentence_words",
            "caption_question_count",
            "caption_imperative_count",
            "caption_second_person_count",
            "caption_lexical_diversity",
            "transcript_word_count",
            "spoken_segment_words_median",
            "first_phrase_word_count",
            "first_phrase_seconds",
            "words_per_minute",
            "median_word_gap_seconds",
            "speech_to_video_ratio",
            "longest_uninterrupted_seconds",
            "pause_count_300ms",
            "pause_count_600ms",
            "pause_count_1000ms",
            "scene_changes_per_minute",
            "slide_count",
            "carousel_total_word_count",
            "carousel_cover_word_count",
            "words_per_slide_median",
            "words_per_slide_maximum",
            "text_density",
            "carousel_bullet_count",
            "carousel_question_count",
            "caption_slide_overlap",
        ),
        minimum_sample=minimum_sample,
        bootstrap_iterations=bootstrap_iterations,
        seed=seed,
    )
    base_analysis_id = analysis["analysis_id"]
    analysis_digest = hashlib.sha256(json.dumps(
        {
            "base_analysis_id": base_analysis_id,
            "profile_run_id": profile_run_id,
            "ranked": [
                {
                    "piece_id": row["piece_id"],
                    "value": row["value"],
                    "observation_ref": row.get("observation_ref"),
                }
                for row in analysis["ranked"]
            ],
            "content_feature_digest": hashlib.sha256(json.dumps(
                {"features": features, "captions": captions},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")).hexdigest(),
            "feature_version": FEATURE_VERSION,
            "label_version": LABEL_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")).hexdigest()[:20]
    analysis["base_analysis_id"] = base_analysis_id
    analysis["analysis_id"] = f"analysis-{analysis_digest}"

    objective_coverage = analysis["coverage"]
    format_counts = Counter(_format(row) for row in pieces)
    failures = [
        dict(row)
        for row in con.execute(
            "SELECT * FROM failures WHERE run_id=? ORDER BY occurred_at, id",
            (profile_run_id,),
        ).fetchall()
    ]
    transcript_count = sum(_has_transcript_text(row.get("transcript")) for row in pieces)
    word_timed_count = sum(
        bool(_flatten_words(_decode(row.get("transcript")))) for row in pieces
    )
    carousel_ocr_count = sum(
        _format(row) == "carousel" and _has_slide_ocr(row.get("slides"))
        for row in pieces
    )
    semantic_dimensions = {
        "topics": lambda row: bool(row.get("topics")),
        "content_intent": lambda row: bool(row.get("content_intent")),
        "hook_archetype": lambda row: bool(row.get("hook_archetype")),
        "cta_type": lambda row: bool(row.get("cta_type")),
        "tone": lambda row: bool(row.get("tone_dimensions")),
        "slide_roles": lambda row: bool(row.get("slide_roles")),
        "reel_structure": lambda row: bool(row.get("reel_structure")),
    }
    semantic_dimension_coverage = {
        name: sum(check(row) for row in features.values())
        for name, check in semantic_dimensions.items()
    }
    coverage = {
        "requested_posts": int(run_row["requested_posts"]),
        "inventoried_posts": len(pieces),
        "formats": dict(sorted(format_counts.items())),
        "objective": objective,
        "eligible_posts": objective_coverage["eligible_pieces"],
        "objective_coverage": objective_coverage["objective_coverage"],
        "objective_exclusions": objective_coverage["excluded"],
        "selected_metric_basis": objective_coverage["selected_metric_basis"],
        "selected_metric_unit": objective_coverage["selected_metric_unit"],
        "transcript_posts": transcript_count,
        "word_timed_posts": word_timed_count,
        "carousel_ocr_posts": carousel_ocr_count,
        "semantic_posts": sum(
            any(check(row) for check in semantic_dimensions.values())
            for row in features.values()
        ),
        "semantic_dimension_coverage": semantic_dimension_coverage,
        "failure_count": len(failures),
        "failures_by_stage": dict(sorted(Counter(row.get("stage") or "unknown" for row in failures).items())),
        "metric_sources": dict(sorted(Counter(row.get("source") or "unknown" for row in observations).items())),
        "metric_capture_range": {
            "first": min((row.get("captured_at") for row in observations if row.get("captured_at")), default=None),
            "last": max((row.get("captured_at") for row in observations if row.get("captured_at")), default=None),
        },
        "evidence_mode": run_row["evidence_mode"],
    }

    evidence = _report_evidence(con, pieces, payloads)
    coverage_evidence_id = f"coverage-{profile_run_id}"
    evidence.append({
        "id": coverage_evidence_id,
        "piece_id": profile_id,
        "kind": "coverage",
        "excerpt": (
            f"{objective_coverage['eligible_pieces']} of {len(pieces)} inventoried posts "
            f"were eligible for {objective}."
        ),
    })
    winner_ids = analysis["cohorts"]["winner_piece_ids"]
    lexical = _lexical_summary(captions, winner_ids)
    lexical_findings = _lexical_findings(lexical, objective=objective)
    findings = _canonical_findings(
        [*analysis["findings"], *lexical_findings],
        coverage_evidence_id=coverage_evidence_id,
        report_evidence=evidence,
    )
    analysis["finding_summary"]["total"] += len(lexical_findings)
    analysis["finding_summary"]["supported"] += sum(
        item["status"] == "supported" for item in lexical_findings
    )
    analysis["finding_summary"]["insufficient_evidence"] += sum(
        item["status"] == "insufficient_evidence" for item in lexical_findings
    )
    row_by_id = {str(row["id"]): row for row in pieces}
    winners = []
    for winner in analysis["winners"]:
        piece = row_by_id[winner["piece_id"]]
        winners.append({
            "piece_id": winner["piece_id"],
            "url": piece.get("canonical_url") or piece.get("source_url"),
            "format": _format(piece),
            "title": str(piece.get("caption") or piece.get("hook_spoken") or "")[:180],
            "objective": objective,
            "objective_value": winner["value"],
            "relative_score": winner["relative_percentile"],
            "rank": winner["rank"],
        })

    supported = [
        item for item in findings
        if item["status"] == "supported" and _is_positive_repeat_signal(item)
    ]
    supported.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("confidence")), 3),
            -_repeat_signal_strength(item),
            str(item.get("id")),
        )
    )
    repeat = [
        {"text": item["claim"], "evidence": item["evidence"][:3]}
        for item in supported[:5]
    ]
    tests = [
        "Test the same topic in more than one format before treating format as the differentiator.",
        "Test adjacent duration or slide-count bands while holding topic and publication window similar.",
    ]
    limitations = [
        "Observed relationships are profile-relative associations, not causal estimates or performance guarantees.",
    ]
    if run_row["evidence_mode"] == "public":
        limitations.append(
            "Public metrics are captured snapshots; private retention, reach, saves, and shares are unavailable unless imported from an owned account."
        )
    else:
        limitations.append(
            "Owned/local metrics are analyzed exactly as supplied; their definitions and denominators must match across compared posts."
        )
    if transcript_count < len(pieces):
        limitations.append(
            f"Speech/style analysis is available for {transcript_count} of {len(pieces)} posts; missing transcripts are not treated as silence."
        )
    if word_timed_count < transcript_count:
        limitations.append(
            f"Measured pause analysis is available for {word_timed_count} of {transcript_count} transcribed posts."
        )
    if carousel_ocr_count < format_counts.get("carousel", 0):
        limitations.append(
            f"Slide anatomy is available for {carousel_ocr_count} of {format_counts.get('carousel', 0)} carousels."
        )
    if objective_coverage["eligible_pieces"] < 20:
        limitations.append("Fewer than 20 eligible posts: the report is descriptive and comparative confidence is limited.")

    report = build_report(
        profile=dict(profile_row),
        run={
            **dict(run_row),
            "config_json": _decode(run_row["config_json"]),
            "coverage_json": _decode(run_row["coverage_json"]),
        },
        coverage=coverage,
        winners=winners,
        findings=findings,
        evidence=evidence,
        repeat=repeat,
        tests=tests,
        cannot_conclude=limitations,
        failures=failures,
        limitations=limitations,
        section_summaries={
            "formats": {
                "summary": "Winner share is shown beside the eligible profile baseline.",
                "details": analysis["format_distribution"],
            },
            "duration_density": {
                "summary": "Bands are derived from this profile's observed distribution.",
                "details": {"method": analysis["method"]["continuous_bands"]},
            },
            "topics_intents": {
                "summary": "Topics, intent, hooks, CTA, and tone are versioned labels with source evidence.",
                "details": {
                    "label_version": LABEL_VERSION,
                    "topic_counts": dict(sorted(Counter(topic for data in features.values() for topic in data.get("topics") or []).items())),
                    "intent_counts": dict(sorted(Counter(intent for data in features.values() for intent in data.get("content_intent") or []).items())),
                },
            },
            "writing_vocabulary": {
                "summary": "Caption, transcript, on-screen, and slide writing are counted separately.",
                "details": {
                    "measurements": _writing_summary(features, winner_ids),
                    **lexical,
                },
            },
            "reel_rhythm": {
                "summary": "Pauses are reported only from word timestamps; segment gaps are not measured pauses.",
                "details": {
                    "transcript_posts": transcript_count,
                    "word_timed_posts": word_timed_count,
                    "median_wpm": _median_present([value.get("words_per_minute") for value in features.values()]),
                    "median_pause_600ms": _median_present([value.get("pause_count_600ms") for value in features.values()]),
                },
            },
            "carousel_anatomy": {
                "summary": "Slide order and OCR blocks remain attached to their source slides.",
                "details": {
                    "carousel_count": format_counts.get("carousel", 0),
                    "ocr_count": carousel_ocr_count,
                    "median_slide_count": _median_present([value.get("slide_count") for value in features.values()]),
                    "median_words_per_slide": _median_present([value.get("words_per_slide_median") for value in features.values()]),
                },
            },
        },
    )
    persist_analysis(
        con,
        profile_run_id=profile_run_id,
        analysis=analysis,
        findings=findings,
        evidence=evidence,
    )
    return report, analysis


def _repeat_signal_strength(finding: dict[str, Any]) -> float:
    effect = finding.get("effect") or {}
    try:
        value = float(effect.get("value"))
    except (TypeError, ValueError):
        return float("-inf")
    unit = str(effect.get("unit") or "")
    if "ratio" in unit:
        return value - 1.0
    return value


def _is_positive_repeat_signal(finding: dict[str, Any]) -> bool:
    """Only turn positively associated findings into repeat recommendations."""
    return _repeat_signal_strength(finding) > 0


def persist_analysis(
    con: sqlite3.Connection,
    *,
    profile_run_id: str,
    analysis: dict[str, Any],
    findings: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> None:
    analysis_id = analysis["analysis_id"]
    evidence_by_id = {str(item["id"]): item for item in evidence}
    con.execute("SAVEPOINT persist_analysis")
    try:
        con.execute(
            "DELETE FROM finding_evidence WHERE finding_id IN (SELECT id FROM findings WHERE analysis_run_id=?)",
            (analysis_id,),
        )
        con.execute("DELETE FROM findings WHERE analysis_run_id=?", (analysis_id,))
        con.execute("DELETE FROM analysis_runs WHERE id=?", (analysis_id,))
        con.execute(
            """
            INSERT INTO analysis_runs(
                id, profile_run_id, objective, cohort_config_json, feature_version,
                label_version, started_at, completed_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'done')
            """,
            (
                analysis_id,
                profile_run_id,
                analysis["objective"]["name"],
                json.dumps(
                    {"cohorts": analysis["cohorts"], "method": analysis["method"]},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                FEATURE_VERSION,
                LABEL_VERSION,
            ),
        )
        for finding in findings:
            # Report finding IDs are deliberately readable and stable. Database
            # IDs are scoped to the analysis so two profile runs can contain the
            # same comparison without colliding.
            database_finding_id = f"{analysis_id}:{finding['id']}"
            con.execute(
                """
                INSERT INTO findings(
                    id, analysis_run_id, dimension, feature, claim, comparison,
                    effect_value, effect_unit, winner_prevalence, baseline_prevalence,
                    sample_winner, sample_baseline, interval_json, confidence, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    database_finding_id,
                    analysis_id,
                    finding.get("dimension") or finding["section"],
                    finding.get("feature") or "unknown",
                    finding["claim"],
                    finding["comparison"],
                    finding["effect"].get("value"),
                    finding["effect"].get("unit"),
                    finding.get("winner_prevalence"),
                    finding.get("baseline_prevalence"),
                    int(finding["sample"].get("feature", 0)),
                    int(finding["sample"].get("control", 0)),
                    json.dumps(finding.get("interval"), ensure_ascii=False, sort_keys=True),
                    finding["confidence"],
                    finding["status"],
                ),
            )
            for reference in finding["evidence"]:
                reference_text = str(reference)
                source = evidence_by_id.get(reference_text) or {}
                source_piece_id = source.get("piece_id") or reference_text
                piece_id = (
                    str(source_piece_id)
                    if con.execute("SELECT 1 FROM pieces WHERE id=?", (str(source_piece_id),)).fetchone()
                    else None
                )
                artifact_id = source.get("artifact_id")
                if artifact_id is not None and not con.execute(
                    "SELECT 1 FROM text_artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone():
                    artifact_id = None
                con.execute(
                    """
                    INSERT INTO finding_evidence(
                        finding_id, piece_id, artifact_id, slide_index,
                        start_sec, end_sec, excerpt, role
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'support')
                    """,
                    (
                        database_finding_id,
                        piece_id,
                        artifact_id,
                        source.get("slide_index"),
                        source.get("start_sec"),
                        source.get("end_sec"),
                        source.get("excerpt") or reference_text,
                    ),
                )
    except Exception:
        con.execute("ROLLBACK TO SAVEPOINT persist_analysis")
        con.execute("RELEASE SAVEPOINT persist_analysis")
        raise
    else:
        con.execute("RELEASE SAVEPOINT persist_analysis")


def write_profile_report(report: dict[str, Any], output_root: Any) -> dict[str, Any]:
    return write_report_bundle(report, output_root)
