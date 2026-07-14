from __future__ import annotations

import json
import sqlite3

import pytest

from content_reference_toolkit import cli
from content_reference_toolkit.evidence import persist_piece_evidence, persist_piece_features


def test_persist_piece_evidence_keeps_words_and_carousel_blocks(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/p/abc/", "views": "100"},
    )
    extracted = {
        "transcript": [{
            "start": 0.0,
            "end": 1.2,
            "text": "Hola mundo",
            "words": [
                {"start": 0.0, "end": 0.4, "word": "Hola", "probability": 0.9},
                {"start": 0.6, "end": 1.0, "word": "mundo", "probability": 0.8},
            ],
        }],
        "slides": [{
            "slide_index": 1,
            "text": "Tres ideas",
            "blocks": [{
                "text": "Tres ideas",
                "confidence": 0.95,
                "x": 10,
                "y": 20,
                "width": 100,
                "height": 30,
                "image_width": 1080,
                "image_height": 1350,
            }],
        }],
    }

    counts = persist_piece_evidence(
        con,
        piece_id=piece["id"],
        caption="Caption",
        extracted=extracted,
    )

    assert counts == {"text_artifacts": 5, "visual_text_blocks": 1}
    words = con.execute(
        "SELECT text, start_sec, end_sec FROM text_artifacts WHERE kind='transcript_word' ORDER BY start_sec"
    ).fetchall()
    assert [tuple(row) for row in words] == [("Hola", 0.0, 0.4), ("mundo", 0.6, 1.0)]
    block = con.execute("SELECT * FROM visual_text_blocks").fetchone()
    assert block["slide_index"] == 1
    assert block["x"] == 10
    assert block["image_width"] == 1080


def test_persist_piece_evidence_replaces_same_extractor_version(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/reel/abc/", "views": "100"},
    )
    payload = {"transcript": [{"start": 0, "end": 1, "text": "one", "words": []}]}

    persist_piece_evidence(con, piece_id=piece["id"], caption=None, extracted=payload)
    persist_piece_evidence(con, piece_id=piece["id"], caption=None, extracted=payload)

    assert con.execute("SELECT COUNT(*) FROM text_artifacts").fetchone()[0] == 1


def test_persist_piece_evidence_rolls_back_failed_replacement(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/reel/abc/", "views": "100"},
    )
    persist_piece_evidence(
        con,
        piece_id=piece["id"],
        caption=None,
        extracted={"transcript": [{"start": 0, "end": 1, "text": "old", "words": []}]},
    )
    con.execute(
        """
        CREATE TRIGGER reject_new_evidence
        BEFORE INSERT ON text_artifacts
        WHEN NEW.text='new'
        BEGIN SELECT RAISE(ABORT, 'injected evidence failure'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected evidence failure"):
        persist_piece_evidence(
            con,
            piece_id=piece["id"],
            caption=None,
            extracted={"transcript": [{"start": 0, "end": 1, "text": "new", "words": []}]},
        )

    rows = con.execute(
        "SELECT text FROM text_artifacts WHERE piece_id=?",
        (piece["id"],),
    ).fetchall()
    assert [row["text"] for row in rows] == ["old"]


def test_feature_payload_is_cached_by_piece_and_version(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={"url": "https://www.instagram.com/reel/abc/", "views": "100"},
    )

    persist_piece_features(
        con,
        piece_id=piece["id"],
        common={"word_count": 10},
        reel={"words_per_minute": 120, "pause_counts": {"0.3": 2}},
        carousel=None,
        duration_sec=5,
    )
    persist_piece_features(
        con,
        piece_id=piece["id"],
        common={"word_count": 12},
        reel={"words_per_minute": 130, "pause_counts": {"0.3": 3}},
        carousel=None,
        duration_sec=6,
    )

    row = con.execute("SELECT * FROM piece_features").fetchone()
    assert con.execute("SELECT COUNT(*) FROM piece_features").fetchone()[0] == 1
    assert row["word_count"] == 12
    assert row["words_per_minute"] == 130
    assert row["pause_300ms"] == 3


def test_normalize_persists_word_timing_scene_cadence_and_metric_snapshot(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    source = "https://www.instagram.com/reel/abc/"
    identifier = cli.source_id(source)
    processed = root / "processed" / "fixture"
    processed.mkdir(parents=True)
    (processed / "extracted.json").write_text(
        json.dumps(
            {
                "format": "reel",
                "transcript": [{
                    "start": 0.0,
                    "end": 1.0,
                    "text": "Hola mundo",
                    "words": [
                        {"start": 0.0, "end": 0.3, "word": "Hola", "probability": 0.9},
                        {"start": 0.7, "end": 1.0, "word": "mundo", "probability": 0.8},
                    ],
                }],
                "transcript_words": [],
                "onscreen_text": [],
                "slides": None,
                "scene_changes": [2.5, 5.0],
            }
        ),
        encoding="utf-8",
    )
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, status_collect, status_extract,
            raw_dir, processed_dir, metadata_json, media_json, collected_at
        ) VALUES (?, ?, 'instagram', 'reel', 'done', 'done', 'raw/fixture',
                  'processed/fixture', ?, '[]', '2026-07-13T00:00:00+00:00')
        """,
        (
            identifier,
            source,
            json.dumps({
                "duration": 8,
                "description": "Caption",
                "view_count": 1000,
                "like_count": 100,
            }),
        ),
    )
    con.commit()
    row = con.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()

    result = cli.normalize_source(con, output_root=root, row=row, force=False)

    assert result["status"] == "done"
    piece = con.execute("SELECT * FROM pieces WHERE id=?", (identifier,)).fetchone()
    assert json.loads(piece["scene_changes"]) == [2.5, 5.0]
    assert con.execute(
        "SELECT COUNT(*) FROM text_artifacts WHERE piece_id=? AND kind='transcript_word'",
        (identifier,),
    ).fetchone()[0] == 2
    assert con.execute(
        "SELECT metric_value FROM metric_observations WHERE piece_id=? AND metric_name='views'",
        (identifier,),
    ).fetchone()[0] == 1000


