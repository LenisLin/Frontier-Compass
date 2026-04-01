from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from frontier_compass.exploration.selector import (
    daily_exploration_note,
    resolve_daily_exploration_picks,
    select_daily_exploration_picks,
)
from frontier_compass.reporting.daily_brief import build_reviewer_shortlist
from frontier_compass.storage.schema import DailyDigest, ExplorationPolicy, PaperRecord, RankedPaper
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zotero" / "sample_library.csl.json"


def test_select_daily_exploration_picks_stays_outside_main_shortlist_and_keeps_main_shortlist_stable() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = _baseline_ranked_pool()

    shortlist_before, _shortlist_title = build_reviewer_shortlist(ranked, max_items=8)
    exploration_picks = select_daily_exploration_picks(ranked, profile)
    shortlist_after, _shortlist_title = build_reviewer_shortlist(ranked, max_items=8)

    shortlist_ids_before = [item.paper.identifier for item in shortlist_before]
    shortlist_ids_after = [item.paper.identifier for item in shortlist_after]
    exploration_ids = [item.paper.identifier for item in exploration_picks]

    assert shortlist_ids_before == shortlist_ids_after
    assert exploration_ids[0] == "candidate-protein"
    assert set(exploration_ids) == {"candidate-protein", "candidate-microscopy", "candidate-single-cell"}
    assert not (set(exploration_ids) & set(shortlist_ids_before))


def test_select_daily_exploration_picks_prefers_biomedical_adjacent_papers_over_generic_cs() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = _baseline_ranked_pool()

    exploration_picks = select_daily_exploration_picks(ranked, profile)
    exploration_ids = {item.paper.identifier for item in exploration_picks}

    assert "candidate-generic-cs" not in exploration_ids
    assert exploration_ids >= {"candidate-protein", "candidate-microscopy", "candidate-single-cell"}


def test_select_daily_exploration_picks_with_zotero_profile_penalizes_core_overlap() -> None:
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        export_path=FIXTURE_PATH,
    )
    ranked = _zotero_ranked_pool()

    exploration_picks = select_daily_exploration_picks(ranked, profile)
    exploration_ids = [item.paper.identifier for item in exploration_picks]

    assert exploration_ids[0] == "candidate-protein"
    assert set(exploration_ids) == {"candidate-protein", "candidate-microscopy", "candidate-single-cell"}
    assert "candidate-zotero-core" not in exploration_ids
    note = daily_exploration_note(exploration_picks[0], ranked_papers=ranked, profile=profile)
    assert "still biomedical-adjacent" in note
    assert "main shortlist" in note


def test_resolve_daily_exploration_picks_backfills_legacy_digest() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=profile,
        ranked=_baseline_ranked_pool(),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV"),
        per_category_counts={"q-bio": 4, "q-bio.GN": 3, "cs.CV": 6},
        total_fetched=13,
    )

    resolved = resolve_daily_exploration_picks(digest)

    resolved_ids = [item.paper.identifier for item in resolved]
    assert resolved_ids[0] == "candidate-protein"
    assert set(resolved_ids) == {"candidate-protein", "candidate-microscopy", "candidate-single-cell"}


def test_select_daily_exploration_picks_honors_explicit_policy_limit_and_note() -> None:
    profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    ranked = _baseline_ranked_pool()
    policy = ExplorationPolicy(
        label="daily-adjacent-tight-v1",
        shortlist_size=8,
        max_items=2,
        max_per_theme=1,
        min_score=0.35,
        min_biomedical_keyword=0.13,
        notes="Tighter deterministic exploration lane for testing.",
    )

    exploration_picks = select_daily_exploration_picks(ranked, profile, policy=policy)
    note = daily_exploration_note(
        exploration_picks[0],
        ranked_papers=ranked,
        profile=profile,
        policy=policy,
    )

    assert [item.paper.identifier for item in exploration_picks] == [
        "candidate-protein",
        "candidate-single-cell",
    ]
    assert "daily-adjacent-tight-v1" in note
    assert "at most 2 picks" in note


