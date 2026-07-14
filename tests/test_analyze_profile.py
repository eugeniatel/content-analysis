from __future__ import annotations

import csv
import json

from content_reference_toolkit import cli
from content_reference_toolkit.profile_pipeline import build_profile_report
from content_reference_toolkit.profiles import associate_piece


def write_inventory(path, *, rows=30, creator="example"):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "url",
                "platform",
                "format",
                "creator",
                "published_at",
                "duration_sec",
                "caption",
                "views",
                "metric_source",
                "metrics_captured_at",
            ],
        )
        writer.writeheader()
        for index in range(rows):
            is_reel = index < rows // 2
            kind = "reel" if is_reel else "p"
            format_ = "reel" if is_reel else "carousel"
            writer.writerow(
                {
                    "url": f"https://www.instagram.com/{kind}/post-{index:02}/",
                    "platform": "instagram",
                    "format": format_,
                    "creator": creator,
                    "published_at": f"2026-06-{(index % 28) + 1:02}",
                    "duration_sec": 12 + index if is_reel else "",
                    "caption": (
                        f"Marketing guide {index}: save this step by step tutorial"
                        if is_reel
                        else f"Business carousel {index}: ideas and examples"
                    ),
                    "views": 10_000 - index * 100 if is_reel else 1_000 - index,
                    "metric_source": "owned_export",
                    "metrics_captured_at": "2026-07-13T12:00:00+00:00",
                }
            )


