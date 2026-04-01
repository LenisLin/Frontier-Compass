from __future__ import annotations

from dataclasses import replace
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from frontier_compass.api import DailyRunResult, FrontierCompassRunner, LocalUISession
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.reporting.daily_brief import GENOMICS_THEME
from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import (
    DailyDigest,
    ExplorationPolicy,
    PaperRecord,
    RankedPaper,
    RequestWindow,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui import (
    BIOMEDICAL_DAILY_MODE,
    BIOMEDICAL_DISCOVERY_MODE,
    BIOMEDICAL_LATEST_MODE,
    BIOMEDICAL_MULTISOURCE_MODE,
    DailyBootstrapResult,
    DailyPreparationResult,
    FrontierCompassApp,
    build_existing_local_file_url,
    build_local_file_url,
    build_daily_run_summary,
    build_ranked_paper_cards,
    format_author_summary,
)
from frontier_compass.ui.streamlit_app import _write_selected_digest
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder


ZOTERO_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zotero" / "sample_library.csl.json"


def test_resolve_latest_daily_cache_path_uses_newest_matching_digest(tmp_path: Path) -> None:
    app = FrontierCompassApp()
    older_path = tmp_path / "frontier_compass_arxiv_q-bio_2026-03-22.json"
    newer_path = tmp_path / "frontier_compass_arxiv_q-bio_2026-03-23.json"
    other_path = tmp_path / "frontier_compass_arxiv_cs-lg_2026-03-24.json"
    empty_path = tmp_path / "frontier_compass_arxiv_q-bio_2026-03-24.json"

    _write_digest(
        older_path,
        category="q-bio",
        target_date=date(2026, 3, 22),
        generated_at=datetime(2026, 3, 22, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.61)],
    )
    _write_digest(
        newer_path,
        category="q-bio",
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.74)],
    )
    _write_digest(
        other_path,
        category="cs.LG",
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.52)],
    )
    _write_digest(
        empty_path,
        category="q-bio",
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        ranked=[],
    )

    assert app.resolve_latest_daily_cache_path(category="q-bio", cache_dir=tmp_path) == empty_path
    assert app.resolve_latest_daily_cache_path(category="q-bio", cache_dir=tmp_path, non_empty_only=True) == newer_path
    assert app.resolve_latest_daily_cache_path(cache_dir=tmp_path) == empty_path


def test_available_daily_caches_scans_nested_directories(tmp_path: Path) -> None:
    app = FrontierCompassApp()
    nested_path = tmp_path / "validation_round9" / "frontier_compass_arxiv_q-bio_2026-03-24.json"
    nested_path.parent.mkdir(parents=True, exist_ok=True)

    _write_digest(
        nested_path,
        category="q-bio",
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.67)],
    )

    available = app.available_daily_caches(tmp_path)

    assert [cached.cache_path for cached in available] == [nested_path]
    assert app.resolve_latest_daily_cache_path(category="q-bio", cache_dir=tmp_path) == nested_path


def test_materialize_daily_digest_reuses_same_day_empty_cache_before_stale_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    requested_date = date(2026, 3, 24)
    same_day_cache = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    older_cache = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    older_ranked = [_sample_ranked_paper(score=0.84, published=date(2026, 3, 23))]

    same_day_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=requested_date,
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                cache_status="fresh",
            ),
        ),
        frontier_report=replace(
            _frontier_report_for(
                [],
                category=BIOMEDICAL_LATEST_MODE,
                requested_date=requested_date,
                effective_date=requested_date,
            ),
            source_run_stats=(
                SourceRunStats(
                    source="arxiv",
                    fetched_count=0,
                    displayed_count=0,
                    status="empty",
                    cache_status="fresh",
                ),
            ),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 0, "q-bio.GN": 0, "cs.LG": 0},
        total_fetched=0,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=requested_date,
        effective_date=requested_date,
    )
    older_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=older_ranked,
        frontier_report=_frontier_report_for(
            older_ranked,
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 23),
            effective_date=date(2026, 3, 23),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 23),
        effective_date=date(2026, 3, 23),
    )

    same_day_cache.write_text(json.dumps(same_day_digest.to_mapping(), indent=2), encoding="utf-8")
    older_cache.write_text(json.dumps(older_digest.to_mapping(), indent=2), encoding="utf-8")

    def fake_write_daily_outputs(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(app, "write_daily_outputs", fake_write_daily_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=requested_date,
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
        allow_stale_cache=True,
    )

    assert result.display_source == "reused same-date cache after fetch failure"
    assert result.cache_path == same_day_cache
    assert result.digest.requested_target_date == requested_date
    assert result.digest.effective_display_date == requested_date
    assert result.digest.stale_cache_fallback_used is False
    assert result.digest.ranked == []
    assert result.digest.source_run_stats[0].cache_status == "same-day-cache"
    assert result.digest.source_run_stats[0].status == "failed"
    assert result.digest.source_run_stats[0].error == "upstream arXiv timeout"
    assert "Same-day cache reused after a fresh fetch failure." in result.digest.source_run_stats[0].note
    assert result.digest.frontier_report is not None
    assert result.digest.frontier_report.source_run_stats[0].cache_status == "same-day-cache"


def test_stale_cache_fallback_digest_rewrites_current_run_frontier_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    requested_date = date(2026, 3, 24)
    cached_requested_date = date(2026, 3, 23)
    cached_effective_date = date(2026, 3, 22)
    cached_generated_at = datetime(2026, 3, 23, 7, 0, tzinfo=timezone.utc)
    older_cache = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    older_ranked = [_sample_ranked_paper(score=0.84, published=cached_effective_date)]

    older_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=cached_requested_date,
        generated_at=cached_generated_at,
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=older_ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
            ),
        ),
        frontier_report=replace(
            _frontier_report_for(
                older_ranked,
                category=BIOMEDICAL_LATEST_MODE,
                requested_date=cached_requested_date,
                effective_date=cached_effective_date,
            ),
            source_run_stats=(
                SourceRunStats(
                    source="arxiv",
                    fetched_count=1,
                    displayed_count=1,
                    status="ready",
                    cache_status="fresh",
                ),
            ),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=cached_requested_date,
        effective_date=cached_effective_date,
    )
    older_cache.write_text(json.dumps(older_digest.to_mapping(), indent=2), encoding="utf-8")

    def fake_write_daily_outputs(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(app, "write_daily_outputs", fake_write_daily_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=requested_date,
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
        allow_stale_cache=True,
    )

    assert result.display_source == "older compatible cache reused after fetch failure"
    assert result.cache_path == tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    assert result.digest.requested_target_date == requested_date
    assert result.digest.effective_display_date == cached_effective_date
    assert result.digest.generated_at != cached_generated_at
    assert result.digest.generated_at.tzinfo == timezone.utc
    assert result.digest.stale_cache_source_requested_date == cached_requested_date
    assert result.digest.stale_cache_source_effective_date == cached_effective_date
    assert result.digest.source_run_stats[0].cache_status == "stale-compatible-cache"
    assert result.digest.source_run_stats[0].error == "upstream arXiv timeout"
    assert "Older compatible cache reused after a fresh fetch failure." in result.digest.source_run_stats[0].note
    assert result.digest.frontier_report is not None
    assert result.digest.frontier_report.requested_date == requested_date
    assert result.digest.frontier_report.effective_date == cached_effective_date
    assert result.digest.frontier_report.source_run_stats[0].cache_status == "stale-compatible-cache"


def test_load_or_materialize_current_digest_discovers_nested_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    requested_date = date(2026, 3, 24)
    nested_cache = tmp_path / "validation_round9" / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    older_requested_date = date(2026, 3, 23)
    older_ranked = [_sample_ranked_paper(score=0.84, published=older_requested_date)]

    older_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=older_requested_date,
        generated_at=datetime(2026, 3, 23, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=older_ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
            ),
        ),
        frontier_report=replace(
            _frontier_report_for(
                older_ranked,
                category=BIOMEDICAL_LATEST_MODE,
                requested_date=older_requested_date,
                effective_date=older_requested_date,
            ),
            source_run_stats=(
                SourceRunStats(
                    source="arxiv",
                    fetched_count=1,
                    displayed_count=1,
                    status="ready",
                    cache_status="fresh",
                ),
            ),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=older_requested_date,
        effective_date=older_requested_date,
    )
    nested_cache.parent.mkdir(parents=True, exist_ok=True)
    nested_cache.write_text(json.dumps(older_digest.to_mapping(), indent=2), encoding="utf-8")

    def fake_write_daily_outputs(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(app, "write_daily_outputs", fake_write_daily_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=requested_date,
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
        allow_stale_cache=True,
    )

    assert result.display_source == "older compatible cache reused after fetch failure"
    assert result.cache_path == tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    assert result.digest.requested_target_date == requested_date
    assert result.digest.effective_display_date == older_requested_date
    assert result.digest.stale_cache_source_requested_date == older_requested_date
    assert result.digest.stale_cache_source_effective_date == older_requested_date
    assert result.digest.source_run_stats[0].cache_status == "stale-compatible-cache"


def test_ui_helpers_build_summary_and_cards() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_DAILY_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DAILY_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher", "B Collaborator", "C Analyst"),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=date(2026, 3, 23),
                    url="https://arxiv.org/abs/2603.20001",
                ),
                score=0.8123,
                reasons=(
                    "biomedical evidence: single-cell, transcriptomics",
                    "topic match: q-bio, q-bio.gn",
                ),
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

    summary = build_daily_run_summary(
        digest,
        cache_path=Path("data/cache/frontier_compass_arxiv_biomedical-daily_2026-03-23.json"),
    )
    cards = build_ranked_paper_cards(digest.ranked, profile=digest.profile)

    assert summary.category == BIOMEDICAL_DAILY_MODE
    assert summary.requested_date == date(2026, 3, 23)
    assert summary.effective_date == date(2026, 3, 23)
    assert summary.used_latest_available_fallback is False
    assert summary.strict_same_day_counts_known is True
    assert summary.stale_cache_fallback_used is False
    assert summary.mode_label == "Biomedical daily"
    assert summary.mode_kind == "bundle"
    assert summary.requested_report_mode == "deterministic"
    assert summary.report_mode == "deterministic"
    assert summary.cost_mode == "zero-token"
    assert summary.enhanced_track == ""
    assert summary.enhanced_item_count == 0
    assert summary.ranked_count == 1
    assert summary.total_fetched == 2
    assert summary.strict_same_day_fetched == 2
    assert summary.strict_same_day_ranked == 1
    assert summary.displayed_fetched == 2
    assert summary.displayed_ranked == 1
    assert summary.searched_categories == ("q-bio", "q-bio.GN", "q-bio.QM")
    assert summary.per_category_counts["q-bio.QM"] == 0
    assert summary.cache_path.endswith("frontier_compass_arxiv_biomedical-daily_2026-03-23.json")
    assert summary.report_path.endswith("frontier_compass_arxiv_biomedical-daily_2026-03-23.html")
    assert summary.display_source == "loaded from cache"
    assert cards[0].authors_text == "A Researcher +2 more"
    assert cards[0].published_text == "2026-03-23"
    assert cards[0].categories == ("q-bio.GN", "q-bio.QM")
    assert cards[0].theme_label == GENOMICS_THEME
    assert cards[0].score == 0.8123
    assert cards[0].status_label == "Priority review"
    assert cards[0].is_recommended is True
    assert cards[0].why_label == "Why it surfaced"
    assert "baseline:" in cards[0].why_it_surfaced
    assert "category: q-bio, q-bio.gn" in cards[0].why_it_surfaced
    assert cards[0].zotero_effect_label == "Zotero: inactive"
    assert cards[0].score_breakdown[0] == ("Biomedical baseline", 0.0)
    assert any(line.startswith("Category matches: q-bio, q-bio.gn") for line in cards[0].score_detail_lines)
    assert format_author_summary(()) == "Unknown authors"


