from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import frontier_compass
import frontier_compass.api as public_api
import frontier_compass.storage as public_storage
from frontier_compass import (
    DailyRunResult,
    FrontierCompassRunner,
    LocalUISession,
    __version__,
    load_recent_history,
    prepare_ui_session,
    run_daily,
)
from frontier_compass.storage import DailyDigest, PaperRecord, RankedPaper, RunHistoryEntry, UserInterestProfile
from frontier_compass.ui.app import DailyBootstrapResult, FrontierCompassApp


def test_public_package_exports_compact_library_surface() -> None:
    assert __version__ == "0.1.0"
    assert frontier_compass.__all__ == [
        "__version__",
        "FrontierCompassRunner",
        "DailyRunResult",
        "LocalUISession",
        "run_daily",
        "prepare_ui_session",
        "load_recent_history",
    ]
    assert FrontierCompassRunner is not None
    assert DailyRunResult is not None
    assert LocalUISession is not None
    assert run_daily is not None
    assert prepare_ui_session is not None
    assert load_recent_history is not None
    assert not hasattr(frontier_compass, "DailyDigest")
    assert not hasattr(frontier_compass, "PaperRecord")
    assert not hasattr(frontier_compass, "build_ui_launch_command")


def test_public_api_module_exports_supported_workflow_surface() -> None:
    assert public_api.__all__ == [
        "FrontierCompassRunner",
        "DailyRunResult",
        "LocalUISession",
        "run_daily",
        "prepare_ui_session",
        "load_recent_history",
    ]


def test_package_root_and_explicit_api_exports_are_same_objects() -> None:
    assert frontier_compass.FrontierCompassRunner is public_api.FrontierCompassRunner
    assert frontier_compass.DailyRunResult is public_api.DailyRunResult
    assert frontier_compass.LocalUISession is public_api.LocalUISession
    assert frontier_compass.run_daily is public_api.run_daily
    assert frontier_compass.prepare_ui_session is public_api.prepare_ui_session
    assert frontier_compass.load_recent_history is public_api.load_recent_history


def test_public_storage_module_exports_schema_contract() -> None:
    assert public_storage.__all__ == [
        "DailyDigest",
        "DailyFrontierReport",
        "FrontierReportHighlight",
        "FrontierReportSignal",
        "PaperRecord",
        "RankedPaper",
        "RunHistoryEntry",
        "UserInterestProfile",
    ]
    assert DailyDigest is not None
    assert PaperRecord is not None
    assert RankedPaper is not None
    assert RunHistoryEntry is not None
    assert UserInterestProfile is not None


