from __future__ import annotations

from datetime import date

from frontier_compass.reporting.daily_brief import (
    GENOMICS_THEME,
    IMAGING_THEME,
    PATHOLOGY_THEME,
    build_daily_brief,
    build_reviewer_shortlist,
    filter_ranked_papers,
    summarize_category_counts,
    theme_label_for_ranked_paper,
)
from frontier_compass.storage.schema import PaperRecord, RankedPaper
from frontier_compass.ui import FrontierCompassApp


def test_build_daily_brief_summarizes_shown_papers_and_signals() -> None:
    profile = FrontierCompassApp.daily_profile("q-bio")
    ranked = [
        _ranked_paper(
            identifier="2603.20001v1",
            title="Single-cell multimodal atlas integration",
            summary="A genomics and transcriptomics workflow for single-cell atlases.",
            categories=("q-bio.GN", "q-bio.QM"),
            score=0.81,
        ),
        _ranked_paper(
            identifier="2603.20002v1",
            title="Genomics representation learning for perturbation screens",
            summary="Bioinformatics approach for genomics and perturbation response prediction.",
            categories=("q-bio.GN", "cs.LG"),
            score=0.67,
        ),
        _ranked_paper(
            identifier="2603.20003v1",
            title="General optimization note",
            summary="An optimization baseline with little biomedical signal.",
            categories=("math.OC",),
            score=0.22,
        ),
    ]

    brief = build_daily_brief(profile, ranked[:2], total_ranked=len(ranked))

    assert brief.shown_count == 2
    assert brief.total_ranked == 3
    assert brief.recommended_count == 2
    assert brief.average_score == 0.74
    assert brief.top_theme_signals[0].label == GENOMICS_THEME
    assert brief.top_category_signals[0].label == "q-bio"
    assert brief.top_keyword_signals[0].label == "genomics"
    assert {signal.label for signal in brief.top_keyword_signals} >= {"genomics", "transcriptomics"}
    assert len(brief.takeaways) == 4
    assert brief.takeaways[0].startswith("Digest shortlist shows 2 of 3 ranked papers for this run")
    assert brief.takeaways[1].startswith("Repeated themes in the shortlist")
    assert GENOMICS_THEME in brief.takeaways[3]


def test_build_reviewer_shortlist_balances_dense_imaging_cluster() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.21001v1",
            title="Sparse Autoencoders for Medical Imaging",
            summary="Medical imaging for MRI and CT review.",
            categories=("cs.CV",),
            score=0.91,
        ),
        _ranked_paper(
            identifier="2603.21002v1",
            title="Radiology Distillation for CT Cohorts",
            summary="Radiology and CT pipeline for medical imaging.",
            categories=("cs.CV",),
            score=0.9,
        ),
        _ranked_paper(
            identifier="2603.21003v1",
            title="Zero-shot Chest Scan Segmentation",
            summary="Medical imaging benchmark for chest scan segmentation.",
            categories=("cs.CV",),
            score=0.89,
        ),
        _ranked_paper(
            identifier="2603.21004v1",
            title="Single-cell Transcriptomics Atlas Integration",
            summary="Genomics and transcriptomics workflow for a single-cell atlas.",
            categories=("q-bio.GN", "cs.LG"),
            score=0.88,
        ),
        _ranked_paper(
            identifier="2603.21005v1",
            title="Whole-slide Histopathology Reasoning",
            summary="Pathology and whole-slide microscopy pipeline for diagnostics.",
            categories=("cs.CV",),
            score=0.87,
        ),
        _ranked_paper(
            identifier="2603.21006v1",
            title="Clinical Tabular Learning for EHR Cohorts",
            summary="Clinical tabular modeling over patient EHR cohorts.",
            categories=("cs.LG",),
            score=0.86,
        ),
    ]

    shortlist, shortlist_title = build_reviewer_shortlist(ranked, max_items=5)
    theme_counts: dict[str, int] = {}
    for item in shortlist:
        theme_label = theme_label_for_ranked_paper(item)
        theme_counts[theme_label] = theme_counts.get(theme_label, 0) + 1

    assert shortlist_title == "Top recommendations"
    assert [item.paper.identifier for item in shortlist] == [
        "2603.21001v1",
        "2603.21002v1",
        "2603.21004v1",
        "2603.21005v1",
        "2603.21006v1",
    ]
    assert theme_counts[IMAGING_THEME] == 2
    assert GENOMICS_THEME in theme_counts
    assert PATHOLOGY_THEME in theme_counts


def test_build_reviewer_shortlist_falls_back_to_score_order_when_single_theme_present() -> None:
    ranked = [
        _ranked_paper(
            identifier=f"2603.2200{index}v1",
            title=f"Medical imaging paper {index}",
            summary="Medical imaging benchmark using MRI and CT.",
            categories=("cs.CV",),
            score=0.92 - (index * 0.01),
        )
        for index in range(1, 6)
    ]

    shortlist, shortlist_title = build_reviewer_shortlist(ranked, max_items=4)

    assert shortlist_title == "Top recommendations"
    assert [item.paper.identifier for item in shortlist] == [
        "2603.22001v1",
        "2603.22002v1",
        "2603.22003v1",
        "2603.22004v1",
    ]
    assert {theme_label_for_ranked_paper(item) for item in shortlist} == {IMAGING_THEME}


def test_filter_ranked_papers_applies_threshold_recommended_only_and_sort() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.20001v1",
            title="Single-cell atlas integration",
            summary="Single-cell atlas modeling.",
            categories=("q-bio.GN",),
            score=0.82,
            published=date(2026, 3, 23),
        ),
        _ranked_paper(
            identifier="2603.20002v1",
            title="Genomics perturbation model",
            summary="Genomics ranking workflow.",
            categories=("q-bio.GN",),
            score=0.51,
            published=date(2026, 3, 22),
        ),
        _ranked_paper(
            identifier="2603.20003v1",
            title="Older watchlist paper",
            summary="Sparse biology signal.",
            categories=("q-bio.BM",),
            score=0.44,
            published=date(2026, 3, 24),
        ),
    ]

    recommended = filter_ranked_papers(ranked, recommended_only=True)
    assert [item.paper.identifier for item in recommended] == ["2603.20001v1", "2603.20002v1"]

    newest = filter_ranked_papers(ranked, min_score=0.4, max_items=2, recommended_only=False, sort_mode="newest")
    assert [item.paper.identifier for item in newest] == ["2603.20003v1", "2603.20001v1"]


def test_summarize_category_counts_preserves_search_order_and_zeroes() -> None:
    labels = summarize_category_counts(
        ("q-bio", "q-bio.GN", "q-bio.QM"),
        {"q-bio.GN": 2, "q-bio": 0},
    )

    assert labels == ("q-bio: 0", "q-bio.GN: 2", "q-bio.QM: 0")


def _ranked_paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    score: float,
    published: date = date(2026, 3, 23),
) -> RankedPaper:
    return RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier=identifier,
            title=title,
            summary=summary,
            authors=("A Researcher",),
            categories=categories,
            published=published,
            updated=published,
            url=f"https://arxiv.org/abs/{identifier.split('v', 1)[0]}",
        ),
        score=score,
        recommendation_summary="Deterministic summary.",
    )