def test_daily_digest_round_trip_preserves_exploration_picks() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
        exploration_picks=[
            _sample_ranked_paper(
                score=0.41,
                identifier="2603.21111v1",
                title="Exploration lane fixture",
                summary="A deterministic exploration pick fixture.",
                categories=("q-bio.BM", "cs.LG"),
            )
        ],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert [item.paper.identifier for item in restored.exploration_picks] == ["2603.21111v1"]


def test_daily_digest_round_trip_preserves_exploration_policy_and_zotero_retrieval_hints() -> None:
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        export_path=ZOTERO_FIXTURE_PATH,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=profile,
        ranked=[_sample_ranked_paper(score=0.83)],
        exploration_policy=ExplorationPolicy(
            label="daily-adjacent-v1",
            shortlist_size=8,
            max_items=3,
            max_per_theme=1,
            min_score=0.35,
            min_biomedical_keyword=0.13,
            notes="Deterministic adjacent lane.",
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.exploration_policy is not None
    assert restored.exploration_policy.label == "daily-adjacent-v1"
    assert [(hint.label, hint.terms) for hint in restored.profile.zotero_retrieval_hints] == [
        ("zotero-omics-pathology", ("spatial transcriptomics", "digital pathology")),
        ("zotero-protein-discovery", ("drug discovery", "protein structure")),
    ]


def test_daily_digest_round_trip_supports_bundle_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_DAILY_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DAILY_MODE),
        ranked=[_sample_ranked_paper(score=0.72)],
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        total_fetched=2,
        feed_urls={
            "q-bio": "https://rss.arxiv.org/atom/q-bio",
            "q-bio.GN": "https://rss.arxiv.org/atom/q-bio.GN",
        },
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.category == BIOMEDICAL_DAILY_MODE
    assert restored.searched_categories == ("q-bio", "q-bio.GN")
    assert restored.per_category_counts == {"q-bio": 1, "q-bio.GN": 1}
    assert restored.total_fetched == 2
    assert restored.feed_urls["q-bio.GN"] == "https://rss.arxiv.org/atom/q-bio.GN"


def test_daily_digest_round_trip_preserves_source_contract_metadata() -> None:
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[_sample_ranked_paper(score=0.78)],
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        source_counts={"arxiv": 1, "biorxiv": 1, "medrxiv": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        source_endpoints={
            "arxiv": "https://export.arxiv.org/api/query",
            "biorxiv": "https://connect.biorxiv.org/biorxiv_xml.php?subject=all",
            "medrxiv": "https://connect.medrxiv.org/medrxiv_xml.php?subject=all",
        },
        source_metadata={
            "arxiv": {
                "mode": "bundle",
                "native_filters": ["q-bio", "q-bio.GN"],
                "native_endpoints": {"q-bio": "https://rss.arxiv.org/atom/q-bio"},
            },
            "biorxiv": {
                "mode": "rss",
                "native_filters": ["all"],
                "native_endpoints": {"all": "https://connect.biorxiv.org/biorxiv_xml.php?subject=all"},
            },
        },
        mode_label="Biomedical multisource",
        mode_kind="multisource",
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.category == BIOMEDICAL_MULTISOURCE_MODE
    assert restored.source_metadata["arxiv"]["mode"] == "bundle"
    assert restored.source_metadata["arxiv"]["native_filters"] == ["q-bio", "q-bio.GN"]
    assert restored.source_metadata["biorxiv"]["native_endpoints"]["all"].endswith("subject=all")


def test_daily_digest_round_trip_supports_discovery_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_DISCOVERY_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DISCOVERY_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
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
            "((cat:q-bio OR cat:stat.ML) AND (all:medical OR all:\"foundation model\"))",
        ),
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.category == BIOMEDICAL_DISCOVERY_MODE
    assert restored.mode_label == "Biomedical discovery"
    assert restored.mode_kind == "hybrid"
    assert restored.search_profile_label == "broader-biomedical-discovery-v1"
    assert restored.search_queries == (
        "((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:\"single-cell\"))",
        "((cat:q-bio OR cat:stat.ML) AND (all:medical OR all:\"foundation model\"))",
    )


def test_daily_digest_round_trip_supports_latest_available_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.91)],
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

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.category == BIOMEDICAL_LATEST_MODE
    assert restored.requested_target_date == date(2026, 3, 24)
    assert restored.effective_display_date == date(2026, 3, 23)
    assert restored.strict_same_day_fetched_count == 0
    assert restored.strict_same_day_ranked_count == 0
    assert restored.used_latest_available_fallback is True
    assert restored.selection_basis_label == "Latest available fallback results"


def test_fetch_biomedical_discovery_pool_adds_zotero_query_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FrontierCompassApp()
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        export_path=ZOTERO_FIXTURE_PATH,
    )
    captured_labels: list[tuple[str, str, tuple[str, ...]]] = []

    def fake_fetch_recent_by_category_with_timings(categories, *, max_results=None):  # type: ignore[no-untyped-def]
        del max_results
        return (
            {
                category: [
                    PaperRecord(
                        source="arxiv",
                        identifier=f"{category}-baseline",
                        title=f"{category} baseline",
                        summary="Baseline category fixture.",
                        categories=(category,),
                        published=date(2026, 3, 24),
                        url=f"https://arxiv.org/abs/{category}",
                    )
                ]
                for category in categories[:1]
            },
            0.7,
            0.2,
        )

    def fake_fetch_recent_by_queries_with_timings(query_definitions, *, max_results=120):  # type: ignore[no-untyped-def]
        del max_results
        captured_labels.extend((definition.label, definition.origin, definition.terms) for definition in query_definitions)
        return (
            {
                definition.label: [
                    PaperRecord(
                        source="arxiv",
                        identifier=f"{definition.label}-paper",
                        title=f"{definition.label} fixture",
                        summary="Query fixture.",
                        categories=("q-bio.GN", "cs.CV"),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.21001",
                        source_metadata={
                            "retrieval_support": [
                                {
                                    "label": definition.label,
                                    "origin": definition.origin,
                                    "terms": list(definition.terms),
                                }
                            ]
                        },
                    )
                ]
                for definition in query_definitions
            },
            0.9,
            0.3,
        )

    monkeypatch.setattr(app.arxiv_client, "fetch_recent_by_category_with_timings", fake_fetch_recent_by_category_with_timings)
    monkeypatch.setattr(app.arxiv_client, "fetch_recent_by_queries_with_timings", fake_fetch_recent_by_queries_with_timings)

    pool = app._fetch_biomedical_discovery_pool(max_results=40, profile=profile)

    assert pool.search_profile_label == "broader-biomedical-discovery-v1 + zotero-biomedical-augmentation-v1"
    assert ("omics-and-single-cell", "baseline", ()) in captured_labels
    assert ("zotero-omics-pathology", "zotero", ("spatial transcriptomics", "digital pathology")) in captured_labels
    assert ("zotero-protein-discovery", "zotero", ("drug discovery", "protein structure")) in captured_labels
    assert any(definition.origin == "zotero" for definition in pool.query_definitions)
    assert pool.network_seconds == pytest.approx(1.6)
    assert pool.parse_seconds == pytest.approx(0.5)


