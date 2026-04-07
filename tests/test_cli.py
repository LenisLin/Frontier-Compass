from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from frontier_compass.api import DailyRunResult, FrontierCompassRunner, LocalUISession
from frontier_compass.cli.main import build_parser, main
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.common.source_bundles import (
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
    SOURCE_BUNDLE_BIOMEDICAL,
)
from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    RankedPaper,
    RequestWindow,
    RunHistoryEntry,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui.app import (
    BIOMEDICAL_DAILY_MODE,
    BIOMEDICAL_DISCOVERY_MODE,
    BIOMEDICAL_LATEST_MODE,
    BIOMEDICAL_MULTISOURCE_MODE,
    DEFAULT_ARXIV_CATEGORY,
    DailyBootstrapResult,
    FrontierCompassApp,
)
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder


ARXIV_DAILY_XML = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns="http://www.w3.org/2005/Atom">
  <id>http://rss.arxiv.org/atom/q-bio</id>
  <title>q-bio updates on arXiv.org</title>
  <updated>2026-03-23T04:00:05.907047+00:00</updated>
  <entry>
    <id>oai:arXiv.org:2603.19236v1</id>
    <title>Single-cell transcriptomics foundation models</title>
    <updated>2026-03-23T04:00:05.965050+00:00</updated>
    <link href="https://arxiv.org/abs/2603.19236" rel="alternate" type="text/html"/>
    <summary>arXiv:2603.19236v1 Announce Type: new Abstract: A multimodal bioinformatics model for single-cell perturbation data.</summary>
    <category term="q-bio.GN"/>
    <category term="q-bio.QM"/>
    <published>2026-03-23T00:00:00-04:00</published>
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:creator>A Researcher, B Curator</dc:creator>
  </entry>
