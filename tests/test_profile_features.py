from __future__ import annotations

import pytest

from content_reference_toolkit.features.carousel import analyze_carousel
from content_reference_toolkit.features.common import (
    analyze_text,
    analyze_text_sources,
    token_overlap,
)
from content_reference_toolkit.features.lexical import (
    aggregate_ngrams,
    winner_baseline_inputs,
)
from content_reference_toolkit.features.reel import analyze_reel, normalize_word_timestamps


def test_common_spanish_writing_counts_are_hand_checkable():
    result = analyze_text(
        "¿Quieres crecer?\n\nGuarda esta guía.\nComparte 3 ideas con tu equipo."
    )

    assert result["word_count"] == 11
    assert result["sentence_count"] == 3
    assert result["sentence_words"] == {
        "average": pytest.approx(11 / 3),
        "median": 3,
        "maximum": 6,
        "values": [2, 3, 6],
    }
    assert result["line_count"] == 3
    assert result["paragraph_count"] == 2
    assert result["question_count"] == 1
    assert result["number_count"] == 1
    assert [item["marker"] for item in result["imperatives"]["markers"]] == [
        "guarda",
        "comparte",
    ]
    assert result["pronouns"]["second_person_count"] == 1
    assert result["language"]["language"] == "es"
    assert result["sentence_evidence"][0]["text"] == "¿Quieres crecer?"


def test_common_english_sources_stay_separate_and_missing_is_not_zero():
    result = analyze_text_sources(
        {
            "caption": "You should save this.\nShare this now!",
            "transcript": None,
            "on_screen": "",
            "slides": [
                {"text": "A practical guide", "slide_index": 1, "confidence": 0.94},
                {"text": "Save this", "slide_index": 2, "confidence": 0.91},
            ],
        }
    )

    caption = result["sources"]["caption"]
    assert caption["word_count"] == 7
    assert caption["language"]["language"] == "en"
    assert caption["imperatives"]["count"] == 2
    assert result["sources"]["transcript"]["available"] is False
    assert result["sources"]["transcript"]["word_count"] is None
    assert result["sources"]["on_screen"]["available"] is True
    assert result["sources"]["on_screen"]["word_count"] == 0
    assert result["sources"]["slides"]["word_count"] == 5
    assert result["sources"]["slides"]["artifacts"][0]["evidence"] == {
        "slide_index": 1,
        "confidence": 0.94,
    }
    assert result["caption_media_overlap"]["available"] is True
    assert "save" in result["caption_media_overlap"]["shared_tokens"]


def test_common_overlap_does_not_turn_missing_evidence_into_no_overlap():
    missing = token_overlap("caption text", None)
    observed_empty = token_overlap("caption text", "")

    assert missing["available"] is False
    assert missing["jaccard"] is None
    assert observed_empty["available"] is True
    assert observed_empty["jaccard"] == 0.0

    sources = analyze_text_sources(
        {"transcript": [{"text": None, "available": False, "reason": "failed"}]}
    )
    assert sources["sources"]["transcript"]["available"] is False
    assert sources["sources"]["transcript"]["word_count"] is None


def test_lexical_ngrams_and_winner_baseline_inputs_keep_examples():
    ngrams = aggregate_ngrams(
        ["Save this tip. Save this tip.", "Save the guide."],
        n=2,
        minimum_count=2,
        remove_stopwords=True,
        language="en",
    )

    assert ngrams[0]["ngram"] == "save this"
    assert ngrams[0]["count"] == 2
    assert ngrams[0]["document_frequency"] == 1
    assert ngrams[0]["examples"][0]["text"] == "Save this"

    comparison = winner_baseline_inputs(
        ["Save this tip. Save this tip."],
        ["Read this guide."],
        n=1,
        minimum_total_count=1,
        language="en",
    )
    save = next(item for item in comparison["terms"] if item["ngram"] == "save")
    assert save["winner_count"] == 2
    assert save["baseline_count"] == 0
    assert save["log2_enrichment_preview"] > 0
    assert save["winner_examples"][0]["text"] == "Save"


