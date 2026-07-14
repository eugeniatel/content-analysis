from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


EXTRACTOR_VERSION = "extract-v2"
FEATURE_VERSION = "features-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _insert_artifact(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    kind: str,
    text: str,
    source_hash: str,
    language: str | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    slide_index: int | None = None,
    source_path: str | None = None,
    confidence: float | None = None,
) -> int | None:
    if not text.strip():
        return None
    cursor = con.execute(
        """
        INSERT INTO text_artifacts(
            piece_id, kind, language, text, start_sec, end_sec, slide_index,
            source_path, confidence, extractor_version, source_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            piece_id,
            kind,
            language,
            text.strip(),
            start_sec,
            end_sec,
            slide_index,
            source_path,
            confidence,
            EXTRACTOR_VERSION,
            source_hash,
        ),
    )
    return int(cursor.lastrowid)


def _persist_piece_evidence_impl(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    caption: str | None,
    extracted: dict[str, Any],
    source_path: str | None = None,
    language: str | None = None,
) -> dict[str, int]:
    """Replace one extractor version atomically while retaining other versions."""
    payload_hash = content_hash({"caption": caption, "extracted": extracted})
    # Findings retain their copied excerpt/timing when an extractor output is
    # superseded; clear the optional FK before replacing the artifact rows.
    con.execute(
        """
        UPDATE finding_evidence
        SET artifact_id=NULL
        WHERE artifact_id IN (
            SELECT id FROM text_artifacts
            WHERE piece_id=? AND extractor_version=?
        )
        """,
        (piece_id, EXTRACTOR_VERSION),
    )
    con.execute(
        "DELETE FROM visual_text_blocks WHERE piece_id=? AND extractor_version=?",
        (piece_id, EXTRACTOR_VERSION),
    )
    con.execute(
        "DELETE FROM text_artifacts WHERE piece_id=? AND extractor_version=?",
        (piece_id, EXTRACTOR_VERSION),
    )
    artifact_count = 0
    block_count = 0
    if _insert_artifact(
        con,
        piece_id=piece_id,
        kind="caption",
        text=caption or "",
        language=language,
        source_path=source_path,
        source_hash=payload_hash,
    ):
        artifact_count += 1

    transcript = extracted.get("transcript") or []
    words_seen: set[tuple[float, float, str]] = set()
    for segment in transcript:
        text = str(segment.get("text") or "")
        if _insert_artifact(
            con,
            piece_id=piece_id,
            kind="transcript_segment",
            text=text,
            language=language,
            start_sec=_float(segment.get("start")),
            end_sec=_float(segment.get("end")),
            source_path=source_path,
            source_hash=payload_hash,
        ):
            artifact_count += 1
        for word in segment.get("words") or []:
            start = _float(word.get("start")) or 0.0
            end = _float(word.get("end")) or start
            word_text = str(word.get("word") or word.get("text") or "").strip()
            key = (start, end, word_text)
            if not word_text or key in words_seen:
                continue
            words_seen.add(key)
            if _insert_artifact(
                con,
                piece_id=piece_id,
                kind="transcript_word",
                text=word_text,
                language=language,
                start_sec=start,
                end_sec=end,
                source_path=source_path,
                confidence=_float(word.get("probability") or word.get("confidence")),
                source_hash=payload_hash,
            ):
                artifact_count += 1
    for word in extracted.get("transcript_words") or []:
        start = _float(word.get("start")) or 0.0
        end = _float(word.get("end")) or start
        word_text = str(word.get("word") or word.get("text") or "").strip()
        key = (start, end, word_text)
        if not word_text or key in words_seen:
            continue
        words_seen.add(key)
        if _insert_artifact(
            con,
            piece_id=piece_id,
            kind="transcript_word",
            text=word_text,
            language=language,
            start_sec=start,
            end_sec=end,
            source_path=source_path,
            confidence=_float(word.get("probability") or word.get("confidence")),
            source_hash=payload_hash,
        ):
            artifact_count += 1

    for frame in extracted.get("onscreen_text") or []:
        timestamp = _float(frame.get("timestamp"))
        blocks = frame.get("blocks") or []
        if not blocks and frame.get("text"):
            blocks = [frame]
        for block in blocks:
            text = str(block.get("text") or "")
            confidence = _float(block.get("confidence"))
            if _insert_artifact(
                con,
                piece_id=piece_id,
                kind="onscreen_block",
                text=text,
                language=language,
                start_sec=timestamp,
                source_path=source_path,
                confidence=confidence,
                source_hash=payload_hash,
            ):
                artifact_count += 1
            if text.strip():
                _insert_visual_block(
                    con,
                    piece_id=piece_id,
                    text=text,
                    timestamp=timestamp,
                    slide_index=None,
                    confidence=confidence,
                    block=block,
                    source_hash=payload_hash,
                )
                block_count += 1

    for position, slide in enumerate(extracted.get("slides") or [], start=1):
        slide_index = int(slide.get("slide_index") or position)
        blocks = slide.get("blocks") or []
        if not blocks and slide.get("text"):
            blocks = [slide]
        for block in blocks:
            text = str(block.get("text") or "")
            confidence = _float(block.get("confidence"))
            if _insert_artifact(
                con,
                piece_id=piece_id,
                kind="slide_block",
                text=text,
                language=language,
                slide_index=slide_index,
                source_path=source_path,
                confidence=confidence,
                source_hash=payload_hash,
            ):
                artifact_count += 1
            if text.strip():
                _insert_visual_block(
                    con,
                    piece_id=piece_id,
                    text=text,
                    timestamp=None,
                    slide_index=slide_index,
                    confidence=confidence,
                    block=block,
                    source_hash=payload_hash,
                )
                block_count += 1
    return {"text_artifacts": artifact_count, "visual_text_blocks": block_count}


def persist_piece_evidence(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    caption: str | None,
    extracted: dict[str, Any],
    source_path: str | None = None,
    language: str | None = None,
) -> dict[str, int]:
    """Replace a piece's evidence as one rollback-safe operation."""
    con.execute("SAVEPOINT persist_piece_evidence")
    try:
        result = _persist_piece_evidence_impl(
            con,
            piece_id=piece_id,
            caption=caption,
            extracted=extracted,
            source_path=source_path,
            language=language,
        )
    except Exception:
        con.execute("ROLLBACK TO SAVEPOINT persist_piece_evidence")
        con.execute("RELEASE SAVEPOINT persist_piece_evidence")
        raise
    else:
        con.execute("RELEASE SAVEPOINT persist_piece_evidence")
        return result


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _insert_visual_block(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    text: str,
    timestamp: float | None,
    slide_index: int | None,
    confidence: float | None,
    block: dict[str, Any],
    source_hash: str,
) -> None:
    con.execute(
        """
        INSERT INTO visual_text_blocks(
            piece_id, slide_index, timestamp_sec, text, confidence, x, y, width,
            height, image_width, image_height, extractor_version, source_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            piece_id,
            slide_index,
            timestamp,
            text.strip(),
            confidence,
            _float(block.get("x")),
            _float(block.get("y")),
            _float(block.get("width")),
            _float(block.get("height")),
            int(block["image_width"]) if block.get("image_width") else None,
            int(block["image_height"]) if block.get("image_height") else None,
            EXTRACTOR_VERSION,
            source_hash,
        ),
    )


def persist_piece_features(
    con: sqlite3.Connection,
    *,
    piece_id: str,
    common: dict[str, Any],
    reel: dict[str, Any] | None,
    carousel: dict[str, Any] | None,
    lexical: dict[str, Any] | None = None,
    duration_sec: float | None = None,
    feature_version: str = FEATURE_VERSION,
) -> None:
    payload = {"common": common, "reel": reel, "carousel": carousel, "lexical": lexical}
    con.execute(
        """
        INSERT INTO piece_features(
            piece_id, feature_version, source_hash, computed_at, word_count,
            slide_count, duration_sec, words_per_minute, pause_300ms, pause_600ms,
            pause_1000ms, common_json, reel_json, carousel_json, lexical_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(piece_id, feature_version) DO UPDATE SET
            source_hash=excluded.source_hash,
            computed_at=excluded.computed_at,
            word_count=excluded.word_count,
            slide_count=excluded.slide_count,
            duration_sec=excluded.duration_sec,
            words_per_minute=excluded.words_per_minute,
            pause_300ms=excluded.pause_300ms,
            pause_600ms=excluded.pause_600ms,
            pause_1000ms=excluded.pause_1000ms,
            common_json=excluded.common_json,
            reel_json=excluded.reel_json,
            carousel_json=excluded.carousel_json,
            lexical_json=excluded.lexical_json
        """,
        (
            piece_id,
            feature_version,
            content_hash(payload),
            utc_now(),
            _common_word_count(common),
            _nested_int(carousel, "slide_count"),
            duration_sec,
            _nested_float(reel, "speech", "words_per_minute")
            or _nested_float(reel, "words_per_minute")
            or _nested_float(reel, "wpm"),
            _pause(reel, "0.3") or _pause(reel, "300"),
            _pause(reel, "0.6") or _pause(reel, "600"),
            _pause(reel, "1.0") or _pause(reel, "1000"),
            json.dumps(common, ensure_ascii=False, sort_keys=True),
            json.dumps(reel, ensure_ascii=False, sort_keys=True) if reel is not None else None,
            json.dumps(carousel, ensure_ascii=False, sort_keys=True) if carousel is not None else None,
            json.dumps(lexical, ensure_ascii=False, sort_keys=True) if lexical is not None else None,
        ),
    )
    con.commit()


def _nested_float(value: dict[str, Any] | None, *keys: str) -> float | None:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _float(current)


def _nested_int(value: dict[str, Any] | None, *keys: str) -> int | None:
    number = _nested_float(value, *keys)
    return int(number) if number is not None else None


def _pause(reel: dict[str, Any] | None, key: str) -> int | None:
    if not reel:
        return None
    pauses = reel.get("pause_counts") or reel.get("pauses") or {}
    if not isinstance(pauses, dict):
        return None
    thresholds = pauses.get("thresholds") if isinstance(pauses.get("thresholds"), dict) else pauses
    value = thresholds.get(key)
    if isinstance(value, dict):
        value = value.get("count")
    return int(value) if isinstance(value, (int, float)) else None


def _common_word_count(common: dict[str, Any]) -> int | None:
    direct = _nested_int(common, "totals", "word_count") or _nested_int(common, "word_count")
    if direct is not None:
        return direct
    sources = common.get("sources")
    if not isinstance(sources, dict):
        return None
    values = [
        source.get("word_count")
        for source in sources.values()
        if isinstance(source, dict) and isinstance(source.get("word_count"), (int, float))
    ]
    return int(sum(values)) if values else None
