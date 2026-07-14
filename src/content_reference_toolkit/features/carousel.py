"""Deterministic carousel anatomy from ordered OCR blocks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median, pvariance
import re
from typing import Any

from .common import analyze_text, token_overlap


FEATURE_VERSION = "carousel-v1"
NUMBERING_RE = re.compile(
    r"^\s*(?:\d+[.):]|[A-Za-z][.)]|(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)[.)])\s+",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*[-*•●▪◦‣⁃]\s+")


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a numeric coordinate, got {value!r}") from exc
    return number


def _dimensions(slide: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> tuple[float | None, float | None]:
    width = _float(slide.get("image_width") or slide.get("width_px"))
    height = _float(slide.get("image_height") or slide.get("height_px"))
    if width is None:
        width = next(
            (_float(block.get("image_width")) for block in blocks if block.get("image_width") is not None),
            None,
        )
    if height is None:
        height = next(
            (_float(block.get("image_height")) for block in blocks if block.get("image_height") is not None),
            None,
        )
    return width, height


def _box(block: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    """Normalize x/y/width/height, [x,y,w,h], or OCR polygon coordinates."""

    x = _float(block.get("x"))
    y = _float(block.get("y"))
    width = _float(block.get("width"))
    height = _float(block.get("height"))
    raw = block.get("bbox") or block.get("box")
    if (x is None or y is None or width is None or height is None) and raw is not None:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("bbox must be [x, y, width, height] or a point polygon")
        if len(raw) == 4 and all(not isinstance(value, Sequence) for value in raw):
            x, y, width, height = (_float(value) for value in raw)
        elif raw and all(isinstance(point, Sequence) and len(point) >= 2 for point in raw):
            maybe_xs = [_float(point[0]) for point in raw]
            maybe_ys = [_float(point[1]) for point in raw]
            if any(value is None for value in maybe_xs + maybe_ys):
                return None
            xs = [float(value) for value in maybe_xs if value is not None]
            ys = [float(value) for value in maybe_ys if value is not None]
            x = min(xs)
            y = min(ys)
            width = max(xs) - x
            height = max(ys) - y
        else:
            raise ValueError("bbox must be [x, y, width, height] or a point polygon")
    if None in (x, y, width, height):
        return None
    assert x is not None and y is not None and width is not None and height is not None
    if width < 0 or height < 0:
        raise ValueError("OCR block width and height cannot be negative")
    return x, y, width, height


def _rectangle_union_area(rectangles: Sequence[tuple[float, float, float, float]]) -> float:
    """Exact union area for axis-aligned OCR rectangles."""

    edges = sorted({x for x, _, width, _ in rectangles for x in (x, x + width)})
    area = 0.0
    for left, right in zip(edges, edges[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (y, y + height)
            for x, y, width, height in rectangles
            if width > 0 and height > 0 and x < right and x + width > left
        )
        if not intervals:
            continue
        covered = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered += end - start
        area += (right - left) * covered
    return area


def _normalize_block(block: Mapping[str, Any] | str, index: int) -> dict[str, Any]:
    if isinstance(block, str):
        source: dict[str, Any] = {"text": block}
    elif isinstance(block, Mapping):
        source = dict(block)
    else:
        raise TypeError("OCR blocks must be strings or mappings")
    text = source.get("text")
    if text is not None and not isinstance(text, str):
        raise TypeError("OCR block text must be a string or None")
    rectangle = _box(source)
    normalized = {
        "block_index": source.get("block_index", source.get("order", index)),
        "source_order": index,
        "available": source.get("available", True) is not False and text is not None,
        "text": text,
        "confidence": _float(source.get("confidence")),
        "x": rectangle[0] if rectangle else None,
        "y": rectangle[1] if rectangle else None,
        "width": rectangle[2] if rectangle else None,
        "height": rectangle[3] if rectangle else None,
        "image_width": _float(source.get("image_width")),
        "image_height": _float(source.get("image_height")),
        "source_path": source.get("source_path"),
        "extractor_version": source.get("extractor_version"),
    }
    normalized["features"] = analyze_text(text, available=normalized["available"])
    return normalized


def _slide_role_hint(position: int, total: int) -> str:
    if total == 1:
        return "cover_and_final_position"
    if position == 0:
        return "cover_position"
    if position == total - 1:
        return "final_position"
    return "body_position"


def _heading_count(blocks: Sequence[Mapping[str, Any]], image_height: float | None) -> int:
    count = 0
    for block in blocks:
        if not block["available"]:
            continue
        text = str(block["text"]).strip()
        features = block["features"]
        short = 0 < int(features["word_count"]) <= 12
        in_upper_region = (
            image_height is not None
            and block.get("y") is not None
            and float(block["y"]) <= image_height * 0.4
        )
        letters = "".join(char for char in text if char.isalpha())
        visual_heading = bool(letters) and (letters.isupper() or text.endswith(":") or in_upper_region)
        if short and visual_heading:
            count += 1
    return count


def _normalize_slide(slide: Any, position: int, total: int) -> dict[str, Any]:
    if slide is None:
        source: dict[str, Any] = {"available": False, "blocks": []}
    elif isinstance(slide, str):
        source = {"blocks": [{"text": slide}]}
    elif isinstance(slide, Sequence) and not isinstance(slide, (str, bytes, bytearray, Mapping)):
        source = {"blocks": slide}
    elif isinstance(slide, Mapping):
        source = dict(slide)
        if "blocks" not in source and "text" in source:
            source["blocks"] = [{"text": source.get("text")}]
    else:
        raise TypeError("slides must be mappings, block sequences, strings, or None")

    slide_available = source.get("available", True) is not False
    raw_blocks = source.get("blocks", [])
    ocr_available = slide_available and source.get("ocr_available", True) is not False
    if raw_blocks is None:
        ocr_available = False
        raw_blocks = []
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
        raise TypeError("slide blocks must be a sequence")
    blocks = [_normalize_block(block, index) for index, block in enumerate(raw_blocks)]
    width, height = _dimensions(source, blocks)
    for block in blocks:
        if block["image_width"] is None:
            block["image_width"] = width
        if block["image_height"] is None:
            block["image_height"] = height

    measured_blocks = [block for block in blocks if block["available"]]
    texts = [str(block["text"]) for block in measured_blocks]
    text = "\n".join(texts)
    text_available = ocr_available and (bool(measured_blocks) or not blocks)
    text_features = analyze_text(text) if text_available else analyze_text(None)
    if slide_available and not text_available:
        text_features["reason"] = "ocr_text_unavailable"
    rectangles = [
        (float(block["x"]), float(block["y"]), float(block["width"]), float(block["height"]))
        for block in measured_blocks
        if None not in (block["x"], block["y"], block["width"], block["height"])
    ]
    if width and height and rectangles:
        clipped = [
            (
                max(0.0, x),
                max(0.0, y),
                max(0.0, min(x + box_width, width) - max(0.0, x)),
                max(0.0, min(y + box_height, height) - max(0.0, y)),
            )
            for x, y, box_width, box_height in rectangles
        ]
        area_ratio = min(1.0, _rectangle_union_area(clipped) / (width * height))
    elif width and height and text_available and not measured_blocks:
        area_ratio = 0.0
    else:
        area_ratio = None

    bullet_count = sum(bool(BULLET_RE.match(text)) for text in texts)
    numbering_count = sum(bool(NUMBERING_RE.match(text)) for text in texts)
    return {
        "slide_index": source.get("slide_index", position + 1),
        "position": position,
        "role_hint": _slide_role_hint(position, total),
        "available": slide_available,
        "reason": None if slide_available else str(source.get("reason") or "slide_unavailable"),
        "ocr_available": text_available,
        "ocr_reason": None if text_available else str(source.get("ocr_reason") or "ocr_text_unavailable"),
        "source_path": source.get("source_path"),
        "image_width": width,
        "image_height": height,
        "block_count": len(measured_blocks) if text_available else None,
        "raw_block_count": len(blocks),
        "box_coverage": round(len(rectangles) / len(measured_blocks), 6) if measured_blocks else None,
        "block_area_ratio": round(area_ratio, 6) if area_ratio is not None else None,
        "text_area_ratio": round(area_ratio, 6) if area_ratio is not None else None,
        "word_density_per_megapixel": round(
            text_features["word_count"] / ((width * height) / 1_000_000), 6
        ) if slide_available and width and height else None,
        "word_count": text_features["word_count"],
        "character_count": text_features["character_count"],
        "bullet_count": bullet_count if text_available else None,
        "numbering_count": numbering_count if text_available else None,
        "question_count": text_features.get("question_count"),
        "heading_count": _heading_count(blocks, height) if slide_available else None,
        "text_features": text_features,
        # Blocks remain the canonical evidence. The joined text is only a derived view.
        "blocks": blocks,
    }


def render_slide_evidence(slide: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact report-ready view without dropping block provenance."""

    return {
        "slide_index": slide.get("slide_index"),
        "available": slide.get("available"),
        "reason": slide.get("reason"),
        "ocr_available": slide.get("ocr_available"),
        "ocr_reason": slide.get("ocr_reason"),
        "source_path": slide.get("source_path"),
        "blocks": [
            {
                key: block.get(key)
                for key in (
                    "block_index", "text", "confidence", "x", "y", "width", "height",
                    "image_width", "image_height", "source_path", "extractor_version",
                )
            }
            for block in slide.get("blocks", [])
        ],
    }