def test_carousel_preserves_ordered_blocks_missing_slide_and_box_evidence():
    result = analyze_carousel(
        [
            {
                "slide_index": 1,
                "image_width": 1000,
                "image_height": 1000,
                "source_path": "slide-01.png",
                "blocks": [
                    {"text": "3 claves", "bbox": [100, 100, 800, 100], "confidence": 0.98},
                    {"text": "¿Quieres crecer?", "bbox": [100, 250, 800, 100], "confidence": 0.96},
                ],
            },
            {"slide_index": 2, "available": False, "reason": "ocr_failed"},
            {
                "slide_index": 3,
                "image_width": 1000,
                "image_height": 1000,
                "blocks": [
                    {"text": "- Comparte este carrusel", "bbox": [100, 100, 800, 100]},
                ],
            },
        ],
        caption="Tres claves para crecer",
    )

    assert result["slide_count"] == 3
    assert result["available_slide_count"] == 2
    assert result["missing_slide_indices"] == [2]
    assert [slide["slide_index"] for slide in result["slides"]] == [1, 2, 3]
    assert result["total_word_count"] == 7
    assert result["cover_word_count"] == 4
    assert result["final_slide_word_count"] == 3
    assert result["bullet_count"] == 1
    assert result["question_count"] == 1
    assert result["slides"][0]["block_area_ratio"] == 0.16
    assert result["slides"][0]["blocks"][0]["confidence"] == 0.98
    assert result["evidence"][0]["blocks"][0]["x"] == 100
    assert result["caption_slide_overlap"]["available"] is True
    assert result["caption_slide_overlap"]["shared_tokens"] == ["claves", "crecer"]


def test_carousel_known_empty_and_unextracted_slides_have_different_semantics():
    unextracted = analyze_carousel(None)
    empty_ocr = analyze_carousel(
        [{"slide_index": 1, "image_width": 1080, "image_height": 1350, "blocks": []}]
    )

    assert unextracted["available"] is False
    assert unextracted["slide_count"] is None
    assert unextracted["block_count"] is None
    assert empty_ocr["available"] is True
    assert empty_ocr["slide_count"] == 1
    assert empty_ocr["block_count"] == 0
    assert empty_ocr["total_word_count"] == 0
    assert empty_ocr["slides"][0]["block_area_ratio"] == 0.0

    unreadable = analyze_carousel(
        [{"slide_index": 1, "blocks": [{"text": None, "available": False}]}]
    )
    assert unreadable["slides"][0]["available"] is True
    assert unreadable["slides"][0]["ocr_available"] is False
    assert unreadable["slides"][0]["word_count"] is None
    assert unreadable["block_count"] is None


def test_reel_word_timing_calculates_exact_pause_bands_and_first_phrase():
    result = analyze_reel(
        word_timestamps=[
            {"word": "Hola", "start": 0.0, "end": 0.3, "segment_index": 0},
            {"word": "mundo.", "start": 0.4, "end": 0.8, "segment_index": 0},
            {"word": "Guarda", "start": 1.1, "end": 1.4, "segment_index": 1},
            {"word": "esto", "start": 2.0, "end": 2.2, "segment_index": 1},
            {"word": "ahora", "start": 3.3, "end": 3.6, "segment_index": 1},
        ],
        duration_seconds=10,
        on_screen_blocks=[
            {"text": "3 claves", "start_sec": 0, "end_sec": 1},
            {"text": "Guárdalo", "start_sec": 2, "end_sec": 4},
        ],
        scene_changes=[1.0, 3.0, 5.0],
        hook={"text": "Hola mundo", "start_sec": 0, "end_sec": 0.8, "source": "transcript"},
    )

    assert result["speech"]["timing_precision"] == "word"
    assert result["speech"]["spoken_word_count"] == 5
    assert result["speech"]["words_per_minute"] == pytest.approx(5 * 60 / 3.6)
    assert result["speech"]["speech_duration_seconds"] == pytest.approx(1.5)
    assert result["speech"]["speech_to_video_ratio"] == pytest.approx(0.15)
    assert result["speech"]["first_phrase"]["text"] == "Hola mundo."
    assert result["speech"]["first_phrase"]["word_count"] == 2
    assert result["speech"]["sentence_word_lengths"] == [2, 3]
    assert result["pauses"]["thresholds"]["0.3"]["count"] == 3
    assert result["pauses"]["thresholds"]["0.6"]["count"] == 2
    assert result["pauses"]["thresholds"]["1.0"]["count"] == 1
    assert result["longest_uninterrupted_run"]["text"] == "Hola mundo."
    assert result["on_screen"]["block_count"] == 2
    assert result["on_screen"]["appearance_rate_per_minute"] == 12
    assert result["on_screen"]["text_card_duration_seconds"] == 3
    assert result["visual_cadence"]["change_count"] == 3
    assert result["visual_cadence"]["changes_per_minute"] == 18
    assert result["hook"]["start_sec"] == 0


