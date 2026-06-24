from __future__ import annotations

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
    assert cli.infer_format("/tmp/a.mp4", None, [Path("/tmp/a.mp4")]) == "reel"
    assert cli.infer_format("/tmp/a.png", None, [Path("/tmp/a.png")]) == "carousel"


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