def test_runner_run_daily_delegates_to_materialize_daily_digest(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "data" / "cache" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "reports" / "daily" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs == {
            "selected_source": "biomedical-latest",
            "requested_date": date(2026, 3, 24),
            "max_results": 40,
            "cache_dir": tmp_path / "data" / "cache",
            "force_fetch": True,
            "cache_path": cache_path,
            "output_path": report_path,
            "feed_url": "file:///tmp/frontier.xml",
            "zotero_export_path": tmp_path / "sample.csl.json",
            "allow_stale_cache": False,
        }
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    result = FrontierCompassRunner(app=app).run_daily(
        source="biomedical-latest",
        requested_date=date(2026, 3, 24),
        max_results=40,
        refresh=True,
        allow_stale_cache=False,
        cache_dir=tmp_path / "data" / "cache",
        cache_path=cache_path,
        report_path=report_path,
        feed_url="file:///tmp/frontier.xml",
        zotero_export_path=tmp_path / "sample.csl.json",
    )

    assert result.digest is digest
    assert result.cache_path == cache_path
    assert result.report_path == report_path
    assert result.display_source == "freshly fetched"
    assert result.fetch_status_label == "fresh source fetch"
    assert result.artifact_source_label == "fresh source fetch"


def test_top_level_run_daily_uses_default_runner(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == "biomedical-latest"
        assert kwargs["requested_date"] == date(2026, 3, 24)
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(public_api, "_DEFAULT_RUNNER", FrontierCompassRunner(app=app))

    result = run_daily(requested_date=date(2026, 3, 24))

    assert result.digest is digest
    assert result.fetch_status_label == "same-day cache"
    assert result.artifact_source_label == "same-day cache"


def test_runner_run_daily_auto_derives_range_full_from_request_window(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-25.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-25.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == "biomedical-latest"
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["start_date"] == date(2026, 3, 24)
        assert kwargs["end_date"] == date(2026, 3, 25)
        assert kwargs["fetch_scope"] == "range-full"
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="aggregated from day artifacts",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    result = FrontierCompassRunner(app=app).run_daily(
        requested_date=date(2026, 3, 24),
        start_date=date(2026, 3, 24),
        end_date=date(2026, 3, 25),
    )

    assert result.digest is digest
    assert result.display_source == "aggregated from day artifacts"


def test_runner_load_recent_history_delegates_to_app(monkeypatch) -> None:
    app = FrontierCompassApp()
    history_entries = [
        RunHistoryEntry(
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            category="biomedical-latest",
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            profile_basis="biomedical baseline",
            fetch_status="fresh source fetch",
            ranked_count=1,
            generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
        )
    ]

    def fake_recent_daily_runs(self, *, limit=10, cache_dir=None, report_dir=None):  # type: ignore[no-untyped-def]
        assert self is app
        assert limit == 5
        assert cache_dir == Path("data/cache")
        assert report_dir == Path("reports/daily")
        return history_entries

    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    result = FrontierCompassRunner(app=app).load_recent_history(limit=5)

    assert result == history_entries


def test_runner_prepare_ui_session_builds_current_run_and_recent_history(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "data" / "cache" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "reports" / "daily" / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    history_entries = [_sample_history_entry()]

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == "biomedical-latest"
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 40
        assert kwargs["force_fetch"] is True
        assert kwargs["allow_stale_cache"] is False
        assert kwargs["cache_dir"] == tmp_path / "data" / "cache"
        assert kwargs["zotero_export_path"] == tmp_path / "sample.csl.json"
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    def fake_recent_daily_runs(self, *, limit=10, cache_dir=None, report_dir=None):  # type: ignore[no-untyped-def]
        assert self is app
        assert limit == 3
        assert cache_dir == tmp_path / "data" / "cache"
        assert report_dir == tmp_path / "reports" / "daily"
        return history_entries

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    session = FrontierCompassRunner(app=app).prepare_ui_session(
        requested_date=date(2026, 3, 24),
        max_results=40,
        refresh=True,
        allow_stale_cache=False,
        cache_dir=tmp_path / "data" / "cache",
        report_dir=tmp_path / "reports" / "daily",
        zotero_export_path=tmp_path / "sample.csl.json",
        history_limit=3,
    )

    assert session.current_run.display_source == "loaded from cache"
    assert session.fetch_status_label == "same-day cache"
    assert session.artifact_source_label == "same-day cache"
    assert session.requested_date == date(2026, 3, 24)
    assert session.effective_date == date(2026, 3, 24)
    assert session.profile_basis_label == "biomedical baseline"
    assert session.cache_path == cache_path
    assert session.report_path == report_path
    assert session.recent_history == tuple(history_entries)
    assert session.recent_history_error == ""


def test_top_level_prepare_ui_session_uses_default_runner(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == "biomedical-latest"
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", lambda self, **kwargs: [_sample_history_entry()])
    monkeypatch.setattr(public_api, "_DEFAULT_RUNNER", FrontierCompassRunner(app=app))

    session = prepare_ui_session(requested_date=date(2026, 3, 24))

    assert session.fetch_status_label == "fresh source fetch"
    assert session.report_path == report_path
    assert len(session.recent_history) == 1


def test_runner_prepare_ui_session_auto_derives_range_full_from_request_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    digest.fetch_scope = "range-full"
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-25.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-25.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == "biomedical-latest"
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["start_date"] == date(2026, 3, 24)
        assert kwargs["end_date"] == date(2026, 3, 25)
        assert kwargs["fetch_scope"] == "range-full"
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="aggregated from day artifacts",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", lambda self, **kwargs: [_sample_history_entry()])

    session = FrontierCompassRunner(app=app).prepare_ui_session(
        requested_date=date(2026, 3, 24),
        start_date=date(2026, 3, 24),
        end_date=date(2026, 3, 25),
    )

    assert session.fetch_scope == "range-full"
    assert session.current_run.display_source == "aggregated from day artifacts"


def _sample_digest() -> DailyDigest:
    return DailyDigest(
        source="arxiv",
        category="biomedical-latest",
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile("biomedical-latest"),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher",),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.20001",
                ),
                score=0.88,
                recommendation_summary="Strong biomedical match for reviewer triage.",
            )
        ],
        searched_categories=("q-bio", "q-bio.GN", "cs.CV"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 1},
        total_fetched=1,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )


def _sample_history_entry() -> RunHistoryEntry:
    return RunHistoryEntry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        category="biomedical-latest",
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        profile_basis="biomedical baseline",
        fetch_status="same-day cache",
        ranked_count=1,
        generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
    )
