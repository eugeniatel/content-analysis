from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def _add_columns(con: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    existing = _columns(con, table)
    for name, declaration in columns:
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _migration_001_profile_entities(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            handle TEXT NOT NULL,
            display_name TEXT,
            primary_language TEXT,
            created_at TEXT NOT NULL,
            last_inventoried_at TEXT,
            UNIQUE(platform, handle)
        );

        CREATE TABLE IF NOT EXISTS profile_runs (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL REFERENCES profiles(id),
            mode TEXT NOT NULL,
            evidence_mode TEXT NOT NULL DEFAULT 'public',
            requested_posts INTEGER NOT NULL,
            selected_objective TEXT NOT NULL,
            top_k INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            config_json TEXT NOT NULL,
            coverage_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_profile_runs_profile_started
            ON profile_runs(profile_id, started_at DESC);
        """
    )
    _add_columns(
        con,
        "pieces",
        (
            ("profile_id", "TEXT REFERENCES profiles(id)"),
            ("platform_content_id", "TEXT"),
            ("canonical_url", "TEXT"),
            ("media_type", "TEXT"),
            ("inventory_run_id", "TEXT REFERENCES profile_runs(id)"),
            ("latest_metric_snapshot_at", "TEXT"),
        ),
    )
    _add_columns(
        con,
        "failures",
        (
            ("profile_id", "TEXT REFERENCES profiles(id)"),
            ("run_id", "TEXT REFERENCES profile_runs(id)"),
            ("piece_id", "TEXT"),
        ),
    )
    piece_columns = _columns(con, "pieces")
    if {"profile_id", "published_at"} <= piece_columns:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_pieces_profile_published "
            "ON pieces(profile_id, published_at DESC)"
        )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_pieces_inventory_run ON pieces(inventory_run_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_failures_profile_run "
        "ON failures(profile_id, run_id, stage)"
    )


def _migration_002_metric_history(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS metric_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            piece_id TEXT NOT NULL REFERENCES pieces(id),
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            metric_unit TEXT NOT NULL,
            denominator_name TEXT,
            metric_basis TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence TEXT,
            captured_at TEXT NOT NULL,
            raw_json TEXT,
            UNIQUE(piece_id, metric_name, metric_unit, metric_basis, source, captured_at)
        );

        CREATE INDEX IF NOT EXISTS idx_metric_observations_piece_name_time
            ON metric_observations(piece_id, metric_name, captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_metric_observations_name_time
            ON metric_observations(metric_name, captured_at DESC);
        """
    )


def _migration_003_evidence_and_features(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS text_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            piece_id TEXT NOT NULL REFERENCES pieces(id),
            kind TEXT NOT NULL,
            language TEXT,
            text TEXT NOT NULL,
            start_sec REAL,
            end_sec REAL,
            slide_index INTEGER,
            source_path TEXT,
            confidence REAL,
            extractor_version TEXT NOT NULL,
            source_hash TEXT,
            UNIQUE(piece_id, kind, start_sec, end_sec, slide_index, text, extractor_version)
        );

        CREATE TABLE IF NOT EXISTS visual_text_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            piece_id TEXT NOT NULL REFERENCES pieces(id),
            slide_index INTEGER,
            timestamp_sec REAL,
            text TEXT NOT NULL,
            confidence REAL,
            x REAL,
            y REAL,
            width REAL,
            height REAL,
            image_width INTEGER,
            image_height INTEGER,
            extractor_version TEXT NOT NULL,
            source_hash TEXT
        );

        CREATE TABLE IF NOT EXISTS piece_features (
            piece_id TEXT NOT NULL REFERENCES pieces(id),
            feature_version TEXT NOT NULL,
            source_hash TEXT,
            computed_at TEXT NOT NULL,
            word_count INTEGER,
            slide_count INTEGER,
            duration_sec REAL,
            words_per_minute REAL,
            pause_300ms INTEGER,
            pause_600ms INTEGER,
            pause_1000ms INTEGER,
            common_json TEXT NOT NULL,
            reel_json TEXT,
            carousel_json TEXT,
            lexical_json TEXT,
            PRIMARY KEY(piece_id, feature_version)
        );

        CREATE INDEX IF NOT EXISTS idx_text_artifacts_piece_kind
            ON text_artifacts(piece_id, kind);
        CREATE INDEX IF NOT EXISTS idx_visual_blocks_piece_slide
            ON visual_text_blocks(piece_id, slide_index, timestamp_sec);
        """
    )


def _migration_004_semantics_and_findings(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            piece_id TEXT NOT NULL REFERENCES pieces(id),
            label_version TEXT NOT NULL,
            dimension TEXT NOT NULL,
            label TEXT NOT NULL,
            confidence REAL,
            model_provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(piece_id, label_version, dimension, label, model_provider, model_name, prompt_version)
        );

        CREATE TABLE IF NOT EXISTS analysis_runs (
            id TEXT PRIMARY KEY,
            profile_run_id TEXT NOT NULL REFERENCES profile_runs(id),
            objective TEXT NOT NULL,
            cohort_config_json TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            label_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running'
        );

        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
            dimension TEXT NOT NULL,
            feature TEXT NOT NULL,
            claim TEXT NOT NULL,
            comparison TEXT NOT NULL,
            effect_value REAL,
            effect_unit TEXT,
            winner_prevalence REAL,
            baseline_prevalence REAL,
            sample_winner INTEGER NOT NULL,
            sample_baseline INTEGER NOT NULL,
            interval_json TEXT,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS finding_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id TEXT NOT NULL REFERENCES findings(id),
            piece_id TEXT REFERENCES pieces(id),
            artifact_id INTEGER REFERENCES text_artifacts(id),
            slide_index INTEGER,
            start_sec REAL,
            end_sec REAL,
            excerpt TEXT,
            role TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_labels_piece_dimension
            ON semantic_labels(piece_id, dimension);
        CREATE INDEX IF NOT EXISTS idx_findings_analysis_dimension
            ON findings(analysis_run_id, dimension);
        CREATE INDEX IF NOT EXISTS idx_finding_evidence_finding
            ON finding_evidence(finding_id);
        """
    )