def test_html_report_builder_surfaces_zotero_retrieval_and_exploration_policy() -> None:
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        export_path=ZOTERO_FIXTURE_PATH,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=profile,
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.24001v1",
                    title="Spatial transcriptomics from digital pathology images",
                    summary="Histopathology and whole-slide tissue modeling for tumor microenvironment analysis.",
                    authors=("A Researcher",),
                    categories=("q-bio.GN", "cs.CV"),
                    published=date(2026, 3, 24),
                    updated=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.24001",
                    source_metadata={
                        "retrieval_support": [
                            {
                                "label": "zotero-omics-pathology",
                                "origin": "zotero",
                                "terms": ["spatial transcriptomics", "digital pathology"],
                            }
                        ]
                    },
                ),
                score=0.81,
                reasons=(
                    "biomedical evidence: transcriptomics, pathology",
                    "topic match: q-bio, q-bio.gn, cs.cv",
                ),
                recommendation_summary="Sample recommendation summary.",
            )
        ],
        exploration_policy=ExplorationPolicy(
            label="daily-adjacent-v1",
            shortlist_size=8,
            max_items=3,
            max_per_theme=1,
            min_score=0.35,
            min_biomedical_keyword=0.13,
            notes="Deterministic adjacent lane.",
        ),
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.81)],
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 1},
        total_fetched=1,
        source_metadata={
            "arxiv": {
                "mode": "hybrid",
                "native_filters": ["q-bio", "q-bio.GN", "cs.CV"],
                "search_profile_label": "broader-biomedical-discovery-v1 + zotero-biomedical-augmentation-v1",
                "search_queries": ['((cat:q-bio OR cat:cs.CV) AND (all:"spatial transcriptomics" OR all:"digital pathology"))'],
                "query_profiles": [
                    {
                        "label": "zotero-omics-pathology",
                        "origin": "zotero",
                        "terms": ["spatial transcriptomics", "digital pathology"],
                    }
                ],
            }
        },
        search_profile_label="broader-biomedical-discovery-v1 + zotero-biomedical-augmentation-v1",
        search_queries=('((cat:q-bio OR cat:cs.CV) AND (all:"spatial transcriptomics" OR all:"digital pathology"))',),
    )

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "Zotero retrieval hints" in html
    assert "spatial transcriptomics + digital pathology" in html
    assert "Zotero retrieval" in html
    assert "daily-adjacent-v1" in html
    assert "zotero_queries=1" in html


def test_materialize_daily_digest_reuses_matching_zotero_cache_path(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    cache_template = app.default_daily_cache_path(
        BIOMEDICAL_LATEST_MODE,
        date(2026, 3, 24),
        zotero_export_path=ZOTERO_FIXTURE_PATH,
    )
    report_template = app.report_path_for_cache_path(cache_template)
    cache_path = tmp_path / cache_template.name
    report_path = tmp_path / report_template.name
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=ZoteroProfileBuilder().build_augmented_profile(
            FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            export_path=ZOTERO_FIXTURE_PATH,
        ),
        ranked=[_sample_ranked_paper(score=0.87)],
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.87)],
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")
    report_path.write_text(HtmlReportBuilder().render_daily_digest(digest), encoding="utf-8")

    def fail_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected fetch path: {kwargs}")

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fail_write_daily_outputs)

    result = app.materialize_daily_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=False,
        zotero_export_path=ZOTERO_FIXTURE_PATH,
    )

    assert result.cache_path == cache_path
    assert result.report_path == report_path
    assert result.display_source == "loaded from cache"
    assert result.digest.profile.basis_label == "biomedical baseline + Zotero export"


def test_load_or_materialize_current_digest_rebuilds_same_day_legacy_cache_without_frontier_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    legacy_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.82)],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    payload = legacy_digest.to_mapping()
    payload.pop("frontier_report", None)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    fresh_ranked = [_sample_ranked_paper(score=0.91)]
    fresh_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=fresh_ranked,
        frontier_report=_frontier_report_for(
            fresh_ranked,
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["cache_path"] == cache_path
        report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
        report_path.write_text("<html><body>fresh report</body></html>", encoding="utf-8")
        return DailyPreparationResult(digest=fresh_digest, cache_path=cache_path, report_path=report_path)

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=False,
    )

    assert result.display_source == "freshly fetched"
    assert result.digest.frontier_report is not None


def test_biomedical_latest_mode_falls_back_to_most_recent_available_release(monkeypatch) -> None:
    requested_date = date(2026, 3, 24)
    fallback_date = date(2026, 3, 23)

    def fake_fetch_recent_by_category_with_timings(self, categories, *, max_results=None, feed_urls=None):  # type: ignore[no-untyped-def]
        del max_results, feed_urls
        return (
            {
                category: [
                    PaperRecord(
                        source="arxiv",
                        identifier=f"2603.2199{index}v1",
                        title=f"Fallback category paper {category}",
                        summary="Fallback category paper.",
                        authors=("A Researcher",),
                        categories=(category, "q-bio.GN"),
                        published=fallback_date,
                        url=f"https://arxiv.org/abs/2603.2199{index}",
                    )
                ]
                for index, category in enumerate(categories, start=1)
            },
            0.9,
            0.2,
        )

    def fake_fetch_recent_by_queries_with_timings(self, query_definitions, *, max_results=120):  # type: ignore[no-untyped-def]
        del max_results
        return (
            {
                definition.label: [
                    PaperRecord(
                        source="arxiv",
                        identifier=f"2603.2188{index}v1",
                        title=f"Fallback query paper {definition.label}",
                        summary="Fallback query paper.",
                        authors=("B Scientist",),
                        categories=("q-bio.GN", "cs.LG"),
                        published=fallback_date,
                        url=f"https://arxiv.org/abs/2603.2188{index}",
                    )
                ]
                for index, definition in enumerate(query_definitions, start=1)
            },
            1.1,
            0.3,
        )

    monkeypatch.setattr(
        "frontier_compass.ingest.arxiv.ArxivClient.fetch_recent_by_category_with_timings",
        fake_fetch_recent_by_category_with_timings,
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.arxiv.ArxivClient.fetch_recent_by_queries_with_timings",
        fake_fetch_recent_by_queries_with_timings,
    )

    digest = FrontierCompassApp().build_daily_digest(
        mode=BIOMEDICAL_LATEST_MODE,
        today=requested_date,
        max_results=80,
    )

    assert digest.category == BIOMEDICAL_LATEST_MODE
    assert digest.requested_target_date == requested_date
    assert digest.effective_display_date == fallback_date
    assert digest.used_latest_available_fallback is True
    assert digest.strict_same_day_fetched_count == 0
    assert digest.strict_same_day_ranked_count == 0
    assert digest.displayed_fetched_count > 0
    assert digest.displayed_ranked_count > 0
    assert digest.run_timings.network_seconds == pytest.approx(2.0)
    assert digest.run_timings.parse_seconds == pytest.approx(0.5)
    assert digest.mode_kind == "latest-available-hybrid"


