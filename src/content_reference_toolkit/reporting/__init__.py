"""Canonical profile-intelligence reports and renderers.

The public API deliberately keeps report construction separate from presentation:
``build_report`` creates one validated, JSON-serializable object, while every
renderer consumes that same object.
"""

from .bundle import write_report_bundle
from .html import render_html
from .json_renderer import render_json
from .markdown import render_markdown
from .schema import (
    REPORT_SCHEMA_VERSION,
    ReportValidationError,
    build_report,
    validate_report,
)

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "ReportValidationError",
    "build_report",
    "render_html",
    "render_json",
    "render_markdown",
    "validate_report",
    "write_report_bundle",
]

