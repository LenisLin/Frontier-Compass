from __future__ import annotations

from datetime import date

from frontier_compass.ranking.relevance import DEFAULT_RECOMMENDED_SCORE_THRESHOLD, RelevanceRanker
from frontier_compass.storage.schema import PaperRecord
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp


def test_strong_biomedical_cv_paper_outranks_generic_multimodal_cv_paper() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().rank(
        [
            _paper(
                identifier="generic",
                title="Multimodal vision-language pretraining for image understanding",
                summary="General-purpose multimodal foundation model for visual reasoning, segmentation, and benchmark transfer.",
                categories=("cs.CV", "cs.LG"),
                published=today,
            ),
            _paper(
                identifier="biomedical",
                title="Pathology-guided spatial transcriptomics inference",
                summary="Histopathology and microscopy model for spatial transcriptomics, single-cell tissue analysis, and perturbation studies.",
                categories=("cs.CV", "q-bio.GN"),
                published=today,
            ),
        ],
        profile,
        today=today,
    )

    assert ranked[0].paper.identifier == "biomedical"
    assert ranked[0].score > ranked[1].score
    assert ranked[1].facets["biomedical_keyword"] == 0.0
    assert ranked[1].facets["generic_cs_penalty"] == 0.1


def test_generic_cs_multimodal_paper_stays_below_recommendation_threshold() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="generic",
            title="Multimodal vision-language pretraining for image understanding",
            summary="General-purpose multimodal foundation model for visual reasoning, segmentation, and benchmark transfer.",
            categories=("cs.CV", "cs.LG"),
            published=today,
        ),
        profile,
        today=today,
    )

    assert ranked.score < DEFAULT_RECOMMENDED_SCORE_THRESHOLD
    assert ranked.facets["biomedical_keyword"] == 0.0
    assert ranked.reasons[0] == "broad ai terms: multimodal, foundation model"
    assert "broad cs penalty" in ranked.reasons[2]


def test_pathology_cv_paper_can_rank_high_with_strong_biomedical_evidence() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().score(
        _paper(
            identifier="pathology",
            title="Whole-slide pathology foundation for histopathology triage",
            summary="Histopathology and whole-slide microscopy modeling for pathology biomarkers in clinical tissue cohorts.",
            categories=("cs.CV",),
            published=today,
        ),
        profile,
        today=today,
    )

    assert ranked.score >= 0.7
    assert ranked.facets["biomedical_keyword"] > 0.0
    assert ranked.facets["generic_cs_penalty"] == 0.0


def test_qbio_category_support_still_helps_true_biomedical_paper() -> None:
    today = date(2026, 3, 24)
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = RelevanceRanker().rank(
        [
            _paper(
                identifier="cs-only",
                title="Genomics perturbation prioritization",
                summary="Genomics and perturbation modeling for transcriptomics-guided target discovery.",
                categories=("cs.LG",),
                published=today,
            ),
            _paper(
                identifier="qbio",
                title="Genomics perturbation prioritization",
                summary="Genomics and perturbation modeling for transcriptomics-guided target discovery.",
                categories=("cs.LG", "q-bio.GN"),
                published=today,
            ),
        ],
        profile,
        today=today,
    )

    assert ranked[0].paper.identifier == "qbio"
    assert ranked[0].facets["category"] > ranked[1].facets["category"]


def _paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    published: date,
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
    )
