from __future__ import annotations

import json

from content_reference_toolkit import cli, instagram_gtm


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_watchlist(path, rows):
    write_json(path, {"schema_version": instagram_gtm.WATCHLIST_SCHEMA_VERSION, "profiles": rows})


def test_gtm_instagram_ingest_imports_posts_reels_carousels_and_resumes(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.json"
    fixtures = tmp_path / "fixtures"
    output = tmp_path / "out"
    write_watchlist(
        watchlist,
        [
            {
                "profile_key": "instagram:lu.gaitan",
                "entity": "Lu Gaitan",
                "market": "AR",
                "language": "es_AR",
                "tier": "T1",
                "instagram_handle": "lu.gaitan",
                "monitor_status": "candidate_ingestable",
                "tags": ["market:AR", "language:es_AR"],
                "source_refs": ["MARA-575 registry-v1 Rev 3", "MARA-1171"],
            },
            {
                "profile_key": "instagram:melisa-espeche",
                "entity": "Melisa Espeche",
                "market": "AR",
                "language": "es_AR",
                "tier": "T2",
                "instagram_handle": "melisa.espeche",
                "monitor_status": "unsupported_or_unresolved",
                "unsupported_reason": "official handle unresolved",
                "next_verification_action": "GTM Lead verifies the handle.",
            },
        ],
    )
    write_json(
        fixtures / "lu.gaitan.json",
        {
            "business_discovery": {
                "id": "17841400000000000",
                "username": "lu.gaitan",
                "media": {
                    "data": [
                        {
                            "id": "post_1",
                            "caption": "Luna nueva: una pregunta para ordenar el deseo.",
                            "media_type": "IMAGE",
                            "permalink": "https://www.instagram.com/p/post_1/",
                            "timestamp": "2026-07-21T10:00:00+0000",
                            "like_count": 101,
                            "comments_count": 7,
                        },
                        {
                            "id": "reel_1",
                            "caption": "Mercurio retrogrado y los mensajes que no llegan.",
                            "media_type": "VIDEO",
                            "media_product_type": "REELS",
                            "permalink": "https://www.instagram.com/reel/reel_1/",
                            "timestamp": "2026-07-21T12:00:00+0000",
                            "like_count": 202,
                            "comments_count": 19,
                        },
                        {
                            "id": "carousel_1",
                            "caption": "Tres formas de mirar tu carta natal esta semana.",
                            "media_type": "CAROUSEL_ALBUM",
                            "permalink": "https://www.instagram.com/p/carousel_1/",
                            "timestamp": "2026-07-21T13:00:00+0000",
                            "like_count": 303,
                            "comments_count": 29,
                            "children": {
                                "data": [
                                    {"id": "slide_1", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/carousel_1/?img_index=1"},
                                    {"id": "slide_2", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/carousel_1/?img_index=2"},
                                    {"id": "slide_3", "media_type": "VIDEO", "permalink": "https://www.instagram.com/p/carousel_1/?img_index=3"},
                                ]
                            },
                        },
                    ]
                },
            }
        },
    )

    args = cli.parse_args(
        [
            "gtm-instagram-ingest",
            "--watchlist",
            str(watchlist),
            "--fixture-response-dir",
            str(fixtures),
            "--output-root",
            str(output),
            "--dry-run",
            "--limit",
            "10",
        ]
    )

    assert args.func(args) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["new_media"] == 3
    assert summary["imported_rows"] == 3
    assert summary["coverage_summary"] == {"ingestable": 1, "unsupported_or_unresolved": 1}
    assert summary["es_AR_coverage_summary"] == {"ingestable": 1, "unsupported_or_unresolved": 1}

    normalized = [
        json.loads(line)
        for line in (output / "instagram" / "normalized" / "media.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["format"] for row in normalized} == {"carousel", "reel", "static"}
    carousel = next(row for row in normalized if row["media_id"] == "carousel_1")
    assert [child["media_id"] for child in carousel["children"]] == ["slide_1", "slide_2", "slide_3"]
    assert (output / "instagram" / "raw" / "lu.gaitan" / "carousel_1.json").exists()

    coverage = json.loads((output / "instagram" / "reports" / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["es_AR"]["rows"] == 2
    assert {row["entity"]: row["coverage_status"] for row in coverage["profiles"]}["Melisa Espeche"] == "unsupported_or_unresolved"

    brief = json.loads((output / "instagram" / "reports" / "signal-brief.json").read_text(encoding="utf-8"))
    testear = next(finding for finding in brief["findings"] if finding["action"] == "testear")
    assert testear["market"] == "AR"
    assert set(instagram_gtm.RECOMMENDATION_FIELDS) <= set(testear["recommendation"])

    con = cli.connect(output)
    assert con.execute("SELECT COUNT(*) FROM pieces").fetchone()[0] == 3
    db_carousel = con.execute("SELECT slides, platform_content_id FROM pieces WHERE platform_content_id='carousel_1'").fetchone()
    assert [slide["media_id"] for slide in json.loads(db_carousel["slides"])] == ["slide_1", "slide_2", "slide_3"]
    assert con.execute("SELECT COUNT(*) FROM metric_observations WHERE source=?", (instagram_gtm.SOURCE_VERSION,)).fetchone()[0] >= 6

    rerun_args = cli.parse_args(
        [
            "gtm-instagram-ingest",
            "--watchlist",
            str(watchlist),
            "--fixture-response-dir",
            str(fixtures),
            "--output-root",
            str(output),
            "--dry-run",
            "--limit",
            "10",
        ]
    )
    assert rerun_args.func(rerun_args) == 0
    rerun_summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rerun_summary["new_media"] == 0
    assert rerun_summary["imported_rows"] == 0
    assert cli.connect(output).execute("SELECT COUNT(*) FROM pieces").fetchone()[0] == 3


def test_gtm_instagram_ingest_records_rate_limit_without_token_leak(tmp_path):
    watchlist = [
        {
            "profile_key": "instagram:example",
            "entity": "Example",
            "market": "AR",
            "language": "es_AR",
            "instagram_handle": "example",
            "monitor_status": "candidate_ingestable",
        }
    ]

    def fake_get(url):
        assert "access_token=" in url
        raise RuntimeError(f"HTTP 429: rate limit on {url}")

    result = instagram_gtm.run_watchlist_ingestion(
        watchlist=watchlist,
        output_root=tmp_path / "out",
        ig_user_id="1784",
        access_token="fake-access-token",
        graph_version="v24.0",
        limit=5,
        dry_run=True,
        http_get=fake_get,
    )

    row = result["coverage"]["profiles"][0]
    assert row["coverage_status"] == "blocked"
    assert row["reason"] == "rate_limited"
    assert "fake-access-token" not in row["error"]
    assert "access_token=<redacted>" in row["error"]
