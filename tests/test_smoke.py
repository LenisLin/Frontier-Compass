from datetime import date

from frontier_compass import __version__
from frontier_compass.ranking.relevance import DEFAULT_RECOMMENDED_SCORE_THRESHOLD, RelevanceRanker
from frontier_compass.storage.schema import PaperRecord, UserInterestProfile
from frontier_compass.ui import BIOMEDICAL_DAILY_MODE, BIOMEDICAL_LATEST_MODE, FrontierCompassApp


def test_package_imports_and_demo_pipeline() -> None:
    assert __version__ == "0.1.0"
    app = FrontierCompassApp()
    result = app.build_demo_report(limit=3)

    assert len(result.ranked) >= 3
    assert len(result.selected) == 3
    assert result.profile.keywords
    assert "FrontierCompass Report" in result.html
    assert result.ranked[0].recommendation_summary


def test_relevance_ranker_generates_recommendation_summary() -> None:
    paper = PaperRecord(
        source="arxiv",
        identifier="2603.19236v1",
        title="Retrieval Agents for Science",
        summary="Agentic ranking for frontier papers.",
        authors=("A Researcher",),
        categories=("cs.IR", "cs.CL"),
        published=date(2026, 3, 23),
        updated=date(2026, 3, 23),
        url="https://arxiv.org/abs/2603.19236",
    )
    profile = UserInterestProfile(
        keywords=("retrieval", "ranking", "agentic"),
        category_weights={"cs.ir": 0.4, "cs.cl": 0.3},
        notes="FrontierCompass daily test profile.",
    )

    ranked = RelevanceRanker().score(paper, profile, today=date(2026, 3, 23))
    assert ranked.score > 0.5
    summary = ranked.recommendation_summary.lower()
    assert "surfaced for" in summary
    assert "in brief:" in summary
    assert "retrieval" in summary or "cs.ir" in summary
    assert "posted 2026-03-23 by a researcher" in summary


def test_daily_profile_prefers_biomedical_papers() -> None:
    profile = FrontierCompassApp.daily_profile("q-bio")
    today = date(2026, 3, 23)

    biomedical_paper = PaperRecord(
        source="arxiv",
        identifier="2603.20001v1",
        title="Single-cell transcriptomics and perturbation modeling",
        summary="Bioinformatics workflow for single-cell genomics, transcriptomics, and perturbation analysis.",
        authors=("A Biologist",),
        categories=("q-bio.GN", "q-bio.QM"),
        published=today,
        url="https://arxiv.org/abs/2603.20001",
    )
    generic_ml_paper = PaperRecord(
        source="arxiv",
        identifier="2603.20002v1",
        title="Multimodal vision-language pretraining for image understanding",
        summary="General-purpose multimodal foundation model for visual reasoning and benchmark transfer.",
        authors=("S Engineer",),
        categories=("cs.CV", "cs.LG"),
        published=today,
        url="https://arxiv.org/abs/2603.20002",
    )

    ranked = RelevanceRanker().rank([generic_ml_paper, biomedical_paper], profile, today=today)
    assert ranked[0].paper.identifier == biomedical_paper.identifier
    assert ranked[1].score < DEFAULT_RECOMMENDED_SCORE_THRESHOLD
    assert ranked[1].facets["biomedical_keyword"] == 0.0
    assert "biomedical" in profile.notes.lower()


def test_biomedical_daily_profile_prefers_biomedical_bundle_papers() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_DAILY_MODE)
    today = date(2026, 3, 24)

    biomedical_paper = PaperRecord(
        source="arxiv",
        identifier="2603.21001v1",
        title="Single-cell perturbation models for transcriptomics",
        summary="Bioinformatics workflow for genomics, transcriptomics, and perturbation analysis.",
        authors=("A Biologist",),
        categories=("q-bio.GN", "q-bio.QM"),
        published=today,
        url="https://arxiv.org/abs/2603.21001",
    )
    generic_ml_paper = PaperRecord(
        source="arxiv",
        identifier="2603.21002v1",
        title="Optimization methods for benchmark tuning",
        summary="A generic machine learning paper with little biomedical signal.",
        authors=("S Engineer",),
        categories=("cs.LG",),
        published=today,
        url="https://arxiv.org/abs/2603.21002",
    )

    ranked = RelevanceRanker().rank([generic_ml_paper, biomedical_paper], profile, today=today)
    assert ranked[0].paper.identifier == biomedical_paper.identifier
    assert ranked[0].facets["generic_cs_penalty"] == 0.0
    assert ranked[1].score < DEFAULT_RECOMMENDED_SCORE_THRESHOLD


def test_biomedical_latest_profile_surfaces_explicit_biomedical_evidence_in_summary() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    today = date(2026, 3, 24)
    paper = PaperRecord(
        source="arxiv",
        identifier="2603.22821v1",
        title="Spatial transcriptomics from pathology images",
        summary="Histopathology model for spatial transcriptomics and microscopy-driven tissue analysis.",
        authors=("A Scientist",),
        categories=("cs.CV", "q-bio.GN"),
        published=today,
        updated=today,
        url="https://arxiv.org/abs/2603.22821",
    )

    ranked = RelevanceRanker().score(paper, profile, today=today)

    assert ranked.score >= 0.7
    assert ranked.reasons[0].startswith("biomedical evidence:")
    assert "spatial transcriptomics" in ranked.recommendation_summary.lower()
