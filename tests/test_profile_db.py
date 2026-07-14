from __future__ import annotations

import json
import sqlite3

import pytest

from content_reference_toolkit import cli
from content_reference_toolkit.migrations import MIGRATIONS, schema_version
from content_reference_toolkit.profiles import (
    associate_piece,
    latest_objective_rows,
    profile_identity,
    start_profile_run,
    store_metric_observations,
    upsert_profile,
)


def test_connect_applies_all_versioned_migrations(tmp_path):
    con = cli.connect(tmp_path / "refs")

    assert schema_version(con) == MIGRATIONS[-1][0]
    tables = {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "profiles",
        "profile_runs",
        "metric_observations",
        "text_artifacts",
        "visual_text_blocks",
        "piece_features",
        "semantic_labels",
        "analysis_runs",
        "findings",
        "finding_evidence",
        "profile_run_pieces",
    } <= tables
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrations_upgrade_existing_minimal_corpus(tmp_path):
    root = tmp_path / "refs"
    root.mkdir()
    raw = sqlite3.connect(root / "corpus.sqlite")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE pieces (id TEXT PRIMARY KEY, source_url TEXT);
        CREATE TABLE failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT, source_url TEXT, stage TEXT, message TEXT, occurred_at TEXT
        );
        """
    )
    raw.commit()
    raw.close()

    con = cli.connect(root)

    piece_columns = {row["name"] for row in con.execute("PRAGMA table_info(pieces)")}
    assert {"profile_id", "inventory_run_id", "latest_metric_snapshot_at"} <= piece_columns
    failure_columns = {row["name"] for row in con.execute("PRAGMA table_info(failures)")}
    assert {"profile_id", "run_id", "piece_id"} <= failure_columns


def test_migration_backfills_legacy_run_membership_with_full_snapshot(tmp_path):
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
        row={"url": "https://www.instagram.com/reel/legacy/", "views": "100"},
    )
    transcript = [{"start": 0.0, "end": 1.0, "text": "legacy words", "words": []}]
    con.execute(
        """
        UPDATE pieces
        SET profile_id=?, inventory_run_id=?, media_type='reel', transcript=?
        WHERE id=?
        """,
        (profile["id"], run_id, json.dumps(transcript), piece["id"]),
    )
    artifact_id = con.execute(
        """
        INSERT INTO text_artifacts(
            piece_id, kind, text, start_sec, end_sec, extractor_version, source_hash
        ) VALUES (?, 'transcript_segment', 'legacy words', 0, 1, 'legacy', 'legacy')
        """,
        (piece["id"],),
    ).lastrowid
    con.execute("DELETE FROM profile_run_pieces")
    con.execute("DELETE FROM schema_migrations WHERE version=7")
    con.commit()
    con.close()

    migrated = cli.connect(root)

    membership = migrated.execute("SELECT * FROM profile_run_pieces").fetchone()
    assert membership["profile_run_id"] == run_id
    snapshot = json.loads(membership["snapshot_json"])
    assert json.loads(snapshot["transcript"])[0]["text"] == "legacy words"
    assert snapshot["_text_artifacts"][0]["id"] == artifact_id


def test_profile_identity_canonicalizes_instagram_handle():
    first = profile_identity("https://instagram.com/Example.Brand/?hl=en")
    second = profile_identity("https://www.instagram.com/example.brand/")

    assert first == second
    assert first["canonical_url"] == "https://www.instagram.com/example.brand"


def test_profile_identity_does_not_accept_host_confusion():
    with pytest.raises(ValueError, match="safe profile URL"):
        profile_identity("https://instagram.com@attacker.example/example/")

    identity = profile_identity("https://evilinstagram.com/example/")
    assert identity["platform"] == "unknown"


def test_metric_history_is_idempotent_and_keeps_new_snapshots(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    profile = upsert_profile(con, "https://www.instagram.com/example/")
    run_id = start_profile_run(
        con,
        profile_id=profile["id"],
        mode="quick",
        evidence_mode="public",
        requested_posts=50,
        objective="views",
        top_k=10,
        config={},
    )
    piece = cli.import_metric_row(
        con,
        output_root=root,
        row={
            "url": "https://www.instagram.com/reel/abc/",
            "views": "100",
            "metrics_captured_at": "2026-07-01T00:00:00+00:00",
        },
    )
    associate_piece(
        con,
        piece_id=piece["id"],
        profile_id=profile["id"],
        run_id=run_id,
        canonical_url=piece["source"],
        media_type="reel",
    )

    assert store_metric_observations(
        con,
        piece_id=piece["id"],
        metrics={"views": 100},
        source="manual_csv",
        captured_at="2026-07-01T00:00:00+00:00",
        confidence="native",
        basis_by_metric={"views": "manual_csv:observed_total"},
        unit_by_metric={"views": "count"},
    ) == 0
    assert store_metric_observations(
        con,
        piece_id=piece["id"],
        metrics={"views": 175},
        source="manual_csv",
        captured_at="2026-07-02T00:00:00+00:00",
        confidence="native",
        basis_by_metric={"views": "manual_csv:observed_total"},
        unit_by_metric={"views": "count"},
    ) == 1

    observations = con.execute(
        "SELECT metric_value FROM metric_observations WHERE piece_id=? AND metric_name='views' ORDER BY captured_at",
        (piece["id"],),
    ).fetchall()
    assert [row[0] for row in observations] == [100.0, 175.0]
    latest = latest_objective_rows(con, profile_id=profile["id"], objective="views")
    assert latest[0]["objective_value"] == 175.0


def test_objective_rows_do_not_fall_back_to_other_metrics(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)
    profile = upsert_profile(con, "https://www.instagram.com/example/")
    run_id = start_profile_run(
        con,
        profile_id=profile["id"],
        mode="quick",
        evidence_mode="public",
        requested_posts=2,
        objective="views",
        top_k=1,
        config={},
    )
    for slug, row in (
        ("views", {"views": "100"}),
        ("retention", {"retention_rate": "90%"}),
    ):
        result = cli.import_metric_row(
            con,
            output_root=root,
            row={"url": f"https://www.instagram.com/reel/{slug}/", **row},
        )
        associate_piece(
            con,
            piece_id=result["id"],
            profile_id=profile["id"],
            run_id=run_id,
            canonical_url=result["source"],
            media_type="reel",
        )

    rows = latest_objective_rows(con, profile_id=profile["id"], objective="views")

    assert len(rows) == 1
    assert rows[0]["source_url"].endswith("/views/")


def test_metric_import_preserves_explicit_basis_unit_and_denominator(tmp_path):
    root = tmp_path / "refs"
    con = cli.connect(root)

    cli.import_metric_row(
        con,
        output_root=root,
        row={
            "url": "https://www.instagram.com/reel/abc/",
            "views": "7500",
            "views_basis": "rolling_7_day",
            "views_unit": "count",
            "views_denominator": "published_post",
            "metrics_captured_at": "2026-07-01T00:00:00+00:00",
        },
    )

    row = con.execute(
        "SELECT * FROM metric_observations WHERE metric_name='views'"
    ).fetchone()
    assert row["metric_value"] == 7500
    assert row["metric_basis"] == "rolling_7_day"
    assert row["metric_unit"] == "count"
    assert row["denominator_name"] == "published_post"
