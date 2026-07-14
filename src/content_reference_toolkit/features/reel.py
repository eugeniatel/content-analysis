"""Deterministic Reel speech, pause, on-screen, and visual-cadence features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median
import re
from typing import Any

from .common import sentence_spans, tokenize


FEATURE_VERSION = "reel-v1"
DEFAULT_PAUSE_THRESHOLDS = (0.3, 0.6, 1.0)
PHRASE_END_RE = re.compile(r"[.!?;:]\s*$")


def _number(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    return result


def _time(mapping: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        if mapping.get(name) is not None:
            return _number(mapping[name], name)
    return None


def normalize_word_timestamps(words: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Normalize backend-specific word rows into one strict timing contract."""

    if words is None:
        return None
    normalized: list[dict[str, Any]] = []
    for source_index, raw in enumerate(words):
        if not isinstance(raw, Mapping):
            raise TypeError("word timestamps must be mappings")
        text = raw.get("word", raw.get("text", raw.get("token")))
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"word timestamp {source_index} has no word text")
        start = _time(raw, ("start_sec", "start", "start_time"))
        end = _time(raw, ("end_sec", "end", "end_time"))
        if end is None and start is not None and raw.get("duration") is not None:
            end = start + float(raw["duration"])
        if start is None or end is None:
            raise ValueError(f"word timestamp {source_index} needs start and end times")
        if start < 0 or end < start:
            raise ValueError(f"invalid word interval at index {source_index}: {start}..{end}")
        normalized.append(
            {
                "word_index": source_index,
                "text": text.strip(),
                "start_sec": start,
                "end_sec": end,
                "confidence": _number(raw.get("confidence"), "confidence"),
                "segment_index": raw.get("segment_index"),
                "source_path": raw.get("source_path"),
                "extractor_version": raw.get("extractor_version"),
            }
        )
    normalized.sort(key=lambda item: (item["start_sec"], item["end_sec"], item["word_index"]))
    for order, item in enumerate(normalized):
        item["order"] = order
    return normalized


def normalize_segments(segments: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    if segments is None:
        return None
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, Mapping):
            raise TypeError("transcript segments must be mappings")
        text = raw.get("text")
        if not isinstance(text, str):
            raise ValueError(f"segment {index} needs text")
        start = _time(raw, ("start_sec", "start", "start_time"))
        end = _time(raw, ("end_sec", "end", "end_time"))
        if start is not None and end is not None and (start < 0 or end < start):
            raise ValueError(f"invalid segment interval at index {index}: {start}..{end}")
        normalized.append(
            {
                "segment_index": raw.get("segment_index", index),
                "text": text,
                "start_sec": start,
                "end_sec": end,
                "word_count": len(tokenize(text)),
                "character_count": len(text),
                "duration_seconds": round(end - start, 6)
                if start is not None and end is not None else None,
                "source_path": raw.get("source_path"),
            }
        )
    return normalized


