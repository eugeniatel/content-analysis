from __future__ import annotations

import csv
import io
from pathlib import Path

from content_reference_toolkit import cli


def test_slug_for_url_is_stable_and_readable():
    slug = cli.source_dir_name("https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/")

    assert slug.startswith("instagram-com-mia-astral-reel-dz8hlogbd7q-")
    assert len(slug.rsplit("-", 1)[-1]) == 8


def test_slug_for_local_file_is_stable_and_readable():
    slug = cli.source_dir_name("/tmp/My Carousel Slide 01.png")

    assert slug.startswith("my-carousel-slide-01-")


def test_detect_format_uses_url_and_media():
    assert cli.infer_format("https://www.instagram.com/x/reel/abc/", None, []) == "reel"
    assert cli.infer_format("https://www.instagram.com/x/p/abc/", None, []) == "carousel"
    assert cli.infer_format("https://www.tiktok.com/@x/video/1", None, []) == "tiktok"
    assert cli.infer_format("https://x.com/euge/status/1", None, []) == "post"
    assert cli.infer_format("https://www.linkedin.com/feed/update/urn:li:activity:1/", None, []) == "post"
    assert cli.infer_format("https://www.youtube.com/shorts/abc", None, []) == "short"
    assert cli.infer_format("/tmp/a.mp4", None, [Path("/tmp/a.mp4")]) == "reel"
    assert cli.infer_format("/tmp/a.png", None, [Path("/tmp/a.png")]) == "carousel"


def test_platform_for_source_detects_planned_platforms():
    assert cli.platform_for_source("https://x.com/euge/status/1") == "x"
    assert cli.platform_for_source("https://twitter.com/euge/status/1") == "x"
    assert cli.platform_for_source("https://www.linkedin.com/feed/update/1") == "linkedin"
    assert cli.platform_for_source("https://www.youtube.com/shorts/abc") == "youtube"
    assert cli.platform_for_source("https://www.threads.net/@x/post/1") == "threads"
    assert cli.platform_for_source("https://www.facebook.com/reel/1") == "facebook"


def test_duration_bucket():
    assert cli.duration_bucket("carousel", None) == "carousel"
    assert cli.duration_bucket("reel", 20) == "short"
    assert cli.duration_bucket("reel", 45) == "mid"
    assert cli.duration_bucket("reel", 61) == "long"


def test_source_id_is_hash_only():
    assert cli.source_id("https://example.com/a") == cli.url_hash("https://example.com/a")
    assert len(cli.source_id("https://example.com/a")) == 16


def test_published_at_from_metadata_normalizes_dates():
    assert cli.published_at_from_metadata({"upload_date": "20260624"}) == "2026-06-24"
    assert cli.published_at_from_metadata({"timestamp": 1782322700}) == "2026-06-24"
    assert cli.published_at_from_metadata({}) is None


def test_tesseract_languages_maps_iso_codes(monkeypatch):
    monkeypatch.setattr(cli, "tool", lambda name: None)  # no tesseract -> skip pack check
    assert cli.tesseract_languages(["es", "pt", "en"]) == "spa+por+eng"
    assert cli.tesseract_languages(["es", "es"]) == "spa"
    assert cli.tesseract_languages([]) == "eng"


def test_tesseract_languages_filters_to_installed_packs(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "List of available languages:\neng\nspa\n"

    monkeypatch.setattr(cli, "tool", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(cli, "run", lambda *a, **k: FakeProc())
    # por is requested but not installed, so it is dropped.
    assert cli.tesseract_languages(["es", "pt", "en"]) == "spa+eng"


def test_derive_metric_summary_prefers_platform_primary_metric():
    summary = cli.derive_metric_summary(
        platform="tiktok",
        metrics={"completion_rate": 0.72, "comments": 12},
        interaction_count=44,
        engagement_rate=0.03,
        engagement_basis="views",
        metric_source="manual_csv",
    )

    assert summary["primary_metric_name"] == "completion_rate"
    assert summary["primary_metric_value"] == 0.72
    assert summary["primary_metric_basis"] == "completion_rate"
    assert summary["metric_confidence"] == "native"


def test_import_metrics_creates_piece_with_linkedin_engagement_rate(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)

    result = cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
            "impressions": "1000",
            "reactions": "40",
            "comments": "10",
            "reposts": "5",
            "engagement_rate": "5.5%",
            "creator": "Euge",
        },
    )

    assert result["status"] == "done"
    row = con.execute("SELECT * FROM pieces").fetchone()
    assert row["platform"] == "linkedin"
    assert row["likes"] == 40
    assert row["comments"] == 10
    assert row["shares"] == 5
    assert row["interaction_count"] == 55
    assert row["primary_metric_name"] == "engagement_rate"
    assert row["primary_metric_value"] == 0.055
    assert row["metric_source"] == "manual_csv"


def test_metric_report_ranks_by_primary_metric(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.tiktok.com/@x/video/1",
            "completion_rate": "40%",
            "likes": "100",
        },
    )
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.tiktok.com/@x/video/2",
            "completion_rate": "75%",
            "likes": "20",
        },
    )

    report = cli.metric_report(con, platform="tiktok", limit=1)

    assert report["rows"] == 1
    assert report["total_rows"] == 2
    assert report["items"][0]["source_url"] == "https://www.tiktok.com/@x/video/2"
    assert report["items"][0]["primary_metric_name"] == "completion_rate"
    assert report["items"][0]["primary_metric_value"] == 0.75


def test_metric_report_falls_back_to_interactions(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={"url": "https://example.com/a", "likes": "2", "comments": "1"},
    )
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={"url": "https://example.com/b", "likes": "20", "comments": "3"},
    )

    report = cli.metric_report(con, limit=2)

    assert report["items"][0]["source_url"] == "https://example.com/b"
    assert report["items"][0]["primary_metric_name"] == "interaction_count"
    assert report["items"][0]["interaction_count"] == 23


def test_metric_template_outputs_linkedin_csv():
    rows = list(csv.DictReader(io.StringIO(cli.metric_template("linkedin"))))

    assert rows[0]["platform"] == "linkedin"
    assert rows[0]["engagement_rate"] == "5.5%"
    assert "comments" in rows[0]
    assert "reposts" in rows[0]


def test_metric_template_rejects_unknown_platform():
    try:
        cli.metric_template("myspace")
    except ValueError as exc:
        assert "unknown platform" in str(exc)
    else:
        raise AssertionError("Expected unknown platform to raise ValueError")


def test_coverage_report_summarizes_platforms_and_metric_sources(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
            "engagement_rate": "5.5%",
        },
    )
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.tiktok.com/@x/video/1",
            "completion_rate": "75%",
        },
    )

    report = cli.coverage_report(con)

    assert report["total_rows"] == 2
    platforms = {row["platform"]: row for row in report["platforms"]}
    assert platforms["linkedin"]["rows"] == 1
    assert platforms["linkedin"]["with_native_primary_metric"] == 1
    assert platforms["linkedin"]["metric_sources"]["manual_csv"] == 1
    assert platforms["tiktok"]["formats"]["tiktok"] == 1