</feed>
"""

ZOTERO_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zotero" / "sample_library.csl.json"


def _write_digest_cache(path: Path, digest: DailyDigest) -> None:
    if digest.frontier_report is None and digest.ranked:
        digest.frontier_report = build_daily_frontier_report(
            paper_pool=[item.paper for item in digest.ranked],
            ranked_papers=digest.ranked,
            requested_date=digest.requested_target_date,
            effective_date=digest.effective_display_date,
            source=digest.source,
            mode=digest.category,
            mode_label=digest.mode_label or digest.category,
            mode_kind=digest.mode_kind,
            searched_categories=digest.searched_categories,
            total_fetched=digest.total_fetched,
        )
    path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")


def test_cli_no_args_prints_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frontier_compass.cli.main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Shortest local path: frontier-compass run-daily, then frontier-compass ui." in result.stdout
    assert "Use frontier-compass history to inspect recent persisted runs." in result.stdout
    assert "Primary local CLI path:" in result.stdout
    assert "Primary local UI path:" in result.stdout
    assert "Local inspection helper:" in result.stdout
    assert "Compatibility email delivery" in result.stdout
    assert "Secondary demo command:" in result.stdout


def test_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frontier_compass.cli.main", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Shortest local path: frontier-compass run-daily, then frontier-compass ui." in result.stdout
    assert "Use frontier-compass history to inspect recent persisted runs." in result.stdout
    assert "Compatibility commands remain available for explicit builds, email delivery, and demos." in result.stdout
    assert "Exact Streamlit launch:" in result.stdout
    assert "Primary local CLI path:" in result.stdout
    assert "Primary local UI path:" in result.stdout
    assert "Local inspection helper:" in result.stdout
    assert "Compatibility explicit build" in result.stdout
    assert "Compatibility email delivery" in result.stdout
    assert "Secondary demo command:" in result.stdout


def test_cli_daily_help_lists_fixed_modes() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frontier_compass.cli.main", "daily", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert BIOMEDICAL_LATEST_MODE in result.stdout
    assert BIOMEDICAL_MULTISOURCE_MODE in result.stdout
    assert BIOMEDICAL_DISCOVERY_MODE in result.stdout
    assert BIOMEDICAL_DAILY_MODE in result.stdout
    assert "default public 2-source bundle" in result.stdout


def test_cli_run_daily_accepts_multisource_mode(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[_sample_ranked_paper_for_cli(score=0.82)],
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        source_counts={"arxiv": 1, "biorxiv": 1, "medrxiv": 1},
        total_fetched=3,
        mode_label="Biomedical multisource",
        mode_kind="multisource",
    )

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["source"] == BIOMEDICAL_MULTISOURCE_MODE
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_multisource_biomedical-multisource_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["run-daily", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Source path: compatibility 3-source run (arXiv + bioRxiv + medRxiv) (cli)" in output
    assert "Source run: compatibility 3-source run (arXiv + bioRxiv + medRxiv)" in output


def test_cli_ui_print_command_accepts_multisource_mode(capsys) -> None:
    assert main(["ui", "--print-command", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Source path: compatibility 3-source run (arXiv + bioRxiv + medRxiv) (cli)" in output
    assert f"--source {BIOMEDICAL_MULTISOURCE_MODE}" in output


def test_cli_ui_print_command_requires_explicit_profile_source_when_both_zotero_inputs_are_supplied(capsys) -> None:
    assert (
        main(
            [
                "ui",
                "--print-command",
                "--today",
                "2026-03-24",
                "--zotero-export",
                str(ZOTERO_FIXTURE_PATH),
                "--zotero-db-path",
                "/tmp/zotero.sqlite",
            ]
        )
        == 1
    )
    error_output = capsys.readouterr().err

    assert "Both a Zotero export path and a Zotero DB path were supplied without an explicit profile_source." in error_output
    assert "profile_source='zotero_export'" in error_output
    assert "profile_source='live_zotero_db'" in error_output


def test_cli_parser_accepts_multisource_mode_for_all_public_daily_commands() -> None:
    parser = build_parser()

    run_daily_args = parser.parse_args(
        ["run-daily", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24"]
    )
    ui_args = parser.parse_args(
        ["ui", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24", "--print-command"]
    )
    daily_args = parser.parse_args(
        ["daily", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24"]
    )
    deliver_args = parser.parse_args(
        ["deliver-daily", "--mode", BIOMEDICAL_MULTISOURCE_MODE, "--today", "2026-03-24"]
    )

    assert run_daily_args.mode == BIOMEDICAL_MULTISOURCE_MODE
    assert ui_args.mode == BIOMEDICAL_MULTISOURCE_MODE
    assert daily_args.mode == BIOMEDICAL_MULTISOURCE_MODE
    assert deliver_args.mode == BIOMEDICAL_MULTISOURCE_MODE


def test_cli_run_daily_accepts_ai_for_medicine_mode(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_AI_FOR_MEDICINE)

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["source"] == SOURCE_BUNDLE_AI_FOR_MEDICINE
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_multisource_ai-for-medicine_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_multisource_ai-for-medicine_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["run-daily", "--mode", SOURCE_BUNDLE_AI_FOR_MEDICINE, "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Source path: advanced AI for medicine bundle override (cli)" in output
    assert "Source run: advanced AI for medicine bundle override" in output


def test_cli_demo_report_writes_file(tmp_path: Path) -> None:
    output = tmp_path / "report.html"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "frontier_compass.cli.main",
            "demo-report",
            "--output",
            str(output),
            "--limit",
            "3",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert output.exists()
    assert "FrontierCompass Report" in output.read_text(encoding="utf-8")


def test_cli_daily_writes_cache_and_report(tmp_path: Path) -> None:
    feed = tmp_path / "arxiv.xml"
    feed.write_text(ARXIV_DAILY_XML, encoding="utf-8")
    cache = tmp_path / "daily.json"
    report = tmp_path / "daily.html"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "frontier_compass.cli.main",
            "daily",
            "--today",
            "2026-03-23",
            "--feed-url",
            feed.as_uri(),
            "--cache",
            str(cache),
            "--output",
            str(report),
            "--max-results",
            "5",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert cache.exists()
    assert report.exists()
    assert "Fetch status: fresh source fetch" in result.stdout
    assert "Tracks: Digest + Frontier Report" in result.stdout
    assert "Requested date: 2026-03-23" in result.stdout
    assert "Effective displayed date: 2026-03-23" in result.stdout
    assert "Latest-available display fallback: no" in result.stdout
    assert "Stale cache fallback: no" in result.stdout
    assert "Display basis: Strict same-day results" in result.stdout
    assert "Source run: q-bio" in result.stdout
    assert "Searched categories: q-bio" in result.stdout
    assert "Strict same-day fetched: 1" in result.stdout
    assert "Strict same-day ranked: 1" in result.stdout
    assert "Total fetched: 1" in result.stdout
    assert "Total ranked pool: 1" in result.stdout
    assert "Total displayed:" in result.stdout
    assert '"category": "q-bio"' in cache.read_text(encoding="utf-8")
    assert "Score" in report.read_text(encoding="utf-8")


def test_cli_daily_defaults_to_reviewer_mode(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 80
        assert kwargs["refresh"] is True
        assert kwargs["allow_stale_cache"] is False
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--today", "2026-03-24", "--max-results", "80"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: fresh source fetch" in output
    assert "Source run: default public bundle (arXiv + bioRxiv)" in output
    assert "Advanced source id:" not in output


def test_cli_daily_biomedical_mode_prints_bundle_summary(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == BIOMEDICAL_DAILY_MODE
        digest = DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_DAILY_MODE,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
            feed_url="",
            profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DAILY_MODE),
            ranked=[
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.21001v1",
                        title="Single-cell perturbation models for transcriptomics",
                        summary="Bioinformatics workflow for same-day biomedical ranking.",
                        authors=("A Biologist",),
                        categories=("q-bio.GN", "q-bio.QM"),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.21001",
                    ),
                    score=0.81,
                    recommendation_summary="Strong biomedical match.",
                )
            ],
            searched_categories=("q-bio", "q-bio.GN", "q-bio.QM"),
            per_category_counts={"q-bio": 1, "q-bio.GN": 1, "q-bio.QM": 0},
            total_fetched=2,
            feed_urls={
                "q-bio": "https://rss.arxiv.org/atom/q-bio",
                "q-bio.GN": "https://rss.arxiv.org/atom/q-bio.GN",
                "q-bio.QM": "https://rss.arxiv.org/atom/q-bio.QM",
            },
            mode_label="Biomedical daily",
            mode_kind="bundle",
            mode_notes="Bundle-based same-day q-bio scouting.",
        )
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-daily_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-daily_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--mode", BIOMEDICAL_DAILY_MODE, "--today", "2026-03-24", "--max-results", "80"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: fresh source fetch" in output
    assert "Requested date: 2026-03-24" in output
    assert "Effective displayed date: 2026-03-24" in output
    assert "Latest-available display fallback: no" in output
    assert "Stale cache fallback: no" in output
    assert "Source run: advanced q-bio bundle mode" in output
    assert f"Advanced source id: {BIOMEDICAL_DAILY_MODE}" in output
    assert "Advanced source label: Biomedical daily" in output
    assert "Advanced source kind: bundle" in output
    assert "Searched categories: q-bio, q-bio.GN, q-bio.QM" in output
    assert "Mode notes: Bundle-based same-day q-bio scouting." in output
    assert "Strict same-day fetched: 2" in output
    assert "Strict same-day ranked: 1" in output
    assert "Total fetched: 2" in output
    assert "Total ranked pool: 1" in output
    assert "Total displayed: 1" in output
    assert "Per-category counts: q-bio: 1 | q-bio.GN: 1 | q-bio.QM: 0" in output


def test_cli_daily_discovery_mode_prints_search_metadata(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == BIOMEDICAL_DISCOVERY_MODE
        digest = DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_DISCOVERY_MODE,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
            feed_url="https://export.arxiv.org/api/query",
            profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DISCOVERY_MODE),
            ranked=[
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.22001v1",
                        title="Same-day biomedical discovery match",
                        summary="Discovery workflow for same-day ranking.",
                        authors=("A Scientist",),
                        categories=("q-bio.GN", "cs.LG"),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.22001",
                    ),
                    score=0.88,
                    recommendation_summary="Strong discovery-mode biomedical match.",
                )
            ],
            searched_categories=("q-bio", "q-bio.GN", "cs.LG", "stat.ML"),
            per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1, "stat.ML": 0},
            total_fetched=4,
            feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
            mode_label="Biomedical discovery",
            mode_kind="hybrid",
            mode_notes="Hybrid q-bio bundle plus fixed broader arXiv API discovery queries.",
            search_profile_label="broader-biomedical-discovery-v1",
            search_queries=(
                "((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:\"single-cell\"))",
                (
                    "((cat:q-bio OR cat:stat.ML) AND (all:biomedical OR all:medical OR all:clinical "
                    "OR all:pathology OR all:histopathology OR all:radiology OR all:microscopy))"
                ),
            ),
        )
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-discovery_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-discovery_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--mode", BIOMEDICAL_DISCOVERY_MODE, "--today", "2026-03-24", "--max-results", "80"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: fresh source fetch" in output
    assert "Requested date: 2026-03-24" in output
    assert "Effective displayed date: 2026-03-24" in output
    assert "Latest-available display fallback: no" in output
    assert "Stale cache fallback: no" in output
    assert "Source run: advanced biomedical discovery mode" in output
    assert f"Advanced source id: {BIOMEDICAL_DISCOVERY_MODE}" in output
    assert "Advanced source label: Biomedical discovery" in output
    assert "Advanced source kind: hybrid" in output
    assert "Search profile: broader-biomedical-discovery-v1" in output
    assert "Mode notes: Hybrid q-bio bundle plus fixed broader arXiv API discovery queries." in output
    assert 'Query 1: ((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:"single-cell"))' in output
    assert (
        "Query 2: ((cat:q-bio OR cat:stat.ML) AND (all:biomedical OR all:medical OR all:clinical "
        "OR all:pathology OR all:histopathology OR all:radiology OR all:microscopy))"
    ) in output
    assert "Strict same-day fetched: 4" in output
    assert "Strict same-day ranked: 1" in output
    assert "Total fetched: 4" in output
    assert "Total ranked pool: 1" in output
    assert "Total displayed: 1" in output


def test_cli_daily_latest_mode_prints_requested_and_effective_dates(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == BIOMEDICAL_LATEST_MODE
        digest = DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_LATEST_MODE,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
            feed_url="https://export.arxiv.org/api/query",
            profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            ranked=[
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.21990v1",
                        title="Latest available biomedical release match",
                        summary="Fallback-mode biomedical ranking.",
                        authors=("A Scientist",),
                        categories=("q-bio.GN", "cs.LG"),
                        published=date(2026, 3, 23),
                        url="https://arxiv.org/abs/2603.21990",
                    ),
                    score=0.9,
                    recommendation_summary="Latest available fallback biomedical match.",
                )
            ],
            searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
            per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
            total_fetched=3,
            feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            mode_notes="Strict same-day first, latest available fallback second.",
            search_profile_label="broader-biomedical-discovery-v1",
            search_queries=("((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:\"single-cell\"))",),
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 23),
            strict_same_day_fetched=0,
            strict_same_day_ranked=0,
            used_latest_available_fallback=True,
        )
        return DailyRunResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--mode", BIOMEDICAL_LATEST_MODE, "--today", "2026-03-24", "--max-results", "80"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: fresh source fetch" in output
    assert "Requested date: 2026-03-24" in output
    assert "Effective displayed date: 2026-03-23" in output
    assert "Latest-available display fallback: yes" in output
    assert "Stale cache fallback: no" in output
    assert "Display basis: Latest available fallback results" in output
    assert "Source run: legacy latest-available biomedical mode" in output
    assert f"Advanced source id: {BIOMEDICAL_LATEST_MODE}" in output
    assert "Advanced source label: Biomedical latest available" in output
    assert "Advanced source kind: latest-available-hybrid" in output
    assert "Strict same-day fetched: 0" in output
    assert "Strict same-day ranked: 0" in output
    assert "Total fetched: 3" in output
    assert "Total ranked pool: 1" in output
    assert "Total displayed: 1" in output


def test_cli_daily_reuses_same_date_cache_after_fetch_failure(monkeypatch, capsys, tmp_path: Path) -> None:
    cache_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.html"
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)
    _write_digest_cache(cache_path, digest)

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == SOURCE_BUNDLE_BIOMEDICAL
        report_path.write_text(
            HtmlReportBuilder().render_daily_digest(
                digest,
                acquisition_status_label="same-date cache reused after fetch failure",
                fetch_error="upstream arXiv timeout",
            ),
            encoding="utf-8",
        )
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="same-date cache reused after fetch failure",
            fetch_error="upstream arXiv timeout",
            fetch_status_label="same-date cache reused after fetch failure",
            artifact_source_label="same-day cache",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert (
        main(
            [
                "daily",
                "--today",
                "2026-03-24",
                "--max-results",
                "80",
                "--cache",
                str(cache_path),
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Fetch status: same-date cache reused after fetch failure" in output
    assert "Fresh fetch error: upstream arXiv timeout" in output
    assert f"Cache: {cache_path}" in output
    assert f"Report: {report_path}" in output
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "Fetch status" in html
    assert "same-date cache reused after fetch failure" in html
    assert "Fresh fetch error" in html


def test_cli_daily_fails_clearly_when_no_same_date_cache_exists(monkeypatch, capsys) -> None:
    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        raise RuntimeError(
            "Fresh source fetch failed for biomedical on 2026-03-24 and no same-date cache is available: "
            "upstream arXiv timeout"
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--today", "2026-03-24", "--max-results", "80"]) == 1
    error_output = capsys.readouterr().err

    assert "Fresh source fetch failed for biomedical on 2026-03-24" in error_output
    assert "no same-date cache is available" in error_output


def test_cli_daily_rejects_feed_url_with_fixed_modes() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["daily", "--mode", BIOMEDICAL_DAILY_MODE, "--feed-url", "https://example.com/feed.xml"])

    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        main(["daily", "--mode", BIOMEDICAL_DISCOVERY_MODE, "--feed-url", "https://example.com/feed.xml"])

    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        main(["daily", "--mode", BIOMEDICAL_LATEST_MODE, "--feed-url", "https://example.com/feed.xml"])

    assert excinfo.value.code == 2


def test_cli_deliver_daily_dry_run_writes_eml_and_prints_provenance(monkeypatch, capsys, tmp_path: Path) -> None:
    cache_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.html"
    eml_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.eml"
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(
            digest,
            acquisition_status_label="same-date cache reused after fetch failure",
            fetch_error="upstream arXiv timeout",
        ),
        encoding="utf-8",
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 80
        assert kwargs["force_fetch"] is False
        assert kwargs["cache_path"] == cache_path
        assert kwargs["output_path"] == report_path
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert (
        main(
            [
                "deliver-daily",
                "--today",
                "2026-03-24",
                "--cache",
                str(cache_path),
                "--report-path",
                str(report_path),
                "--email-to",
                "reviewer@example.com",
                "--email-from",
                "frontier@example.com",
                "--eml-output",
                str(eml_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Fetch status: same-date cache reused after fetch failure" in output
    assert "Artifact source: same-day cache" in output
    assert "Fresh fetch error: upstream arXiv timeout" in output
    assert "Delivery: dry-run .eml written" in output
    assert f"EML: {eml_path}" in output
    assert eml_path.exists()
    assert "same-date cache reused after fetch failure" in eml_path.read_text(encoding="utf-8")


def test_cli_deliver_daily_send_requires_smtp_settings(monkeypatch, capsys, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.21990v1",
                    title="Fresh reviewer path paper",
                    summary="Fresh reviewer-safe coverage.",
                    authors=("A Scientist",),
                    categories=("q-bio.GN", "cs.LG"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.21990",
                ),
                score=0.87,
                recommendation_summary="Fresh reviewer path biomedical match.",
            )
        ],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(
            digest,
            acquisition_status_label="fresh source fetch",
        ),
        encoding="utf-8",
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return DailyBootstrapResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    for key in (
        "FRONTIER_COMPASS_SMTP_HOST",
        "FRONTIER_COMPASS_SMTP_PORT",
        "FRONTIER_COMPASS_SMTP_SECURITY",
        "FRONTIER_COMPASS_SMTP_USERNAME",
        "FRONTIER_COMPASS_SMTP_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    assert (
        main(
            [
                "deliver-daily",
                "--today",
                "2026-03-24",
                "--report-path",
                str(report_path),
                "--email-to",
                "reviewer@example.com",
                "--email-from",
                "frontier@example.com",
                "--send",
            ]
        )
        == 1
    )
    error_output = capsys.readouterr().err

    assert "Missing SMTP settings for --send:" in error_output
    assert "FRONTIER_COMPASS_SMTP_HOST" in error_output
    assert "Use dry-run without --send to review a .eml file instead." in error_output


def test_cli_daily_zotero_export_prints_profile_provenance(monkeypatch, capsys, tmp_path: Path) -> None:
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(SOURCE_BUNDLE_BIOMEDICAL),
        export_path=ZOTERO_FIXTURE_PATH,
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["zotero_export_path"] == ZOTERO_FIXTURE_PATH
        cache_path = FrontierCompassApp.default_daily_cache_path(
            SOURCE_BUNDLE_BIOMEDICAL,
            date(2026, 3, 24),
            zotero_export_path=ZOTERO_FIXTURE_PATH,
        )
        report_path = FrontierCompassApp.report_path_for_cache_path(cache_path)
        digest = DailyDigest(
            source="multisource",
            category=SOURCE_BUNDLE_BIOMEDICAL,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
            feed_url="",
            profile=profile,
            ranked=[_sample_ranked_paper_for_cli(score=0.91)],
            searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
            per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
            total_fetched=2,
            source_counts={"arxiv": 1, "biorxiv": 1},
            feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
            mode_label="Biomedical",
            mode_kind="source-bundle",
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        )
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert (
        main(
            [
                "daily",
                "--today",
                "2026-03-24",
                "--max-results",
                "80",
                "--zotero-export",
                str(ZOTERO_FIXTURE_PATH),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Profile basis: biomedical baseline + Zotero export" in output
    assert f"Zotero export: {ZOTERO_FIXTURE_PATH.name}" in output
    assert "Zotero items parsed / used: 4 / 3" in output
    assert "Top Zotero signals:" in output


def test_cli_daily_uses_config_defaults_when_args_omitted(monkeypatch, capsys, tmp_path: Path) -> None:
    config_path = _write_user_defaults_config(
        tmp_path,
        {
            "default_mode": BIOMEDICAL_DISCOVERY_MODE,
            "default_max_results": 33,
            "default_zotero_export_path": str(ZOTERO_FIXTURE_PATH),
        },
    )
    digest = _sample_daily_digest_for_cli(BIOMEDICAL_DISCOVERY_MODE)

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_DISCOVERY_MODE
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 33
        assert kwargs["force_fetch"] is True
        assert kwargs["zotero_export_path"] == ZOTERO_FIXTURE_PATH
        return DailyBootstrapResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-discovery_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-discovery_2026-03-24.html",
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert main(["daily", "--config", str(config_path), "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert f"Config: loaded from {config_path}" in output
    assert "Source path: advanced biomedical discovery mode (config)" in output
    assert "Max results: 33 (config)" in output
    assert f"Zotero export: {ZOTERO_FIXTURE_PATH} (config)" in output


def test_cli_daily_cli_values_override_config(monkeypatch, capsys, tmp_path: Path) -> None:
    config_path = _write_user_defaults_config(
        tmp_path,
        {
            "default_mode": BIOMEDICAL_LATEST_MODE,
            "default_max_results": 33,
            "default_zotero_export_path": str(ZOTERO_FIXTURE_PATH),
        },
    )
    override_zotero = tmp_path / "override.csl.json"
    digest = _sample_daily_digest_for_cli(BIOMEDICAL_DAILY_MODE)

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_DAILY_MODE
        assert kwargs["max_results"] == 7
        assert kwargs["force_fetch"] is True
        assert kwargs["zotero_export_path"] == override_zotero
        return DailyBootstrapResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-daily_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-daily_2026-03-24.html",
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert (
        main(
            [
                "daily",
                "--config",
                str(config_path),
                "--today",
                "2026-03-24",
                "--mode",
                BIOMEDICAL_DAILY_MODE,
                "--max-results",
                "7",
                "--zotero-export",
                str(override_zotero),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Source path: advanced q-bio bundle mode (cli)" in output
    assert "Max results: 7 (cli)" in output
    assert f"Zotero export: {override_zotero} (cli)" in output


def test_cli_deliver_daily_uses_config_email_defaults(monkeypatch, capsys, tmp_path: Path) -> None:
    config_path = _write_user_defaults_config(
        tmp_path,
        {
            "default_mode": BIOMEDICAL_LATEST_MODE,
            "default_max_results": 21,
            "default_email_to": ["reviewer@example.com", "second@example.com"],
            "default_email_from": "frontier@example.com",
        },
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    eml_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.eml"
    digest = _sample_daily_digest_for_cli(BIOMEDICAL_LATEST_MODE)
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(digest, acquisition_status_label="same-day cache"),
        encoding="utf-8",
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_LATEST_MODE
        assert kwargs["max_results"] == 21
        assert kwargs["force_fetch"] is False
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert main(["deliver-daily", "--config", str(config_path), "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert f"Config: loaded from {config_path}" in output
    assert "Email to: reviewer@example.com, second@example.com" in output
    assert "Email from: frontier@example.com" in output
    assert "Email to: reviewer@example.com, second@example.com (config)" in output
    assert "Email from: frontier@example.com (config)" in output
    assert "Delivery: dry-run .eml written" in output
    assert f"EML: {eml_path}" in output
    assert eml_path.exists()
    assert eml_path.stat().st_size > 0


def test_cli_run_daily_uses_config_dry_run_defaults(monkeypatch, capsys, tmp_path: Path) -> None:
    config_path = _write_user_defaults_config(
        tmp_path,
        {
            "default_mode": BIOMEDICAL_LATEST_MODE,
            "default_max_results": 44,
            "default_email_to": "reviewer@example.com, second@example.com",
            "default_email_from": "frontier@example.com",
            "default_allow_stale_cache": False,
            "default_generate_dry_run_email": True,
        },
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    digest = _sample_daily_digest_for_cli(BIOMEDICAL_LATEST_MODE)
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(digest, acquisition_status_label="same-day cache"),
        encoding="utf-8",
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_LATEST_MODE
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 44
        assert kwargs["force_fetch"] is False
        assert kwargs["allow_stale_cache"] is False
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    assert main(["run-daily", "--config", str(config_path), "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert f"Config: loaded from {config_path}" in output
    assert "Allow stale cache fallback: no (config)" in output
    assert "Dry-run email: yes (config)" in output
    assert "Email to: reviewer@example.com, second@example.com (config)" in output
    assert "Email from: frontier@example.com (config)" in output
    assert "Delivery: dry-run .eml written" in output
    eml_path = report_path.with_suffix(".eml")
    assert f"EML: {eml_path}" in output
    assert eml_path.exists()
    assert eml_path.stat().st_size > 0


def test_cli_run_daily_no_config_uses_built_in_defaults(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)
    cache_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.html"

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 80
        assert kwargs["refresh"] is False
        assert kwargs["allow_stale_cache"] is True
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
            fetch_status_label="same-day cache",
            artifact_source_label="same-day cache",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["run-daily", "--no-config", "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Config: disabled by --no-config" in output
    assert "Tracks: Digest + Frontier Report" in output
    assert "Source path: default public bundle (arXiv + bioRxiv) (built-in)" in output
    assert "Max results: 80 (built-in)" in output
    assert "Allow stale cache fallback: yes (built-in)" in output
    assert "Dry-run email: no (built-in)" in output
    assert "Delivery: not requested" in output


def test_cli_run_daily_range_window_derives_range_full(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)
    cache_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24_to_2026-03-25.json"
    report_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24_to_2026-03-25.html"

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["source"] == SOURCE_BUNDLE_BIOMEDICAL
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["start_date"] == date(2026, 3, 24)
        assert kwargs["end_date"] == date(2026, 3, 25)
        assert kwargs["fetch_scope"] == "range-full"
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="aggregated from day artifacts",
            fetch_status_label="aggregated from day artifacts",
            artifact_source_label="aggregated from day artifacts",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert (
        main(
            [
                "run-daily",
                "--today",
                "2026-03-24",
                "--start-date",
                "2026-03-24",
                "--end-date",
                "2026-03-25",
                "--no-dry-run-email",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Fetch scope: range-full (derived)" in output


def test_cli_run_daily_no_stale_cache_flag_overrides_default(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(BIOMEDICAL_LATEST_MODE)
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["allow_stale_cache"] is False
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
            fetch_status_label="same-day cache",
            artifact_source_label="same-day cache",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["run-daily", "--today", "2026-03-24", "--no-stale-cache"]) == 0
    output = capsys.readouterr().out

    assert "Allow stale cache fallback: no (cli)" in output


def test_cli_run_daily_prints_stale_cache_provenance(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 23, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper_for_cli(score=0.88)],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 23),
        strict_same_day_counts_known=False,
        stale_cache_source_requested_date=date(2026, 3, 23),
        stale_cache_source_effective_date=date(2026, 3, 23),
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs["allow_stale_cache"] is True
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="older compatible cache reused after fetch failure",
            fetch_error="upstream arXiv timeout",
            fetch_status_label="older compatible cache reused after fetch failure",
            artifact_source_label="older compatible cache",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["run-daily", "--today", "2026-03-24"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: older compatible cache reused after fetch failure" in output
    assert "Artifact source: older compatible cache" in output
    assert "Fresh fetch error: upstream arXiv timeout" in output
    assert "Latest-available display fallback: no" in output
    assert "Stale cache fallback: yes" in output
    assert "Stale cache source requested date: 2026-03-23" in output
    assert "Stale cache source effective date: 2026-03-23" in output


def test_cli_daily_routes_through_public_runner(monkeypatch, capsys, tmp_path: Path) -> None:
    digest = _sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL)
    cache_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_bundle_biomedical_2026-03-24.html"

    def fake_run_daily(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        assert kwargs == {
            "source": SOURCE_BUNDLE_BIOMEDICAL,
            "requested_date": date(2026, 3, 24),
            "max_results": 80,
            "refresh": True,
            "allow_stale_cache": False,
            "cache_path": None,
            "report_path": None,
            "feed_url": None,
            "profile_source": "baseline",
            "zotero_export_path": None,
        }
        return DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        )

    monkeypatch.setattr(FrontierCompassRunner, "run_daily", fake_run_daily)

    assert main(["daily", "--today", "2026-03-24", "--max-results", "80"]) == 0
    output = capsys.readouterr().out

    assert "Fetch status: fresh source fetch" in output
    assert "Source run: default public bundle (arXiv + bioRxiv)" in output


def test_cli_history_prints_recent_runs(monkeypatch, capsys) -> None:
    history_entries = [
        _sample_run_history_entry(
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
            fetch_status="fresh source fetch",
            report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
            eml_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.eml",
        )
    ]

    def fake_recent_daily_runs(self, *, limit=10, cache_dir=None, report_dir=None):  # type: ignore[no-untyped-def]
        assert limit == 10
        del self, cache_dir, report_dir
        return history_entries

    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    assert main(["history"]) == 0
    output = capsys.readouterr().out

    assert "Recent runs (current-contract first)" in output
    assert "2026-03-24 | Biomedical latest available" in output
    assert "Requested -> showing: 2026-03-24" in output
    assert "Request window: 2026-03-24" in output
    assert "Generated: 2026-03-27T02:09:45+00:00" in output
    assert (
        "fresh source fetch | ranked 12 | report deterministic/ready | zero-token | biomedical baseline | "
        "arxiv 12/12 [live-success; ready; fresh] | time 0.35s | zotero sample_library.csl.json | exploration 2"
    ) in output
    assert "LLM requested no | applied no | provider none | fallback none | time n/a" in output
    assert "Frontier Report present: yes" in output
    assert "Sources: arxiv fetched 12 / retained 12 [live-success; ready; fresh] (total 0.35s)" in output
    assert "Run timings: total 0.35s" in output
    assert "Report: reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html" in output
    assert "Cache: data/cache/frontier_compass_arxiv_biomedical-latest_2026-03-24.json" in output
    assert "EML: reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.eml" in output


def test_cli_history_separates_compatibility_entries(monkeypatch, capsys) -> None:
    current_entry = _sample_run_history_entry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
        fetch_status="fresh source fetch",
        report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
    )
    compatibility_entry = _sample_run_history_entry(
        requested_date=date(2026, 3, 25),
        effective_date=date(2026, 3, 25),
        generated_at=datetime(2026, 3, 27, 3, 9, 45, tzinfo=timezone.utc),
        fetch_status="fresh source fetch",
        report_path="reports/daily/live_validation/frontier_compass_arxiv_biomedical-latest_2026-03-25_zotero-old.html",
        compatibility_status="archived",
        compatibility_reasons=("archived live-validation artifact",),
    )

    def fake_recent_daily_runs(self, *, limit=10, cache_dir=None, report_dir=None):  # type: ignore[no-untyped-def]
        assert limit == 10
        del self, cache_dir, report_dir
        return [current_entry, compatibility_entry]

    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    assert main(["history"]) == 0
    output = capsys.readouterr().out

    assert "Recent runs (current-contract first)" in output
    assert "Compatibility / archived entries" in output
    assert "Compatibility: compatibility / archived: archived live-validation artifact" in output


def test_cli_history_routes_through_public_runner(monkeypatch, capsys) -> None:
    history_entries = [
        _sample_run_history_entry(
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 27, 2, 9, 45, tzinfo=timezone.utc),
            fetch_status="fresh source fetch",
            report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
        )
    ]

    def fake_load_recent_history(
        *,
        limit=10,
        cache_dir=Path("data/cache"),
        report_dir=Path("reports/daily"),
    ):  # type: ignore[no-untyped-def]
        assert limit == 10
        assert cache_dir == Path("data/cache")
        assert report_dir == Path("reports/daily")
        return history_entries

    monkeypatch.setattr("frontier_compass.cli.main.load_recent_history", fake_load_recent_history)

    assert main(["history"]) == 0
    output = capsys.readouterr().out

    assert "Biomedical latest available" in output


def test_cli_history_limit_truncates_results(monkeypatch, capsys) -> None:
    first_entry = _sample_run_history_entry(
        requested_date=date(2026, 3, 26),
        effective_date=date(2026, 3, 26),
        generated_at=datetime(2026, 3, 27, 4, 0, tzinfo=timezone.utc),
        fetch_status="fresh source fetch",
        report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-26.html",
    )
    second_entry = _sample_run_history_entry(
        requested_date=date(2026, 3, 25),
        effective_date=date(2026, 3, 25),
        generated_at=datetime(2026, 3, 27, 3, 0, tzinfo=timezone.utc),
        fetch_status="same-day cache",
        report_path="reports/daily/frontier_compass_arxiv_biomedical-latest_2026-03-25.html",
    )

    def fake_recent_daily_runs(self, *, limit=10, cache_dir=None, report_dir=None):  # type: ignore[no-untyped-def]
        assert limit == 5
        del self, cache_dir, report_dir
        return [first_entry, second_entry][:limit]

    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    assert main(["history", "--limit", "5"]) == 0
    output = capsys.readouterr().out

    assert "2026-03-26 | Biomedical latest available" in output
    assert "2026-03-25 | Biomedical latest available" in output


def test_cli_history_empty_prints_friendly_message(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        FrontierCompassApp,
        "recent_daily_runs",
        lambda self, **kwargs: [],
    )

    assert main(["history"]) == 0
    output = capsys.readouterr().out

    assert output.strip() == "No recent daily runs found under data/cache."


def test_cli_history_help_mentions_artifacts_and_latest_first() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frontier_compass.cli.main", "history", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Inspect recent local runs, requested vs shown dates, and saved artifacts." in result.stdout
    assert "Latest-first output shows the report, cache JSON, and optional .eml path" in result.stdout


def test_cli_ui_print_command_reports_launch_path(capsys) -> None:
    assert main(["ui", "--print-command", "--today", "2026-03-24", "--port", "8601", "--server-headless"]) == 0
    output = capsys.readouterr().out

    assert "Requested date: 2026-03-24" in output
    assert "Streamlit app:" in output
    assert "streamlit_app.py" in output
    assert "Launch command:" in output
    assert "-m streamlit run" in output
    assert "--server.headless true" in output
    assert "--server.port 8601" in output
    assert "-- --requested-date 2026-03-24" in output
    assert "--source biomedical" not in output
    assert "--max-results 80" not in output
    assert "--report-mode deterministic" not in output
    assert "--allow-stale-cache" not in output


def test_cli_ui_print_command_range_window_includes_range_full_fetch_scope(capsys) -> None:
    assert (
        main(
            [
                "ui",
                "--print-command",
                "--today",
                "2026-03-24",
                "--start-date",
                "2026-03-24",
                "--end-date",
                "2026-03-25",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "--start-date 2026-03-24" in output
    assert "--end-date 2026-03-25" in output
    assert "--fetch-scope range-full" in output


def test_cli_ui_print_command_uses_internal_launch_helpers(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "frontier_compass.cli.main._resolve_ui_app_path",
        lambda: Path("/tmp/custom_streamlit_app.py"),
    )
    monkeypatch.setattr(
        "frontier_compass.cli.main._build_ui_launch_command",
        lambda *, port=None, headless=False, startup_args=(): [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "/tmp/custom_streamlit_app.py",
            "--server.headless",
            "true" if headless else "false",
            "--server.port",
            str(port or 8501),
            "--",
            *startup_args,
        ],
    )

    assert (
        main(
            [
                "ui",
                "--print-command",
                "--mode",
                BIOMEDICAL_DAILY_MODE,
                "--today",
                "2026-03-24",
                "--zotero-export",
                str(ZOTERO_FIXTURE_PATH),
                "--port",
                "8601",
                "--server-headless",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Streamlit app: /tmp/custom_streamlit_app.py" in output
    assert "Launch command:" in output
    assert f"--zotero-export {ZOTERO_FIXTURE_PATH}" in output
    assert "--source biomedical-daily" in output


def test_cli_ui_prewarms_session_before_launch(monkeypatch, capsys) -> None:
    recorded_prepare: dict[str, object] = {}
    recorded_command: dict[str, object] = {}

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        recorded_prepare.update(kwargs)
        return LocalUISession(
            current_run=DailyRunResult(
                digest=_sample_daily_digest_for_cli(SOURCE_BUNDLE_BIOMEDICAL),
                cache_path=Path("data/cache/frontier_compass_bundle_biomedical_2026-03-24.json"),
                report_path=Path("reports/daily/frontier_compass_bundle_biomedical_2026-03-24.html"),
                display_source="freshly fetched",
                fetch_status_label="fresh source fetch",
                artifact_source_label="fresh source fetch",
            )
        )

    def fake_build_ui_launch_command(*, port=None, headless=False, startup_args=()):  # type: ignore[no-untyped-def]
        recorded_command["startup_args"] = list(startup_args)
        return [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "/tmp/custom_streamlit_app.py",
            *(["--server.headless", "true"] if headless else []),
            *(["--server.port", str(port)] if port is not None else []),
            "--",
            *startup_args,
        ]

    def fake_subprocess_run(command, check=False):  # type: ignore[no-untyped-def]
        recorded_command["command"] = list(command)
        recorded_command["check"] = check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)
    monkeypatch.setattr("frontier_compass.cli.main._resolve_ui_app_path", lambda: Path("/tmp/custom_streamlit_app.py"))
    monkeypatch.setattr("frontier_compass.cli.main._build_ui_launch_command", fake_build_ui_launch_command)
    monkeypatch.setattr("frontier_compass.cli.main.subprocess.run", fake_subprocess_run)

    assert (
        main(
            [
                "ui",
                "--today",
                "2026-03-24",
                "--refresh",
                "--zotero-export",
                str(ZOTERO_FIXTURE_PATH),
                "--server-headless",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert recorded_prepare == {
        "source": SOURCE_BUNDLE_BIOMEDICAL,
        "requested_date": date(2026, 3, 24),
        "max_results": 80,
        "refresh": True,
        "allow_stale_cache": True,
        "profile_source": "zotero_export",
        "zotero_export_path": ZOTERO_FIXTURE_PATH,
    }
    assert recorded_command["startup_args"] == [
        "--requested-date",
        "2026-03-24",
        "--zotero-export",
        str(ZOTERO_FIXTURE_PATH),
    ]
    assert recorded_command["check"] is False
    assert "Current UI run: default public bundle (arXiv + bioRxiv)" in output
    assert "Tracks: Digest + Frontier Report" in output
    assert "Refresh prewarm: yes" in output
    assert "Launching FrontierCompass UI from /tmp/custom_streamlit_app.py" in output


def test_cli_ui_launches_even_when_prewarm_fails(monkeypatch, capsys) -> None:
    recorded_command: dict[str, object] = {}

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        raise RuntimeError("arXiv request failed with HTTP 429 Too Many Requests")

    def fake_build_ui_launch_command(*, port=None, headless=False, startup_args=()):  # type: ignore[no-untyped-def]
        recorded_command["startup_args"] = list(startup_args)
        return [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "/tmp/custom_streamlit_app.py",
            *(["--server.headless", "true"] if headless else []),
            *(["--server.port", str(port)] if port is not None else []),
            "--",
            *startup_args,
        ]

    def fake_subprocess_run(command, check=False):  # type: ignore[no-untyped-def]
        recorded_command["command"] = list(command)
        recorded_command["check"] = check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)
    monkeypatch.setattr("frontier_compass.cli.main._resolve_ui_app_path", lambda: Path("/tmp/custom_streamlit_app.py"))
    monkeypatch.setattr("frontier_compass.cli.main._build_ui_launch_command", fake_build_ui_launch_command)
    monkeypatch.setattr("frontier_compass.cli.main.subprocess.run", fake_subprocess_run)

    assert main(["ui", "--today", "2026-03-24", "--server-headless"]) == 0
    captured = capsys.readouterr()

    assert recorded_command["startup_args"] == [
        "--requested-date",
        "2026-03-24",
        "--skip-initial-load",
    ]
    assert recorded_command["check"] is False
    assert "Refresh prewarm: no" in captured.out
    assert "Launching FrontierCompass UI from /tmp/custom_streamlit_app.py" in captured.out
    assert "UI prewarm failed; launching Streamlit without an active digest." in captured.err
    assert "HTTP 429 Too Many Requests" in captured.err


def _sample_ranked_paper_for_cli(*, score: float) -> RankedPaper:
    return RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.22821v1",
            title="Spatial transcriptomics from digital pathology images",
            summary="Histopathology model for spatial transcriptomics and tissue analysis.",
            authors=("A Scientist",),
            categories=("q-bio.GN", "cs.CV"),
            published=date(2026, 3, 24),
            updated=date(2026, 3, 24),
            url="https://arxiv.org/abs/2603.22821",
        ),
        score=score,
        reasons=(
            "biomedical evidence: spatial transcriptomics, pathology",
            "zotero profile match: spatial transcriptomics, digital pathology",
        ),
        recommendation_summary="Priority review for biomedical evidence plus Zotero profile overlap.",
    )


def _sample_daily_digest_for_cli(category: str) -> DailyDigest:
    mode_label = {
        SOURCE_BUNDLE_BIOMEDICAL: "Biomedical",
        SOURCE_BUNDLE_AI_FOR_MEDICINE: "AI for medicine",
        BIOMEDICAL_MULTISOURCE_MODE: "Biomedical multisource",
        BIOMEDICAL_LATEST_MODE: "Biomedical latest available",
        BIOMEDICAL_DISCOVERY_MODE: "Biomedical discovery",
        BIOMEDICAL_DAILY_MODE: "Biomedical daily",
    }.get(category, category)
    mode_kind = {
        SOURCE_BUNDLE_BIOMEDICAL: "source-bundle",
        SOURCE_BUNDLE_AI_FOR_MEDICINE: "source-bundle",
        BIOMEDICAL_MULTISOURCE_MODE: "multisource",
        BIOMEDICAL_LATEST_MODE: "latest-available-hybrid",
        BIOMEDICAL_DISCOVERY_MODE: "hybrid",
        BIOMEDICAL_DAILY_MODE: "bundle",
    }.get(category, "category-feed")
    is_source_bundle = category in {SOURCE_BUNDLE_BIOMEDICAL, SOURCE_BUNDLE_AI_FOR_MEDICINE}
    ranked = [_sample_ranked_paper_for_cli(score=0.88)]
    return DailyDigest(
        source="multisource" if category == BIOMEDICAL_MULTISOURCE_MODE or is_source_bundle else "arxiv",
        category=category,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="" if category == BIOMEDICAL_MULTISOURCE_MODE or is_source_bundle else "https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(category),
        ranked=ranked,
        frontier_report=build_daily_frontier_report(
            paper_pool=[item.paper for item in ranked],
            ranked_papers=ranked,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource" if category == BIOMEDICAL_MULTISOURCE_MODE or is_source_bundle else "arxiv",
            mode=category,
            mode_label=mode_label,
            mode_kind=mode_kind,
            searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
            total_fetched=3 if category == BIOMEDICAL_MULTISOURCE_MODE else 2 if is_source_bundle else 3,
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3 if category == BIOMEDICAL_MULTISOURCE_MODE else 2 if is_source_bundle else 3,
        source_counts={"arxiv": 1, "biorxiv": 1, "medrxiv": 1}
        if category == BIOMEDICAL_MULTISOURCE_MODE
        else {"arxiv": 1, "biorxiv": 1}
        if is_source_bundle
        else {},
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label=mode_label,
        mode_kind=mode_kind,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )


def _sample_run_history_entry(
    *,
    requested_date: date,
    effective_date: date,
    generated_at: datetime,
    fetch_status: str,
    report_path: str,
    eml_path: str | None = None,
    compatibility_status: str = "",
    compatibility_reasons: tuple[str, ...] = (),
) -> RunHistoryEntry:
    return RunHistoryEntry(
        requested_date=requested_date,
        effective_date=effective_date,
        category=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        profile_basis="biomedical baseline",
        zotero_export_name="sample_library.csl.json",
        fetch_status=fetch_status,
        request_window=RequestWindow(kind="day", requested_date=requested_date),
        report_status="ready",
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=12,
                displayed_count=12,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(total_seconds=0.35),
            ),
        ),
        run_timings=RunTimings(total_seconds=0.35),
        frontier_report_present=True,
        report_artifact_aligned=True,
        same_date_cache_reused=fetch_status.startswith("same-day cache"),
        stale_cache_fallback_used="older compatible cache" in fetch_status,
        ranked_count=12,
        exploration_pick_count=2,
        cache_path=f"data/cache/frontier_compass_arxiv_biomedical-latest_{requested_date.isoformat()}.json",
        report_path=report_path,
        eml_path=eml_path,
        generated_at=generated_at,
        compatibility_status=compatibility_status,
        compatibility_reasons=compatibility_reasons,
    )


def _write_user_defaults_config(tmp_path: Path, payload: dict[str, object]) -> Path:
    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path