def _baseline_ranked_pool() -> list[RankedPaper]:
    return [
        _ranked_paper(
            identifier="main-imaging-1",
            title="Sparse Autoencoders for Medical Imaging",
            summary="Medical imaging workflow for MRI and CT review in hospital cohorts.",
            categories=("cs.CV",),
            score=0.92,
            biomedical_keyword=0.55,
        ),
        _ranked_paper(
            identifier="main-pathology-1",
            title="Whole-slide Histopathology Reasoning",
            summary="Histopathology and whole-slide microscopy pipeline for tissue diagnostics.",
            categories=("cs.CV",),
            score=0.91,
            biomedical_keyword=0.55,
        ),
        _ranked_paper(
            identifier="main-clinical-1",
            title="Clinical Tabular Learning for EHR Cohorts",
            summary="Clinical patient cohort modeling over biomedical tabular datasets.",
            categories=("cs.LG",),
            score=0.90,
            biomedical_keyword=0.45,
        ),
        _ranked_paper(
            identifier="main-genomics-1",
            title="Single-cell Transcriptomics Atlas Integration",
            summary="Single-cell transcriptomics workflow for atlas integration.",
            categories=("q-bio.GN", "cs.LG"),
            score=0.89,
            biomedical_keyword=0.60,
        ),
        _ranked_paper(
            identifier="main-imaging-2",
            title="Radiology Distillation for CT Cohorts",
            summary="Radiology and CT pipeline for medical imaging.",
            categories=("cs.CV",),
            score=0.88,
            biomedical_keyword=0.45,
        ),
        _ranked_paper(
            identifier="main-pathology-2",
            title="Microscopy-guided Pathology Segmentation",
            summary="Microscopy and pathology segmentation for whole-slide review.",
            categories=("cs.CV",),
            score=0.87,
            biomedical_keyword=0.45,
        ),
        _ranked_paper(
            identifier="main-protein-1",
            title="Protein Structure Priors for Biomolecular Discovery",
            summary="Protein biomolecular priors for therapeutic discovery.",
            categories=("q-bio.BM", "cs.LG"),
            score=0.86,
            biomedical_keyword=0.35,
        ),
        _ranked_paper(
            identifier="main-general-1",
            title="General Biomedical Modeling Notes",
            summary="Biomedical methods for translational studies.",
            categories=("q-bio.QM",),
            score=0.85,
            biomedical_keyword=0.20,
        ),
        _ranked_paper(
            identifier="candidate-protein",
            title="Protein Transfer for Enzyme Screening",
            summary="Protein biomolecular enzyme screening for therapeutic discovery.",
            categories=("q-bio.BM", "cs.LG"),
            score=0.44,
            biomedical_keyword=0.33,
        ),
        _ranked_paper(
            identifier="candidate-microscopy",
            title="Fluorescence Microscopy Unmixing with Self-Supervision",
            summary="Fluorescence microscopy workflow for tissue imaging and spectral separation.",
            categories=("cs.CV", "cs.AI"),
            score=0.43,
            biomedical_keyword=0.33,
        ),
        _ranked_paper(
            identifier="candidate-single-cell",
            title="Statistical Transport for Single-cell Dynamics",
            summary="Single-cell dynamics modeling for cell state transitions.",
            categories=("stat.ML", "cs.LG"),
            score=0.42,
            biomedical_keyword=0.33,
        ),
        _ranked_paper(
            identifier="candidate-generic-cs",
            title="Benchmarking Foundation Models for Code Generation",
            summary="Generic benchmark tuning for code generation and graph pretraining.",
            categories=("cs.LG",),
            score=0.41,
            biomedical_keyword=0.0,
            generic_cs_penalty=0.10,
        ),
    ]


def _zotero_ranked_pool() -> list[RankedPaper]:
    return _baseline_ranked_pool() + [
        _ranked_paper(
            identifier="candidate-zotero-core",
            title="Spatial Transcriptomics and Digital Pathology Fusion",
            summary="Spatial transcriptomics and digital pathology models for tumor microenvironment analysis.",
            categories=("q-bio.GN", "cs.CV"),
            score=0.44,
            biomedical_keyword=0.45,
        )
    ]


def _ranked_paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    score: float,
    biomedical_keyword: float,
    generic_cs_penalty: float = 0.0,
    published: date = date(2026, 3, 24),
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
            url=f"https://arxiv.org/abs/{identifier}",
        ),
        score=score,
        reasons=("deterministic exploration fixture",),
        facets={
            "biomedical_keyword": biomedical_keyword,
            "generic_cs_penalty": generic_cs_penalty,
        },
        recommendation_summary="Deterministic exploration selector fixture.",
    )
