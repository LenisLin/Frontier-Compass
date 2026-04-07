from __future__ import annotations

from dataclasses import replace
import json
from datetime import date, datetime, timezone
from pathlib import Path

from frontier_compass.common.source_bundles import (
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
    SOURCE_BUNDLE_BIOMEDICAL,
)
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    ProfileBasis,
    RankedPaper,
    RequestWindow,
    RequestWindowFailure,
    RunTimings,
    SourceRunStats,
    UserInterestProfile,
)
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, BIOMEDICAL_MULTISOURCE_MODE, FrontierCompassApp
from frontier_compass.ui.history import (
    build_history_artifact_rows,
    build_history_summary_bits,
    format_history_compatibility_text,
    format_history_llm_provenance_text,
    format_history_requested_effective_label,
    list_recent_daily_runs,
    report_path_for_cache_artifact,
)


def test_list_recent_daily_runs_scans_nested_cache_and_attaches_artifacts(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "validation_round9" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    eml_path = report_path.with_suffix(".eml")
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        exploration_pick_count=1,
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")
    eml_path.parent.mkdir(parents=True, exist_ok=True)
    eml_path.write_text("dry-run email", encoding="utf-8")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.fetch_status == "fresh source fetch"
    assert entry.report_mode == "deterministic"
    assert entry.cost_mode == "zero-token"
    assert entry.same_date_cache_reused is False
    assert entry.stale_cache_fallback_used is False
    assert entry.ranked_count == 1
    assert entry.exploration_pick_count == 1
    assert entry.frontier_report_present is True
    assert entry.report_artifact_aligned is True
    assert entry.source_run_stats[0].source == "arxiv"
    assert entry.run_timings.total_seconds == 0.35
    assert entry.cache_path == str(cache_path)
    assert entry.report_path == str(report_path)
    assert entry.eml_path == str(eml_path)


def test_list_recent_daily_runs_detects_same_date_cache_reuse_from_report(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(
        report_path,
        digest,
        acquisition_status_label="same-date cache reused after fetch failure",
        fetch_error="upstream arXiv timeout",
    )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.fetch_status == "same-date cache reused after fetch failure"
    assert entry.same_date_cache_reused is True
    assert entry.stale_cache_fallback_used is False


def test_list_recent_daily_runs_normalizes_legacy_fetch_status_labels(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh arXiv fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    assert rows[0].fetch_status == "fresh source fetch"


def test_list_recent_daily_runs_normalizes_legacy_same_day_retry_label(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="same-day cache reused after fetch failure")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    assert rows[0].fetch_status == "same-date cache reused after fetch failure"
    assert rows[0].same_date_cache_reused is True
    assert rows[0].stale_cache_fallback_used is False


def test_list_recent_daily_runs_normalizes_legacy_stale_cache_label(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        stale_cache_fallback=True,
        strict_same_day_counts_known=False,
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="older compatible cache")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    assert rows[0].fetch_status == "older compatible cache reused after fetch failure"
    assert rows[0].same_date_cache_reused is False
    assert rows[0].stale_cache_fallback_used is True


def test_list_recent_daily_runs_reads_profile_source_from_orphan_report_run_summary(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    report_path = report_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24_profile-live-zotero-db-zotero-a1b2c3d4.html"
    digest = replace(
        _sample_digest(
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        ),
        profile=UserInterestProfile(
            basis_label="biomedical baseline + live Zotero DB",
            profile_basis=ProfileBasis(
                source="live_zotero_db",
                label="Live Zotero DB",
                path="/tmp/zotero.sqlite",
                item_count=9,
                used_item_count=5,
            ),
            zotero_db_name="zotero.sqlite",
            zotero_keywords=("spatial transcriptomics", "digital pathology"),
        ),
    )

    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.cache_path is None
    assert entry.report_path == str(report_path)
    assert entry.profile_source == "live_zotero_db"
    assert entry.profile_path == "/tmp/zotero.sqlite"
    assert entry.profile_item_count == 9
    assert entry.profile_used_item_count == 5
    assert entry.profile_terms == ("spatial transcriptomics", "digital pathology")


def test_list_recent_daily_runs_includes_multisource_artifacts(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json"
    ranked_paper = RankedPaper(
        paper=PaperRecord(
            source="biorxiv",
            identifier="10.1101/2026.03.24.000001v1",
            title="Multisource frontier fixture",
            summary="Multisource fixture for history scanning.",
            authors=("A Scientist",),
            categories=("q-bio.GN",),
            published=date(2026, 3, 24),
            updated=date(2026, 3, 24),
            url="https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
        ),
        score=0.82,
        recommendation_summary="Multisource history fixture.",
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[ranked_paper],
        frontier_report=build_daily_frontier_report(
            paper_pool=[ranked_paper.paper],
            ranked_papers=[ranked_paper],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
            mode=BIOMEDICAL_MULTISOURCE_MODE,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            total_fetched=3,
        ),
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        source_counts={"arxiv": 1, "biorxiv": 1, "medrxiv": 1},
        total_fetched=3,
        mode_label="Biomedical multisource",
        mode_kind="",
    )

    _write_digest_cache(cache_path, digest)

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.category == BIOMEDICAL_MULTISOURCE_MODE
    assert entry.mode_label == "Biomedical multisource"
    assert entry.mode_kind == "multisource"
    assert entry.fetch_status == "fetch status unavailable (report missing)"


def test_list_recent_daily_runs_prioritizes_current_contract_over_archived_compatibility(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    current_cache_path = cache_dir / "frontier_compass_bundle_biomedical_2026-03-24.json"
    current_report_path = report_path_for_cache_artifact(current_cache_path, cache_dir=cache_dir, report_dir=report_dir)
    compatibility_cache_path = (
        cache_dir / "live_validation" / "frontier_compass_arxiv_biomedical-latest_2026-03-25_zotero-old.json"
    )
    compatibility_report_path = report_path_for_cache_artifact(
        compatibility_cache_path,
        cache_dir=cache_dir,
        report_dir=report_dir,
    )

    current_digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        category=SOURCE_BUNDLE_BIOMEDICAL,
        mode_label="Biomedical",
        mode_kind="source-bundle",
        source="multisource",
    )
    compatibility_digest = _sample_digest(
        requested_date=date(2026, 3, 25),
        effective_date=date(2026, 3, 25),
        generated_at=datetime(2026, 3, 27, 3, 9, 45, tzinfo=timezone.utc),
    )

    _write_digest_cache(current_cache_path, current_digest)
    _write_report(current_report_path, current_digest, acquisition_status_label="fresh source fetch")
    _write_digest_cache(compatibility_cache_path, compatibility_digest)
    _write_report(compatibility_report_path, compatibility_digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 2
    assert rows[0].requested_date == date(2026, 3, 24)
    assert rows[0].is_compatibility_entry is False
    assert rows[1].requested_date == date(2026, 3, 25)
    assert rows[1].is_compatibility_entry is True
    assert rows[1].compatibility_status == "archived"
    assert "archived live-validation artifact" in rows[1].compatibility_reasons
    assert "compatibility / archived" in build_history_summary_bits(rows[1])
    assert "compatibility / archived" in format_history_compatibility_text(rows[1])


def test_list_recent_daily_runs_marks_legacy_latest_mode_as_compatibility(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.is_compatibility_entry is True
    assert entry.compatibility_status == "legacy"
    assert entry.compatibility_reasons == ("legacy compatibility mode id: biomedical-latest",)
    assert "compatibility-only" in build_history_summary_bits(entry)
    assert format_history_compatibility_text(entry) == "compatibility-only: legacy compatibility mode id: biomedical-latest"


def test_write_daily_outputs_persists_fresh_fetch_status_for_history(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = report_dir / "frontier_compass_bundle_biomedical_2026-03-24.html"
    requested_date = date(2026, 3, 24)
    digest = _sample_digest(
        requested_date=requested_date,
        effective_date=requested_date,
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        category=SOURCE_BUNDLE_BIOMEDICAL,
        mode_label="Biomedical",
        mode_kind="source-bundle",
        source="multisource",
    )
    app = FrontierCompassApp()

    def fake_build_daily_digest(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["category"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["today"] == requested_date
        return digest

    app.build_daily_digest = fake_build_daily_digest  # type: ignore[method-assign]

    app.write_daily_outputs(
        category=SOURCE_BUNDLE_BIOMEDICAL,
        today=requested_date,
        cache_path=cache_path,
        output_path=report_path,
    )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    assert rows[0].fetch_status == "fresh source fetch"


def test_list_recent_daily_runs_marks_official_bundle_with_unexpected_source_rows_as_legacy(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_bundle_ai-for-medicine_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    ranked_paper = RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.24002v1",
            title="Legacy public-bundle provenance fixture",
            summary="Fixture for unexpected source provenance classification.",
            authors=("A Scientist",),
            categories=("q-bio.GN",),
            published=date(2026, 3, 24),
            updated=date(2026, 3, 24),
            url="https://arxiv.org/abs/2603.24002",
        ),
        score=0.81,
        recommendation_summary="Unexpected-source provenance fixture.",
    )
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            cache_status="fresh",
        ),
        SourceRunStats(
            source="biorxiv",
            fetched_count=1,
            displayed_count=0,
            status="ready",
            cache_status="fresh",
        ),
        SourceRunStats(
            source="medrxiv",
            fetched_count=0,
            displayed_count=0,
            status="failed",
            cache_status="fresh",
            error="legacy medRxiv row should not appear in official bundle artifacts",
        ),
    )
    digest = DailyDigest(
        source="multisource",
        category=SOURCE_BUNDLE_AI_FOR_MEDICINE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 4, 9, 45, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(SOURCE_BUNDLE_AI_FOR_MEDICINE),
        ranked=[ranked_paper],
        source_run_stats=source_run_stats,
        frontier_report=replace(
            build_daily_frontier_report(
                paper_pool=[ranked_paper.paper],
                ranked_papers=[ranked_paper],
                requested_date=date(2026, 3, 24),
                effective_date=date(2026, 3, 24),
                source="multisource",
                mode=SOURCE_BUNDLE_AI_FOR_MEDICINE,
                mode_label="AI for medicine",
                mode_kind="source-bundle",
                total_fetched=2,
            ),
            source_run_stats=source_run_stats,
            source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
        ),
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
        total_fetched=2,
        mode_label="AI for medicine",
        mode_kind="source-bundle",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.is_compatibility_entry is True
    assert entry.compatibility_status == "legacy"
    assert "official bundle artifact contains unexpected source rows: medrxiv" in entry.compatibility_reasons
    assert "compatibility-only" in build_history_summary_bits(entry)
    assert (
        format_history_compatibility_text(entry)
        == "compatibility-only: official bundle artifact contains unexpected source rows: medrxiv"
    )


def test_list_recent_daily_runs_reads_embedded_multisource_run_summary(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    ranked_paper = RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.24001v1",
            title="Multisource summary fixture",
            summary="Embedded summary fixture for history scanning.",
            authors=("A Scientist",),
            categories=("q-bio.GN",),
            published=date(2026, 3, 24),
            updated=date(2026, 3, 24),
            url="https://arxiv.org/abs/2603.24001",
        ),
        score=0.82,
        recommendation_summary="Embedded summary fixture.",
    )
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.8, parse_seconds=0.2, total_seconds=1.0),
        ),
        SourceRunStats(
            source="biorxiv",
            fetched_count=0,
            displayed_count=0,
            status="empty",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.1, parse_seconds=0.0, total_seconds=0.1),
        ),
        SourceRunStats(
            source="medrxiv",
            fetched_count=0,
            displayed_count=0,
            status="failed",
            cache_status="same-day-cache",
            error="medRxiv unavailable",
            timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
        ),
    )
    run_timings = RunTimings(
        network_seconds=1.1,
        parse_seconds=0.2,
        rank_seconds=0.3,
        report_seconds=0.4,
        total_seconds=2.0,
    )
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[ranked_paper.paper],
            ranked_papers=[ranked_paper],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
            mode=BIOMEDICAL_MULTISOURCE_MODE,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            total_fetched=1,
        ),
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        report_status="partial",
        report_error="medRxiv unavailable",
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[ranked_paper],
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
        total_fetched=1,
        mode_label="Biomedical multisource",
        mode_kind="multisource",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        report_status="partial",
        report_error="medRxiv unavailable",
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert tuple(row.source for row in entry.source_run_stats) == ("arxiv", "biorxiv", "medrxiv")
    assert entry.source_run_stats[1].status == "empty"
    assert entry.source_run_stats[2].status == "failed"
    assert entry.source_run_stats[2].error == "medRxiv unavailable"
    assert entry.run_timings.total_seconds == 2.0
    assert entry.report_status == "partial"
    assert entry.frontier_report_present is True
    assert entry.report_artifact_aligned is True


def test_list_recent_daily_runs_roundtrips_range_request_window_provenance(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "daily"
    report_path = report_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-26.html"
    requested_date = date(2026, 3, 24)
    end_date = date(2026, 3, 26)
    request_window = RequestWindow(
        kind="range",
        requested_date=requested_date,
        start_date=requested_date,
        end_date=end_date,
        status="partial",
        completed_dates=(date(2026, 3, 24), date(2026, 3, 25)),
        failed_date=end_date,
        failed_source="arxiv",
        failure_reason="upstream arXiv timeout",
    )
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=2,
            displayed_count=2,
            status="partial",
            cache_status="same-day-cache",
            error="upstream arXiv timeout",
            note="Same-day cache reused after a fresh fetch failure.",
            timings=RunTimings(network_seconds=0.8, parse_seconds=0.2, total_seconds=1.0),
        ),
    )
    ranked_paper = RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.24001v1",
            title="Range provenance fixture",
            summary="Range provenance fixture for history scanning.",
            authors=("A Scientist",),
            categories=("q-bio.GN",),
            published=requested_date,
            updated=requested_date,
            url="https://arxiv.org/abs/2603.24001",
        ),
        score=0.82,
        recommendation_summary="Range provenance fixture.",
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=requested_date,
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[ranked_paper],
        request_window=request_window,
        source_run_stats=source_run_stats,
        run_timings=RunTimings(network_seconds=0.8, parse_seconds=0.2, total_seconds=1.0),
        frontier_report=replace(
            build_daily_frontier_report(
                paper_pool=[ranked_paper.paper],
                ranked_papers=[ranked_paper],
                requested_date=requested_date,
                effective_date=end_date,
                source="arxiv",
                mode=BIOMEDICAL_LATEST_MODE,
                mode_label="Biomedical latest available range",
                mode_kind="latest-available-hybrid-range",
                total_fetched=2,
            ),
            request_window=request_window,
            source_run_stats=source_run_stats,
            report_status="partial",
            report_error="upstream arXiv timeout",
            source_counts={"arxiv": 2},
        ),
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        source_counts={"arxiv": 2},
        total_fetched=2,
        mode_label="Biomedical latest available range",
        mode_kind="latest-available-hybrid-range",
        requested_date=requested_date,
        effective_date=end_date,
        report_status="partial",
        report_error="upstream arXiv timeout",
        fetch_scope="range-full",
    )

    _write_report(
        report_path,
        digest,
        acquisition_status_label="fresh source fetch",
        fetch_error="upstream arXiv timeout",
    )

    rows = list_recent_daily_runs(cache_dir=tmp_path / "data" / "cache", report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.cache_path is None
    assert entry.request_window.kind == "range"
    assert entry.request_window.completed_dates == (date(2026, 3, 24), date(2026, 3, 25))
    assert entry.request_window.failed_date == end_date
    assert entry.request_window.failed_source == "arxiv"
    assert entry.request_window.failure_reason == "upstream arXiv timeout"
    assert entry.request_window.failure_entries == (
        RequestWindowFailure(date=end_date, source="arxiv", reason="upstream arXiv timeout"),
    )
    assert "completed 2026-03-24, 2026-03-25" in format_history_requested_effective_label(entry)
    assert "failed 2026-03-26 / arxiv" in format_history_requested_effective_label(entry)


def test_list_recent_daily_runs_defaults_legacy_single_day_fetch_scope_to_day_full(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )
    payload = digest.to_mapping()
    payload.pop("fetch_scope", None)
    frontier_report_payload = payload.get("frontier_report")
    if isinstance(frontier_report_payload, dict):
        frontier_report_payload.pop("fetch_scope", None)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.fetch_scope == "day-full"
    assert "day-full" not in build_history_summary_bits(entry)


def test_list_recent_daily_runs_includes_orphan_stale_fallback_report(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    report_path = report_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        stale_cache_fallback=True,
        strict_same_day_counts_known=False,
    )

    _write_report(
        report_path,
        digest,
        acquisition_status_label="older compatible cache reused after fetch failure",
        fetch_error="upstream arXiv timeout",
    )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.cache_path is None
    assert entry.report_path == str(report_path)
    assert entry.fetch_status == "older compatible cache reused after fetch failure"
    assert entry.same_date_cache_reused is False
    assert entry.stale_cache_fallback_used is True
    assert entry.requested_date == date(2026, 3, 24)
    assert entry.effective_date == date(2026, 3, 23)


def test_list_recent_daily_runs_orders_by_generated_at_then_requested_date(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    first_cache = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    second_cache = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-25.json"
    third_cache = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-26.json"

    first_digest = _sample_digest(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )
    second_digest = _sample_digest(
        requested_date=date(2026, 3, 25),
        effective_date=date(2026, 3, 25),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )
    third_digest = _sample_digest(
        requested_date=date(2026, 3, 26),
        effective_date=date(2026, 3, 26),
        generated_at=datetime(2026, 3, 26, 23, 59, tzinfo=timezone.utc),
    )

    for cache_path, digest in (
        (first_cache, first_digest),
        (second_cache, second_digest),
        (third_cache, third_digest),
    ):
        _write_digest_cache(cache_path, digest)
        _write_report(
            report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir),
            digest,
            acquisition_status_label="fresh source fetch",
        )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert [entry.requested_date for entry in rows] == [
        date(2026, 3, 25),
        date(2026, 3, 24),
        date(2026, 3, 26),
    ]


def test_report_path_for_cache_artifact_preserves_nested_subdirectories(tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "validation_round9" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"

    assert report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir) == (
        report_dir / "validation_round9" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    )


def test_history_helpers_format_requested_and_effective_dates() -> None:
    same_day_entry = _sample_history_entry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )
    fallback_entry = _sample_history_entry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
    )

    assert format_history_requested_effective_label(same_day_entry) == "2026-03-24"
    assert format_history_requested_effective_label(fallback_entry) == "2026-03-24 -> 2026-03-23"


