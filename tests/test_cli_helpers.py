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


def test_extracts_platform_ids():
    assert cli.youtube_video_id("https://www.youtube.com/shorts/abc123") == "abc123"
    assert cli.youtube_video_id("https://youtu.be/abc123") == "abc123"
    assert cli.youtube_video_id("https://www.youtube.com/watch?v=abc123&t=1") == "abc123"
    assert cli.x_post_id("https://x.com/euge/status/123456") == "123456"
    assert cli.linkedin_activity_urn("https://www.linkedin.com/feed/update/urn:li:activity:123456/") == "urn:li:activity:123456"


def test_platform_for_source_detects_planned_platforms():
    assert cli.platform_for_source("https://x.com/euge/status/1") == "x"
    assert cli.platform_for_source("https://twitter.com/euge/status/1") == "x"
    assert cli.platform_for_source("https://www.linkedin.com/feed/update/1") == "linkedin"
    assert cli.platform_for_source("https://www.youtube.com/shorts/abc") == "youtube"
    assert cli.platform_for_source("https://www.threads.net/@x/post/1") == "threads"
    assert cli.platform_for_source("https://www.facebook.com/reel/1") == "facebook"


def test_link_intake_detects_social_profile():
    result = cli.link_intake("https://www.instagram.com/example_brand/")

    assert result["status"] == "done"
    assert result["kind"] == "social_profile"
    assert result["platform"] == "instagram"
    assert result["connector_options_command"] == "content-reference connector-options instagram"


def test_link_intake_discovers_social_profiles_on_website():
    html = """
    <html><body>
      <a href="https://www.instagram.com/example_brand/">Instagram</a>
      <a href="https://www.tiktok.com/@example_brand">TikTok</a>
      <a href="/about">About</a>
    </body></html>
    """

    result = cli.link_intake("https://example.com", fetch_html=lambda url: html)

    assert result["kind"] == "website"
    assert result["profiles"] == [
        {"platform": "instagram", "url": "https://www.instagram.com/example_brand"},
        {"platform": "tiktok", "url": "https://www.tiktok.com/@example_brand"},
    ]
    assert result["prompt"] == (
        "I found these profiles [instagram: https://www.instagram.com/example_brand, "
        "tiktok: https://www.tiktok.com/@example_brand], would you like to run content-analysis on all of them?"
    )
    assert result["suggested_command"] == "content-reference research-link https://example.com --all"


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
    assert "hook_onscreen" in rows[0]


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


def test_import_metrics_canonicalizes_export_headers(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)

    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "Post URL": "https://www.instagram.com/reel/abc/",
            "Average Percentage Watched": "38%",
            "Share Count": "1,200",
            "Save Count": "300",
            "Visual Hook": "3 signos con suerte",
            "Spoken CTA": "guardalo para despues",
        },
    )

    row = con.execute("SELECT * FROM pieces").fetchone()
    assert row["platform"] == "instagram"
    assert row["primary_metric_name"] == "retention_rate"
    assert row["primary_metric_value"] == 0.38
    assert row["shares"] == 1200
    assert row["hook_onscreen"] == "3 signos con suerte"
    assert row["cta_spoken"] == "guardalo para despues"


def test_analytics_report_groups_by_format_hook_and_cta(tmp_path):
    output_root = tmp_path / "references"
    con = cli.connect(output_root)
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.tiktok.com/@x/video/1",
            "completion_rate": "40%",
            "hook_onscreen": "hook A",
            "cta_spoken": "comment below",
        },
    )
    cli.import_metric_row(
        con,
        output_root=output_root,
        row={
            "url": "https://www.tiktok.com/@x/video/2",
            "completion_rate": "80%",
            "hook_onscreen": "hook A",
            "cta_spoken": "comment below",
        },
    )

    report = cli.analytics_report(con, platform="tiktok", min_count=2)

    assert report["usable_rows"] == 2
    assert report["formats"][0]["key"] == "tiktok"
    assert round(report["formats"][0]["avg_metric"], 3) == 0.6
    assert report["hook_onscreen"][0]["key"] == "hook A"
    assert report["cta_spoken"][0]["key"] == "comment below"


def test_fetch_youtube_metrics_maps_statistics():
    def fake_get(url, **kwargs):
        assert "youtube/v3/videos" in url
        return {
            "items": [{
                "snippet": {"channelTitle": "Creator", "publishedAt": "2026-06-29T00:00:00Z", "title": "Short title"},
                "statistics": {"viewCount": "1000", "likeCount": "70", "commentCount": "9"},
            }]
        }

    row = cli.fetch_youtube_metrics("https://www.youtube.com/shorts/abc123", api_key="key", http_get=fake_get)

    assert row["platform"] == "youtube"
    assert row["format"] == "short"
    assert row["views"] == "1000"
    assert row["metric_source"] == "youtube_api"


