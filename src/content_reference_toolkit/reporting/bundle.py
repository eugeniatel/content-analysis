"""Filesystem output for canonical report bundles."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .html import render_html
from .json_renderer import render_json
from .markdown import render_markdown
from .schema import validate_report


def write_report_bundle(
    report: Mapping[str, Any], output_dir: str | Path
) -> dict[str, Path]:
    """Write ``findings.json``, ``report.md``, and ``report.html`` together."""

    validate_report(report)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": destination / "findings.json",
        "markdown": destination / "report.md",
        "html": destination / "report.html",
    }
    paths["json"].write_text(render_json(report), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(report), encoding="utf-8")
    paths["html"].write_text(render_html(report), encoding="utf-8")
    return paths

