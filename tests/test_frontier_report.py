from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path

from frontier_compass.common.frontier_report import (
    IMAGING_THEME,
    PATHOLOGY_THEME,
    build_daily_frontier_report,
)
from frontier_compass.reporting.daily_brief import build_reviewer_shortlist
from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    RankedPaper,
    RecommendationExplanation,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp


def test_build_daily_frontier_report_summarizes_full_pool() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.30001v1",
            title="Sparse Autoencoders for Medical Imaging",
            summary="Medical imaging and radiology with interpretable sparse features.",
            categories=("cs.CV", "cs.LG"),
            score=0.92,
        ),
        _ranked_paper(
            identifier="2603.30002v1",
            title="Radiology Distillation for CT Cohorts",
            summary="Medical imaging workflow for CT radiology cohorts.",
            categories=("cs.CV",),
            score=0.89,
        ),
        _ranked_paper(
            identifier="2603.30003v1",
            title="Whole-slide Histopathology Reasoning",
            summary="Pathology and whole-slide microscopy pipeline for diagnostics.",
            categories=("cs.CV",),
            score=0.78,
        ),
        _ranked_paper(
            identifier="2603.30004v1",
            title="Single-cell Transcriptomics Atlas Integration",
            summary="Single-cell transcriptomics atlas modeling for genomics.",
            categories=("q-bio.GN", "cs.LG"),
            score=0.84,
        ),
    ]

    report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        total_fetched=9,
    )

    assert report.requested_date == date(2026, 3, 24)
    assert report.effective_date == date(2026, 3, 24)
    assert report.source == "arxiv"
    assert report.mode == BIOMEDICAL_LATEST_MODE
    assert report.requested_report_mode == "deterministic"
    assert report.report_mode == "deterministic"
    assert report.cost_mode == "zero-token"
    assert report.total_fetched == 9
    assert report.total_ranked == 4
    assert report.repeated_themes[0].label == IMAGING_THEME
    assert report.repeated_themes[0].count == 2
    assert any(signal.label == "medical imaging / radiology" for signal in report.salient_topics)
    assert report.field_highlights
    assert report.takeaways[0].startswith("Frontier view covers 4 ranked papers")


def test_frontier_report_field_highlights_are_broader_than_personalized_shortlist() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.31001v1",
            title="Clinical Tabular Learning for EHR Cohorts",
            summary="Clinical tabular modeling over patient EHR cohorts.",
            categories=("cs.LG",),
            score=0.95,
        ),
        _ranked_paper(
            identifier="2603.31002v1",
            title="Single-cell Atlas Alignment",
            summary="Single-cell transcriptomics atlas alignment for genomics.",
            categories=("q-bio.GN",),
            score=0.94,
        ),
        _ranked_paper(
            identifier="2603.31003v1",
            title="Whole-slide Histopathology Reasoning",
            summary="Pathology and whole-slide microscopy reasoning for diagnostics.",
            categories=("cs.CV",),
            score=0.70,
        ),
        _ranked_paper(
            identifier="2603.31004v1",
            title="Microscopy-guided Pathology Segmentation",
            summary="Pathology microscopy segmentation for whole-slide review.",
            categories=("cs.CV",),
            score=0.69,
        ),
        _ranked_paper(
            identifier="2603.31005v1",
            title="Pathology Report Grounding with Multimodal Models",
            summary="Pathology grounding over whole-slide microscopy with multimodal models.",
            categories=("cs.CV", "cs.AI"),
            score=0.68,
        ),
    ]

    shortlist, _ = build_reviewer_shortlist(ranked, max_items=2)
    report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        total_fetched=7,
    )

    shortlist_ids = {item.paper.identifier for item in shortlist}
    frontier_ids = {item.identifier for item in report.field_highlights}

    assert shortlist_ids == {"2603.31001v1", "2603.31002v1"}
    assert any(item.theme_label == PATHOLOGY_THEME for item in report.field_highlights)
    assert not frontier_ids.issubset(shortlist_ids)


