from __future__ import annotations

from pathlib import Path

from content_reference_toolkit import cli


def test_slug_for_url_is_stable_and_readable():
    slug = cli.slug_for_source("https://www.instagram.com/mia_astral/reel/DZ8HloGBd7Q/")

    assert slug.startswith("instagram-com-mia-astral-reel-dz8hlogbd7q-")
    assert len(slug.rsplit("-", 1)[-1]) == 8


def test_slug_for_local_file_is_stable_and_readable():
    slug = cli.slug_for_source("/tmp/My Carousel Slide 01.png")

    assert slug.startswith("my-carousel-slide-01-")


def test_detect_format_uses_url_and_media():
    assert cli.detect_format("https://www.instagram.com/x/reel/abc/", None, []) == "reel"
    assert cli.detect_format("https://www.instagram.com/x/p/abc/", None, []) == "carousel_or_post"
    assert cli.detect_format("https://www.tiktok.com/@x/video/1", None, []) == "tiktok"
    assert cli.detect_format("/tmp/a.mp4", None, [Path("/tmp/a.mp4")]) == "video"
    assert cli.detect_format("/tmp/a.png", None, [Path("/tmp/a.png")]) == "image_or_carousel"
