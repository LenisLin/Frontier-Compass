from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import frontier_compass
import frontier_compass.api as public_api
from frontier_compass import (
    DailyRunResult,
    FrontierCompassRunner,
    LocalUISession,
    load_recent_history,
    prepare_ui_session,
    run_daily,
)
from frontier_compass.cli.main import build_parser, main
from frontier_compass.common.source_bundles import SOURCE_BUNDLE_BIOMEDICAL
from frontier_compass.storage.schema import DailyDigest, PaperRecord, RankedPaper
from frontier_compass.ui.app import DailyBootstrapResult, FrontierCompassApp


def test_surface_freeze_exports_match_between_package_root_and_api() -> None:
    assert frontier_compass.__all__ == [
        "__version__",
        "FrontierCompassRunner",
        "DailyRunResult",
        "LocalUISession",
        "run_daily",
        "prepare_ui_session",
        "load_recent_history",
    ]
    assert public_api.__all__ == [
        "FrontierCompassRunner",
        "DailyRunResult",
        "LocalUISession",
        "run_daily",
        "prepare_ui_session",
        "load_recent_history",
    ]
    assert frontier_compass.__all__[1:] == public_api.__all__
    assert frontier_compass.FrontierCompassRunner is FrontierCompassRunner
    assert frontier_compass.DailyRunResult is DailyRunResult
    assert frontier_compass.LocalUISession is LocalUISession
    assert frontier_compass.run_daily is run_daily
    assert frontier_compass.prepare_ui_session is prepare_ui_session
    assert frontier_compass.load_recent_history is load_recent_history
    assert frontier_compass.run_daily is public_api.run_daily
    assert frontier_compass.prepare_ui_session is public_api.prepare_ui_session
    assert frontier_compass.load_recent_history is public_api.load_recent_history
    assert not hasattr(frontier_compass, "FrontierCompassApp")


def test_surface_freeze_shortest_python_path_uses_public_run_daily(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = _sample_digest()
    cache_path = tmp_path / "data" / "cache" / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = tmp_path / "reports" / "daily" / "frontier_compass_bundle_biomedical_2026-03-24.html"

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["selected_source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 80
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(public_api, "_DEFAULT_RUNNER", FrontierCompassRunner(app=app))

    result = run_daily(requested_date=date(2026, 3, 24))

    assert isinstance(result, DailyRunResult)
    assert result.digest.category == SOURCE_BUNDLE_BIOMEDICAL
    assert result.fetch_status_label == "same-day cache"
    assert result.cache_path == cache_path
    assert result.report_path == report_path


def test_surface_freeze_cli_help_emphasizes_run_daily_then_ui() -> None:
    help_text = build_parser().format_help()

    assert "Shortest local path: frontier-compass run-daily, then frontier-compass ui." in help_text
    assert "Use frontier-compass history to inspect recent persisted runs." in help_text
    assert "run-daily" in help_text
    assert "ui" in help_text
    assert "Local inspection helper:" in help_text
    assert "Compatibility explicit build" in help_text


def test_surface_freeze_ui_print_command_exposes_same_digest_story(capsys) -> None:
    assert main(["ui", "--print-command", "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Requested date: 2026-03-24" in output
    assert "Source path: default public bundle (arXiv + bioRxiv)" in output
    assert "Streamlit app:" in output
    assert "Launch command:" in output
    assert "--requested-date 2026-03-24" in output
    assert "--source biomedical" not in output
    assert "--max-results 80" not in output


def test_surface_freeze_readme_documents_primary_workflow_and_scope() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "`Digest`" in readme
    assert "Frontier Report" in readme
    assert "Configure Zotero once in `configs/user_defaults.json`" in readme
    assert "`Daily Full Report`" in readme
    assert "`Most Relevant to Your Zotero`" in readme
    assert "`Other Frontier Signals`" in readme
    assert "Python API" in readme
    assert "local CLI" in readme
    assert "local interactive UI" in readme
    assert "`default_zotero_db_path`" in readme
    assert "`default_zotero_export_path`" in readme
    assert "frontier-compass ui" in readme
    assert "frontier-compass run-daily --today 2026-03-24" in readme
    assert "from frontier_compass import run_daily" in readme
    assert "--zotero-db-path /path/to/zotero.sqlite" in readme
    assert "--zotero-export path/to/zotero-export.csl.json" in readme
    assert "default public bundle (arXiv + bioRxiv)" in readme
    assert "biomedical-multisource" in readme
    assert "compatibility" in readme.lower()
    assert "zero-token" in readme
    assert "no full-text reading" in readme
    assert "docs/provenance.md" in readme


def test_surface_freeze_provenance_doc_covers_dates_freshness_and_outputs() -> None:
    provenance_doc = Path("docs/provenance.md").read_text(encoding="utf-8")

    assert "requested date" in provenance_doc.lower()
    assert "effective displayed date" in provenance_doc.lower()
    assert "same-day cache" in provenance_doc
    assert "older compatible cache reused after fetch failure" in provenance_doc
    assert "report mode" in provenance_doc.lower()
    assert "cost mode" in provenance_doc.lower()
    assert "data/cache/" in provenance_doc
    assert "reports/daily/" in provenance_doc
    assert ".eml" in provenance_doc


def _sample_digest() -> DailyDigest:
    return DailyDigest(
        source="multisource",
        category=SOURCE_BUNDLE_BIOMEDICAL,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(SOURCE_BUNDLE_BIOMEDICAL),
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
        source_counts={"arxiv": 1, "biorxiv": 0},
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical",
        mode_kind="source-bundle",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