def test_failed_forced_collection_preserves_prior_download_evidence(tmp_path, monkeypatch):
    root = tmp_path / "refs"
    con = cli.connect(root)
    source = "https://www.instagram.com/reel/abc/"
    identifier = cli.source_id(source)
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, status_collect, raw_dir,
            processed_dir, metadata_json, media_json, collected_at
        ) VALUES (?, ?, 'instagram', 'reel', 'done', 'raw/prior',
                  'processed/prior', ?, ?, '2026-07-01T00:00:00+00:00')
        """,
        (
            identifier,
            source,
            json.dumps({"description": "valuable caption"}),
            json.dumps(["raw/prior/abc.mp4"]),
        ),
    )
    con.commit()
    monkeypatch.setattr(cli, "yt_dlp_metadata", lambda *args: (None, "metadata failed"))
    monkeypatch.setattr(cli, "yt_dlp_download", lambda *args: ([], "download failed"))

    result = cli.collect_source(
        con,
        output_root=root,
        source=source,
        format_hint="reel",
        notes="",
        cookies_file=None,
        delay=0,
        force=True,
    )

    assert result["status"] == "failed"
    stored = con.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
    assert stored["status_collect"] == "done"
    assert json.loads(stored["metadata_json"])["description"] == "valuable caption"
    assert json.loads(stored["media_json"]) == ["raw/prior/abc.mp4"]
    assert stored["collected_at"] == "2026-07-01T00:00:00+00:00"


def test_normalize_views_without_interactions_keeps_engagement_missing(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    source = "https://www.instagram.com/reel/views-only/"
    identifier = cli.source_id(source)
    processed = root / "processed" / "views-only"
    processed.mkdir(parents=True)
    (processed / "extracted.json").write_text(
        json.dumps({"format": "reel", "transcript": [], "onscreen_text": [], "slides": None}),
        encoding="utf-8",
    )
    con.execute(
        """
        INSERT INTO sources(
            id, source_url, platform, format_hint, status_collect, status_extract,
            raw_dir, processed_dir, metadata_json, media_json, collected_at
        ) VALUES (?, ?, 'instagram', 'reel', 'done', 'done', 'raw/views-only',
                  'processed/views-only', ?, '[]', '2026-07-13T00:00:00+00:00')
        """,
        (identifier, source, json.dumps({"duration": 8, "view_count": 100})),
    )
    con.commit()

    result = cli.normalize_source(
        con,
        output_root=root,
        row=con.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone(),
        force=False,
    )

    assert result["status"] == "done"
    piece = con.execute("SELECT * FROM pieces WHERE id=?", (identifier,)).fetchone()
    assert piece["interaction_count"] is None
    assert piece["engagement_rate"] is None
