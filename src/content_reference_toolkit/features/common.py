"""Deterministic writing features shared by every content format.

The functions in this module deliberately return JSON-compatible dictionaries.  A
source which was not extracted is represented as unavailable; it is never converted
to an empty string and therefore never mistaken for a measured zero.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import median
import re
import unicodedata
from typing import Any


FEATURE_VERSION = "common-v1"

WORD_RE = re.compile(r"[^\W_]+(?:[\-'’][^\W_]+)*", re.UNICODE)
SENTENCE_END_RE = re.compile(r"(?:[.!?]+(?=\s|$)|\n+)", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)*)(?!\w)", re.UNICODE)
BULLET_RE = re.compile(r"(?m)^\s*(?:[-*•●▪◦‣⁃]|\d+[.):])\s+")

FIRST_PERSON = {
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves",
    "yo", "me", "mí", "mio", "mía", "míos", "mías", "nos", "nosotros",
    "nosotras", "nuestro", "nuestra", "nuestros", "nuestras",
}
SECOND_PERSON = {
    "you", "your", "yours", "yourself", "yourselves",
    "tú", "tu", "tus", "te", "ti", "contigo", "usted", "ustedes", "vos",
    "vosotros", "vosotras", "vuestro", "vuestra", "vuestros", "vuestras",
}
CERTAINTY_MARKERS = {
    "always", "certainly", "clearly", "definitely", "never", "obviously", "proven",
    "siempre", "ciertamente", "claramente", "definitivamente", "nunca", "obvio",
    "obviamente", "comprobado", "demostrado", "sin duda",
}
HEDGE_MARKERS = {
    "apparently", "could", "likely", "maybe", "might", "perhaps", "possibly",
    "seem", "seems", "usually", "apparently",
    "aparentemente", "podría", "podria", "quizá", "quizas", "quizás", "tal vez",
    "posiblemente", "probablemente", "parece", "suele", "usualmente",
}
IMPERATIVE_STARTERS = {
    # English
    "ask", "choose", "click", "comment", "compare", "create", "discover", "download",
    "follow", "get", "give", "join", "learn", "look", "make", "read", "remember",
    "save", "share", "start", "stop", "subscribe", "swipe", "take", "tap", "tell",
    "try", "use", "watch", "write", "avoid",
    # Spanish (accented and common unaccented spellings from social copy)
    "aprende", "comenta", "comparte", "compara", "crea", "cuenta", "cuéntame",
    "deja", "descarga", "descubre", "desliza", "elige", "empieza", "escribe", "evita",
    "guarda", "haz", "lee", "mira", "pregunta", "prueba", "recuerda", "sigue", "toca",
    "únete", "unete", "usa", "ve",
}
SPAN_IMPERATIVES = {
    "you should", "you need to", "make sure", "let's",
    "deberías", "deberias", "tienes que", "asegúrate", "asegurate", "vamos a",
}

SPANISH_SIGNALS = {
    "aquí", "asi", "así", "con", "como", "cómo", "cuando", "de", "del", "el", "ella",
    "en", "es", "esta", "esto", "gracias", "hola", "la", "las", "lo", "los", "más",
    "mundo", "no", "para", "pero", "por", "porque", "que", "qué", "se", "si", "sin",
    "son", "su", "te", "tu", "tú", "un", "una", "y", "ya",
}
ENGLISH_SIGNALS = {
    "a", "about", "and", "are", "as", "at", "be", "because", "but", "by", "for",
    "from", "hello", "here", "how", "in", "is", "it", "more", "no", "not", "of",
    "on", "or", "our", "that", "the", "their", "this", "to", "we", "what", "when",
    "with", "world", "you", "your",
}


def _clean_token(token: str) -> str:
    return token.casefold().replace("’", "'")


def token_spans(text: str) -> list[dict[str, Any]]:
    """Return normalized tokens and their exact character spans."""

    return [
        {
            "token": match.group(0),
            "normalized": _clean_token(match.group(0)),
            "start": match.start(),
            "end": match.end(),
        }
        for match in WORD_RE.finditer(text)
    ]


def tokenize(text: str) -> list[str]:
    return [item["normalized"] for item in token_spans(text)]


def sentence_spans(text: str) -> list[dict[str, Any]]:
    """Split prose while retaining offsets into the unmodified source string."""

    spans: list[dict[str, Any]] = []
    cursor = 0
    for match in SENTENCE_END_RE.finditer(text):
        end = match.end()
        raw = text[cursor:end]
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        if right > left:
            spans.append(
                {
                    "index": len(spans),
                    "start": cursor + left,
                    "end": cursor + right,
                    "text": raw[left:right],
                }
            )
        cursor = end
    raw = text[cursor:]
    left = len(raw) - len(raw.lstrip())
    right = len(raw.rstrip())
    if right > left:
        spans.append(
            {
                "index": len(spans),
                "start": cursor + left,
                "end": cursor + right,
                "text": raw[left:right],
            }
        )
    return spans


def _paired_punctuation_count(text: str, closing: str, opening: str) -> int:
    closed = text.count(closing)
    # An inverted mark without a closing mark is still a visible signal, but a normal
    # Spanish pair represents one question/exclamation rather than two.
    return closed if closed else text.count(opening)


def _emoji_count(text: str) -> int:
    count = 0
    for char in text:
        code = ord(char)
        if (
            0x1F000 <= code <= 0x1FAFF
            or 0x2600 <= code <= 0x26FF
            or 0x2700 <= code <= 0x27BF
        ):
            count += 1
    return count


def detect_language(text: str) -> dict[str, Any]:
    """Conservative Spanish/English heuristic with visible scoring evidence."""

    tokens = tokenize(text)
    if not tokens:
        return {
            "language": "undetermined",
            "confidence": 0.0,
            "method": "deterministic-stopword-v1",
            "scores": {"es": 0, "en": 0},
            "evidence": {"es": [], "en": []},
        }

    es_hits = [token for token in tokens if token in SPANISH_SIGNALS]
    en_hits = [token for token in tokens if token in ENGLISH_SIGNALS]
    es_score = len(es_hits)
    en_score = len(en_hits)
    if re.search(r"[áéíóúüñ¿¡]", text.casefold()):
        es_score += 2

    if es_score == en_score:
        language = "undetermined"
        confidence = 0.0 if es_score == 0 else 0.5
    else:
        language = "es" if es_score > en_score else "en"
        confidence = round(max(es_score, en_score) / max(1, es_score + en_score), 4)

    return {
        "language": language,
        "confidence": confidence,
        "method": "deterministic-stopword-v1",
        "scores": {"es": es_score, "en": en_score},
        "evidence": {"es": es_hits[:12], "en": en_hits[:12]},
    }


def _phrase_occurrences(text: str, phrases: set[str]) -> list[dict[str, Any]]:
    normalized = text.casefold().replace("’", "'")
    occurrences: list[dict[str, Any]] = []
    for phrase in sorted(phrases):
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(normalized):
            occurrences.append(
                {
                    "marker": phrase,
                    "text": text[match.start():match.end()],
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return sorted(occurrences, key=lambda item: (item["start"], item["end"]))


def imperative_markers(text: str) -> list[dict[str, Any]]:
    """Return conservative imperative/directive markers with source offsets."""

    markers: list[dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()
    for sentence in sentence_spans(text):
        words = token_spans(sentence["text"])
        if words and words[0]["normalized"] in IMPERATIVE_STARTERS:
            word = words[0]
            start = sentence["start"] + word["start"]
            end = sentence["start"] + word["end"]
            occupied.add((start, end))
            markers.append(
                {
                    "marker": word["normalized"],
                    "text": text[start:end],
                    "start": start,
                    "end": end,
                    "rule": "sentence_initial_verb",
                }
            )
    for occurrence in _phrase_occurrences(text, SPAN_IMPERATIVES):
        span = (occurrence["start"], occurrence["end"])
        if span not in occupied:
            occurrence["rule"] = "directive_phrase"
            markers.append(occurrence)
    return sorted(markers, key=lambda item: (item["start"], item["end"]))


def _token_markers(text: str, vocabulary: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "marker": item["normalized"],
            "text": item["token"],
            "start": item["start"],
            "end": item["end"],
        }
        for item in token_spans(text)
        if item["normalized"] in vocabulary
    ]


def ngram_frequencies(
    text: str,
    *,
    n_values: Sequence[int] = (1, 2, 3),
    minimum_count: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Count contiguous token n-grams and retain up to three source examples."""

    words = token_spans(text)
    result: dict[str, list[dict[str, Any]]] = {}
    for n in n_values:
        if n < 1:
            raise ValueError("n_values must contain positive integers")
        counts: Counter[tuple[str, ...]] = Counter()
        examples: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for index in range(0, len(words) - n + 1):
            window = words[index:index + n]
            key = tuple(item["normalized"] for item in window)
            counts[key] += 1
            if len(examples.setdefault(key, [])) < 3:
                start = window[0]["start"]
                end = window[-1]["end"]
                examples[key].append({"start": start, "end": end, "text": text[start:end]})
        rows = [
            {
                "ngram": " ".join(key),
                "count": count,
                "examples": examples[key],
            }
            for key, count in counts.items()
            if count >= minimum_count
        ]
        rows.sort(key=lambda item: (-item["count"], item["ngram"]))
        result[str(n)] = rows
    return result


