"""Profile and winner/baseline lexical inputs built from deterministic counts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
import math
from typing import Any

from .common import detect_language, ngram_frequencies, token_spans, tokenize


FEATURE_VERSION = "lexical-v1"

STOPWORDS = {
    "en": {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "we",
        "with", "you", "your",
    },
    "es": {
        "a", "al", "como", "con", "de", "del", "el", "en", "es", "esta", "esto",
        "la", "las", "lo", "los", "o", "para", "pero", "por", "que", "se", "si",
        "sin", "su", "un", "una", "y",
    },
}


def content_tokens(text: str, *, language: str | None = None) -> list[str]:
    """Tokenize and remove a small, transparent language-specific stop list."""

    if language is None:
        language = detect_language(text)["language"]
    blocked = STOPWORDS.get(language, set())
    return [token for token in tokenize(text) if token not in blocked]


def aggregate_ngrams(
    texts: Iterable[str],
    *,
    n: int = 1,
    minimum_count: int = 1,
    remove_stopwords: bool = False,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate n-grams across documents and retain document-level evidence."""

    if n < 1:
        raise ValueError("n must be positive")
    counts: Counter[tuple[str, ...]] = Counter()
    document_frequency: Counter[tuple[str, ...]] = Counter()
    examples: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    document_count = 0
    for document_index, text in enumerate(texts):
        document_count += 1
        raw = token_spans(text)
        blocked: set[str] = set()
        if remove_stopwords:
            doc_language = language or detect_language(text)["language"]
            blocked = STOPWORDS.get(doc_language, set())
        observed: set[tuple[str, ...]] = set()
        for offset in range(0, len(raw) - n + 1):
            window = raw[offset:offset + n]
            key = tuple(item["normalized"] for item in window)
            # Preserve true adjacency for phrases. Stop-word filtering removes a
            # unigram (or a phrase made entirely of stop words), not the words from
            # inside a window, which would silently turn n-grams into skip-grams.
            if blocked and all(token in blocked for token in key):
                continue
            counts[key] += 1
            observed.add(key)
            if len(examples[key]) < 3:
                start = window[0]["start"]
                end = window[-1]["end"]
                examples[key].append(
                    {
                        "document_index": document_index,
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                    }
                )
        document_frequency.update(observed)

    rows = [
        {
            "ngram": " ".join(term),
            "count": count,
            "document_frequency": document_frequency[term],
            "document_prevalence": round(document_frequency[term] / document_count, 6)
            if document_count else None,
            "examples": examples[term],
        }
        for term, count in counts.items()
        if count >= minimum_count
    ]
    rows.sort(key=lambda item: (-item["count"], item["ngram"]))
    return rows


def winner_baseline_inputs(
    winner_texts: Sequence[str],
    baseline_texts: Sequence[str],
    *,
    n: int = 1,
    minimum_total_count: int = 2,
    remove_stopwords: bool = True,
    language: str | None = None,
) -> dict[str, Any]:
    """Build comparable lexical inputs and a smoothed enrichment preview.

    The returned raw counts are the contract; the enrichment value is a deterministic
    preview which downstream analytics can replace without losing provenance.
    """

    winners = aggregate_ngrams(
        winner_texts,
        n=n,
        minimum_count=1,
        remove_stopwords=remove_stopwords,
        language=language,
    )
    baseline = aggregate_ngrams(
        baseline_texts,
        n=n,
        minimum_count=1,
        remove_stopwords=remove_stopwords,
        language=language,
    )
    winner_by_term = {row["ngram"]: row for row in winners}
    baseline_by_term = {row["ngram"]: row for row in baseline}
    winner_total = sum(row["count"] for row in winners)
    baseline_total = sum(row["count"] for row in baseline)
    vocabulary = sorted(set(winner_by_term) | set(baseline_by_term))
    vocabulary_size = max(1, len(vocabulary))
    rows: list[dict[str, Any]] = []
    for term in vocabulary:
        winner = winner_by_term.get(term)
        control = baseline_by_term.get(term)
        winner_count = int(winner["count"]) if winner else 0
        baseline_count = int(control["count"]) if control else 0
        if winner_count + baseline_count < minimum_total_count:
            continue
        winner_rate = (winner_count + 0.5) / (winner_total + 0.5 * vocabulary_size)
        baseline_rate = (baseline_count + 0.5) / (baseline_total + 0.5 * vocabulary_size)
        rows.append(
            {
                "ngram": term,
                "winner_count": winner_count,
                "baseline_count": baseline_count,
                "winner_document_frequency": winner["document_frequency"] if winner else 0,
                "baseline_document_frequency": control["document_frequency"] if control else 0,
                "winner_rate_smoothed": round(winner_rate, 8),
                "baseline_rate_smoothed": round(baseline_rate, 8),
                "log2_enrichment_preview": round(math.log2(winner_rate / baseline_rate), 6),
                "winner_examples": winner["examples"] if winner else [],
                "baseline_examples": control["examples"] if control else [],
            }
        )
    rows.sort(key=lambda item: (-item["log2_enrichment_preview"], item["ngram"]))
    return {
        "feature_version": FEATURE_VERSION,
        "n": n,
        "winner_document_count": len(winner_texts),
        "baseline_document_count": len(baseline_texts),
        "winner_token_count": winner_total,
        "baseline_token_count": baseline_total,
        "minimum_total_count": minimum_total_count,
        "terms": rows,
    }


def profile_fingerprint_inputs(
    profile_texts: Sequence[str],
    background_texts: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Use the same comparable contract for creator-vs-background analysis."""

    result = winner_baseline_inputs(profile_texts, background_texts, **kwargs)
    result["comparison"] = "profile_vs_background"
    return result


# Re-export the simple single-document helper from the common contract.
repeated_ngrams = ngram_frequencies
