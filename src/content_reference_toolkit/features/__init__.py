"""Deterministic feature engines for writing, carousels, and Reels."""

from .common import analyze_text, analyze_text_sources, compute_common_features
from .carousel import analyze_carousel, compute_carousel_features
from .lexical import aggregate_ngrams, profile_fingerprint_inputs, winner_baseline_inputs
from .reel import analyze_reel, compute_reel_features, normalize_word_timestamps

__all__ = [
    "aggregate_ngrams",
    "analyze_carousel",
    "analyze_reel",
    "analyze_text",
    "analyze_text_sources",
    "compute_carousel_features",
    "compute_common_features",
    "compute_reel_features",
    "normalize_word_timestamps",
    "profile_fingerprint_inputs",
    "winner_baseline_inputs",
]
