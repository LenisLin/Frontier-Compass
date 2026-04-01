from __future__ import annotations

from datetime import date
from pathlib import Path

from frontier_compass.ranking.relevance import (
    RelevanceRanker,
    explanation_detail_lines,
    explanation_summary_line,
    recommendation_explanation_for_ranked_paper,
)
from frontier_compass.storage.schema import PaperRecord, RankedPaper
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zotero" / "sample_library.csl.json"


def test_recommendation_explanation_tracks_weighted_score_components() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="biomedical",
            title="Pathology-guided spatial transcriptomics inference",
            summary="Histopathology and microscopy model for spatial transcriptomics, single-cell tissue analysis, and perturbation studies.",
            categories=("cs.CV", "q-bio.GN"),
            published=today,
        ),
        profile,
        today=today,
    )

    explanation = ranked.explanation

    assert explanation is not None
    assert explanation.zotero_effect == "inactive"
    recomposed = round(
        explanation.baseline_contribution
        + explanation.category_contribution
        + explanation.recency_contribution
        + explanation.zotero_bonus_contribution
        - explanation.generic_cs_penalty_contribution,
        4,
    )
    assert abs(explanation.total_score - recomposed) <= 0.0001


def test_recommendation_explanation_marks_no_zotero_augmentation_as_inactive() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="baseline-only",
            title="Medical imaging representation learning for CT cohorts",
            summary="Medical imaging model for CT and MRI cohorts.",
            categories=("cs.CV",),
            published=today,
        ),
        profile,
        today=today,
    )

    explanation = ranked.explanation

    assert explanation is not None
    assert explanation.zotero_active is False
    assert explanation.zotero_effect == "inactive"


def test_recommendation_explanation_marks_active_but_no_overlap_as_none() -> None:
    today = date(2026, 3, 24)
    baseline = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    profile = ZoteroProfileBuilder().build_augmented_profile(baseline, export_path=FIXTURE_PATH)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="no-overlap",
            title="Interpretable medical imaging representation learning",
            summary="Medical imaging model for CT and MRI cohorts.",
            categories=("cs.CV",),
            published=today,
        ),
        profile,
        today=today,
    )

    explanation = ranked.explanation

    assert explanation is not None
    assert explanation.zotero_active is True
    assert explanation.zotero_bonus_contribution == 0.0
    assert explanation.zotero_effect == "none"


def test_recommendation_explanation_distinguishes_mild_and_strong_zotero_overlap() -> None:
    today = date(2026, 3, 24)
    baseline = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    profile = ZoteroProfileBuilder().build_augmented_profile(baseline, export_path=FIXTURE_PATH)
    ranker = RelevanceRanker()

    mild_ranked = ranker.score(
        _paper(
            identifier="mild",
            title="Single-cell transport dynamics",
            summary="Single-cell modeling for cell state trajectories.",
            categories=("q-bio.GN", "cs.LG"),
            published=today,
        ),
        profile,
        today=today,
    )
    strong_ranked = ranker.score(
        _paper(
            identifier="strong",
            title="Spatial transcriptomics from digital pathology images",
            summary="Histopathology and whole-slide tissue modeling for tumor microenvironment analysis.",
            categories=("q-bio.GN", "cs.CV"),
            published=today,
        ),
        profile,
        today=today,
    )

    assert mild_ranked.explanation is not None
    assert strong_ranked.explanation is not None
    assert mild_ranked.explanation.zotero_effect == "mild"
    assert strong_ranked.explanation.zotero_effect == "strong"
    assert mild_ranked.explanation.zotero_bonus_contribution < 0.05
    assert strong_ranked.explanation.zotero_bonus_contribution >= 0.05


def test_legacy_ranked_paper_without_explanation_can_be_backfilled() -> None:
    today = date(2026, 3, 24)
    baseline = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    profile = ZoteroProfileBuilder().build_augmented_profile(baseline, export_path=FIXTURE_PATH)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="legacy",
            title="Spatial transcriptomics from digital pathology images",
            summary="Histopathology and whole-slide tissue modeling for tumor microenvironment analysis.",
            categories=("q-bio.GN", "cs.CV"),
            published=today,
        ),
        profile,
        today=today,
    )

    payload = ranked.to_mapping()
    payload.pop("explanation", None)
    legacy_ranked = RankedPaper.from_mapping(payload)
    explanation = recommendation_explanation_for_ranked_paper(legacy_ranked, profile=profile)

    assert legacy_ranked.explanation is None
    assert explanation.zotero_effect == "strong"
    assert explanation.category_hits == ("q-bio", "q-bio.gn", "cs.cv")
    assert "transcriptomics" in explanation.baseline_keyword_hits
    assert "pathology" in explanation.baseline_keyword_hits


def test_recommendation_explanation_tracks_zotero_retrieval_support_separately_from_zotero_bonus() -> None:
    today = date(2026, 3, 24)
    baseline = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    profile = ZoteroProfileBuilder().build_augmented_profile(baseline, export_path=FIXTURE_PATH)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="retrieval-support",
            title="Spatial transcriptomics from digital pathology images",
            summary="Histopathology and whole-slide tissue modeling for tumor microenvironment analysis.",
            categories=("q-bio.GN", "cs.CV"),
            published=today,
            source_metadata={
                "retrieval_support": [
                    {
                        "label": "zotero-omics-pathology",
                        "origin": "zotero",
                        "terms": ["spatial transcriptomics", "digital pathology"],
                    }
                ]
            },
        ),
        profile,
        today=today,
    )

    explanation = ranked.explanation

    assert explanation is not None
    assert explanation.retrieval_support_origin == "zotero"
    assert explanation.retrieval_support_labels == ("zotero-omics-pathology",)
    assert explanation.retrieval_support_terms == ("spatial transcriptomics", "digital pathology")
    assert "retrieval: Zotero hint" in explanation_summary_line(explanation)
    assert any("Candidate expansion: surfaced by Zotero retrieval hint" in line for line in explanation_detail_lines(explanation))


def _paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    published: date,
    source_metadata: dict | None = None,
) -> PaperRecord:
    return PaperRecord(
        source="arxiv",
        identifier=identifier,
        title=title,
        summary=summary,
        authors=("A Researcher",),
        categories=categories,
        published=published,
        updated=published,
        url=f"https://arxiv.org/abs/{identifier}",
        source_metadata=source_metadata or {},
    )
