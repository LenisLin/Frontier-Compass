from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.api import load_recent_history
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    RankedPaper,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, BIOMEDICAL_MULTISOURCE_MODE, FrontierCompassApp


def _ranked_fixture(
    *,
    source: str = "arxiv",
    identifier: str = "fixture-001",
    published: date = date(2026, 3, 24),
) -> RankedPaper:
    return RankedPaper(
        paper=PaperRecord(
            source=source,
            identifier=identifier,
            title="Fixture paper",
            summary="Deterministic fixture summary.",
            authors=("A Researcher",),
            categories=("q-bio",),
            published=published,
            url=f"https://example.org/{identifier}",
        ),
        score=0.91,
        reasons=("fixture",),
    )


def test_multisource_fresh_runs_record_truthful_source_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    target_date = date(2026, 3, 24)

    def fake_fetch_today_by_category_with_timings(self, categories, *, today=None, max_results=None, feed_urls=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_urls
        return (
            {
                categories[0]: [
                    PaperRecord(
                        source="arxiv",
                        identifier="arxiv-live",
                        title="arXiv live paper",
                        summary="Fresh arXiv paper.",
                        authors=("A Researcher",),
                        categories=(categories[0],),
                        published=target_date,
                        url="https://arxiv.org/abs/2603.24001",
                    )
                ]
            },
            0.9,
            0.2,
        )

    monkeypatch.setattr(
        "frontier_compass.ingest.arxiv.ArxivClient.fetch_today_by_category_with_timings",
        fake_fetch_today_by_category_with_timings,
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.biorxiv.BioRxivClient.fetch_today_with_timings",
        lambda self, **kwargs: ([], 0.1, 0.0),
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.medrxiv.MedRxivClient.fetch_today_with_timings",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("medRxiv unavailable")),
    )

    digest = FrontierCompassApp().build_daily_digest(
        mode=BIOMEDICAL_MULTISOURCE_MODE,
        today=target_date,
        max_results=20,
    )

    source_stats = {row.source: row for row in digest.source_run_stats}
    assert source_stats["arxiv"].outcome == "live-success"
    assert source_stats["arxiv"].live_outcome == "live-success"
    assert source_stats["biorxiv"].outcome == "live-zero"
    assert source_stats["biorxiv"].live_outcome == "live-zero"
    assert source_stats["medrxiv"].outcome == "live-failed"
    assert source_stats["medrxiv"].live_outcome == "live-failed"
    assert digest.frontier_report is not None
    frontier_source_stats = {row.source: row for row in digest.frontier_report.source_run_stats}
    assert frontier_source_stats["arxiv"].outcome == "live-success"
    assert frontier_source_stats["biorxiv"].outcome == "live-zero"
    assert frontier_source_stats["medrxiv"].outcome == "live-failed"


def test_load_daily_digest_marks_missing_multisource_observability_as_unknown_legacy(tmp_path: Path) -> None:
    ranked = [_ranked_fixture()]
    frontier_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="multisource",
        mode=BIOMEDICAL_MULTISOURCE_MODE,
        mode_label="Biomedical multisource",
        mode_kind="multisource",
        total_fetched=1,
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
            ),
        ),
        frontier_report=frontier_report,
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
        total_fetched=1,
        mode_label="Biomedical multisource",
        mode_kind="multisource",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    cache_path = tmp_path / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json"
    cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")

    loaded = FrontierCompassApp().load_daily_digest(cache_path)

    source_stats = {row.source: row for row in loaded.source_run_stats}
    assert source_stats["arxiv"].outcome == "live-success"
    assert source_stats["biorxiv"].outcome == "unknown-legacy"
    assert source_stats["biorxiv"].live_outcome == "unknown-legacy"
    assert source_stats["medrxiv"].outcome == "unknown-legacy"
    assert loaded.frontier_report is not None
    frontier_source_stats = {row.source: row for row in loaded.frontier_report.source_run_stats}
    assert frontier_source_stats["biorxiv"].outcome == "unknown-legacy"
    assert frontier_source_stats["medrxiv"].outcome == "unknown-legacy"


def test_same_day_cache_reuse_records_cache_lookup_timing_and_preserves_live_outcome(tmp_path: Path) -> None:
    app = FrontierCompassApp()
    ranked = [_ranked_fixture()]
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            outcome="live-success",
            live_outcome="live-success",
            cache_status="fresh",
        ),
    )
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[item.paper for item in ranked],
            ranked_papers=ranked,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="arxiv",
            mode=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            total_fetched=1,
        ),
        source_run_stats=source_run_stats,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        source_run_stats=source_run_stats,
        run_timings=RunTimings(network_seconds=1.2, parse_seconds=0.3, rank_seconds=0.1, total_seconds=1.6),
        frontier_report=frontier_report,
        total_fetched=1,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")

    result = app.materialize_daily_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=20,
        cache_dir=tmp_path,
        cache_path=cache_path,
        output_path=report_path,
        force_fetch=False,
    )

    assert result.digest.run_timings.cache_seconds is not None
    assert result.digest.run_timings.cache_seconds >= 0.0
    assert result.digest.run_timings.network_seconds is None
    assert result.digest.run_timings.parse_seconds is None
    assert result.digest.run_timings.rank_seconds is None
    assert result.digest.source_run_stats[0].outcome == "same-day-cache"
    assert result.digest.source_run_stats[0].live_outcome == "live-success"
    assert result.digest.source_run_stats[0].timings.total_seconds is None
    assert result.digest.generated_at > datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc)
    assert result.digest.frontier_report is not None
    assert result.digest.frontier_report.source_run_stats[0].outcome == "same-day-cache"
    assert result.digest.frontier_report.source_run_stats[0].live_outcome == "live-success"


def test_fresh_materialization_does_not_re_render_report_after_write_daily_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    ranked = [_ranked_fixture()]
    render_calls: list[str] = []

    class _CountingReportBuilder:
        def render_daily_digest(self, digest, *, title=None, acquisition_status_label="", fetch_error=""):  # type: ignore[no-untyped-def]
            del digest, title, acquisition_status_label, fetch_error
            render_calls.append("render")
            return "<html><body>fixture</body></html>"

    def fake_build_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
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
        return DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_LATEST_MODE,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
            feed_url="https://export.arxiv.org/api/query",
            profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            ranked=ranked,
            frontier_report=frontier_report,
            total_fetched=1,
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        )

    monkeypatch.setattr(FrontierCompassApp, "build_daily_digest", fake_build_daily_digest)
    app.report_builder = _CountingReportBuilder()  # type: ignore[assignment]

    result = app.materialize_daily_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=20,
        cache_dir=tmp_path,
        cache_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
        output_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
        force_fetch=True,
    )

    assert result.display_source == "freshly fetched"
    assert len(render_calls) == 2


def test_history_uses_cache_status_when_report_artifact_is_missing(tmp_path: Path) -> None:
    ranked = [_ranked_fixture()]
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                outcome="same-day-cache",
                live_outcome="live-success",
                cache_status="same-day-cache",
            ),
        ),
        total_fetched=1,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    cache_path = tmp_path / "data" / "cache" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")
    report_dir = tmp_path / "reports" / "daily"
    report_dir.mkdir(parents=True, exist_ok=True)

    history = load_recent_history(
        cache_dir=cache_path.parent,
        report_dir=report_dir,
        limit=5,
    )

    assert history[0].fetch_status == "same-day cache"
