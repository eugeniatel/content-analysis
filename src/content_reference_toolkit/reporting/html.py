"""Safely escaped HTML renderer for canonical reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from ._render import display_value, evidence_target, format_seconds, percentage
from .schema import safe_http_url, validate_report


def _text(value: Any) -> str:
    return escape(display_value(value), quote=True)


def _fields(mapping: Mapping[str, Any]) -> str:
    return "<dl>" + "".join(
        f"<dt>{_text(str(key).replace('_', ' ').title())}</dt><dd>{_text(value)}</dd>"
        for key, value in mapping.items()
    ) + "</dl>"


def _link(url_value: Any, label: str) -> str:
    url = safe_http_url(url_value)
    if url is None:
        return ""
    return f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer">{_text(label)}</a>'


def _references(report: Mapping[str, Any], references: Sequence[str]) -> str:
    links: list[str] = []
    for reference in references:
        target = evidence_target(report, reference)
        if target:
            links.append(f'<a href="#{escape(target, quote=True)}">{_text(reference)}</a>')
        else:
            links.append(_text(reference))
    return ", ".join(links) if links else "None"


def _finding(report: Mapping[str, Any], finding: Mapping[str, Any]) -> str:
    effect = finding["effect"]
    sample = finding["sample"]
    interval = ""
    if finding.get("interval") is not None:
        interval = f"<dt>Interval</dt><dd>{_text(finding['interval'])}</dd>"
    return (
        f'<article class="finding {escape(finding["status"], quote=True)}" '
        f'id="finding-{escape(str(finding["id"]), quote=True)}">'
        f"<h3>{_text(finding['id'])}: {_text(finding['claim'])}</h3>"
        "<dl>"
        f"<dt>Status</dt><dd>{_text(finding['status'])}</dd>"
        f"<dt>Objective</dt><dd>{_text(finding['objective'])}</dd>"
        f"<dt>Comparison</dt><dd>{_text(finding['comparison'])}</dd>"
        f"<dt>Effect</dt><dd>{_text(effect.get('value'))} {_text(effect.get('unit'))}</dd>"
        f"<dt>Winner prevalence</dt><dd>{percentage(finding.get('winner_prevalence'))}</dd>"
        f"<dt>Baseline prevalence</dt><dd>{percentage(finding.get('baseline_prevalence'))}</dd>"
        f"<dt>Sample</dt><dd>feature n={_text(sample.get('feature'))}; control n={_text(sample.get('control'))}</dd>"
        f"<dt>Confidence</dt><dd>{_text(finding['confidence'])}</dd>"
        f"{interval}"
        f"<dt>Evidence</dt><dd>{_references(report, finding['evidence'])}</dd>"
        f"<dt>Caveat</dt><dd>{_text(finding['caveat'])}</dd>"
        "</dl></article>"
    )


def _actions(report: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return "<p>No pattern is supported by the current evidence.</p>"
    rendered = []
    for item in items:
        evidence = ""
        if item.get("evidence"):
            evidence = f" <span>Evidence: {_references(report, item['evidence'])}.</span>"
        rendered.append(
            f'<li id="action-{escape(str(item["id"]), quote=True)}"><strong>{_text(item["id"])}:</strong> '
            f"{_text(item['text'])}{evidence}</li>"
        )
    return "<ul>" + "".join(rendered) + "</ul>"


def render_html(report: Mapping[str, Any]) -> str:
    """Render a standalone HTML report with no active untrusted markup or URLs."""

    validate_report(report)
    profile_name = report["profile"].get("display_name") or report["profile"].get("handle") or report["profile"].get("id") or "Profile"
    findings = {item["id"]: item for item in report["findings"]}
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>Content performance report: {_text(profile_name)}</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:70rem;margin:0 auto;padding:2rem;line-height:1.5;color:#17202a}nav ul{display:flex;flex-wrap:wrap;gap:.5rem 1.2rem;padding-left:1rem}section{border-top:1px solid #d9dee3;padding-top:1rem;margin-top:2rem}dl{display:grid;grid-template-columns:minmax(10rem,1fr) 3fr;gap:.35rem 1rem}dt{font-weight:650}dd{margin:0}.finding{background:#f6f8fa;border-left:4px solid #667085;padding:.6rem 1rem;margin:1rem 0}.finding.insufficient_evidence{border-color:#b7791f}blockquote{white-space:pre-wrap;background:#f6f8fa;padding:.75rem}.muted{color:#667085}.anchor-target{scroll-margin-top:1rem}code{overflow-wrap:anywhere}@media(max-width:640px){dl{display:block}dd{margin:0 0 .65rem}}</style>",
        "</head><body>",
        f"<header><h1>Content performance report: {_text(profile_name)}</h1><p class=\"muted\">Schema {_text(report['schema_version'])} · generated {_text(report['generated_at'])}</p></header>",
        "<nav aria-label=\"Report sections\"><ul>",
        '<li><a href="#profile-run">Profile and run</a></li>',
        '<li><a href="#coverage">Coverage</a></li>',
        '<li><a href="#winners">Top posts</a></li>',
    ]
    for key, section in report["sections"].items():
        parts.append(f'<li><a href="#section-{escape(key, quote=True)}">{_text(section["title"])}</a></li>')
    parts.extend(
        [
            '<li><a href="#recommendations">Repeat and test</a></li>',
            '<li><a href="#failures">Failures</a></li>',
            '<li><a href="#evidence-appendix">Evidence</a></li>',
            "</ul></nav>",
            '<main><section id="profile-run"><h2>Profile and run configuration</h2><h3>Profile</h3>',
            _fields(report["profile"]),
            "<h3>Run</h3>",
            _fields(report["run"]),
            "</section>",
            '<section id="coverage"><h2>Data and metric coverage</h2><p><strong>Coverage and missing evidence are shown before any conclusions.</strong></p>',
            _fields(report["coverage"]),
            "<h3>Limitations</h3><ul>",
        ]
    )
    parts.extend(
        f'<li id="{escape(item["id"], quote=True)}"><strong>{_text(item["id"])}:</strong> {_text(item["text"])}</li>'
        for item in report["limitations"]
    )
    parts.extend(["</ul></section>", '<section id="winners"><h2>Top posts for the selected objective</h2>'])
    if report["winners"]:
        parts.append("<ol>")
        for rank, winner in enumerate(report["winners"], 1):
            piece_id = str(winner.get("piece_id") or winner.get("id") or f"winner-{rank}")
            post_anchor = next(
                (
                    item["navigation"]["post_anchor"]
                    for item in report["evidence"]
                    if item["piece_id"] == piece_id
                ),
                None,
            )
            title = winner.get("title") or piece_id
            fields = []
            for key in ("format", "media_type", "objective", "objective_value", "objective_unit", "relative_score"):
                if key in winner and winner[key] is not None:
                    fields.append(f"{_text(key.replace('_', ' '))}: {_text(winner[key])}")
            links = []
            if post_anchor:
                links.append(f'<a href="#{escape(post_anchor, quote=True)}">evidence</a>')
            source_link = _link(winner.get("url") or winner.get("canonical_url"), "source")
            if source_link:
                links.append(source_link)
            details = (" — " + "; ".join(fields)) if fields else ""
            link_text = (" (" + ", ".join(links) + ")") if links else ""
            parts.append(f"<li><strong>{_text(title)}</strong>{link_text}{details}</li>")
        parts.append("</ol>")
    else:
        parts.append("<p>No eligible winner posts were available.</p>")
    parts.append("</section>")

    for key, section in report["sections"].items():
        parts.append(f'<section id="section-{escape(key, quote=True)}"><h2>{_text(section["title"])}</h2>')
        if section.get("summary"):
            parts.append(f"<p>{_text(section['summary'])}</p>")
        if section.get("details"):
            parts.append(_fields(section["details"]))
        if section["finding_ids"]:
            parts.extend(_finding(report, findings[finding_id]) for finding_id in section["finding_ids"])
        else:
            parts.append("<p>No supported comparative finding for this section.</p>")
        parts.append("</section>")

    recommendations = report["recommendations"]
    parts.extend(
        [
            '<section id="recommendations"><h2>Repeatable patterns and suggested tests</h2>',
            "<h3>What to repeat</h3>",
            _actions(report, recommendations["repeat"]),
            "<h3>What to test</h3>",
            _actions(report, recommendations["test"]),
            "<h3>What cannot be concluded</h3>",
            _actions(report, recommendations["cannot_conclude"]),
            "</section>",
            '<section id="failures"><h2>Extraction failures</h2>',
        ]
    )
    if report["failures"]:
        parts.append("<ul>")
        for failure in report["failures"]:
            if isinstance(failure, Mapping):
                value = "; ".join(f"{key}={display_value(item)}" for key, item in failure.items())
            else:
                value = display_value(failure)
            parts.append(f"<li>{_text(value)}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>No extraction failures were recorded.</p>")
    parts.extend(["</section>", '<section id="evidence-appendix"><h2>Evidence appendix</h2><h3>Posts</h3>'])
    seen_pieces: set[str] = set()
    for item in report["evidence"]:
        piece_id = item["piece_id"]
        if piece_id in seen_pieces:
            continue
        seen_pieces.add(piece_id)
        source_link = _link(item.get("url") or item.get("canonical_url"), "source")
        source_suffix = f" ({source_link})" if source_link else ""
        parts.append(
            f'<article class="anchor-target" id="{escape(item["navigation"]["post_anchor"], quote=True)}">'
            f"<h4>Post {_text(piece_id)}</h4><p>{source_suffix}</p></article>"
        )
    if not seen_pieces:
        parts.append("<p>No post evidence was available.</p>")
    parts.append("<h3>Evidence items</h3>")
    for item in report["evidence"]:
        nav = item["navigation"]
        for optional_anchor in ("timestamp_anchor", "slide_anchor"):
            if optional_anchor in nav:
                parts.append(f'<span class="anchor-target" id="{escape(nav[optional_anchor], quote=True)}"></span>')
        parts.append(
            f'<article class="anchor-target" id="{escape(nav["evidence_anchor"], quote=True)}">'
            f"<h4>Evidence {_text(item['id'])} · post {_text(item['piece_id'])}</h4>"
            f"<p><strong>Kind:</strong> {_text(item.get('kind'))}</p>"
        )
        if item.get("start_sec") is not None:
            end = (
                f"–{format_seconds(item['end_sec'])}"
                if item.get("end_sec") is not None
                else ""
            )
            parts.append(
                f"<p><strong>Timestamp:</strong> {format_seconds(item['start_sec'])}{end} "
                f"<code>{_text(nav['timestamp_anchor'])}</code></p>"
            )
        if item.get("slide_index") is not None:
            parts.append(
                f"<p><strong>Slide:</strong> {_text(item['slide_index'])} "
                f"<code>{_text(nav['slide_anchor'])}</code></p>"
            )
        if item.get("excerpt") is not None:
            parts.append(f"<blockquote>{_text(item['excerpt'])}</blockquote>")
        parts.append("</article>")
    parts.extend(["</section></main>", "</body></html>\n"])
    return "".join(parts)