def _migration_005_visual_cadence(con: sqlite3.Connection) -> None:
    _add_columns(con, "pieces", (("scene_changes", "TEXT"),))


def _migration_006_run_membership(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile_run_pieces (
            profile_run_id TEXT NOT NULL REFERENCES profile_runs(id) ON DELETE CASCADE,
            piece_id TEXT NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
            inventory_position INTEGER,
            observed_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            PRIMARY KEY(profile_run_id, piece_id)
        );

        CREATE INDEX IF NOT EXISTS idx_profile_run_pieces_piece
            ON profile_run_pieces(piece_id, profile_run_id);
        """
    )


def _query_dicts(
    con: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    cursor = con.execute(query, parameters)
    columns = [str(item[0]) for item in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _migration_007_historical_run_snapshots(con: sqlite3.Connection) -> None:
    """Backfill legacy run membership and freeze complete evidence snapshots."""
    piece_columns = _columns(con, "pieces")
    observed_candidates = [
        f"p.{name}"
        for name in ("collected_at", "metrics_captured_at", "latest_metric_snapshot_at")
        if name in piece_columns
    ]
    observed_expression = ", ".join(
        [*observed_candidates, "r.started_at", "'1970-01-01T00:00:00+00:00'"]
    )
    legacy_rows = _query_dicts(
        con,
        f"""
        SELECT p.id AS piece_id, p.inventory_run_id AS profile_run_id,
               COALESCE({observed_expression}) AS observed_at
        FROM pieces p
        JOIN profile_runs r ON r.id=p.inventory_run_id
        WHERE p.inventory_run_id IS NOT NULL
        """,
    )
    for row in legacy_rows:
        con.execute(
            """
            INSERT OR IGNORE INTO profile_run_pieces(
                profile_run_id, piece_id, inventory_position, observed_at, snapshot_json
            ) VALUES (?, ?, NULL, ?, '{}')
            """,
            (row["profile_run_id"], row["piece_id"], row["observed_at"]),
        )

    memberships = _query_dicts(
        con,
        "SELECT profile_run_id, piece_id, snapshot_json FROM profile_run_pieces",
    )
    for membership in memberships:
        piece_rows = _query_dicts(
            con,
            "SELECT * FROM pieces WHERE id=?",
            (membership["piece_id"],),
        )
        if not piece_rows:
            continue
        try:
            snapshot = json.loads(str(membership["snapshot_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        for key, value in piece_rows[0].items():
            snapshot.setdefault(key, value)
        snapshot.setdefault(
            "_text_artifacts",
            _query_dicts(
                con,
                "SELECT * FROM text_artifacts WHERE piece_id=? ORDER BY id",
                (membership["piece_id"],),
            ),
        )
        snapshot.setdefault(
            "_visual_text_blocks",
            _query_dicts(
                con,
                "SELECT * FROM visual_text_blocks WHERE piece_id=? ORDER BY id",
                (membership["piece_id"],),
            ),
        )
        con.execute(
            """
            UPDATE profile_run_pieces
            SET snapshot_json=?
            WHERE profile_run_id=? AND piece_id=?
            """,
            (
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                membership["profile_run_id"],
                membership["piece_id"],
            ),
        )


MIGRATIONS: tuple[Migration, ...] = (
    (1, "profile_entities", _migration_001_profile_entities),
    (2, "metric_history", _migration_002_metric_history),
    (3, "evidence_and_features", _migration_003_evidence_and_features),
    (4, "semantics_and_findings", _migration_004_semantics_and_findings),
    (5, "visual_cadence", _migration_005_visual_cadence),
    (6, "run_membership", _migration_006_run_membership),
    (7, "historical_run_snapshots", _migration_007_historical_run_snapshots),
)


def run_migrations(con: sqlite3.Connection) -> list[int]:
    """Apply all additive schema migrations and return the versions applied now."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {int(row[0]) for row in con.execute("SELECT version FROM schema_migrations")}
    completed: list[int] = []
    for version, name, migration in MIGRATIONS:
        if version in applied:
            continue
        with con:
            migration(con)
            con.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (version, name),
            )
        completed.append(version)
    return completed


def schema_version(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0