def test_analyze_profile_offline_inventory_writes_complete_bundle(tmp_path, capsys):
    inventory = tmp_path / "inventory.csv"
    output = tmp_path / "analysis"
    write_inventory(inventory)
    args = cli.parse_args(
        [
            "analyze-profile",
            "https://www.instagram.com/example/",
            "--inventory-input",
            str(inventory),
            "--posts",
            "30",
            "--goal",
            "views",
            "--top",
            "10",
            "--mode",
            "quick",
            "--evidence-mode",
            "owned",
            "--bootstrap-iterations",
            "100",
            "--output-root",
            str(output),
        ]
    )

    assert args.func(args) == 0

    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["status"] == "done"
    assert summary["coverage"]["inventoried_posts"] == 30
    assert summary["coverage"]["eligible_posts"] == 30
    assert summary["coverage"]["formats"] == {"carousel": 15, "reel": 15}
    assert (output / "report.md").exists()
    assert (output / "report.html").exists()
    assert (output / "findings.json").exists()

    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert report["profile"]["handle"] == "example"
    assert report["run"]["status"] == "done"
    assert report["run"]["completed_at"]
    assert report["winners"][0]["format"] == "reel"
    assert report["sections"]["writing_vocabulary"]["details"]["profile_unigrams"]
    assert any(
        finding["feature"] == "format=reel" and finding["status"] == "supported"
        for finding in report["findings"]
    )

    con = cli.connect(output)
    assert con.execute("SELECT status FROM profile_runs").fetchone()[0] == "done"
    assert con.execute("SELECT COUNT(*) FROM metric_observations WHERE metric_name='views'").fetchone()[0] == 30
    assert con.execute("SELECT COUNT(*) FROM piece_features").fetchone()[0] == 30
    assert con.execute("SELECT COUNT(*) FROM semantic_labels").fetchone()[0] > 30
    assert con.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 0

    winner_piece_id = report["winners"][0]["piece_id"]
    artifact_id = con.execute(
        """
        INSERT INTO text_artifacts(
            piece_id, kind, language, text, start_sec, end_sec,
            extractor_version, source_hash
        ) VALUES (?, 'transcript_segment', 'en', ?, 0.2, 2.4, 'test-v1', 'test')
        """,
        (winner_piece_id, "Open with a concrete promise, then explain the steps."),
    ).lastrowid
    con.commit()
    profile_id = con.execute("SELECT id FROM profiles").fetchone()[0]
    run_id = con.execute("SELECT id FROM profile_runs").fetchone()[0]
    winner_piece = con.execute("SELECT * FROM pieces WHERE id=?", (winner_piece_id,)).fetchone()
    associate_piece(
        con,
        piece_id=winner_piece_id,
        profile_id=profile_id,
        run_id=run_id,
        canonical_url=winner_piece["canonical_url"],
        media_type=winner_piece["media_type"],
        platform_content_id=winner_piece["platform_content_id"],
    )

    rich_report, _ = build_profile_report(
        con,
        profile_id=profile_id,
        profile_run_id=run_id,
        objective="views",
        top_k=10,
        bootstrap_iterations=100,
    )

    artifact = next(item for item in rich_report["evidence"] if item.get("artifact_id") == artifact_id)
    assert artifact["kind"] == "transcript_span"
    assert artifact["start_sec"] == 0.2
    assert artifact["navigation"]["timestamp_anchor"].startswith("timestamp-")
    assert con.execute(
        "SELECT COUNT(*) FROM finding_evidence WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()[0] > 0


def test_analyze_profile_can_rerun_without_losing_run_membership(tmp_path, capsys):
    inventory = tmp_path / "inventory.csv"
    output = tmp_path / "analysis"
    write_inventory(inventory, rows=31)

    def run(posts):
        args = cli.parse_args(
            [
                "analyze-profile",
                "https://www.instagram.com/example/",
                "--inventory-input",
                str(inventory),
                "--posts",
                str(posts),
                "--top",
                "10",
                "--mode",
                "quick",
                "--bootstrap-iterations",
                "100",
                "--output-root",
                str(output),
            ]
        )
        assert args.func(args) == 0
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    first = run(30)
    second = run(31)

    assert first["run_id"] != second["run_id"]
    assert first["analysis_id"] != second["analysis_id"]
    con = cli.connect(output)
    assert con.execute("SELECT COUNT(*) FROM pieces").fetchone()[0] == 31
    assert con.execute("SELECT COUNT(*) FROM profile_runs WHERE status='done'").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM analysis_runs WHERE status='done'").fetchone()[0] == 2
    memberships = con.execute(
        """
        SELECT profile_run_id, COUNT(*) AS count
        FROM profile_run_pieces
        GROUP BY profile_run_id
        ORDER BY count
        """
    ).fetchall()
    assert [row["count"] for row in memberships] == [30, 31]
    assert con.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 2
    rerun_report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert rerun_report["run"]["id"] == second["run_id"]
    assert rerun_report["run"]["status"] == "done"


def test_inventory_rows_for_another_profile_are_rejected(tmp_path):
    inventory = tmp_path / "wrong-profile.csv"
    output = tmp_path / "analysis"
    write_inventory(inventory, rows=3, creator="someone_else")
    args = cli.parse_args(
        [
            "analyze-profile",
            "https://www.instagram.com/example/",
            "--inventory-input",
            str(inventory),
            "--posts",
            "3",
            "--top",
            "1",
            "--bootstrap-iterations",
            "50",
            "--output-root",
            str(output),
        ]
    )

    assert args.func(args) == 0

    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert report["coverage"]["inventoried_posts"] == 0
    assert report["coverage"]["failure_count"] == 3
    assert {row["stage"] for row in report["failures"]} == {
        "inventory-profile-validation"
    }


def test_analyze_profile_with_no_public_inventory_still_reports_coverage(tmp_path, monkeypatch):
    output = tmp_path / "empty-analysis"
    monkeypatch.setattr(cli, "yt_dlp_flat_entries", lambda *args, **kwargs: ([], "profile unavailable"))
    args = cli.parse_args(
        [
            "analyze-profile",
            "https://www.instagram.com/example/",
            "--posts",
            "20",
            "--output-root",
            str(output),
        ]
    )

    assert args.func(args) == 0

    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert report["coverage"]["inventoried_posts"] == 0
    assert report["coverage"]["eligible_posts"] == 0
    assert report["findings"][0]["status"] == "insufficient_evidence"
    assert "profile unavailable" in report["failures"][0]["message"]


def test_analyze_profile_parser_exposes_required_product_controls():
    args = cli.parse_args(
        [
            "analyze-profile",
            "https://www.instagram.com/example/",
            "--posts",
            "100",
            "--goal",
            "shares",
            "--top",
            "10",
            "--mode",
            "deep",
            "--deep-limit",
            "24",
        ]
    )

    assert args.posts == 100
    assert args.goal == "shares"
    assert args.top == 10
    assert args.mode == "deep"
    assert args.deep_limit == 24
