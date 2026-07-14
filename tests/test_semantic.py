from __future__ import annotations

import sqlite3

import pytest

from content_reference_toolkit import cli
from content_reference_toolkit.semantic import (
    OllamaSemanticAnalyzer,
    RuleBasedSemanticAnalyzer,
    persist_semantic_result,
    validate_semantic_result,
)


def test_rule_analyzer_labels_bilingual_topic_hook_cta_and_evidence():
    result = RuleBasedSemanticAnalyzer().analyze(
        {
            "caption": "Marketing sin una audiencia clara no funciona. Guarda esta guía.",
            "hook_onscreen": "3 errores de marketing que frenan tus ventas",
            "slides": [
                {"text": "3 errores de marketing"},
                {"text": "Paso 1: define tu audiencia"},
                {"text": "Guárdalo para después"},
            ],
        }
    )

    assert "marketing" in result["topics"]
    assert result["hook_archetype"] == "number_list"
    assert result["cta_type"] == "save"
    assert result["slide_roles"] == ["cover_hook", "step", "cta"]
    assert result["evidence"]
    assert "spoken_delivery" in result["not_analyzable"]


def test_rule_analyzer_evidence_offsets_match_exact_excerpt():
    caption = "Before marketing advice and more context. Save this guide."
    result = RuleBasedSemanticAnalyzer().analyze({"caption": caption})
    topic = next(
        item
        for item in result["evidence"]
        if item["dimension"] == "topic" and item["label"] == "marketing"
    )

    assert caption[topic["start_char"]:topic["end_char"]] == topic["excerpt"]


def test_rule_analyzer_does_not_claim_spoken_style_without_transcript():
    result = RuleBasedSemanticAnalyzer().analyze({"caption": "A short caption"})

    assert "spoken_delivery" in result["not_analyzable"]
    assert "spoken_structure" in result["not_analyzable"]
    assert result["reel_structure"] == []


def test_validate_semantic_result_rejects_unknown_taxonomy_value():
    valid = RuleBasedSemanticAnalyzer().analyze({"caption": "How to build software step by step"})
    valid["hook_archetype"] = "made_up"

    with pytest.raises(ValueError, match="hook archetype"):
        validate_semantic_result(valid)


def test_ollama_adapter_rejects_non_local_endpoint_before_network():
    analyzer = OllamaSemanticAnalyzer(model_name="model", endpoint="https://example.com/api/generate")

    with pytest.raises(ValueError, match="local http"):
        analyzer.analyze({"caption": "untrusted"})


def test_semantic_labels_are_versioned_and_persist_evidence(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/p/abc/", "views": "100"},
    )
    result = RuleBasedSemanticAnalyzer().analyze(
        {"caption": "How to use AI software: save this guide", "hook_onscreen": "How to use AI"}
    )

    count = persist_semantic_result(con, piece_id=piece["id"], result=result)

    assert count > 0
    row = con.execute(
        "SELECT * FROM semantic_labels WHERE piece_id=? AND dimension='topic' AND label='technology'",
        (piece["id"],),
    ).fetchone()
    assert row["label_version"] == "semantic-v1"
    assert row["model_provider"] == "rules"
    assert "excerpt" in row["evidence_json"]


def test_semantic_reanalysis_replaces_labels_and_rolls_back_on_failure(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/p/abc/", "views": "100"},
    )
    original = RuleBasedSemanticAnalyzer().analyze(
        {"caption": "How to use AI software: save this guide"}
    )
    persist_semantic_result(con, piece_id=piece["id"], result=original)
    con.execute(
        """
        CREATE TRIGGER reject_marketing_label
        BEFORE INSERT ON semantic_labels
        WHEN NEW.label='marketing'
        BEGIN SELECT RAISE(ABORT, 'injected semantic failure'); END
        """
    )
    replacement = RuleBasedSemanticAnalyzer().analyze(
        {"caption": "Three marketing ideas to improve your campaign"}
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected semantic failure"):
        persist_semantic_result(con, piece_id=piece["id"], result=replacement)

    topics = {
        row["label"]
        for row in con.execute(
            "SELECT label FROM semantic_labels WHERE piece_id=? AND dimension='topic'",
            (piece["id"],),
        )
    }
    assert "technology" in topics
    assert "marketing" not in topics


def test_semantic_result_without_source_evidence_persists_no_labels(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/p/empty/", "views": "100"},
    )

    count = persist_semantic_result(
        con,
        piece_id=piece["id"],
        result=RuleBasedSemanticAnalyzer().analyze({}),
    )

    assert count == 0
    assert con.execute(
        "SELECT COUNT(*) FROM semantic_labels WHERE piece_id=?",
        (piece["id"],),
    ).fetchone()[0] == 0
