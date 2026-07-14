"""JSON renderer for canonical reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .schema import validate_report


def render_json(report: Mapping[str, Any]) -> str:
    """Serialize the canonical report without lossy presentation transforms."""

    validate_report(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