def test_build_daily_digest_multisource_tracks_source_mix_and_contract_metadata(monkeypatch) -> None:
    target_date = date(2026, 3, 24)

    def fake_fetch_today_by_category_with_timings(self, categories, *, today=None, max_results=None, feed_urls=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_urls
        return (
            {
                category: [
                    PaperRecord(
                        source="arxiv",
                        identifier=f"{category}-fixture",
                        title=f"arXiv {category} fixture",
                        summary="Deterministic arXiv fixture.",
                        authors=("A Researcher",),
                        categories=(category, "q-bio.GN"),
                        published=target_date,
                        url=f"https://arxiv.org/abs/{category}",
                        source_metadata={
                            "native_identifier": f"{category}-fixture",
                            "native_url": f"https://arxiv.org/abs/{category}",
                            "tags": [category, "q-bio.GN"],
                        },
                    )
                ]
                for category in categories[:2]
            },
            1.2,
            0.3,
        )

    def fake_biorxiv_fetch_today_with_timings(self, *, today=None, subject="all", max_results=None, feed_url=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_url
        return (
            [
                PaperRecord(
                    source="biorxiv",
                    identifier="10.1101/2026.03.24.000001v1",
                    title="bioRxiv fixture",
                    summary="Deterministic bioRxiv fixture.",
                    authors=("B Biologist",),
                    categories=("bioinformatics",),
                    published=target_date,
                    url="https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                    source_metadata={
                        "native_identifier": "10.1101/2026.03.24.000001v1",
                        "native_url": "https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                        "tags": ["bioinformatics"],
                        "subject": subject,
                    },
                )
            ],
            0.4,
            0.05,
        )

    def fake_medrxiv_fetch_today_with_timings(self, *, today=None, subject="all", max_results=None, feed_url=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_url
        return (
            [
                PaperRecord(
                    source="medrxiv",
                    identifier="10.1101/2026.03.24.000002v1",
                    title="medRxiv fixture",
                    summary="Deterministic medRxiv fixture.",
                    authors=("C Clinician",),
                    categories=("clinical informatics",),
                    published=target_date,
                    url="https://www.medrxiv.org/content/10.1101/2026.03.24.000002v1",
                    source_metadata={
                        "native_identifier": "10.1101/2026.03.24.000002v1",
                        "native_url": "https://www.medrxiv.org/content/10.1101/2026.03.24.000002v1",
                        "tags": ["clinical informatics"],
                        "subject": subject,
                    },
                )
            ],
            0.6,
            0.07,
        )

    monkeypatch.setattr(
        "frontier_compass.ingest.arxiv.ArxivClient.fetch_today_by_category_with_timings",
        fake_fetch_today_by_category_with_timings,
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.biorxiv.BioRxivClient.fetch_today_with_timings",
        fake_biorxiv_fetch_today_with_timings,
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.medrxiv.MedRxivClient.fetch_today_with_timings",
        fake_medrxiv_fetch_today_with_timings,
    )

    digest = FrontierCompassApp().build_daily_digest(
        mode=BIOMEDICAL_MULTISOURCE_MODE,
        today=target_date,
        max_results=20,
    )

    assert digest.category == BIOMEDICAL_MULTISOURCE_MODE
    assert digest.source == "multisource"
    assert digest.source_counts["arxiv"] == 2
    assert digest.source_counts["biorxiv"] == 1
    assert digest.source_counts["medrxiv"] == 1
    assert digest.source_metadata["arxiv"]["mode"] == "bundle"
    assert digest.source_metadata["biorxiv"]["native_filters"] == ["all"]
    assert digest.source_metadata["medrxiv"]["native_endpoints"]["all"].endswith("subject=all")
    source_stats = {row.source: row for row in digest.source_run_stats}
    assert source_stats["arxiv"].timings.network_seconds == pytest.approx(1.2)
    assert source_stats["arxiv"].timings.parse_seconds == pytest.approx(0.3)
    assert source_stats["biorxiv"].status == "ready"
    assert source_stats["medrxiv"].status == "ready"
    assert digest.run_timings.network_seconds == pytest.approx(2.2)
    assert digest.run_timings.parse_seconds == pytest.approx(0.42)
    assert digest.frontier_report is not None
    assert digest.frontier_report.run_timings.network_seconds == pytest.approx(2.2)
    assert any(item.paper.source == "biorxiv" for item in digest.ranked)
    assert any(item.paper.source == "medrxiv" for item in digest.ranked)


def test_build_daily_digest_multisource_preserves_zero_count_sources(monkeypatch) -> None:
    target_date = date(2026, 3, 24)

    def fake_fetch_today_by_category_with_timings(self, categories, *, today=None, max_results=None, feed_urls=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_urls
        return (
            {
                categories[0]: [
                    PaperRecord(
                        source="arxiv",
                        identifier="arxiv-fixture",
                        title="arXiv fixture",
                        summary="Deterministic arXiv fixture.",
                        authors=("A Researcher",),
                        categories=(categories[0],),
                        published=target_date,
                        url="https://arxiv.org/abs/2603.24001",
                    )
                ]
            },
            0.8,
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
        lambda self, **kwargs: ([], 0.1, 0.0),
    )

    digest = FrontierCompassApp().build_daily_digest(
        mode=BIOMEDICAL_MULTISOURCE_MODE,
        today=target_date,
        max_results=20,
    )

    source_stats = {row.source: row for row in digest.source_run_stats}
    assert set(source_stats) == {"arxiv", "biorxiv", "medrxiv"}
    assert source_stats["arxiv"].fetched_count == 1
    assert source_stats["biorxiv"].fetched_count == 0
    assert source_stats["medrxiv"].fetched_count == 0
    assert source_stats["biorxiv"].status == "empty"
    assert source_stats["medrxiv"].status == "empty"
    assert source_stats["biorxiv"].cache_status == "fresh"
    assert source_stats["medrxiv"].cache_status == "fresh"
    assert digest.frontier_report is not None
    assert {row.source: row.fetched_count for row in digest.frontier_report.source_run_stats} == {
        "arxiv": 1,
        "biorxiv": 0,
        "medrxiv": 0,
    }


def test_build_daily_digest_multisource_preserves_failed_source_rows(monkeypatch) -> None:
    target_date = date(2026, 3, 24)

    def fake_fetch_today_by_category_with_timings(self, categories, *, today=None, max_results=None, feed_urls=None):  # type: ignore[no-untyped-def]
        del self, today, max_results, feed_urls
        return (
            {
                categories[0]: [
                    PaperRecord(
                        source="arxiv",
                        identifier="arxiv-fixture",
                        title="arXiv fixture",
                        summary="Deterministic arXiv fixture.",
                        authors=("A Researcher",),
                        categories=(categories[0],),
                        published=target_date,
                        url="https://arxiv.org/abs/2603.24001",
                    )
                ]
            },
            0.8,
            0.2,
        )

    monkeypatch.setattr(
        "frontier_compass.ingest.arxiv.ArxivClient.fetch_today_by_category_with_timings",
        fake_fetch_today_by_category_with_timings,
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.biorxiv.BioRxivClient.fetch_today_with_timings",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("bioRxiv unavailable")),
    )
    monkeypatch.setattr(
        "frontier_compass.ingest.medrxiv.MedRxivClient.fetch_today_with_timings",
        lambda self, **kwargs: ([], 0.1, 0.0),
    )

    digest = FrontierCompassApp().build_daily_digest(
        mode=BIOMEDICAL_MULTISOURCE_MODE,
        today=target_date,
        max_results=20,
    )

    source_stats = {row.source: row for row in digest.source_run_stats}
    assert source_stats["arxiv"].status == "ready"
    assert source_stats["biorxiv"].status == "failed"
    assert source_stats["biorxiv"].fetched_count == 0
    assert source_stats["biorxiv"].displayed_count == 0
    assert source_stats["biorxiv"].error == "bioRxiv unavailable"
    assert source_stats["medrxiv"].status == "empty"
    assert digest.report_status == "partial"
    assert digest.report_error == "bioRxiv unavailable"
    assert digest.frontier_report is not None
    assert {row.source: row.status for row in digest.frontier_report.source_run_stats} == {
        "arxiv": "ready",
        "biorxiv": "failed",
        "medrxiv": "empty",
    }


def test_streamlit_write_selected_digest_routes_fixed_discovery_mode(monkeypatch) -> None:
    recorded_kwargs: dict[str, object] = {}

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        recorded_kwargs.update(kwargs)
        return LocalUISession(
            current_run=DailyRunResult(
                digest=DailyDigest(
                    source="arxiv",
                    category=BIOMEDICAL_DISCOVERY_MODE,
                    target_date=date(2026, 3, 24),
                    generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
                    feed_url="https://export.arxiv.org/api/query",
                    profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DISCOVERY_MODE),
                    ranked=[_sample_ranked_paper(score=0.88, published=date(2026, 3, 24))],
                    searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
                    per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
                    total_fetched=3,
                ),
                cache_path=Path("data/cache/frontier_compass_arxiv_biomedical-discovery_2026-03-24.json"),
                report_path=Path("reports/daily/frontier_compass_arxiv_biomedical-discovery_2026-03-24.html"),
                display_source="freshly fetched",
                fetch_status_label="fresh source fetch",
                artifact_source_label="fresh source fetch",
            )
        )

    runner = FrontierCompassRunner()
    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    result = _write_selected_digest(
        runner,
        selected_source=BIOMEDICAL_DISCOVERY_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
    )

    assert result.display_source == "freshly fetched"
    assert recorded_kwargs == {
        "source": BIOMEDICAL_DISCOVERY_MODE,
        "requested_date": date(2026, 3, 24),
        "max_results": 80,
        "refresh": True,
        "allow_stale_cache": True,
        "report_mode": "deterministic",
        "zotero_export_path": None,
    }


def test_load_requested_daily_digest_uses_exact_requested_date(tmp_path: Path) -> None:
    app = FrontierCompassApp()
    older_requested = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    requested = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"

    _write_digest(
        older_requested,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.68)],
    )
    _write_digest(
        requested,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.74)],
    )

    cached = app.load_requested_daily_digest(
        category=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        cache_dir=tmp_path,
        non_empty_only=True,
    )

    assert cached is not None
    assert cached.cache_path == requested
    assert cached.digest.requested_target_date == date(2026, 3, 24)


def test_load_or_materialize_current_digest_prefers_same_day_non_empty_cache(tmp_path: Path) -> None:
    app = FrontierCompassApp()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    _write_digest(
        cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.77)],
    )
    app.default_daily_report_path = lambda category, target_date: report_path  # type: ignore[method-assign]

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
    )

    assert result.display_source == "loaded from cache"
    assert result.cache_path == cache_path
    assert result.fetch_error == ""
    assert result.report_path == report_path


