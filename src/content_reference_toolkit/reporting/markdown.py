"""Markdown renderer for canonical reports."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from ._render import display_value, evidence_target, format_seconds, markdown_url, percentage
from .schema import validate_report


def _text(value: Any) -> str:
    # Escaping HTML is important because many Markdown renderers allow raw markup.
    return escape(display_value(value), quote=False).replace("\\", "\\\\")


def _heading_text(value: Any) -> str:
    return re.sub(r"([#*_`\[\]])", r"\\\1", _text(value))


def _mapping_lines(mapping: Mapping[str, Any]) -> list[str]:
    return [f"- **{_heading_text(key).replace('_', ' ').title()}:** {_text(value)}" for key, value in mapping.items()]


def _references(report: Mapping[str, Any], references: Sequence[str]) -> str:
    links: list[str] = []
    for reference in references:
        anchor = evidence_target(report, reference)
        label = _heading_text(reference)
        links.append(f"[{label}](#{anchor})" if anchor else label)
    return ", ".join(links) if links else "None"


def _finding(report: Mapping[str, Any], finding: Mapping[str, Any]) -> list[str]:
    effect = finding["effect"]
    sample = finding["sample"]
    lines = [
        f"### {_heading_text(finding['id'])}: {_heading_text(finding['claim'])}",
        "",
        f"- **Status:** {_text(finding['status'])}",
        f"- **Objective:** {_text(finding['objective'])}",
        f"- **Comparison:** {_text(finding['comparison'])}",
        f"- **Effect:** {_text(effect.get('value'))} {_text(effect.get('unit'))}",
        f"- **Winner prevalence:** {percentage(finding.get('winner_prevalence'))}",
        f"- **Baseline prevalence:** {percentage(finding.get('baseline_prevalence'))}",
        f"- **Sample:** feature n={_text(sample.get('feature'))}; control n={_text(sample.get('control'))}",
        f"- **Confidence:** {_text(finding['confidence'])}",
    ]
    if finding.get("interval") is not None:
        lines.append(f"- **Interval:** {_text(finding['interval'])}")
    lines.extend(
        [
            f"- **Evidence:** {_references(report, finding['evidence'])}",
            f"- **Caveat:** {_text(finding['caveat'])}",
            "",
        ]
    )
    return lines


def _winner(report: Mapping[str, Any], winner: Mapping[str, Any], rank: int) -> list[str]:
    piece_id = str(winner.get("piece_id") or winner.get("id") or f"winner-{rank}")
    post_anchor = None
    for item in report["evidence"]:
        if item["piece_id"] == piece_id:
            post_anchor = item["navigation"]["post_anchor"]
            break
    title = winner.get("title") or piece_id
    prefix = f"{rank}. **{_heading_text(title)}**"
    if post_anchor:
        prefix += f" ([evidence](#{post_anchor}))"
    url = markdown_url(winner.get("url") or winner.get("canonical_url"))
    if url:
        prefix += f" ([source]({url}))"
    fields = []
    for key in ("format", "media_type", "objective", "objective_value", "objective_unit", "relative_score"):
        if key in winner and winner[key] is not None:
            fields.append(f"{key.replace('_', ' ')}: {_text(winner[key])}")
    if fields:
        prefix += " — " + "; ".join(fields)
    return [prefix]


def _action_items(report: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["- None supported by the current evidence."]
    lines: list[str] = []
    for item in items:
        line = f"- **{_heading_text(item['id'])}:** {_text(item['text'])}"
        if item.get("evidence"):
            line += f" Evidence: {_references(report, item['evidence'])}."
        lines.append(line)
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render an audit-friendly Markdown report from the canonical object."""

    validate_report(report)
    profile_name = report["profile"].get("display_name") or report["profile"].get("handle") or report["profile"].get("id") or "Profile"
    findings = {item["id"]: item for item in report["findings"]}
    lines = [
        f"# Content performance report: {_heading_text(profile_name)}",
        "",
        f"Schema `{_text(report['schema_version'])}` · generated {_text(report['generated_at'])}",
        "",
        "## Profile and run configuration",
        "",
        "### Profile",
        "",
        *_mapping_lines(report["profile"]),
        "",
        "### Run",
        "",
        *_mapping_lines(report["run"]),
        "",
        "## Data and metric coverage",
        "",
        "> Coverage and missing evidence are shown before any conclusions.",
        "",
        *_mapping_lines(report["coverage"]),
        "",
        "### Limitations",
        "",
        *[f"- **{_heading_text(item['id'])}:** {_text(item['text'])}" for item in report["limitations"]],
        "",
        "## Top posts for the selected objective",
        "",
    ]
    if report["winners"]:
        for rank, winner in enumerate(report["winners"], 1):
            lines.extend(_winner(report, winner, rank))
    else:
        lines.append("No eligible winner posts were available.")
    lines.append("")

    for section_key, section in report["sections"].items():
        lines.extend([f"## {_heading_text(section['title'])}", ""])
        if section.get("summary"):
            lines.extend([_text(section["summary"]), ""])
        if section.get("details"):
            lines.extend(_mapping_lines(section["details"]))
            lines.append("")
        if section["finding_ids"]:
            for finding_id in section["finding_ids"]:
                lines.extend(_finding(report, findings[finding_id]))
        else:
            lines.extend(["No supported comparative finding for this section.", ""])

    recommendations = report["recommendations"]
    lines.extend(["## Repeatable patterns and suggested tests", "", "### What to repeat", ""])
    lines.extend(_action_items(report, recommendations["repeat"]))
    lines.extend(["", "### What to test", ""])
    lines.extend(_action_items(report, recommendations["test"]))
    lines.extend(["", "### What cannot be concluded", ""])
    lines.extend(_action_items(report, recommendations["cannot_conclude"]))

    lines.extend(["", "## Extraction failures", ""])
    if report["failures"]:
        for failure in report["failures"]:
            if isinstance(failure, Mapping):
                fields = "; ".join(
                    f"{_text(key)}={_text(value)}" for key, value in failure.items()
                )
                lines.append(f"- {fields}")
            else:
                lines.append(f"- {_text(failure)}")
    else:
        lines.append("No extraction failures were recorded.")

    lines.extend(["", "## Evidence appendix", "", "### Posts", ""])
    seen_pieces: set[str] = set()
    for item in report["evidence"]:
        if item["piece_id"] in seen_pieces:
            continue
        seen_pieces.add(item["piece_id"])
        url = markdown_url(item.get("url") or item.get("canonical_url"))
        line = f"- <a id=\"{item['navigation']['post_anchor']}\"></a>**Post {_heading_text(item['piece_id'])}**"
        if url:
            line += f" ([source]({url}))"
        lines.append(line)
    if not seen_pieces:
        lines.append("No post evidence was available.")

    lines.extend(["", "### Evidence items", ""])
    for item in report["evidence"]:
        nav = item["navigation"]
        anchor_tags = [f"<a id=\"{nav['evidence_anchor']}\"></a>"]
        if "timestamp_anchor" in nav:
            anchor_tags.append(f"<a id=\"{nav['timestamp_anchor']}\"></a>")
        if "slide_anchor" in nav:
            anchor_tags.append(f"<a id=\"{nav['slide_anchor']}\"></a>")
        lines.extend(
            [
                "".join(anchor_tags)
                + f"#### Evidence {_heading_text(item['id'])} · post {_heading_text(item['piece_id'])}",
                "",
                f"- **Kind:** {_text(item.get('kind'))}",
            ]
        )
        if item.get("start_sec") is not None:
            end = (
                f"–{format_seconds(item['end_sec'])}"
                if item.get("end_sec") is not None
                else ""
            )
            lines.append(f"- **Timestamp:** {format_seconds(item['start_sec'])}{end} (`{nav['timestamp_anchor']}`)")
        if item.get("slide_index") is not None:
            lines.append(f"- **Slide:** {_text(item['slide_index'])} (`{nav['slide_anchor']}`)")
        if item.get("excerpt") is not None:
            excerpt_lines = _text(item["excerpt"]).splitlines() or [""]
            lines.extend(["", *[f"> {line}" for line in excerpt_lines]])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

