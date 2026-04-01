"""Acceptance tests for cache reuse, stale fallback, multisource 0-count rows, and range partial runs."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    RankedPaper,
    RequestWindow,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, BIOMEDICAL_MULTISOURCE_MODE, FrontierCompassApp
from frontier_compass.ui.history import (
    format_history_requested_effective_label,
    list_recent_daily_runs,
    report_path_for_cache_artifact,
)


# ---------------------------------------------------------------------------
# Helpers (same patterns as test_history.py)
# ---------------------------------------------------------------------------


def _make_paper(
    *,
    source: str = "arxiv",
    identifier: str = "2603.24001v1",
    title: str = "Acceptance test fixture paper",
    published: date = date(2026, 3, 24),
) -> PaperRecord:
    return PaperRecord(
        source=source,
        identifier=identifier,
        title=title,
        summary="Fixture paper for acceptance testing.",
        authors=("A Scientist",),
        categories=("q-bio.GN",),
        published=published,
        updated=published,
        url=f"https://arxiv.org/abs/{identifier}" if source == "arxiv" else f"https://www.{source}.org/content/{identifier}",
    )


def _make_ranked(paper: PaperRecord, score: float = 0.85) -> RankedPaper:
    return RankedPaper(
        paper=paper,
        score=score,
        recommendation_summary="Acceptance test fixture.",
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


# ---------------------------------------------------------------------------
# Test 1: Same-day cache reuse
# ---------------------------------------------------------------------------


def test_same_day_cache_reuse_flag_set_in_history(tmp_path: Path) -> None:
    """When the same digest is loaded twice for the same date and the report
    indicates same-date cache reuse, the history entry must have
    same_date_cache_reused=True and stale_cache_fallback_used=False."""

    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"

    # Write the same digest to cache (simulating a second load reusing the
    # earlier same-day cache).
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)

    paper = _make_paper(published=date(2026, 3, 24))
    ranked = _make_ranked(paper)
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=5,
            displayed_count=5,
            status="ready",
            cache_status="same-day-cache",
            note="Same-day cache reused after a fresh fetch failure.",
            timings=RunTimings(network_seconds=0.1, parse_seconds=0.05, total_seconds=0.15),
        ),
    )
    run_timings = RunTimings(network_seconds=0.1, parse_seconds=0.05, rank_seconds=0.1, total_seconds=0.25)
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[paper],
            ranked_papers=[ranked],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="arxiv",
            mode=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            total_fetched=5,
        ),
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        source_counts={"arxiv": 5},
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 14, 30, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[ranked],
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 5},
        source_counts={"arxiv": 5},
        total_fetched=5,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
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
    assert entry.same_date_cache_reused is True
    assert entry.stale_cache_fallback_used is False
    assert entry.fetch_status == "same-date cache reused after fetch failure"
    assert entry.requested_date == date(2026, 3, 24)
    assert entry.effective_date == date(2026, 3, 24)


# ---------------------------------------------------------------------------
# Test 2: Stale-compatible fallback
# ---------------------------------------------------------------------------


def test_stale_compatible_fallback_reflected_in_history(tmp_path: Path) -> None:
    """When a digest has stale_cache_fallback_used=True and the stale cache
    source dates are set, the history entry must reflect
    stale_cache_fallback_used=True, same_date_cache_reused=False, and the
    effective date should differ from the requested date."""

    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)

    paper = _make_paper(published=date(2026, 3, 23))
    ranked = _make_ranked(paper)
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=3,
            displayed_count=3,
            status="ready",
            cache_status="stale-cache",
            timings=RunTimings(network_seconds=0.2, parse_seconds=0.05, total_seconds=0.25),
        ),
    )
    run_timings = RunTimings(network_seconds=0.2, parse_seconds=0.05, rank_seconds=0.1, total_seconds=0.35)
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[paper],
            ranked_papers=[ranked],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 23),
            source="arxiv",
            mode=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            total_fetched=3,
        ),
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        source_counts={"arxiv": 3},
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 15, 0, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[ranked],
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 3},
        source_counts={"arxiv": 3},
        total_fetched=3,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 23),
        strict_same_day_counts_known=False,
        stale_cache_source_requested_date=date(2026, 3, 23),
        stale_cache_source_effective_date=date(2026, 3, 23),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(
        report_path,
        digest,
        acquisition_status_label="older compatible cache reused after fetch failure",
        fetch_error="upstream arXiv timeout",
    )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]
    assert entry.stale_cache_fallback_used is True
    assert entry.same_date_cache_reused is False
    assert entry.fetch_status == "older compatible cache reused after fetch failure"
    assert entry.requested_date == date(2026, 3, 24)
    assert entry.effective_date == date(2026, 3, 23)


# ---------------------------------------------------------------------------
# Test 3: Multisource source row remains visible at 0
# ---------------------------------------------------------------------------


def test_multisource_zero_count_source_rows_remain_visible(tmp_path: Path) -> None:
    """In biomedical-multisource mode, even when biorxiv and medrxiv return 0
    papers, source_run_stats must still contain rows for all three sources
    (arxiv, biorxiv, medrxiv), each with the correct fetched_count. The UI
    must never silently drop a source from the source mix display."""

    cache_dir = tmp_path / "data" / "cache"
    report_dir = tmp_path / "reports" / "daily"
    cache_path = cache_dir / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json"
    report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_dir, report_dir=report_dir)

    paper = _make_paper(source="arxiv", identifier="2603.24001v1", published=date(2026, 3, 24))
    ranked = _make_ranked(paper)

    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=2,
            displayed_count=2,
            status="ready",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.5, parse_seconds=0.1, total_seconds=0.6),
        ),
        SourceRunStats(
            source="biorxiv",
            fetched_count=0,
            displayed_count=0,
            status="empty",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.3, parse_seconds=0.0, total_seconds=0.3),
        ),
        SourceRunStats(
            source="medrxiv",
            fetched_count=0,
            displayed_count=0,
            status="empty",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.2, parse_seconds=0.0, total_seconds=0.2),
        ),
    )
    run_timings = RunTimings(
        network_seconds=1.0,
        parse_seconds=0.1,
        rank_seconds=0.2,
        report_seconds=0.1,
        total_seconds=1.4,
    )
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[paper],
            ranked_papers=[ranked],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
            mode=BIOMEDICAL_MULTISOURCE_MODE,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            total_fetched=2,
        ),
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        source_counts={"arxiv": 2, "biorxiv": 0, "medrxiv": 0},
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 16, 0, 0, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[ranked],
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 2, "q-bio.GN": 2},
        source_counts={"arxiv": 2, "biorxiv": 0, "medrxiv": 0},
        total_fetched=2,
        mode_label="Biomedical multisource",
        mode_kind="multisource",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    _write_digest_cache(cache_path, digest)
    _write_report(report_path, digest, acquisition_status_label="fresh source fetch")

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]

    # All three sources must be present in the source_run_stats.
    sources = tuple(row.source for row in entry.source_run_stats)
    assert sources == ("arxiv", "biorxiv", "medrxiv"), (
        f"Expected all three sources to be present even at 0-count; got {sources}"
    )

    # Verify fetched_count values: arxiv=2, biorxiv=0, medrxiv=0.
    stats_by_source = {row.source: row for row in entry.source_run_stats}
    assert stats_by_source["arxiv"].fetched_count == 2
    assert stats_by_source["biorxiv"].fetched_count == 0
    assert stats_by_source["medrxiv"].fetched_count == 0

    # Status values should reflect the 0-count correctly.
    assert stats_by_source["biorxiv"].status == "empty"
    assert stats_by_source["medrxiv"].status == "empty"

    # The overall entry mode should be multisource.
    assert entry.category == BIOMEDICAL_MULTISOURCE_MODE
    assert entry.mode_label == "Biomedical multisource"


# ---------------------------------------------------------------------------
# Test 4: Range partial run is not silently truncated
# ---------------------------------------------------------------------------


def test_range_partial_run_not_silently_truncated(tmp_path: Path) -> None:
    """When a 3-day range run partially fails (day 3 fails), the resulting
    digest must still contain papers from completed days. The request_window
    must have status='partial' with correct completed_dates, failed_date,
    failed_source, and failure_reason. The report_status must be 'partial'
    and must not be silently set to 'ready' or truncated to zero papers."""

    report_dir = tmp_path / "reports" / "daily"
    cache_dir = tmp_path / "data" / "cache"

    requested_date = date(2026, 3, 24)
    end_date = date(2026, 3, 26)

    # Build papers from the two completed days.
    paper_day1 = _make_paper(identifier="2603.24001v1", title="Day 1 paper", published=date(2026, 3, 24))
    paper_day2 = _make_paper(identifier="2603.25001v1", title="Day 2 paper", published=date(2026, 3, 25))
    ranked_day1 = _make_ranked(paper_day1, score=0.90)
    ranked_day2 = _make_ranked(paper_day2, score=0.80)

    request_window = RequestWindow(
        kind="range",
        requested_date=requested_date,
        start_date=requested_date,
        end_date=end_date,
        status="partial",
        completed_dates=(date(2026, 3, 24), date(2026, 3, 25)),
        failed_date=end_date,
        failed_source="arxiv",
        failure_reason="upstream arXiv timeout on day 3",
    )
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=2,
            displayed_count=2,
            status="partial",
            cache_status="fresh",
            error="upstream arXiv timeout on day 3",
            timings=RunTimings(network_seconds=1.2, parse_seconds=0.3, total_seconds=1.5),
        ),
    )
    run_timings = RunTimings(network_seconds=1.2, parse_seconds=0.3, rank_seconds=0.2, total_seconds=1.7)
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[paper_day1, paper_day2],
            ranked_papers=[ranked_day1, ranked_day2],
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
        run_timings=run_timings,
        report_status="partial",
        report_error="upstream arXiv timeout on day 3",
        source_counts={"arxiv": 2},
    )

    report_path = report_dir / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-26.html"

    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=requested_date,
        generated_at=datetime(2026, 3, 27, 3, 0, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[ranked_day1, ranked_day2],
        request_window=request_window,
        source_run_stats=source_run_stats,
        run_timings=run_timings,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 2, "q-bio.GN": 2},
        source_counts={"arxiv": 2},
        total_fetched=2,
        mode_label="Biomedical latest available range",
        mode_kind="latest-available-hybrid-range",
        requested_date=requested_date,
        effective_date=end_date,
        report_status="partial",
        report_error="upstream arXiv timeout on day 3",
        fetch_scope="range-full",
    )

    _write_report(
        report_path,
        digest,
        acquisition_status_label="fresh source fetch",
        fetch_error="upstream arXiv timeout on day 3",
    )

    rows = list_recent_daily_runs(cache_dir=cache_dir, report_dir=report_dir)

    assert len(rows) == 1
    entry = rows[0]

    # Papers from completed days must NOT be truncated to zero.
    assert entry.ranked_count >= 2, (
        f"Expected at least 2 papers from completed days; got {entry.ranked_count}"
    )

    # request_window must reflect the partial range.
    assert entry.request_window.kind == "range"
    assert entry.request_window.status == "partial"
    assert entry.request_window.completed_dates == (date(2026, 3, 24), date(2026, 3, 25))
    assert entry.request_window.failed_date == date(2026, 3, 26)
    assert entry.request_window.failed_source == "arxiv"
    assert entry.request_window.failure_reason == "upstream arXiv timeout on day 3"

    # report_status must be 'partial', NOT silently set to 'ready'.
    assert entry.report_status == "partial", (
        f"Expected report_status='partial'; got '{entry.report_status}'"
    )

    # The formatted label must mention both completed and failed dates.
    label = format_history_requested_effective_label(entry)
    assert "completed 2026-03-24, 2026-03-25" in label
    assert "failed 2026-03-26" in label
    assert "arxiv" in label