def test_load_or_materialize_current_digest_materializes_report_for_cached_digest(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    _write_digest(
        cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.77)],
    )
    monkeypatch.setattr(
        FrontierCompassApp,
        "default_daily_report_path",
        staticmethod(lambda category, target_date: tmp_path / f"frontier_compass_arxiv_{category}_{target_date.isoformat()}.html"),
    )

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
    )

    assert result.report_path == report_path
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "Personalized Digest" in html
    assert "Frontier Report" in html
    assert "Top recommendations" in html
    assert "Audit trail" in html
    assert html.index("What to read first") < html.index("Frontier runtime")
    assert "<strong>Source mix</strong>\n        <p></p>" not in html


def test_load_or_materialize_current_digest_fetches_when_same_day_cache_absent(monkeypatch, tmp_path: Path) -> None:
    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        cache_lookup_seconds = kwargs.pop("cache_lookup_seconds")
        assert cache_lookup_seconds >= 0.0
        assert kwargs == {
            "mode": BIOMEDICAL_LATEST_MODE,
            "today": date(2026, 3, 24),
            "max_results": 80,
            "cache_path": tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
            "output_path": tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
        }
        digest = DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_LATEST_MODE,
            target_date=date(2026, 3, 24),
            generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
            feed_url="https://export.arxiv.org/api/query",
            profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            ranked=[_sample_ranked_paper(score=0.82)],
            searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
            per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
            total_fetched=3,
            feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        )
        return DailyPreparationResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
        )

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)

    result = FrontierCompassApp().load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
    )

    assert result.display_source == "freshly fetched"
    assert result.fetch_error == ""
    assert result.cache_path.name == "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    assert result.report_path.name == "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"


def test_load_or_materialize_current_digest_reuses_same_day_cache_when_fetch_fails(monkeypatch, tmp_path: Path) -> None:
    fallback_cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    _write_digest(
        fallback_cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.71)],
    )

    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)
    monkeypatch.setattr(
        FrontierCompassApp,
        "default_daily_report_path",
        staticmethod(lambda category, target_date: report_path),
    )

    result = FrontierCompassApp().load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
    )

    assert result.display_source == "reused same-date cache after fetch failure"
    assert result.fetch_error == "upstream arXiv timeout"
    assert result.cache_path == fallback_cache_path
    assert result.report_path == report_path
    assert result.digest.requested_target_date == date(2026, 3, 24)
    html = report_path.read_text(encoding="utf-8")
    assert "Fetch status" in html
    assert "same-date cache reused after fetch failure" in html
    assert "Fresh fetch error" in html
    assert "upstream arXiv timeout" in html


def test_load_or_materialize_current_digest_reuses_older_compatible_cache_when_fetch_fails(monkeypatch, tmp_path: Path) -> None:
    older_cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    requested_report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    requested_cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    _write_digest(
        older_cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.74)],
    )

    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)

    result = FrontierCompassApp().load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
    )

    assert result.display_source == "older compatible cache reused after fetch failure"
    assert result.fetch_error == "upstream arXiv timeout"
    assert result.cache_path == requested_cache_path
    assert result.report_path == requested_report_path
    assert result.digest.requested_target_date == date(2026, 3, 24)
    assert result.digest.effective_display_date == date(2026, 3, 23)
    assert result.digest.stale_cache_fallback_used is True
    assert result.digest.stale_cache_source_requested_date == date(2026, 3, 23)
    assert result.digest.stale_cache_source_effective_date == date(2026, 3, 23)
    assert result.digest.strict_same_day_counts_known is False
    html = requested_report_path.read_text(encoding="utf-8")
    assert "older compatible cache reused after fetch failure" in html
    assert "Stale cache fallback" in html
    assert "Stale cache source requested date" in html
    assert "unavailable / unavailable" in html
    assert requested_cache_path.exists()


def test_load_or_materialize_current_digest_reuses_same_day_range_cache_without_truncating_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    requested_date = date(2026, 3, 24)
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-24.html"
    digest = _range_digest_fixture(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        requested_date=requested_date,
        start_date=requested_date,
        end_date=requested_date,
        request_window=RequestWindow(
            kind="range",
            requested_date=requested_date,
            start_date=requested_date,
            end_date=requested_date,
            status="complete",
            completed_dates=(requested_date,),
        ),
        ranked=[
            _sample_ranked_paper(score=0.96, identifier="2603.24001v1"),
            _sample_ranked_paper(score=0.91, identifier="2603.24002v1"),
            _sample_ranked_paper(score=0.87, identifier="2603.24003v1"),
        ],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=3,
                displayed_count=3,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.5, parse_seconds=0.1, total_seconds=0.6),
            ),
        ),
        report_status="ready",
        source_counts={"arxiv": 3},
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")

    def fake_write_daily_outputs(**kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"write_daily_outputs should not run for cached range reuse: {kwargs}")

    monkeypatch.setattr(app, "write_daily_outputs", fake_write_daily_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=requested_date,
        start_date=requested_date,
        end_date=requested_date,
        max_results=1,
        cache_dir=tmp_path,
        cache_path=cache_path,
        output_path=report_path,
        fetch_scope="range-full",
    )

    assert result.display_source == "loaded from cache"
    assert result.cache_path == cache_path
    assert result.report_path == report_path
    assert result.digest.request_window.kind == "range"
    assert result.digest.request_window.completed_dates == (requested_date,)
    assert result.digest.request_window.status == "complete"
    assert len(result.digest.ranked) == 3
    assert result.digest.frontier_report is not None
    assert result.digest.frontier_report.total_ranked == 3


def test_build_range_digest_preserves_partial_window_provenance_and_marks_report_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    start_date = date(2026, 3, 24)
    end_date = date(2026, 3, 25)
    first_child = _range_digest_fixture(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        requested_date=start_date,
        start_date=start_date,
        end_date=start_date,
        ranked=[
            _sample_ranked_paper(score=0.96, identifier="2603.24011v1"),
            _sample_ranked_paper(score=0.89, identifier="2603.24012v1"),
        ],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=2,
                displayed_count=2,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.4, parse_seconds=0.1, total_seconds=0.5),
            ),
        ),
        report_status="ready",
        source_counts={"arxiv": 2},
    )
    second_child = _range_digest_fixture(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        requested_date=end_date,
        start_date=end_date,
        end_date=end_date,
        ranked=[
            _sample_ranked_paper(score=0.84, identifier="2603.24021v1"),
            _sample_ranked_paper(score=0.81, identifier="2603.24022v1"),
        ],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=2,
                displayed_count=2,
                status="partial",
                cache_status="same-day-cache",
                error="upstream arXiv timeout",
                note="Same-day cache reused after a fresh fetch failure.",
                timings=RunTimings(network_seconds=0.3, parse_seconds=0.1, total_seconds=0.4),
            ),
        ),
        report_status="partial",
        report_error="upstream arXiv timeout",
        source_counts={"arxiv": 2},
    )
    child_by_date = {
        start_date: first_child,
        end_date: second_child,
    }

    def fake_build_daily_digest(**kwargs):  # type: ignore[no-untyped-def]
        return child_by_date[kwargs["today"]]

    monkeypatch.setattr(app, "build_daily_digest", fake_build_daily_digest)

    result = app._build_range_digest(
        category=BIOMEDICAL_LATEST_MODE,
        mode=None,
        report_mode="deterministic",
        start_date=start_date,
        end_date=end_date,
        max_results=1,
        feed_url=None,
        profile_source="baseline",
        zotero_export_path=None,
        zotero_db_path=None,
    )

    assert result.report_status == "partial"
    assert result.report_error == "upstream arXiv timeout"
    assert result.request_window.kind == "range"
    assert result.request_window.status == "partial"
    assert result.request_window.completed_dates == (start_date, end_date)
    assert result.request_window.failed_date == end_date
    assert result.request_window.failed_source == "arxiv"
    assert result.request_window.failure_reason == "upstream arXiv timeout"
    assert len(result.ranked) == 4
    assert result.frontier_report is not None
    assert result.frontier_report.report_status == "partial"
    assert result.frontier_report.report_error == "upstream arXiv timeout"

    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24_to_2026-03-25.json"
    cache_path.write_text(json.dumps(result.to_mapping(), indent=2), encoding="utf-8")
    reloaded = FrontierCompassApp().load_daily_digest(cache_path)
    assert reloaded.request_window.kind == "range"
    assert reloaded.request_window.completed_dates == (start_date, end_date)
    assert reloaded.request_window.failed_date == end_date
    assert reloaded.request_window.failed_source == "arxiv"
    assert reloaded.request_window.failure_reason == "upstream arXiv timeout"
    assert reloaded.report_status == "partial"