def test_history_helpers_build_summary_bits_and_artifact_rows() -> None:
    entry = _sample_history_entry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
        eml_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.eml",
    )

    assert build_history_summary_bits(entry) == (
        "fresh source fetch",
        "window 2026-03-24",
        "scope day-full",
        "profile source baseline",
        "source composition arxiv 12/12",
        "report deterministic/ready",
        "cache story fresh source fetch",
        "ranked 12",
        "biomedical baseline",
        "zero-token",
        "time 0.35s",
        "zotero sample_library.csl.json",
        "exploration 2",
    )
    assert build_history_artifact_rows(entry) == (
        ("Report", "reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html"),
        ("Cache", "data/cache/frontier_compass_arxiv_biomedical-latest_2026-03-24.json"),
        ("EML", "reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.eml"),
    )


def test_history_helpers_format_llm_provenance_text() -> None:
    entry = _sample_history_entry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
    )
    entry = replace(
        entry,
        llm_requested=True,
        llm_applied=False,
        llm_provider="openai",
        llm_fallback_reason="A model-assisted provider is configured, but the Frontier Report run stayed deterministic.",
        llm_seconds=None,
    )

    assert format_history_llm_provenance_text(entry) == (
        "LLM requested yes | applied no | provider openai | fallback "
        "A model-assisted provider is configured, but the Frontier Report run stayed deterministic. | "
        "time n/a"
    )