def token_overlap(left: str | None, right: str | None) -> dict[str, Any]:
    """Measure unique-token overlap without treating a missing side as zero."""

    if left is None or right is None:
        return {
            "available": False,
            "reason": "missing_text_source",
            "jaccard": None,
            "left_coverage": None,
            "right_coverage": None,
            "shared_tokens": [],
        }
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    shared = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return {
        "available": True,
        "reason": None,
        "jaccard": round(len(shared) / len(union), 6) if union else None,
        "left_coverage": round(len(shared) / len(left_tokens), 6) if left_tokens else None,
        "right_coverage": round(len(shared) / len(right_tokens), 6) if right_tokens else None,
        "shared_tokens": sorted(shared),
    }


def analyze_text(text: str | None, *, available: bool | None = None) -> dict[str, Any]:
    """Analyze one exact text source.

    ``None`` means extraction evidence is missing.  ``""`` means extraction ran and
    observed an empty source, so count metrics are valid zeros.
    """

    is_available = text is not None if available is None else bool(available and text is not None)
    if not is_available:
        return {
            "available": False,
            "reason": "source_not_extracted" if text is None else "source_marked_unavailable",
            "word_count": None,
            "character_count": None,
            "sentence_count": None,
            "line_count": None,
            "paragraph_count": None,
            "language": {
                "language": "undetermined",
                "confidence": 0.0,
                "method": "deterministic-stopword-v1",
                "scores": {"es": 0, "en": 0},
                "evidence": {"es": [], "en": []},
            },
        }

    assert text is not None
    words = token_spans(text)
    sentences = sentence_spans(text)
    sentence_lengths = [len(tokenize(item["text"])) for item in sentences]
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    normalized_words = [item["normalized"] for item in words]
    unique_words = set(normalized_words)
    first_person = _token_markers(text, FIRST_PERSON)
    second_person = _token_markers(text, SECOND_PERSON)
    imperatives = imperative_markers(text)
    certainty = _phrase_occurrences(text, CERTAINTY_MARKERS)
    hedging = _phrase_occurrences(text, HEDGE_MARKERS)

    return {
        "available": True,
        "reason": None,
        "word_count": len(words),
        "character_count": len(text),
        "non_whitespace_character_count": sum(not char.isspace() for char in text),
        "sentence_count": len(sentences),
        "sentence_words": {
            "average": round(sum(sentence_lengths) / len(sentence_lengths), 6)
            if sentence_lengths else None,
            "median": median(sentence_lengths) if sentence_lengths else None,
            "maximum": max(sentence_lengths) if sentence_lengths else None,
            "values": sentence_lengths,
        },
        "line_count": len(nonempty_lines),
        "raw_line_count": len(text.splitlines()) if text else 0,
        "paragraph_count": len(paragraphs),
        "question_count": _paired_punctuation_count(text, "?", "¿"),
        "exclamation_count": _paired_punctuation_count(text, "!", "¡"),
        "number_count": len(NUMBER_RE.findall(text)),
        "bullet_count": len(BULLET_RE.findall(text)),
        "emoji_count": _emoji_count(text),
        "imperatives": {"count": len(imperatives), "markers": imperatives},
        "pronouns": {
            "first_person_count": len(first_person),
            "second_person_count": len(second_person),
            "first_person_markers": first_person,
            "second_person_markers": second_person,
        },
        "stance": {
            "certainty_count": len(certainty),
            "hedging_count": len(hedging),
            "certainty_markers": certainty,
            "hedging_markers": hedging,
        },
        "lexical_diversity": {
            "unique_word_count": len(unique_words),
            "type_token_ratio": round(len(unique_words) / len(words), 6) if words else None,
        },
        "language": detect_language(text),
        "repeated_ngrams": ngram_frequencies(text),
        "sentence_evidence": sentences,
    }