def test_build_range_digest_keeps_failed_source_blank_when_first_partial_child_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FrontierCompassApp()
    start_date = date(2026, 3, 24)
    end_date = date(2026, 3, 26)
    ambiguous_child = _range_digest_fixture(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        requested_date=start_date,
        start_date=start_date,
        end_date=start_date,
        ranked=[_sample_ranked_paper(score=0.95, identifier="2603.24041v1")],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.4, parse_seconds=0.1, total_seconds=0.5),
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="failed",
                cache_status="fresh",
                error="biorxiv unavailable",
                timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
            ),
            SourceRunStats(
                source="medrxiv",
                fetched_count=0,
                displayed_count=0,
                status="failed",
                cache_status="fresh",
                error="medrxiv unavailable",
                timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
            ),
        ),
        report_status="partial",
        report_error="biorxiv unavailable; medrxiv unavailable",
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
    )
    later_child = _range_digest_fixture(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        requested_date=end_date,
        start_date=end_date,
        end_date=end_date,
        ranked=[_sample_ranked_paper(score=0.88, identifier="2603.24042v1")],
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=1,
                displayed_count=1,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.3, parse_seconds=0.1, total_seconds=0.4),
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.1, total_seconds=0.1),
            ),
            SourceRunStats(
                source="medrxiv",
                fetched_count=0,
                displayed_count=0,
                status="failed",
                cache_status="fresh",
                error="medrxiv unavailable",
                timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
            ),
        ),
        report_status="partial",
        report_error="medrxiv unavailable",
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
    )
    child_by_date = {
        start_date: ambiguous_child,
        end_date: later_child,
    }

    def fake_build_daily_digest(**kwargs):  # type: ignore[no-untyped-def]
        return child_by_date[kwargs["today"]]

    monkeypatch.setattr(app, "build_daily_digest", fake_build_daily_digest)

    result = app._build_range_digest(
        category=BIOMEDICAL_MULTISOURCE_MODE,
        mode=None,
        report_mode="deterministic",
        start_date=start_date,
        end_date=end_date,
        max_results=1,
        feed_url=None,
        profile_source="baseline",
        zotero_export_path=None,
        zotero_db_path=None,
    )

    assert result.request_window.failed_date == start_date
    assert result.request_window.failed_source == ""
    assert result.request_window.failure_reason.startswith("biorxiv unavailable")
    assert " / medrxiv" not in result.request_window.label
    assert result.report_status == "partial"


def test_build_range_digest_keeps_multisource_zero_count_and_failed_source_rows_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FrontierCompassApp()
    start_date = date(2026, 3, 24)
    end_date = date(2026, 3, 25)
    ranked = [
        _sample_ranked_paper(score=0.95, identifier="2603.24031v1"),
        _sample_ranked_paper(score=0.88, identifier="2603.24032v1"),
    ]
    first_child = _range_digest_fixture(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        requested_date=start_date,
        start_date=start_date,
        end_date=start_date,
        ranked=ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=2,
                displayed_count=2,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.7, parse_seconds=0.2, total_seconds=0.9),
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.1, total_seconds=0.1),
            ),
            SourceRunStats(
                source="medrxiv",
                fetched_count=0,
                displayed_count=0,
                status="failed",
                cache_status="fresh",
                error="medRxiv unavailable",
                note="medRxiv unavailable for the requested day.",
                timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
            ),
        ),
        report_status="partial",
        report_error="medRxiv unavailable",
        source_counts={"arxiv": 2, "biorxiv": 0, "medrxiv": 0},
    )
    second_child = _range_digest_fixture(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        requested_date=end_date,
        start_date=end_date,
        end_date=end_date,
        ranked=ranked,
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=2,
                displayed_count=2,
                status="ready",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.6, parse_seconds=0.2, total_seconds=0.8),
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                cache_status="fresh",
                timings=RunTimings(network_seconds=0.1, total_seconds=0.1),
            ),
            SourceRunStats(
                source="medrxiv",
                fetched_count=0,
                displayed_count=0,
                status="failed",
                cache_status="same-day-cache",
                error="medRxiv unavailable",
                note="Same-day cache reused after a fresh fetch failure.",
                timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
            ),
        ),
        report_status="partial",
        report_error="medRxiv unavailable",
        source_counts={"arxiv": 2, "biorxiv": 0, "medrxiv": 0},
    )
    child_by_date = {
        start_date: first_child,
        end_date: second_child,
    }

    def fake_build_daily_digest(**kwargs):  # type: ignore[no-untyped-def]
        return child_by_date[kwargs["today"]]

    monkeypatch.setattr(app, "build_daily_digest", fake_build_daily_digest)

    result = app._build_range_digest(
        category=BIOMEDICAL_MULTISOURCE_MODE,
        mode=None,
        report_mode="deterministic",
        start_date=start_date,
        end_date=end_date,
        max_results=1,
        feed_url=None,
        profile_source="baseline",
        zotero_export_path=None,
        zotero_db_path=None,
    )

    assert result.report_status == "partial"
    assert result.request_window.kind == "range"
    assert result.request_window.failed_source == "medrxiv"
    assert result.request_window.failed_date == start_date
    assert result.request_window.failure_reason == "medRxiv unavailable"
    source_stats = {row.source: row for row in result.source_run_stats}
    assert tuple(source_stats) == ("arxiv", "biorxiv", "medrxiv")
    assert source_stats["biorxiv"].displayed_count == 0
    assert source_stats["biorxiv"].status == "empty"
    assert source_stats["medrxiv"].displayed_count == 0
    assert source_stats["medrxiv"].status == "failed"
    assert source_stats["medrxiv"].error == "medRxiv unavailable"
    assert "medRxiv unavailable" in source_stats["medrxiv"].note
    assert result.frontier_report is not None
    frontier_source_stats = {row.source: row for row in result.frontier_report.source_run_stats}
    assert frontier_source_stats["biorxiv"].displayed_count == 0
    assert frontier_source_stats["medrxiv"].status == "failed"


def test_run_daily_workflow_reuses_cache_and_writes_dry_run_email(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.82)],
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
        HtmlReportBuilder().render_daily_digest(digest, acquisition_status_label="same-day cache"),
        encoding="utf-8",
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_LATEST_MODE
        assert kwargs["requested_date"] == date(2026, 3, 24)
        assert kwargs["max_results"] == 80
        assert kwargs["force_fetch"] is False
        return DailyBootstrapResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="loaded from cache",
        )

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)

    result = app.run_daily_workflow(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
        generate_dry_run_email=True,
        email_to=("reviewer@example.com",),
        email_from="frontier@example.com",
    )

    assert result.cache_path == cache_path
    assert result.report_path == report_path
    assert result.display_source == "loaded from cache"
    assert result.fetch_status_label == "same-day cache"
    assert result.artifact_source_label == "same-day cache"
    assert result.delivery_label == "dry-run .eml written"
    assert result.email_subject.startswith("FrontierCompass Biomedical Latest Available arXiv Brief")
    assert result.email_to == "reviewer@example.com"
    assert result.email_from == "frontier@example.com"
    assert result.eml_path is not None
    assert result.eml_path.exists()
    assert result.eml_path.stat().st_size > 0


def test_prepare_ui_session_keeps_recent_history_error_non_fatal(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.82)],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    def fake_materialize_daily_digest(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["selected_source"] == BIOMEDICAL_LATEST_MODE
        return DailyBootstrapResult(
            digest=digest,
            cache_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json",
            report_path=tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html",
            display_source="loaded from cache",
        )

    def fake_recent_daily_runs(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        raise RuntimeError("history temporarily unavailable")

    monkeypatch.setattr(FrontierCompassApp, "materialize_daily_digest", fake_materialize_daily_digest)
    monkeypatch.setattr(FrontierCompassApp, "recent_daily_runs", fake_recent_daily_runs)

    session = FrontierCompassRunner(app=app).prepare_ui_session(
        source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        max_results=80,
    )

    assert session.fetch_status_label == "same-day cache"
    assert session.recent_history == ()
    assert session.recent_history_error == "history temporarily unavailable"


def test_load_or_materialize_current_digest_does_not_reuse_older_cache_when_fetch_fails_when_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    older_cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-23.json"
    _write_digest(
        older_cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.71)],
    )

    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)

    with pytest.raises(RuntimeError, match="no same-date cache is available"):
        FrontierCompassApp().load_or_materialize_current_digest(
            selected_source=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            max_results=80,
            cache_dir=tmp_path,
            force_fetch=True,
            allow_stale_cache=False,
        )


def test_load_or_materialize_current_digest_rejects_incompatible_older_zotero_cache(monkeypatch, tmp_path: Path) -> None:
    app = FrontierCompassApp()
    older_cache_path = tmp_path / app.default_daily_cache_path(
        BIOMEDICAL_LATEST_MODE,
        date(2026, 3, 23),
        zotero_export_path=ZOTERO_FIXTURE_PATH,
    ).name
    _write_digest(
        older_cache_path,
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 23),
        generated_at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc),
        ranked=[_sample_ranked_paper(score=0.71)],
        profile=ZoteroProfileBuilder().build_augmented_profile(
            FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            export_path=ZOTERO_FIXTURE_PATH,
        ),
    )

    different_export_path = tmp_path / "other_export.csl.json"
    different_export_path.write_text("[]", encoding="utf-8")

    def fake_write_daily_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream arXiv timeout")

    monkeypatch.setattr(FrontierCompassApp, "write_daily_outputs", fake_write_daily_outputs)

    with pytest.raises(RuntimeError, match="no same-date cache or compatible older cache is available"):
        FrontierCompassApp().load_or_materialize_current_digest(
            selected_source=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            max_results=80,
            cache_dir=tmp_path,
            force_fetch=True,
            zotero_export_path=different_export_path,
            allow_stale_cache=True,
        )


