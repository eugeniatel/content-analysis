from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from content_reference_toolkit.analytics import (
    analyze_winners,
    bootstrap_median_ratio,
)


CAPTURED_AT = datetime(2026, 7, 1, tzinfo=timezone.utc)


def piece(identifier: str, media_type: str, *, age_days: int = 20) -> dict:
    return {
        "id": identifier,
        "profile_id": "profile-1",
        "platform": "instagram",
        "media_type": media_type,
        "published_at": (CAPTURED_AT - timedelta(days=age_days)).isoformat(),
    }


def observation(
    identifier: str,
    value: float,
    *,
    basis: str = "lifetime",
    unit: str = "count",
) -> dict:
    return {
        "id": f"obs-{identifier}",
        "piece_id": identifier,
        "metric_name": "views",
        "metric_value": value,
        "metric_basis": basis,
        "metric_unit": unit,
        "source": "public_metadata",
        "captured_at": CAPTURED_AT.isoformat(),
    }


def finding(result: dict, feature: str) -> dict:
    return next(item for item in result["findings"] if item["feature"] == feature)


def test_reel_format_enrichment_uses_winners_and_profile_baseline() -> None:
    pieces = []
    observations = []
    for index in range(40):
        identifier = f"post-{index:02}"
        media_type = "reel" if index < 20 else "carousel"
        pieces.append(piece(identifier, media_type))
        if index < 10:
            value = 1_000 - index
        elif index < 20:
            value = 300 - index
        else:
            value = 100 - index
        observations.append(observation(identifier, value))

    result = analyze_winners(
        pieces,
        observations,
        objective="views",
        top_k=10,
        categorical_fields=["format"],
        continuous_fields=[],
        bootstrap_iterations=300,
        seed=7,
    )

    reel = finding(result, "format=reel")
    assert result["format_distribution"]["winners"]["reel"] == {
        "count": 10,
        "prevalence": 1.0,
    }
    assert result["format_distribution"]["eligible_corpus"]["reel"] == {
        "count": 20,
        "prevalence": 0.5,
    }
    assert reel["effect"] == {"value": 2.0, "unit": "winner_enrichment_ratio"}
    assert reel["winner_prevalence"] == 1.0
    assert reel["baseline_prevalence"] == 0.5
    assert reel["status"] == "supported"
    assert "associated" in reel["claim"]
    assert reel["caveat"].startswith("Association")
    assert reel["sample"]["feature"] == 20
    assert reel["sample"]["control"] == 20
    assert reel["evidence_refs"][0]["objective_value"] == 1_000


def test_duration_quantile_bands_preserve_raw_values_and_find_long_band() -> None:
    pieces = []
    observations = []
    features = {}
    for index in range(30):
        identifier = f"reel-{index:02}"
        pieces.append(piece(identifier, "reel"))
        if index < 10:
            duration = 10 + index
            views = 100 + index
        elif index < 20:
            duration = 40 + (index - 10)
            views = 200 + index
        else:
            duration = 80 + (index - 20)
            views = 1_000 + index
        features[identifier] = {"duration_sec": duration}
        observations.append(observation(identifier, views))

    result = analyze_winners(
        pieces,
        observations,
        objective="views",
        features=features,
        top_k=10,
        categorical_fields=[],
        continuous_fields=["duration_sec"],
        continuous_band_count=3,
        bootstrap_iterations=400,
        seed=19,
    )

    duration_findings = [
        item for item in result["findings"] if item["feature"].startswith("duration_sec:")
    ]
    assert len(duration_findings) == 3
    strongest = max(duration_findings, key=lambda item: item["effect"]["value"] or 0)
    assert strongest["band"]["low"] > 49
    assert strongest["sample"]["feature"] == 10
    assert strongest["sample"]["control"] == 20
    assert strongest["control_match"] == "format_publication_age"
    assert strongest["effect"]["value"] > 4
    assert strongest["status"] == "supported"
    assert sorted(strongest["raw_values"]["feature"]) == list(range(80, 90))
    assert len(strongest["raw_values"]["feature_objective"]) == 10
    assert len(strongest["relative_percentiles"]["feature"]) == 10
    assert strongest["interval"]["seed"] >= 0


def test_unit_mismatch_is_excluded_instead_of_blended() -> None:
    pieces = [piece(f"post-{index}", "reel") for index in range(12)]
    observations = [
        observation(f"post-{index}", 100 + index, basis="lifetime", unit="count")
        for index in range(6)
    ] + [
        observation(f"post-{index}", 0.4 + index / 100, basis="lifetime", unit="rate")
        for index in range(6, 12)
    ]

    result = analyze_winners(
        pieces,
        observations,
        objective="views",
        metric_basis="lifetime",
        metric_unit="count",
        top_k=3,
        categorical_fields=[],
        continuous_fields=[],
    )

    assert result["coverage"]["eligible_pieces"] == 6
    assert result["coverage"]["excluded"]["unit_mismatch"] == 6
    assert result["objective"] == {
        "name": "views",
        "metric_basis": "lifetime",
        "metric_unit": "count",
    }
    assert {row["piece_id"] for row in result["ranked"]} == {
        f"post-{index}" for index in range(6)
    }
    assert all(row["value"] >= 100 for row in result["ranked"])


def test_small_groups_emit_insufficient_evidence_findings() -> None:
    pieces = [
        piece("reel-1", "reel"),
        piece("reel-2", "reel"),
        piece("carousel-1", "carousel"),
        piece("carousel-2", "carousel"),
    ]
    observations = [
        observation("reel-1", 400),
        observation("reel-2", 300),
        observation("carousel-1", 200),
        observation("carousel-2", 100),
    ]

    result = analyze_winners(
        pieces,
        observations,
        objective="views",
        top_k=2,
        categorical_fields=["format"],
        continuous_fields=[],
        bootstrap_iterations=100,
    )

    assert result["findings"]
    assert all(item["status"] == "insufficient_evidence" for item in result["findings"])
    assert all(item["confidence"] == "insufficient" for item in result["findings"])
    assert all("insufficient evidence" in item["claim"].casefold() for item in result["findings"])


def test_bootstrap_interval_is_deterministic_for_seed() -> None:
    first = bootstrap_median_ratio(
        [100, 110, 120, 130, 140, 150],
        [40, 50, 60, 70, 80, 90],
        iterations=500,
        seed=12345,
    )
    second = bootstrap_median_ratio(
        [100, 110, 120, 130, 140, 150],
        [40, 50, 60, 70, 80, 90],
        iterations=500,
        seed=12345,
    )

    assert first == second
    assert first is not None
    assert first["estimate"] == pytest.approx(125 / 65)
    assert first["low"] <= first["estimate"] <= first["high"]
    assert first["neutral"] == 1.0
    assert first["valid_iterations"] == 500
