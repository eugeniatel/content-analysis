"""Shared renderer helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from .schema import safe_http_url


def display_value(value: Any) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "Unavailable"
        return f"{value:.4g}"
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def percentage(value: Any) -> str:
    if value is None:
        return "Unavailable"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return display_value(value)
    if not math.isfinite(numeric):
        return "Unavailable"
    return f"{numeric * 100:.1f}%"


def format_seconds(value: Any) -> str:
    try:
        seconds = max(0.0, float(value))
    except (TypeError, ValueError):
        return display_value(value)
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:06.3f}"
    return f"{minutes:02d}:{remainder:06.3f}"


def markdown_url(value: Any) -> str | None:
    url = safe_http_url(value)
    if url is None:
        return None
    return quote(url, safe=":/?#[]@!$&'*+,;=%~._-")


def evidence_target(report: Mapping[str, Any], reference: str) -> str | None:
    for item in report["evidence"]:
        if item["id"] == reference:
            return item["navigation"]["evidence_anchor"]
    for item in report["evidence"]:
        if item["piece_id"] == reference:
            return item["navigation"]["post_anchor"]
    return None