def test_load_or_materialize_current_digest_normalizes_inverted_range_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = FrontierCompassApp()
    normalized_start = date(2026, 3, 20)
    normalized_end = date(2026, 3, 24)
    range_digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=normalized_start,
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.82, published=normalized_end)],
        requested_date=normalized_start,
        effective_date=normalized_end,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
    )

    def fake_load_matching_cached_daily_digest(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del self, args, kwargs
        return None

    def fake_materialize_range_outputs(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self is app
        assert kwargs["requested_date"] == normalized_start
        assert kwargs["start_date"] == normalized_start
        assert kwargs["end_date"] == normalized_end
        assert Path(kwargs["cache_path"]).name.endswith("2026-03-20_to_2026-03-24.json")
        assert Path(kwargs["output_path"]).name.endswith("2026-03-20_to_2026-03-24.html")
        return DailyPreparationResult(
            digest=range_digest,
            cache_path=Path(kwargs["cache_path"]),
            report_path=Path(kwargs["output_path"]),
        )

    monkeypatch.setattr(FrontierCompassApp, "_load_matching_cached_daily_digest", fake_load_matching_cached_daily_digest)
    monkeypatch.setattr(FrontierCompassApp, "_materialize_range_outputs", fake_materialize_range_outputs)

    result = app.load_or_materialize_current_digest(
        selected_source=BIOMEDICAL_LATEST_MODE,
        requested_date=date(2026, 3, 24),
        start_date=date(2026, 3, 24),
        end_date=date(2026, 3, 20),
        max_results=80,
        cache_dir=tmp_path,
        force_fetch=True,
        allow_stale_cache=True,
        fetch_scope="range-full",
    )

    assert result.display_source == "aggregated from day artifacts"
    assert result.cache_path.name.endswith("2026-03-20_to_2026-03-24.json")
    assert result.report_path.name.endswith("2026-03-20_to_2026-03-24.html")
    assert result.digest.requested_target_date == normalized_start


def test_build_local_file_url_uses_file_scheme(tmp_path: Path) -> None:
    report_path = tmp_path / "report.html"
    assert build_local_file_url(report_path) == report_path.resolve().as_uri()


def test_build_existing_local_file_url_returns_empty_for_missing_path(tmp_path: Path) -> None:
    report_path = tmp_path / "report.html"
    assert build_existing_local_file_url(report_path) == ""
    report_path.write_text("<html></html>", encoding="utf-8")
    assert build_existing_local_file_url(report_path) == report_path.resolve().as_uri()


def test_html_report_builder_renders_discovery_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_DISCOVERY_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_DISCOVERY_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.83)],
            category=BIOMEDICAL_DISCOVERY_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical discovery",
        mode_kind="hybrid",
        mode_notes="Hybrid q-bio bundle plus fixed broader arXiv API discovery queries.",
        search_profile_label="broader-biomedical-discovery-v1",
        search_queries=("((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:\"single-cell\"))",),
    )

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "FrontierCompass Biomedical Discovery arXiv Brief (2026-03-24)" in html
    assert "Biomedical discovery (biomedical-discovery)" in html
    assert "hybrid" in html
    assert "Requested date" in html
    assert "2026-03-24" in html
    assert "Effective release date" in html
    assert "Latest-available display fallback" in html
    assert "Stale cache fallback" in html
    assert "Showing strict same-day results for the requested date." in html
    assert "Why it surfaced" in html
    assert "Score details" in html
    assert "broader-biomedical-discovery-v1" in html
    assert "Hybrid q-bio bundle plus fixed broader arXiv API discovery queries." in html
    assert "((cat:q-bio OR cat:cs.LG) AND (all:bioinformatics OR all:&quot;single-cell&quot;))" in html


def test_html_report_builder_renders_source_contract_and_source_identifier() -> None:
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="biorxiv",
                    identifier="10.1101/2026.03.24.000001v1",
                    title="bioRxiv contract fixture",
                    summary="Fixture-backed multisource rendering check.",
                    authors=("A Biologist",),
                    categories=("bioinformatics",),
                    published=date(2026, 3, 24),
                    url="https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                    source_metadata={
                        "native_identifier": "10.1101/2026.03.24.000001v1",
                        "native_url": "https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                        "tags": ["bioinformatics"],
                    },
                ),
                score=0.82,
                recommendation_summary="Fixture-backed multisource rendering check.",
            )
        ],
        frontier_report=_frontier_report_for(
            [
                RankedPaper(
                    paper=PaperRecord(
                        source="biorxiv",
                        identifier="10.1101/2026.03.24.000001v1",
                        title="bioRxiv contract fixture",
                        summary="Fixture-backed multisource rendering check.",
                        authors=("A Biologist",),
                        categories=("bioinformatics",),
                        published=date(2026, 3, 24),
                        url="https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                        source_metadata={
                            "native_identifier": "10.1101/2026.03.24.000001v1",
                            "native_url": "https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
                            "tags": ["bioinformatics"],
                        },
                    ),
                    score=0.82,
                    recommendation_summary="Fixture-backed multisource rendering check.",
                )
            ],
            category=BIOMEDICAL_MULTISOURCE_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
        ),
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 0, "q-bio.GN": 0},
        source_counts={"biorxiv": 1},
        total_fetched=1,
        source_metadata={
            "biorxiv": {
                "mode": "rss",
                "native_filters": ["all"],
                "native_endpoints": {"all": "https://connect.biorxiv.org/biorxiv_xml.php?subject=all"},
            }
        },
        mode_label="Biomedical multisource",
        mode_kind="multisource",
    )

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "Source contract" in html
    assert "bioRxiv: mode=rss | filters=all | endpoints=1" in html
    assert "10.1101/2026.03.24.000001v1" in html
    assert "bioRxiv (1)" in html


def test_html_report_builder_renders_profile_basis_for_zotero_augmented_digest() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=ZoteroProfileBuilder().build_augmented_profile(
            FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
            export_path=ZOTERO_FIXTURE_PATH,
        ),
        ranked=[_sample_ranked_paper(score=0.83)],
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.83)],
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "Profile mode" in html
    assert "Profile basis" in html
    assert "biomedical baseline + Zotero export" in html
    assert ZOTERO_FIXTURE_PATH.name in html
    assert "Zotero signals" in html


def test_html_report_builder_renders_latest_available_fallback_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.83)],
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 23),
        ),
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

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "FrontierCompass Biomedical Latest Available arXiv Brief (requested 2026-03-24)" in html
    assert "Effective release date" in html
    assert "2026-03-23" in html
    assert "Latest-available display fallback" in html
    assert "Stale cache fallback" in html
    assert "yes" in html
    assert "Latest available fallback results" in html
    assert "Showing latest available fallback results because the strict same-day subset for the requested date was empty." in html


def test_html_report_builder_renders_stale_cache_fallback_metadata() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 23, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
        frontier_report=_frontier_report_for(
            [_sample_ranked_paper(score=0.83)],
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 23),
        ),
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

    html = HtmlReportBuilder().render_daily_digest(
        digest,
        acquisition_status_label="older compatible cache reused after fetch failure",
        fetch_error="upstream arXiv timeout",
    )

    assert "older compatible cache reused after fetch failure" in html
    assert "Stale cache fallback" in html
    assert "Stale cache source requested date" in html
    assert "Fresh fetch error" in html
    assert "upstream arXiv timeout" in html
    assert "unavailable / unavailable" in html


def test_html_report_builder_handles_legacy_cache_without_frontier_report_honestly() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_sample_ranked_paper(score=0.83)],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    html = HtmlReportBuilder().render_daily_digest(digest, acquisition_status_label="same-day cache")

    assert "Frontier report unavailable" in html
    assert "will not infer one from the personalized slice" in html


def test_html_report_builder_balances_reviewer_shortlist_and_keeps_full_ranked_order() -> None:
    ranked = [
        _sample_ranked_paper(
            score=0.91,
            identifier="2603.23001v1",
            title="Sparse Autoencoders for Medical Imaging",
            summary="Medical imaging for MRI and CT review.",
            categories=("cs.CV",),
            published=date(2026, 3, 20),
        ),
        _sample_ranked_paper(
            score=0.9,
            identifier="2603.23002v1",
            title="Radiology Distillation for CT Cohorts",
            summary="Radiology and CT pipeline for medical imaging.",
            categories=("cs.CV",),
            published=date(2026, 3, 20),
        ),
        _sample_ranked_paper(
            score=0.89,
            identifier="2603.23003v1",
            title="Zero-shot Chest Scan Segmentation",
            summary="Medical imaging benchmark for chest scan segmentation.",
            categories=("cs.CV",),
            published=date(2026, 3, 20),
        ),
        _sample_ranked_paper(
            score=0.88,
            identifier="2603.23004v1",
            title="Single-cell Transcriptomics Atlas Integration",
            summary="Genomics and transcriptomics workflow for a single-cell atlas.",
            categories=("q-bio.GN", "cs.LG"),
            published=date(2026, 3, 24),
        ),
        _sample_ranked_paper(
            score=0.87,
            identifier="2603.23005v1",
            title="Whole-slide Histopathology Reasoning",
            summary="Pathology and whole-slide microscopy pipeline for diagnostics.",
            categories=("cs.CV",),
            published=date(2026, 3, 24),
        ),
    ]
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=_frontier_report_for(
            ranked,
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 4, "cs.LG": 1},
        total_fetched=6,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    html = HtmlReportBuilder().render_daily_digest(digest)
    shortlist_section = html.split('<section class="section"><h2>Audit trail</h2>', 1)[0]

    assert "Personalized Digest" in html
    assert "Frontier Report" in html
    assert "Top recommendations" in html
    assert "Repeated themes" in html
    assert "Theme: genomics / transcriptomics / single-cell" in html
    assert "Theme: pathology / histopathology / microscopy" in html
    assert shortlist_section.index("Single-cell Transcriptomics Atlas Integration") < shortlist_section.index("Zero-shot Chest Scan Segmentation")
    assert shortlist_section.index("Whole-slide Histopathology Reasoning") < shortlist_section.index("Zero-shot Chest Scan Segmentation")
    assert "All ranked papers" not in html


