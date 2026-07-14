from __future__ import annotations

import json
import sqlite3

import pytest

from content_reference_toolkit import cli
from content_reference_toolkit.profile_pipeline import (
    _is_positive_repeat_signal,
    build_profile_report,
    persist_analysis,
)
from content_reference_toolkit.profiles import associate_piece, start_profile_run, upsert_profile


def _finding(piece_id: str, *, claim: str) -> dict:
    return {
        "id": "format-reel",
        "section": "formats",
        "dimension": "format",
        "feature": "format=reel",
        "claim": claim,
        "comparison": "reels vs other posts",
        "effect": {"value": 2.0, "unit": "winner_enrichment_ratio"},
        "winner_prevalence": 1.0,
        "baseline_prevalence": 0.5,
        "sample": {"feature": 10, "control": 20},
        "interval": None,
        "confidence": "high",
        "status": "supported",
        "evidence": [piece_id],
    }


def test_analysis_replacement_is_atomic(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    profile = upsert_profile(con, "https://www.instagram.com/example/")
    run_id = start_profile_run(
        con,
        profile_id=profile["id"],
        mode="quick",
        evidence_mode="public",
        requested_posts=1,
        objective="views",
        top_k=1,
        config={},
    )
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/reel/abc/", "views": "100"},
    )
    associate_piece(
        con,
        piece_id=piece["id"],
        profile_id=profile["id"],
        run_id=run_id,
        canonical_url=piece["source"],
        media_type="reel",
    )
    analysis = {
        "analysis_id": "analysis-test",
        "objective": {"name": "views"},
        "cohorts": {},
        "method": {},
    }
    evidence = [{"id": piece["id"], "piece_id": piece["id"], "excerpt": "old"}]
    persist_analysis(
        con,
        profile_run_id=run_id,
        analysis=analysis,
        findings=[_finding(piece["id"], claim="old claim")],
        evidence=evidence,
    )
    con.execute(
        """
        CREATE TRIGGER reject_broken_finding
        BEFORE INSERT ON findings
        WHEN NEW.claim='boom'
        BEGIN SELECT RAISE(ABORT, 'injected finding failure'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected finding failure"):
        persist_analysis(
            con,
            profile_run_id=run_id,
            analysis=analysis,
            findings=[_finding(piece["id"], claim="boom")],
            evidence=evidence,
        )

    assert con.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
    stored = con.execute("SELECT claim FROM findings").fetchone()
    assert stored["claim"] == "old claim"
    assert con.execute("SELECT COUNT(*) FROM finding_evidence").fetchone()[0] == 1


def test_repeat_recommendations_require_a_positive_effect():
    assert _is_positive_repeat_signal(
        {"effect": {"value": 1.4, "unit": "winner_enrichment_ratio"}}
    )
    assert not _is_positive_repeat_signal(
        {"effect": {"value": 0.7, "unit": "winner_enrichment_ratio"}}
    )
    assert not _is_positive_repeat_signal(
        {"effect": {"value": -0.5, "unit": "log2_smoothed_term_enrichment"}}
    )


def test_historical_report_rebuild_uses_frozen_text_and_artifacts(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    profile = upsert_profile(con, "https://www.instagram.com/example/")
    run_id = start_profile_run(
        con,
        profile_id=profile["id"],
        mode="deep",
        evidence_mode="public",
        requested_posts=1,
        objective="views",
        top_k=1,
        config={},
    )
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={
            "url": "https://www.instagram.com/reel/abc/",
            "format": "reel",
            "caption": "Original caption",
            "views": "100",
        },
    )
    old_transcript = [{
        "start": 0.0,
        "end": 1.0,
        "text": "old words",
        "words": [
            {"start": 0.0, "end": 0.4, "word": "old"},
            {"start": 0.5, "end": 1.0, "word": "words"},
        ],
    }]
    con.execute(
        "UPDATE pieces SET transcript=? WHERE id=?",
        (json.dumps(old_transcript), piece["id"]),
    )
    con.execute(
        """
        INSERT INTO text_artifacts(
            piece_id, kind, text, start_sec, end_sec, extractor_version, source_hash
        ) VALUES (?, 'transcript_segment', 'old words', 0, 1, 'old', 'old')
        """,
        (piece["id"],),
    )
    associate_piece(
        con,
        piece_id=piece["id"],
        profile_id=profile["id"],
        run_id=run_id,
        canonical_url=piece["source"],
        media_type="reel",
    )

    first, first_analysis = build_profile_report(
        con,
        profile_id=profile["id"],
        profile_run_id=run_id,
        objective="views",
        top_k=1,
        bootstrap_iterations=50,
    )
    con.execute(
        "UPDATE pieces SET transcript=? WHERE id=?",
        (json.dumps([{"start": 0, "end": 1, "text": "entirely new words", "words": []}]), piece["id"]),
    )
    con.execute(
        """
        INSERT INTO text_artifacts(
            piece_id, kind, text, start_sec, end_sec, extractor_version, source_hash
        ) VALUES (?, 'transcript_segment', 'entirely new words', 0, 1, 'new', 'new')
        """,
        (piece["id"],),
    )
    con.commit()

    second, second_analysis = build_profile_report(
        con,
        profile_id=profile["id"],
        profile_run_id=run_id,
        objective="views",
        top_k=1,
        bootstrap_iterations=50,
    )

    assert first_analysis["analysis_id"] == second_analysis["analysis_id"]
    first_excerpts = {item.get("excerpt") for item in first["evidence"]}
    second_excerpts = {item.get("excerpt") for item in second["evidence"]}
    assert "old words" in first_excerpts == second_excerpts
    assert "entirely new words" not in second_excerpts


def test_measured_empty_arrays_do_not_count_as_content_coverage(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    profile = upsert_profile(con, "https://www.instagram.com/example/")
    run_id = start_profile_run(
        con,
        profile_id=profile["id"],
        mode="deep",
        evidence_mode="public",
        requested_posts=2,
        objective="views",
        top_k=1,
        config={},
    )
    for slug, format_, views in (("reel", "reel", 200), ("carousel", "carousel", 100)):
        piece = cli.import_metric_row(
            con,
            output_root=root,
            row={
                "url": f"https://www.instagram.com/{'reel' if format_ == 'reel' else 'p'}/{slug}/",
                "format": format_,
                "views": str(views),
            },
        )
        con.execute(
            "UPDATE pieces SET transcript='[]', slides='[]' WHERE id=?",
            (piece["id"],),
        )
        associate_piece(
            con,
            piece_id=piece["id"],
            profile_id=profile["id"],
            run_id=run_id,
            canonical_url=piece["source"],
            media_type=format_,
        )

    report, _ = build_profile_report(
        con,
        profile_id=profile["id"],
        profile_run_id=run_id,
        objective="views",
        top_k=1,
        bootstrap_iterations=50,
    )

    assert report["coverage"]["formats"] == {"carousel": 1, "reel": 1}
    assert report["coverage"]["transcript_posts"] == 0
    assert report["coverage"]["word_timed_posts"] == 0
    assert report["coverage"]["carousel_ocr_posts"] == 0