def _sample_digest(
    *,
    requested_date: date,
    effective_date: date,
    generated_at: datetime,
    exploration_pick_count: int = 0,
    stale_cache_fallback: bool = False,
    strict_same_day_counts_known: bool = True,
    category: str = BIOMEDICAL_LATEST_MODE,
    mode_label: str = "Biomedical latest available",
    mode_kind: str = "latest-available-hybrid",
    source: str = "arxiv",
) -> DailyDigest:
    ranked_paper = RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.22821v1",
            title="Spatial transcriptomics from digital pathology images",
            summary="Histopathology model for spatial transcriptomics and tissue analysis.",
            authors=("A Scientist",),
            categories=("q-bio.GN", "cs.CV"),
            published=requested_date,
            updated=requested_date,
            url="https://arxiv.org/abs/2603.22821",
        ),
        score=0.88,
        recommendation_summary="Priority review for biomedical evidence.",
    )
    exploration_picks = [ranked_paper] if exploration_pick_count else []
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.20, parse_seconds=0.05, total_seconds=0.25),
        ),
    )
    run_timings = RunTimings(
        network_seconds=0.20,
        parse_seconds=0.05,
        rank_seconds=0.10,
        total_seconds=0.35,
    )
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[ranked_paper.paper],
            ranked_papers=[ranked_paper],
            requested_date=requested_date,
            effective_date=effective_date,
            source=source,
            mode=category,
            mode_label=mode_label,
            mode_kind=mode_kind,
            searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
            total_fetched=3,
        ),
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        source_counts={"arxiv": 1},
    )
    return DailyDigest(
        source=source,
        category=category,
        target_date=requested_date,
        generated_at=generated_at,
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(category),
        ranked=[ranked_paper],
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        exploration_picks=exploration_picks,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label=mode_label,
        mode_kind=mode_kind,
        requested_date=requested_date,
        effective_date=effective_date,
        strict_same_day_counts_known=strict_same_day_counts_known,
        stale_cache_source_requested_date=date(2026, 3, 23) if stale_cache_fallback else None,
        stale_cache_source_effective_date=date(2026, 3, 23) if stale_cache_fallback else None,
    )