def test_html_report_builder_renders_exploration_section_when_present() -> None:
    exploration_pick = _sample_ranked_paper(
        score=0.41,
        identifier="2603.23109v1",
        title="Exploration lane microscopy fixture",
        summary="Microscopy-led exploration fixture outside the main shortlist.",
        categories=("cs.CV", "cs.AI"),
    )
    ranked = [
        _sample_ranked_paper(score=0.91, identifier="2603.23001v1", title="Sparse Autoencoders for Medical Imaging"),
        _sample_ranked_paper(score=0.9, identifier="2603.23002v1", title="Radiology Distillation for CT Cohorts"),
        _sample_ranked_paper(score=0.89, identifier="2603.23003v1", title="Whole-slide Histopathology Reasoning"),
        _sample_ranked_paper(score=0.88, identifier="2603.23004v1", title="Single-cell Transcriptomics Atlas Integration"),
        _sample_ranked_paper(score=0.87, identifier="2603.23005v1", title="Clinical Tabular Learning for EHR Cohorts"),
        _sample_ranked_paper(score=0.86, identifier="2603.23006v1", title="Protein Structure Priors for Biomolecular Discovery"),
        _sample_ranked_paper(score=0.85, identifier="2603.23007v1", title="General Biomedical Modeling Notes"),
        _sample_ranked_paper(score=0.84, identifier="2603.23008v1", title="Microscopy-guided Pathology Segmentation"),
        exploration_pick,
    ]
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        exploration_picks=[exploration_pick],
        frontier_report=_frontier_report_for(
            ranked,
            category=BIOMEDICAL_LATEST_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 5, "cs.LG": 3},
        total_fetched=9,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    html = HtmlReportBuilder().render_daily_digest(digest)

    assert "Exploration" in html
    assert "Why it&#x27;s exploratory" in html
    assert "These use the same biomedical baseline profile" in html
    assert "Exploration lane microscopy fixture" in html


def test_daily_digest_from_mapping_backfills_legacy_single_category_fields() -> None:
    payload = {
        "source": "arxiv",
        "category": "q-bio",
        "target_date": "2026-03-24",
        "generated_at": "2026-03-24T07:15:00+00:00",
        "feed_url": "https://rss.arxiv.org/atom/q-bio",
        "profile": FrontierCompassApp.daily_profile("q-bio").to_mapping(),
        "ranked": [_sample_ranked_paper(score=0.64).to_mapping()],
    }

    digest = DailyDigest.from_mapping(payload)

    assert digest.searched_categories == ("q-bio",)
    assert digest.total_fetched == 1
    assert digest.per_category_counts == {"q-bio": 1}
    assert digest.feed_urls == {"q-bio": "https://rss.arxiv.org/atom/q-bio"}
    assert digest.requested_target_date == date(2026, 3, 24)
    assert digest.effective_display_date == date(2026, 3, 24)
    assert digest.strict_same_day_fetched_count == 1
    assert digest.strict_same_day_ranked_count == 1


def test_load_daily_digest_backfills_legacy_multisource_source_rows_as_unknown(tmp_path: Path) -> None:
    ranked = [
        RankedPaper(
            paper=PaperRecord(
                source="arxiv",
                identifier="2603.24001v1",
                title="Legacy multisource fixture",
                summary="Legacy multisource fixture.",
                authors=("A Researcher",),
                categories=("q-bio.GN",),
                published=date(2026, 3, 24),
                updated=date(2026, 3, 24),
                url="https://arxiv.org/abs/2603.24001",
            ),
            score=0.82,
            recommendation_summary="Legacy multisource fixture.",
        )
    ]
    partial_source_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            cache_status="fresh",
        ),
    )
    frontier_report = replace(
        _frontier_report_for(
            ranked,
            category=BIOMEDICAL_MULTISOURCE_MODE,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
        ),
        source_run_stats=partial_source_stats,
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=ranked,
        source_run_stats=partial_source_stats,
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
    assert set(source_stats) == {"arxiv", "biorxiv", "medrxiv"}
    assert source_stats["biorxiv"].status == "unknown"
    assert source_stats["medrxiv"].status == "unknown"
    assert "Legacy artifact is missing source-level observability" in source_stats["biorxiv"].note
    assert loaded.frontier_report is not None
    assert {row.source for row in loaded.frontier_report.source_run_stats} == {"arxiv", "biorxiv", "medrxiv"}


def _write_digest(
    path: Path,
    *,
    category: str,
    target_date: date,
    generated_at: datetime,
    ranked: list[RankedPaper] | None = None,
    profile=None,
) -> None:
    digest = DailyDigest(
        source="arxiv",
        category=category,
        target_date=target_date,
        generated_at=generated_at,
        feed_url=f"https://rss.arxiv.org/atom/{category}",
        profile=profile or FrontierCompassApp.daily_profile(category),
        ranked=ranked or [],
        frontier_report=(
            _frontier_report_for(
                ranked or [],
                category=category,
                requested_date=target_date,
                effective_date=target_date,
            )
            if ranked
            else None
        ),
        searched_categories=(category,),
        per_category_counts={category: len(ranked or [])},
        total_fetched=len(ranked or []),
        feed_urls={category: f"https://rss.arxiv.org/atom/{category}"},
    )
    path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")


def _sample_ranked_paper(
    *,
    score: float,
    identifier: str = "2603.20077v1",
    title: str = "Sample biomedical shortlist paper",
    summary: str = "A sample ranked paper used for cache resolution tests.",
    categories: tuple[str, ...] = ("q-bio.GN",),
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
        reasons=(
            "biomedical evidence: genomics, transcriptomics",
            "topic match: q-bio, q-bio.gn",
        ),
        recommendation_summary="Sample recommendation summary.",
    )


def _frontier_report_for(
    ranked: list[RankedPaper],
    *,
    category: str,
    requested_date: date,
    effective_date: date,
    source: str = "arxiv",
) -> object:
    return build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=requested_date,
        effective_date=effective_date,
        source=source,
        mode=category,
        mode_label=category,
        total_fetched=len(ranked),
    )


def _range_digest_fixture(
    *,
    source: str,
    category: str,
    requested_date: date,
    start_date: date,
    end_date: date,
    ranked: list[RankedPaper],
    source_run_stats: tuple[SourceRunStats, ...],
    report_status: str = "ready",
    report_error: str = "",
    source_counts: dict[str, int] | None = None,
    request_window: RequestWindow | None = None,
    generated_at: datetime | None = None,
) -> DailyDigest:
    resolved_request_window = request_window or RequestWindow(
        kind="day",
        requested_date=requested_date,
    )
    resolved_source_counts = source_counts or {"arxiv": len(ranked)}
    resolved_generated_at = generated_at or datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc)
    mode_label = "Biomedical multisource" if category == BIOMEDICAL_MULTISOURCE_MODE else "Biomedical latest available"
    mode_kind = "multisource" if category == BIOMEDICAL_MULTISOURCE_MODE else "latest-available-hybrid"
    frontier_report = replace(
        _frontier_report_for(
            ranked,
            category=category,
            requested_date=requested_date,
            effective_date=end_date,
            source=source,
        ),
        request_window=resolved_request_window,
        source_run_stats=source_run_stats,
        source_counts=resolved_source_counts,
        report_status=report_status,
        report_error=report_error,
    )
    return DailyDigest(
        source=source,
        category=category,
        target_date=requested_date,
        generated_at=resolved_generated_at,
        feed_url="https://export.arxiv.org/api/query" if source != "multisource" else "",
        profile=FrontierCompassApp.daily_profile(category),
        ranked=ranked,
        request_window=resolved_request_window,
        source_run_stats=source_run_stats,
        run_timings=RunTimings(
            network_seconds=0.5,
            parse_seconds=0.1,
            rank_seconds=0.2,
            total_seconds=0.8,
        ),
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN", "cs.LG") if source != "multisource" else ("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": len(ranked)},
        source_counts=resolved_source_counts,
        total_fetched=sum(resolved_source_counts.values()),
        mode_label=mode_label,
        mode_kind=mode_kind,
        requested_date=requested_date,
        effective_date=end_date,
        report_status=report_status,
        report_error=report_error,
        fetch_scope="range-full" if start_date != end_date or resolved_request_window.kind == "range" else "day-full",
        search_profile_label="",
        search_queries=(),
    )