def test_reel_segment_only_path_never_claims_measured_pauses():
    result = analyze_reel(
        segments=[
            {
                "text": "Hola, esto es una prueba.",
                "start_sec": 0,
                "end_sec": 3,
                "source_path": "transcript.json",
            }
        ],
        duration_seconds=10,
    )

    assert result["speech"]["available"] is True
    assert result["speech"]["timing_precision"] == "segment"
    assert result["speech"]["spoken_word_count"] == 5
    assert result["speech"]["words_per_minute"] == 100
    assert result["speech"]["speech_duration_seconds"] == 3
    assert result["speech"]["segment_lengths"]["values"][0]["source_path"] == "transcript.json"
    assert result["pauses"]["available"] is False
    assert result["pauses"]["reason"] == "word_timestamps_required"
    assert result["pauses"]["thresholds"]["0.3"]["count"] is None
    assert result["longest_uninterrupted_run"] is None

    partial_timing = analyze_reel(
        segments=[
            {"text": "Timed words", "start_sec": 0, "end_sec": 1},
            {"text": "Untimed words"},
        ],
        duration_seconds=10,
    )
    assert partial_timing["speech"]["spoken_word_count"] == 4
    assert partial_timing["speech"]["words_per_minute"] is None
    assert partial_timing["speech"]["speech_duration_seconds"] is None
    assert partial_timing["speech"]["segment_lengths"]["timing_coverage"] == 0.5


def test_reel_missing_transcript_is_unknown_while_one_timed_word_means_zero_pauses():
    missing = analyze_reel(duration_seconds=10)
    one_word = analyze_reel(
        word_timestamps=[{"word": "Hola", "start_sec": 1, "end_sec": 1.4}],
        duration_seconds=10,
        on_screen_blocks=[],
        scene_changes=0,
    )

    assert missing["speech"]["spoken_word_count"] is None
    assert missing["pauses"]["available"] is False
    assert missing["on_screen"]["block_count"] is None
    assert missing["visual_cadence"]["change_count"] is None
    assert one_word["pauses"]["available"] is True
    assert one_word["pauses"]["thresholds"]["0.3"]["count"] == 0
    assert one_word["on_screen"]["block_count"] == 0
    assert one_word["visual_cadence"]["change_count"] == 0


def test_word_timestamp_normalization_keeps_source_links_and_rejects_partial_timing():
    words = normalize_word_timestamps(
        [
            {
                "text": "second",
                "start_time": 1.0,
                "end_time": 1.2,
                "source_path": "clip.wav",
                "extractor_version": "whisper-test",
            },
            {"token": "first", "start_sec": 0.1, "duration": 0.2},
        ]
    )

    assert [word["text"] for word in words] == ["first", "second"]
    assert words[1]["source_path"] == "clip.wav"
    assert words[1]["extractor_version"] == "whisper-test"
    with pytest.raises(ValueError, match="needs start and end"):
        normalize_word_timestamps([{"word": "broken", "start": 0.0}])