def test_fetch_x_metrics_maps_public_metrics():
    def fake_get(url, **kwargs):
        assert "api.x.com/2/tweets/123456" in url
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        return {
            "data": {
                "created_at": "2026-06-29T00:00:00Z",
                "text": "hello",
                "public_metrics": {"like_count": 10, "reply_count": 4, "retweet_count": 3, "quote_count": 2},
            },
            "includes": {"users": [{"username": "euge"}]},
        }

    row = cli.fetch_x_metrics("https://x.com/euge/status/123456", bearer_token="token", http_get=fake_get)

    assert row["platform"] == "x"
    assert row["creator"] == "euge"
    assert row["replies"] == "4"
    assert row["metric_source"] == "x_api"


def test_fetch_linkedin_metrics_maps_social_actions():
    def fake_get(url, **kwargs):
        assert "socialActions/urn%3Ali%3Aactivity%3A123456" in url
        assert kwargs["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
        return {
            "likesSummary": {"totalLikes": 11},
            "commentsSummary": {"aggregatedTotalComments": 6},
            "sharesSummary": {"totalShares": 2},
        }

    row = cli.fetch_linkedin_metrics(
        "https://www.linkedin.com/feed/update/urn:li:activity:123456/",
        access_token="token",
        restli_protocol_version="2.0.0",
        http_get=fake_get,
    )

    assert row["platform"] == "linkedin"
    assert row["likes"] == "11"
    assert row["comments"] == "6"
    assert row["reposts"] == "2"


def test_fetch_meta_graph_insights_maps_instagram_metrics():
    def fake_get(url, **kwargs):
        assert "graph.facebook.com/v24.0/1789/insights" in url
        assert "metric=reach%2Cplays%2Csaved%2Cshares" in url
        return {
            "data": [
                {"name": "reach", "values": [{"value": 1000}]},
                {"name": "plays", "values": [{"value": 1400}]},
                {"name": "saved", "values": [{"value": 50}]},
                {"name": "shares", "values": [{"value": 20}]},
            ]
        }

    row = cli.fetch_meta_graph_insights(
        "1789",
        platform="instagram",
        access_token="token",
        graph_version="v24.0",
        metrics="reach,plays,saved,shares",
        http_get=fake_get,
    )

    assert row["platform"] == "instagram"
    assert row["reach"] == "1000"
    assert row["plays"] == "1400"
    assert row["saves"] == "50"
    assert row["shares"] == "20"
    assert row["metric_source"] == "instagram_graph_api"


def test_fetch_meta_graph_insights_requires_object_id():
    try:
        cli.fetch_meta_graph_insights(
            "https://www.instagram.com/reel/abc/",
            platform="instagram",
            access_token="token",
            graph_version="v24.0",
            metrics="reach",
            http_get=lambda *a, **k: {},
        )
    except ValueError as exc:
        assert "object/media ID" in str(exc)
    else:
        raise AssertionError("Expected public URL to be rejected")


def test_connector_options_and_setup_connector_describe_modes():
    options = cli.connector_options("x")

    assert options["status"] == "done"
    assert "api" in options["modes"]
    assert "scrape" in options["modes"]

    setup = cli.setup_connector("x", "api")
    assert setup["requires"] == ["X_BEARER_TOKEN"]
    assert setup["command"] == "fetch-metrics --platform x"


def test_metric_row_from_metadata_maps_yt_dlp_fields():
    row = cli.metric_row_from_metadata(
        "https://www.tiktok.com/@x/video/1",
        {
            "uploader": "creator",
            "timestamp": 1782720000,
            "duration": 18,
            "description": "caption",
            "view_count": 1000,
            "like_count": 90,
            "comment_count": 7,
            "share_count": 4,
        },
        metric_source="scrape_yt_dlp",
    )

    assert row["platform"] == "tiktok"
    assert row["format"] == "tiktok"
    assert row["creator"] == "creator"
    assert row["views"] == "1000"
    assert row["metric_source"] == "scrape_yt_dlp"


def test_yt_dlp_flat_entries_maps_youtube_ids(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = '{"entries":[{"url":"abc123","ie_key":"Youtube"},{"webpage_url":"https://www.youtube.com/watch?v=def456"}]}'
        stderr = ""

    monkeypatch.setattr(cli, "tool", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(cli, "run", lambda *a, **k: FakeProc())

    urls, error = cli.yt_dlp_flat_entries("https://www.youtube.com/@example", None, limit=2)

    assert error == ""
    assert urls == ["https://www.youtube.com/watch?v=abc123", "https://www.youtube.com/watch?v=def456"]