def analyze_carousel(
    slides: Sequence[Any] | None,
    *,
    caption: str | None = None,
) -> dict[str, Any]:
    """Analyze an ordered carousel without inventing values for missing slides."""

    if slides is None:
        return {
            "feature_version": FEATURE_VERSION,
            "available": False,
            "reason": "slides_not_extracted",
            "slide_count": None,
            "block_count": None,
            "slides": [],
            "evidence": [],
            "caption_slide_overlap": token_overlap(caption, None),
        }
    if isinstance(slides, (str, bytes, bytearray)):
        raise TypeError("slides must be an ordered sequence, not a string")

    normalized = [_normalize_slide(slide, index, len(slides)) for index, slide in enumerate(slides)]
    available_slides = [slide for slide in normalized if slide["available"]]
    measured_slides = [slide for slide in normalized if slide["ocr_available"]]
    word_counts = [int(slide["word_count"]) for slide in measured_slides]
    block_count = sum(int(slide["block_count"]) for slide in measured_slides) if measured_slides else None
    all_slide_text = "\n".join(
        str(block["text"])
        for slide in measured_slides
        for block in slide["blocks"]
        if block["available"]
    )
    cover = normalized[0] if normalized else None
    final = normalized[-1] if normalized else None
    return {
        "feature_version": FEATURE_VERSION,
        "available": True,
        "reason": None,
        "slide_count": len(normalized),
        "available_slide_count": len(available_slides),
        "missing_slide_count": len(normalized) - len(available_slides),
        "missing_slide_indices": [slide["slide_index"] for slide in normalized if not slide["available"]],
        "ocr_available_slide_count": len(measured_slides),
        "ocr_unavailable_slide_indices": [
            slide["slide_index"] for slide in normalized if not slide["ocr_available"]
        ],
        "block_count": block_count,
        "total_word_count": sum(word_counts),
        "cover_word_count": cover["word_count"] if cover else None,
        "final_slide_word_count": final["word_count"] if final else None,
        "words_per_slide": {
            "values": [
                {"slide_index": slide["slide_index"], "word_count": slide["word_count"]}
                for slide in normalized
            ],
            "minimum": min(word_counts) if word_counts else None,
            "median": median(word_counts) if word_counts else None,
            "maximum": max(word_counts) if word_counts else None,
            "variance": round(pvariance(word_counts), 6) if word_counts else None,
        },
        "median_density": median(word_counts) if word_counts else None,
        "maximum_density": max(word_counts) if word_counts else None,
        "bullet_count": sum(int(slide["bullet_count"]) for slide in measured_slides)
        if measured_slides else None,
        "numbering_count": sum(int(slide["numbering_count"]) for slide in measured_slides)
        if measured_slides else None,
        "question_count": sum(int(slide["question_count"]) for slide in measured_slides)
        if measured_slides else None,
        "heading_count": sum(int(slide["heading_count"]) for slide in measured_slides)
        if measured_slides else None,
        "cover": {
            "slide_index": cover["slide_index"],
            "available": cover["available"],
            "word_count": cover["word_count"],
            "question_count": cover["question_count"],
            "imperative_count": cover["text_features"].get("imperatives", {}).get("count"),
        } if cover else None,
        "final_slide": {
            "slide_index": final["slide_index"],
            "available": final["available"],
            "word_count": final["word_count"],
            "question_count": final["question_count"],
            "imperative_count": final["text_features"].get("imperatives", {}).get("count"),
        } if final else None,
        "caption_slide_overlap": token_overlap(
            caption,
            all_slide_text if measured_slides else None,
        ),
        "slides": normalized,
        "evidence": [render_slide_evidence(slide) for slide in normalized],
    }


compute_carousel_features = analyze_carousel
