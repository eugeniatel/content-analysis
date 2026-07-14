from __future__ import annotations

import json

import pytest

from content_reference_toolkit.reporting import (
    ReportValidationError,
    build_report,
    render_html,
    render_json,
    render_markdown,
    write_report_bundle,
)


def sample_report(**overrides):
    values = {
        "profile": {
            "id": "profile-1",
            "platform": "instagram",
            "handle": "example",
            "display_name": 'Example <script>alert("profile")</script>',
        },
        "run": {
            "id": "run-1",
            "mode": "deep",
            "selected_objective": "views",
            "requested_posts": 40,
            "top_k": 10,
        },
        "coverage": {
            "inventoried_posts": 40,
            "eligible_posts": 34,
            "transcript_posts": 18,
            "carousel_ocr_posts": 12,
            "metric_source": "public metadata",
        },
        "winners": [
            {
                "piece_id": "post-1",
                "title": 'A "quoted" winning Reel',
                "format": "reel",
                "objective": "views",
                "objective_value": 120_000,
                "relative_score": 0.98,
                # Untrusted URLs remain data, but never become active links.
                "url": "javascript:alert(1)",
            }
        ],
        "findings": [
            {
                "id": "finding-format-1",
                "section": "formats",
                "claim": "Reels are associated with higher views in this profile",
                "objective": "views",
                "comparison": "eligible Reels vs other eligible posts",
                "effect": {"value": 1.7, "unit": "median_ratio"},
                "winner_prevalence": 0.8,
                "baseline_prevalence": 0.46,
                "sample": {"feature": 19, "control": 24},
                "interval": {"low": 1.2, "high": 2.1},
                "confidence": "medium",
                # Piece references are allowed by the report contract.
                "evidence": ["post-1", "slide-evidence"],
                "caveat": "Association within this profile; not a causal estimate",
            }
        ],
        "evidence": [
            {
                "id": "transcript-evidence",
                "piece_id": "post-1",
                "kind": "transcript_span",
                "start_sec": 12.345,
                "end_sec": 16.0,
                "excerpt": '<script>alert("evidence")</script> & pause',
                "url": "https://www.instagram.com/reel/safe/",
            },
            {
                "id": "slide-evidence",
                "piece_id": "post-2",
                "kind": "carousel_slide",
                "slide_index": 3,
                "excerpt": 'Slide says "do this" <img src=x onerror=alert(1)>',
                "url": "javascript:alert(document.cookie)",
            },
        ],
        "repeat": [
            {
                "text": "Repeat the tested Reel structure as a hypothesis",
                "evidence": ["transcript-evidence"],
            }
        ],
        "tests": ["Test the same topic in a carousel and a Reel"],
        "cannot_conclude": ["The observed association does not establish causation"],
        "failures": [
            {
                "piece_id": "post-9",
                "stage": "ocr",
                "message": '<script>alert("failure")</script>',
            }
        ],
        "limitations": [
            "Public view counts are snapshots and do not control for audience growth",
            "Transcript coverage is incomplete",
        ],
        "section_summaries": {
            "reel_rhythm": {
                "summary": "Rhythm comparisons use only word-timed Reels.",
                "details": {"word_timed_reels": 18},
            }
        },
        "generated_at": "2026-07-13T12:00:00+00:00",
    }
    values.update(overrides)
    return build_report(**values)


def test_all_renderers_use_same_finding_ids_and_core_values():
    report = sample_report()

    rendered_json = render_json(report)
    rendered_markdown = render_markdown(report)
    rendered_html = render_html(report)
    decoded = json.loads(rendered_json)

    assert decoded["findings"][0]["id"] == "finding-format-1"
    assert decoded["findings"][0]["effect"] == {"unit": "median_ratio", "value": 1.7}
    for rendered in (rendered_markdown, rendered_html):
        assert "finding-format-1" in rendered
        assert "eligible Reels vs other eligible posts" in rendered
        assert "1.7" in rendered
        assert "median_ratio" in rendered
        assert "feature n=19" in rendered
        assert "control n=24" in rendered
        assert "medium" in rendered


def test_html_escapes_untrusted_text_and_rejects_non_http_links():
    rendered = render_html(sample_report())

    assert "<script>alert" not in rendered
    assert "<img src=x" not in rendered
    assert '&lt;script&gt;alert(&quot;' in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert 'href="javascript:' not in rendered.casefold()
    assert "javascript:alert(document.cookie)" not in rendered
    assert 'href="https://www.instagram.com/reel/safe/"' in rendered


def test_evidence_posts_timestamps_and_slides_have_navigable_anchors():
    report = sample_report()
    rendered_html = render_html(report)
    rendered_markdown = render_markdown(report)

    transcript_nav = report["evidence"][0]["navigation"]
    slide_nav = report["evidence"][1]["navigation"]
    for anchor in (
        transcript_nav["post_anchor"],
        transcript_nav["evidence_anchor"],
        transcript_nav["timestamp_anchor"],
        slide_nav["post_anchor"],
        slide_nav["evidence_anchor"],
        slide_nav["slide_anchor"],
    ):
        assert f'id="{anchor}"' in rendered_html
        assert anchor in rendered_markdown

    assert f'href="#{transcript_nav["post_anchor"]}"' in rendered_html
    assert f'href="#{slide_nav["evidence_anchor"]}"' in rendered_html
    assert "00:12.345–00:16.000" in rendered_markdown
    assert "Slide:** 3" in rendered_markdown


def test_coverage_and_limitations_precede_conclusions():
    report = sample_report()
    for rendered in (render_markdown(report), render_html(report)):
        assert rendered.index("Data and metric coverage") < rendered.index(
            "Top posts for the selected objective"
        )
        assert rendered.index("Limitations") < rendered.index("finding-format-1")
        assert "What cannot be concluded" in rendered
        assert "Transcript coverage is incomplete" in rendered


def test_missing_evidence_reference_is_rejected():
    with pytest.raises(ReportValidationError, match="missing evidence or piece"):
        sample_report(evidence=[])


def test_finding_without_evidence_is_rejected():
    finding = sample_report()["findings"][0]
    finding["evidence"] = []

    with pytest.raises(ReportValidationError, match="must contain at least one"):
        sample_report(findings=[finding])


def test_missing_limitations_are_rejected():
    with pytest.raises(ReportValidationError, match="limitations must contain"):
        sample_report(limitations=[])


def test_causal_guarantee_language_is_rejected():
    finding = sample_report()["findings"][0]
    finding["claim"] = "Long Reels cause this creator to go viral"

    with pytest.raises(ReportValidationError, match="causal or guaranteed wording"):
        sample_report(findings=[finding])


def test_report_bundle_writes_all_three_outputs(tmp_path):
    report = sample_report()

    paths = write_report_bundle(report, tmp_path / "profile-report")

    assert set(paths) == {"json", "markdown", "html"}
    assert paths["json"].name == "findings.json"
    assert paths["markdown"].name == "report.md"
    assert paths["html"].name == "report.html"
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["schema_version"] == "1.0"
    assert "finding-format-1" in paths["markdown"].read_text(encoding="utf-8")
    assert "finding-format-1" in paths["html"].read_text(encoding="utf-8")