def _source_artifacts(value: Any) -> tuple[bool, str | None, list[dict[str, Any]]]:
    if value is None:
        return False, "source_not_extracted", []
    if isinstance(value, str):
        return True, None, [{"text": value}]
    if isinstance(value, Mapping):
        if value.get("available") is False:
            return False, str(value.get("reason") or "source_marked_unavailable"), []
        if "artifacts" in value:
            raw = value.get("artifacts")
            if raw is None:
                return False, "source_not_extracted", []
            value = raw
        elif "text" in value:
            return True, None, [dict(value)]
        else:
            raise ValueError("source mappings need a text or artifacts field")
    if isinstance(value, str):
        return True, None, [{"text": value}]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        artifacts: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                artifacts.append({"text": item})
            elif isinstance(item, Mapping):
                artifacts.append(dict(item))
            else:
                raise TypeError("source artifacts must be strings or mappings")
        return True, None, artifacts
    raise TypeError("text sources must be strings, mappings, sequences, or None")


def analyze_text_sources(
    sources: Mapping[str, Any],
    *,
    hook: Mapping[str, Any] | str | None = None,
    cta: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Compute source-specific writing features while preserving artifact evidence."""

    by_source: dict[str, Any] = {}
    available_texts: dict[str, str] = {}
    for source_name, value in sources.items():
        available, reason, artifacts = _source_artifacts(value)
        if not available:
            source_result = analyze_text(None)
            source_result["reason"] = reason
            source_result["artifact_count"] = 0
            source_result["artifacts"] = []
            by_source[source_name] = source_result
            continue

        artifact_results: list[dict[str, Any]] = []
        texts: list[str] = []
        for index, artifact in enumerate(artifacts):
            artifact_available = artifact.get("available", True) is not False
            artifact_text = artifact.get("text")
            if artifact_text is not None and not isinstance(artifact_text, str):
                raise TypeError("artifact text must be a string or None")
            features = analyze_text(artifact_text, available=artifact_available)
            evidence = {
                key: value
                for key, value in artifact.items()
                if key not in {"text", "available"}
            }
            artifact_results.append(
                {
                    "artifact_index": index,
                    "text": artifact_text,
                    "evidence": evidence,
                    "features": features,
                }
            )
            if artifact_available and artifact_text is not None:
                texts.append(artifact_text)
        combined_text = "\n".join(texts)
        # An explicitly empty artifact list is a measured empty source.  A non-empty
        # list in which every artifact is unavailable is missing evidence, not zero.
        has_available_artifact = bool(texts) or not artifacts
        source_result = analyze_text(combined_text) if has_available_artifact else analyze_text(None)
        if not has_available_artifact:
            source_result["reason"] = "no_available_artifacts"
        source_result["artifact_count"] = len(artifacts)
        source_result["available_artifact_count"] = len(texts)
        source_result["artifacts"] = artifact_results
        by_source[source_name] = source_result
        if has_available_artifact:
            available_texts[source_name] = combined_text

    media_names = {
        name for name in available_texts
        if name.casefold() not in {"caption", "description"}
    }
    caption_name = next(
        (name for name in available_texts if name.casefold() in {"caption", "description"}),
        None,
    )
    media_text = "\n".join(available_texts[name] for name in sorted(media_names))
    caption_text = available_texts.get(caption_name) if caption_name else None

    def span_payload(span: Mapping[str, Any] | str | None) -> dict[str, Any]:
        if span is None:
            return {"available": False, "reason": "span_not_supplied", "features": None}
        if isinstance(span, str):
            return {"available": True, "reason": None, "text": span, "features": analyze_text(span)}
        text = span.get("text")
        if not isinstance(text, str):
            return {"available": False, "reason": "span_text_missing", "features": None}
        return {
            "available": True,
            "reason": None,
            "text": text,
            "source": span.get("source"),
            "start": span.get("start"),
            "end": span.get("end"),
            "features": analyze_text(text),
        }

    return {
        "feature_version": FEATURE_VERSION,
        "sources": by_source,
        "coverage": {
            name: {
                "available": result["available"],
                "reason": result.get("reason"),
                "artifact_count": result.get("artifact_count", 0),
                "nonempty": bool(result.get("word_count")),
            }
            for name, result in by_source.items()
        },
        "hook": span_payload(hook),
        "cta": span_payload(cta),
        "caption_media_overlap": token_overlap(
            caption_text,
            media_text if media_names else None,
        ),
    }


# Stable, discoverable aliases for callers that use "compute" terminology.
compute_common_features = analyze_text_sources