def _write_digest_cache(path: Path, digest: DailyDigest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")


def _write_report(
    path: Path,
    digest: DailyDigest,
    *,
    acquisition_status_label: str,
    fetch_error: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        HtmlReportBuilder().render_daily_digest(
            digest,
            acquisition_status_label=acquisition_status_label,
            fetch_error=fetch_error,
        ),
        encoding="utf-8",
    )


def _sample_history_entry(
    *,
    requested_date: date,
    effective_date: date,
    generated_at: datetime,
    report_path: str | None = None,
    eml_path: str | None = None,
):
    from frontier_compass.storage.schema import RunHistoryEntry

    return RunHistoryEntry(
        requested_date=requested_date,
        effective_date=effective_date,
        category=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        request_window=RequestWindow(kind="day", requested_date=requested_date),
        profile_basis="biomedical baseline",
        profile_source="baseline",
        zotero_export_name="sample_library.csl.json",
        fetch_status="fresh source fetch",
        requested_report_mode="deterministic",
        report_mode="deterministic",
        cost_mode="zero-token",
        report_status="ready",
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=12,
                displayed_count=12,
                status="ready",
                cache_status="fresh",
            ),
        ),
        run_timings=RunTimings(total_seconds=0.35),
        frontier_report_present=True,
        report_artifact_aligned=True,
        same_date_cache_reused=False,
        stale_cache_fallback_used=False,
        ranked_count=12,
        exploration_pick_count=2,
        cache_path="data/cache/frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
        report_path=report_path,
        eml_path=eml_path,
        generated_at=generated_at,
    )