def test_frontier_report_profile_relevant_highlights_keep_zotero_secondary() -> None:
    zotero_profile = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    zotero_profile.zotero_export_name = "sample_library.csl.json"
    zotero_profile.zotero_keywords = ("histopathology",)
    zotero_profile.zotero_concepts = ("whole-slide",)

    baseline_ranked = [
        _ranked_paper(
            identifier="2603.32001v1",
            title="Histopathology Retrieval Fixture",
            summary="Pathology and histopathology workflow for microscopy review.",
            categories=("cs.CV",),
            score=0.88,
        )
    ]
    zotero_ranked = [
        _ranked_paper(
            identifier="2603.32002v1",
            title="Whole-slide Histopathology Reasoning",
            summary="Whole-slide pathology workflow for microscopy review.",
            categories=("cs.CV",),
            score=0.89,
            explanation=RecommendationExplanation(
                total_score=0.89,
                baseline_keyword_hits=("pathology", "microscopy"),
                category_hits=("cs.cv",),
                zotero_keyword_hits=("histopathology",),
                zotero_concept_hits=("whole-slide",),
                zotero_effect="strong",
                zotero_active=True,
            ),
        )
    ]

    baseline_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in baseline_ranked],
        ranked_papers=baseline_ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=2,
    )
    zotero_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in zotero_ranked],
        ranked_papers=zotero_ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=2,
    )

    assert baseline_report.profile_relevant_highlights
    assert "Zotero signals" not in baseline_report.profile_relevant_highlights[0].why
    assert zotero_report.profile_relevant_highlights
    assert "Zotero signals" in zotero_report.profile_relevant_highlights[0].why


def test_daily_digest_round_trip_preserves_frontier_report_contract() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.33001v1",
            title="Medical Imaging Frontier Fixture",
            summary="Medical imaging and radiology frontier fixture.",
            categories=("cs.CV",),
            score=0.81,
        )
    ]
    frontier_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=1,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "cs.CV"),
        per_category_counts={"q-bio": 1, "cs.CV": 1},
        total_fetched=1,
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.frontier_report is not None
    assert restored.frontier_report.total_ranked == 1
    assert restored.frontier_report.field_highlights[0].title == "Medical Imaging Frontier Fixture"


def test_load_daily_digest_preserves_missing_frontier_report_for_legacy_cache(tmp_path: Path) -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            _ranked_paper(
                identifier="2603.34001v1",
                title="Legacy Cache Frontier Fixture",
                summary="Medical imaging legacy cache fixture.",
                categories=("cs.CV",),
                score=0.8,
            )
        ],
        searched_categories=("q-bio", "cs.CV"),
        per_category_counts={"q-bio": 1, "cs.CV": 1},
        total_fetched=1,
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    payload = digest.to_mapping()
    payload.pop("frontier_report", None)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = FrontierCompassApp().load_daily_digest(cache_path)

    assert loaded.frontier_report is None
    assert "frontier_report" not in json.loads(cache_path.read_text(encoding="utf-8"))


def test_html_report_embeds_machine_readable_run_summary() -> None:
    ranked = [
        _ranked_paper(
            identifier="2603.35001v1",
            title="Embedded summary fixture",
            summary="Embedded summary fixture.",
            categories=("q-bio.GN",),
            score=0.84,
        )
    ]
    frontier_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="multisource",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=1,
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=frontier_report,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                cache_status="same-day-cache",
            ),
        ),
        run_timings=RunTimings(
            network_seconds=0.8,
            parse_seconds=0.2,
            rank_seconds=0.3,
            report_seconds=0.4,
            total_seconds=1.7,
        ),
        source_counts={"arxiv": 1, "biorxiv": 0},
        total_fetched=1,
        report_status="partial",
        report_error="bioRxiv empty for the current run.",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    html = HtmlReportBuilder().render_daily_digest(digest, acquisition_status_label="fresh source fetch")

    match = re.search(
        r'<script id="frontier-compass-run-summary" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    payload = json.loads(unescape(match.group(1)))

    assert payload["frontier_report_present"] is True
    assert payload["report_artifact_aligned"] is True
    assert payload["report_status"] == "partial"
    assert payload["source_run_stats"][1]["source"] == "biorxiv"
    assert payload["source_run_stats"][1]["status"] == "empty"
    assert payload["source_run_stats"][1]["cache_status"] == "same-day-cache"
    assert payload["run_timings"]["report_seconds"] == 0.4
    assert payload["run_timings"]["total_seconds"] == 1.7


def _ranked_paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    score: float,
    explanation: RecommendationExplanation | None = None,
) -> RankedPaper:
    published = date(2026, 3, 24)
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
        reasons=("deterministic fixture",),
        recommendation_summary="Deterministic summary.",
        explanation=explanation
        or RecommendationExplanation(
            total_score=score,
            baseline_keyword_hits=("biomedical",),
            category_hits=tuple(category.lower() for category in categories[:2]),
            zotero_effect="inactive",
            zotero_active=False,
        ),
    )