def _interval_union_duration(intervals: Sequence[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    start, end = ordered[0]
    total = 0.0
    for next_start, next_end in ordered[1:]:
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    return total + end - start


def _word_gaps(words: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for left, right in zip(words, words[1:]):
        raw_gap = float(right["start_sec"]) - float(left["end_sec"])
        gaps.append(
            {
                "after_word_index": left["word_index"],
                "before_word_index": right["word_index"],
                "after_text": left["text"],
                "before_text": right["text"],
                "start_sec": left["end_sec"],
                "end_sec": right["start_sec"],
                "duration_seconds": round(max(0.0, raw_gap), 6),
                "raw_gap_seconds": round(raw_gap, 6),
                "overlap": raw_gap < 0,
            }
        )
    return gaps


def _first_phrase(words: Sequence[Mapping[str, Any]], pause_threshold: float) -> dict[str, Any] | None:
    if not words:
        return None
    end_index = len(words) - 1
    for index, word in enumerate(words):
        if PHRASE_END_RE.search(str(word["text"])):
            end_index = index
            break
        if index < len(words) - 1:
            gap = float(words[index + 1]["start_sec"]) - float(word["end_sec"])
            if gap >= pause_threshold:
                end_index = index
                break
    selected = words[:end_index + 1]
    return {
        "text": " ".join(str(word["text"]) for word in selected),
        "word_count": len(selected),
        "start_sec": selected[0]["start_sec"],
        "end_sec": selected[-1]["end_sec"],
        "duration_seconds": round(float(selected[-1]["end_sec"]) - float(selected[0]["start_sec"]), 6),
        "start_word_index": selected[0]["word_index"],
        "end_word_index": selected[-1]["word_index"],
        "boundary_rule": "punctuation_or_pause",
        "pause_threshold_seconds": pause_threshold,
    }


def _longest_run(words: Sequence[Mapping[str, Any]], break_threshold: float) -> dict[str, Any] | None:
    if not words:
        return None
    candidates: list[Sequence[Mapping[str, Any]]] = []
    run_start = 0
    for index in range(len(words) - 1):
        gap = float(words[index + 1]["start_sec"]) - float(words[index]["end_sec"])
        if gap >= break_threshold:
            candidates.append(words[run_start:index + 1])
            run_start = index + 1
    candidates.append(words[run_start:])
    best = max(
        candidates,
        key=lambda run: (
            float(run[-1]["end_sec"]) - float(run[0]["start_sec"]),
            len(run),
            -int(run[0]["word_index"]),
        ),
    )
    return {
        "word_count": len(best),
        "text": " ".join(str(word["text"]) for word in best),
        "start_sec": best[0]["start_sec"],
        "end_sec": best[-1]["end_sec"],
        "duration_seconds": round(float(best[-1]["end_sec"]) - float(best[0]["start_sec"]), 6),
        "start_word_index": best[0]["word_index"],
        "end_word_index": best[-1]["word_index"],
        "break_threshold_seconds": break_threshold,
    }


def _pause_features(
    words: Sequence[Mapping[str, Any]] | None,
    thresholds: Sequence[float],
) -> dict[str, Any]:
    if not words:
        return {
            "available": False,
            "reason": "word_timestamps_required",
            "median_word_gap_seconds": None,
            "overlap_count": None,
            "thresholds": {
                str(threshold): {"threshold_seconds": threshold, "count": None, "events": []}
                for threshold in thresholds
            },
            "gaps": [],
        }
    gaps = _word_gaps(words)
    return {
        "available": True,
        "reason": None,
        "median_word_gap_seconds": round(median([gap["duration_seconds"] for gap in gaps]), 6)
        if gaps else None,
        "overlap_count": sum(bool(gap["overlap"]) for gap in gaps),
        "thresholds": {
            str(threshold): {
                "threshold_seconds": threshold,
                "count": sum(gap["duration_seconds"] >= threshold for gap in gaps),
                "events": [gap for gap in gaps if gap["duration_seconds"] >= threshold],
            }
            for threshold in thresholds
        },
        "gaps": gaps,
    }


def _onscreen_features(blocks: Sequence[Mapping[str, Any]] | None, duration: float | None) -> dict[str, Any]:
    if blocks is None:
        return {
            "available": False,
            "reason": "onscreen_text_not_extracted",
            "block_count": None,
            "appearance_rate_per_minute": None,
            "text_card_duration_seconds": None,
            "blocks": [],
        }
    normalized: list[dict[str, Any]] = []
    intervals: list[tuple[float, float]] = []
    for index, raw in enumerate(blocks):
        if not isinstance(raw, Mapping):
            raise TypeError("on-screen blocks must be mappings")
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise TypeError("on-screen block text must be a string or None")
        start = _time(raw, ("start_sec", "start", "timestamp_sec"))
        end = _time(raw, ("end_sec", "end"))
        if start is not None and end is not None:
            if start < 0 or end < start:
                raise ValueError(f"invalid on-screen interval at index {index}: {start}..{end}")
            intervals.append((start, end))
        normalized.append(
            {
                "block_index": raw.get("block_index", index),
                "text": text,
                "start_sec": start,
                "end_sec": end,
                "confidence": _number(raw.get("confidence"), "confidence"),
                "source_path": raw.get("source_path"),
            }
        )
    all_timed = len(intervals) == len(normalized)
    return {
        "available": True,
        "reason": None,
        "block_count": len(normalized),
        "appearance_rate_per_minute": round(len(normalized) * 60 / duration, 6)
        if duration and duration > 0 else None,
        "text_card_duration_seconds": round(_interval_union_duration(intervals), 6)
        if all_timed else None,
        "timing_coverage": round(len(intervals) / len(normalized), 6) if normalized else None,
        "blocks": normalized,
    }


def _scene_features(scene_changes: int | Sequence[Any] | None, duration: float | None) -> dict[str, Any]:
    if scene_changes is None:
        return {
            "available": False,
            "reason": "scene_detection_not_run",
            "change_count": None,
            "changes_per_minute": None,
            "events": [],
        }
    if isinstance(scene_changes, bool):
        raise TypeError("scene_changes cannot be a boolean")
    if isinstance(scene_changes, int):
        if scene_changes < 0:
            raise ValueError("scene change count cannot be negative")
        count = scene_changes
        events: list[dict[str, Any]] = []
    elif isinstance(scene_changes, Sequence) and not isinstance(scene_changes, (str, bytes)):
        events = []
        for index, raw in enumerate(scene_changes):
            if isinstance(raw, Mapping):
                timestamp = _time(raw, ("timestamp_sec", "start_sec", "time"))
                events.append({**dict(raw), "event_index": index, "timestamp_sec": timestamp})
            else:
                events.append({"event_index": index, "timestamp_sec": _number(raw, "scene timestamp")})
        count = len(events)
    else:
        raise TypeError("scene_changes must be a count, sequence, or None")
    return {
        "available": True,
        "reason": None,
        "change_count": count,
        "changes_per_minute": round(count * 60 / duration, 6) if duration and duration > 0 else None,
        "events": events,
    }


def duration_percentile(duration_seconds: float | None, profile_durations: Sequence[float] | None) -> float | None:
    if duration_seconds is None or not profile_durations:
        return None
    measured = sorted(float(value) for value in profile_durations if value is not None)
    if not measured:
        return None
    below = sum(value < duration_seconds for value in measured)
    equal = sum(value == duration_seconds for value in measured)
    return round((below + 0.5 * equal) / len(measured), 6)


def _explicit_span(span: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    if span is None:
        return {"available": False, "reason": f"{name}_not_labeled"}
    return {
        "available": True,
        "reason": None,
        "text": span.get("text"),
        "start_sec": _time(span, ("start_sec", "start")),
        "end_sec": _time(span, ("end_sec", "end")),
        "source": span.get("source"),
        "evidence": dict(span),
    }


def analyze_reel(
    *,
    word_timestamps: Sequence[Mapping[str, Any]] | None = None,
    segments: Sequence[Mapping[str, Any]] | None = None,
    duration_seconds: float | None = None,
    on_screen_blocks: Sequence[Mapping[str, Any]] | None = None,
    scene_changes: int | Sequence[Any] | None = None,
    pause_thresholds: Sequence[float] = DEFAULT_PAUSE_THRESHOLDS,
    phrase_pause_threshold: float = 0.6,
    uninterrupted_break_threshold: float = 0.3,
    profile_durations: Sequence[float] | None = None,
    hook: Mapping[str, Any] | None = None,
    cta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute Reel features while keeping each evidence channel independent."""

    duration = _number(duration_seconds, "duration_seconds")
    if duration is not None and duration <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    thresholds = tuple(float(value) for value in pause_thresholds)
    if any(value <= 0 for value in thresholds):
        raise ValueError("pause thresholds must be greater than zero")
    if list(thresholds) != sorted(set(thresholds)):
        raise ValueError("pause thresholds must be unique and sorted")

    words = normalize_word_timestamps(word_timestamps)
    normalized_segments = normalize_segments(segments)
    has_words = bool(words)
    has_segments = bool(normalized_segments)
    timing_precision = "word" if has_words else "segment" if has_segments else "none"

    if has_words:
        assert words is not None
        spoken_word_count = len(words)
        transcript_start = float(words[0]["start_sec"])
        transcript_end = float(words[-1]["end_sec"])
        transcript_span = transcript_end - transcript_start
        wpm = spoken_word_count * 60 / transcript_span if transcript_span > 0 else None
    elif has_segments:
        assert normalized_segments is not None
        spoken_word_count = sum(int(segment["word_count"]) for segment in normalized_segments)
        timed_segments = [
            segment for segment in normalized_segments
            if segment["start_sec"] is not None and segment["end_sec"] is not None
        ]
        if timed_segments and len(timed_segments) == len(normalized_segments):
            transcript_start = min(float(segment["start_sec"]) for segment in timed_segments)
            transcript_end = max(float(segment["end_sec"]) for segment in timed_segments)
            transcript_span = transcript_end - transcript_start
            wpm = spoken_word_count * 60 / transcript_span if transcript_span > 0 else None
        else:
            transcript_start = transcript_end = transcript_span = wpm = None
    else:
        spoken_word_count = None
        transcript_start = transcript_end = transcript_span = wpm = None

    fully_timed_segments = bool(normalized_segments) and all(
        segment["start_sec"] is not None and segment["end_sec"] is not None
        for segment in normalized_segments
    )
    if fully_timed_segments:
        assert normalized_segments is not None
        speech_intervals = [
            (float(segment["start_sec"]), float(segment["end_sec"]))
            for segment in normalized_segments
            if segment["start_sec"] is not None and segment["end_sec"] is not None
        ]
        speech_basis = "segment_intervals"
    elif words:
        speech_intervals = [(float(word["start_sec"]), float(word["end_sec"])) for word in words]
        speech_basis = "word_intervals"
    else:
        speech_intervals = []
        speech_basis = None
    speech_duration = _interval_union_duration(speech_intervals) if speech_intervals else None

    pauses = _pause_features(words, thresholds)
    transcript_text = " ".join(str(word["text"]) for word in words) if words else None
    sentences = sentence_spans(transcript_text) if transcript_text else []
    sentence_lengths = [len(tokenize(sentence["text"])) for sentence in sentences]

    return {
        "feature_version": FEATURE_VERSION,
        "duration_seconds": duration,
        "duration_percentile": duration_percentile(duration, profile_durations),
        "speech": {
            "available": has_words or has_segments,
            "reason": None if has_words or has_segments else "transcript_not_extracted",
            "timing_precision": timing_precision,
            "spoken_word_count": spoken_word_count,
            "words_per_minute": round(wpm, 6) if wpm is not None else None,
            "wpm_basis": "first_to_last_spoken_timestamp" if wpm is not None else None,
            "transcript_start_sec": transcript_start,
            "transcript_end_sec": transcript_end,
            "transcript_span_seconds": round(transcript_span, 6) if transcript_span is not None else None,
            "speech_duration_seconds": round(speech_duration, 6) if speech_duration is not None else None,
            "speech_duration_basis": speech_basis,
            "speech_to_video_ratio": round(speech_duration / duration, 6)
            if speech_duration is not None and duration else None,
            "first_phrase": _first_phrase(words, phrase_pause_threshold) if words else None,
            "sentence_word_lengths": sentence_lengths if words else None,
            "segment_lengths": {
                "available": normalized_segments is not None,
                "reason": None if normalized_segments is not None else "segments_not_extracted",
                "timing_coverage": round(
                    sum(
                        segment["start_sec"] is not None and segment["end_sec"] is not None
                        for segment in normalized_segments
                    ) / len(normalized_segments),
                    6,
                ) if normalized_segments else None,
                "values": normalized_segments or [],
            },
            "words": words or [],
        },
        "pauses": pauses,
        "longest_uninterrupted_run": _longest_run(words, uninterrupted_break_threshold)
        if words else None,
        "hook": _explicit_span(hook, "hook"),
        "cta": _explicit_span(cta, "cta"),
        "on_screen": _onscreen_features(on_screen_blocks, duration),
        "visual_cadence": _scene_features(scene_changes, duration),
        "coverage": {
            "word_timestamps": word_timestamps is not None and bool(words),
            "segments": segments is not None and bool(normalized_segments),
            "on_screen_text": on_screen_blocks is not None,
            "scene_changes": scene_changes is not None,
            "video_duration": duration is not None,
        },
    }


compute_reel_features = analyze_reel
