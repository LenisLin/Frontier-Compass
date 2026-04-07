"""Application orchestration for FrontierCompass workflows."""

from __future__ import annotations

import json
from hashlib import sha1
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.common.frontier_report_llm import (
    FrontierReportLLMConfigurationError,
    FrontierReportLLMError,
    FrontierReportLLMSettings,
    build_model_assisted_frontier_report,
    frontier_report_llm_unavailable_reason,
    resolve_frontier_report_llm_settings,
)
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    build_report_runtime_contract,
)
from frontier_compass.common.source_bundles import (
    DEFAULT_PUBLIC_SOURCE_BUNDLE,
    DEFAULT_SOURCE_BUNDLES_PATH,
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
    SOURCE_BUNDLE_BIOMEDICAL,
    SourceBundleDefinition,
    build_custom_source_bundle,
    bundle_matches_paper,
    delete_custom_source_bundle,
    filter_papers_for_bundle,
    list_custom_source_bundles,
    list_public_source_bundles,
    load_source_bundles,
    normalize_source_bundle_id,
    resolve_source_bundle,
    source_bundle_label,
    upsert_custom_source_bundle,
)
from frontier_compass.exploration.selector import (
    DEFAULT_DAILY_EXPLORATION_POLICY,
    ExplorationSelector,
    daily_exploration_note,
    select_daily_exploration_picks,
)
from frontier_compass.ingest.arxiv import (
    ArxivQueryDefinition,
    ArxivClient,
    BIOMEDICAL_DAILY_CATEGORIES,
    BIOMEDICAL_DISCOVERY_CATEGORIES,
    BIOMEDICAL_DISCOVERY_PROFILE_LABEL,
    ZOTERO_RETRIEVAL_PROFILE_LABEL,
    build_biomedical_discovery_queries,
    build_zotero_retrieval_queries,
    filter_paper_batches_by_date,
    latest_available_paper_date,
    merge_category_papers,
    merge_paper_batches,
)
from frontier_compass.ingest.biorxiv import BioRxivClient
from frontier_compass.ingest.common import FeedFetchDetails, measure_operation
from frontier_compass.ingest.medrxiv import MedRxivClient
from frontier_compass.ingest.source_snapshots import (
    DEFAULT_SOURCE_SNAPSHOT_DIR,
    DailySourceSnapshot,
    load_daily_source_snapshot,
    write_daily_source_snapshot,
)
from frontier_compass.ranking.relevance import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    RelevanceRanker,
    explanation_breakdown_rows,
    explanation_detail_lines,
    interest_relevance_line,
    priority_label_for_score,
    recommendation_explanation_for_ranked_paper,
    score_explanation_line,
    why_this_paper_line,
    zotero_effect_badge_text,
)
from frontier_compass.reporting.daily_brief import theme_label_for_ranked_paper
from frontier_compass.reporting.html_report import HtmlReportBuilder, daily_digest_title
from frontier_compass.storage.schema import (
    DailyDigest,
    FETCH_SCOPE_DAY_FULL as SCHEMA_FETCH_SCOPE_DAY_FULL,
    FETCH_SCOPE_OPTIONS as SCHEMA_FETCH_SCOPE_OPTIONS,
    FETCH_SCOPE_RANGE_FULL as SCHEMA_FETCH_SCOPE_RANGE_FULL,
    FETCH_SCOPE_SHORTLIST as SCHEMA_FETCH_SCOPE_SHORTLIST,
    PaperRecord,
    PROFILE_SOURCE_BASELINE as SCHEMA_PROFILE_SOURCE_BASELINE,
    PROFILE_SOURCE_LIVE_ZOTERO_DB as SCHEMA_PROFILE_SOURCE_LIVE_ZOTERO_DB,
    PROFILE_SOURCE_ZOTERO_EXPORT as SCHEMA_PROFILE_SOURCE_ZOTERO_EXPORT,
    ProfileBasis,
    RankedPaper,
    RequestWindow,
    RequestWindowFailure,
    RunHistoryEntry,
    RunTimings,
    SourceRunStats,
    UserInterestProfile,
    normalize_profile_source,
    normalize_fetch_scope as normalize_schema_fetch_scope,
    resolve_requested_profile_source,
)
from frontier_compass.ui.history import list_recent_daily_runs, report_path_for_cache_artifact
from frontier_compass.zotero.export_loader import load_csl_json_export
from frontier_compass.zotero.local_library import (
    DEFAULT_ZOTERO_EXPORT_PATH,
    DEFAULT_ZOTERO_STATUS_PATH,
    ZoteroLibraryState,
    discover_local_zotero_db_details,
    ensure_local_zotero_export,
    filter_items_by_collections,
    read_local_zotero_state,
)
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder
from frontier_compass.zotero.sqlite_loader import load_sqlite_library


DEFAULT_DAILY_CACHE_DIR = Path("data/cache")
DEFAULT_DAILY_REPORT_DIR = Path("reports/daily")
DEFAULT_ARXIV_CATEGORY = "q-bio"
DEFAULT_DAILY_LIMIT = 120
DEFAULT_DISCOVERY_QUERY_FETCH_LIMIT = 120
BIOMEDICAL_LATEST_MODE = "biomedical-latest"
BIOMEDICAL_DISCOVERY_MODE = "biomedical-discovery"
BIOMEDICAL_DAILY_MODE = "biomedical-daily"
BIOMEDICAL_MULTISOURCE_MODE = "biomedical-multisource"
BIOMEDICAL_ARXIV_CATEGORIES = BIOMEDICAL_DAILY_CATEGORIES
DEFAULT_REVIEWER_SOURCE = DEFAULT_PUBLIC_SOURCE_BUNDLE
FIXED_DAILY_MODES = (
    BIOMEDICAL_LATEST_MODE,
    BIOMEDICAL_DISCOVERY_MODE,
    BIOMEDICAL_DAILY_MODE,
    BIOMEDICAL_MULTISOURCE_MODE,
)
PUBLIC_SOURCE_BUNDLE_IDS = (
    SOURCE_BUNDLE_BIOMEDICAL,
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
)
DAILY_SOURCE_OPTIONS = PUBLIC_SOURCE_BUNDLE_IDS
DISPLAY_SOURCE_FRESH = "freshly fetched"
DISPLAY_SOURCE_CACHE = "loaded from cache"
DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE = "reused same-date cache after fetch failure"
DISPLAY_SOURCE_REUSED_STALE_CACHE = "older compatible cache reused after fetch failure"
DISPLAY_SOURCE_RANGE_AGGREGATED = "aggregated from day artifacts"
PROFILE_SOURCE_BASELINE = SCHEMA_PROFILE_SOURCE_BASELINE
PROFILE_SOURCE_ZOTERO_EXPORT = SCHEMA_PROFILE_SOURCE_ZOTERO_EXPORT
PROFILE_SOURCE_LIVE_ZOTERO_DB = SCHEMA_PROFILE_SOURCE_LIVE_ZOTERO_DB
FETCH_SCOPE_SHORTLIST = SCHEMA_FETCH_SCOPE_SHORTLIST
FETCH_SCOPE_DAY_FULL = SCHEMA_FETCH_SCOPE_DAY_FULL
FETCH_SCOPE_RANGE_FULL = SCHEMA_FETCH_SCOPE_RANGE_FULL
FETCH_SCOPE_OPTIONS = SCHEMA_FETCH_SCOPE_OPTIONS
MULTISOURCE_EXPECTED_SOURCES = ("arxiv", "biorxiv", "medrxiv")
CACHE_STATUS_FRESH = "fresh"
CACHE_STATUS_SAME_DAY = "same-day-cache"
CACHE_STATUS_STALE = "stale-compatible-cache"
SOURCE_OUTCOME_LIVE_SUCCESS = "live-success"
SOURCE_OUTCOME_LIVE_ZERO = "live-zero"
SOURCE_OUTCOME_LIVE_FAILED = "live-failed"
SOURCE_OUTCOME_SAME_DAY_CACHE = "same-day-cache"
SOURCE_OUTCOME_STALE_CACHE = "stale-cache"
SOURCE_OUTCOME_UNKNOWN_LEGACY = "unknown-legacy"


@dataclass(slots=True, frozen=True)
class BiomedicalDiscoveryPool:
    category_papers: dict[str, list[PaperRecord]]
    query_papers: dict[str, list[PaperRecord]]
    query_definitions: tuple[ArxivQueryDefinition, ...]
    search_profile_label: str
    network_seconds: float | None = None
    parse_seconds: float | None = None

    @property
    def search_queries(self) -> tuple[str, ...]:
        return tuple(definition.query for definition in self.query_definitions)


@dataclass(slots=True)
class WorkflowResult:
    profile: UserInterestProfile
    ranked: list[RankedPaper]
    selected: list[RankedPaper]
    html: str


@dataclass(slots=True)
class DailyPreparationResult:
    digest: DailyDigest
    cache_path: Path
    report_path: Path


@dataclass(slots=True)
class CachedDailyDigest:
    digest: DailyDigest
    cache_path: Path


@dataclass(slots=True, frozen=True)
class ResolvedProfileSelection:
    profile_source: str
    zotero_export_path: Path | None = None
    zotero_db_path: Path | None = None


@dataclass(slots=True, frozen=True)
class DailyBootstrapResult:
    digest: DailyDigest
    cache_path: Path
    report_path: Path
    display_source: str
    fetch_error: str = ""


@dataclass(slots=True, frozen=True)
class RangeDigestCollection:
    requested_dates: tuple[date, ...]
    completed_dates: tuple[date, ...]
    child_digests: tuple[DailyDigest, ...]
    failures: tuple[RequestWindowFailure, ...]
    failure_reasons: tuple[str, ...]
    failed_date: date | None = None
    failed_source: str = ""
    saw_partial_child: bool = False


@dataclass(slots=True, frozen=True)
class RunDailyWorkflowResult:
    digest: DailyDigest
    cache_path: Path
    report_path: Path
    display_source: str
    fetch_error: str = ""
    fetch_status_label: str = ""
    artifact_source_label: str = ""
    delivery_label: str = ""
    email_subject: str = ""
    email_to: str = ""
    email_from: str = ""
    eml_path: Path | None = None


@dataclass(slots=True, frozen=True)
class DailyRunSummary:
    requested_date: date
    effective_date: date
    request_window: RequestWindow
    source_run_stats: tuple[SourceRunStats, ...]
    run_timings: RunTimings
    used_latest_available_fallback: bool
    strict_same_day_counts_known: bool
    strict_same_day_fetched: int | None
    strict_same_day_ranked: int | None
    stale_cache_fallback_used: bool
    stale_cache_source_requested_date: date | None
    stale_cache_source_effective_date: date | None
    displayed_fetched: int
    displayed_ranked: int
    category: str
    mode_label: str
    mode_kind: str
    requested_report_mode: str
    report_mode: str
    cost_mode: str
    enhanced_track: str
    enhanced_item_count: int
    runtime_note: str
    llm_requested: bool
    llm_applied: bool
    llm_provider: str | None
    llm_fallback_reason: str | None
    llm_seconds: float | None
    report_status: str
    report_error: str
    fetch_scope: str
    profile_source: str
    mode_notes: str
    search_profile_label: str
    search_queries: tuple[str, ...]
    ranked_count: int
    frontier_report_present: bool
    report_artifact_aligned: bool
    searched_categories: tuple[str, ...]
    per_category_counts: dict[str, int]
    source_counts: dict[str, int]
    total_fetched: int
    total_displayed: int
    cache_path: str
    report_path: str
    display_source: str
    feed_url: str
    feed_urls: dict[str, str]
    source_endpoints: dict[str, str]

    @property
    def target_date(self) -> date:
        return self.requested_date

    @property
    def zero_token(self) -> bool:
        return self.cost_mode == "zero-token"

    @property
    def model_assisted(self) -> bool:
        return not self.zero_token


@dataclass(slots=True, frozen=True)
class RankedPaperCard:
    title: str
    source_label: str
    theme_label: str
    authors_text: str
    published_text: str
    categories: tuple[str, ...]
    score: float
    status_label: str
    is_recommended: bool
    why_label: str
    why_it_surfaced: str
    score_explanation: str
    relevance_explanation: str
    zotero_effect_label: str
    score_breakdown: tuple[tuple[str, float], ...]
    score_detail_lines: tuple[str, ...]
    recommendation_summary: str
    url: str


class FrontierCompassApp:
    def __init__(
        self,
        *,
        profile_builder: ZoteroProfileBuilder | None = None,
        ranker: RelevanceRanker | None = None,
        selector: ExplorationSelector | None = None,
        report_builder: HtmlReportBuilder | None = None,
        arxiv_client: ArxivClient | None = None,
        biorxiv_client: BioRxivClient | None = None,
        medrxiv_client: MedRxivClient | None = None,
        source_bundle_config_path: str | Path = DEFAULT_SOURCE_BUNDLES_PATH,
        source_snapshot_root: str | Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
        zotero_export_path: str | Path = DEFAULT_ZOTERO_EXPORT_PATH,
        zotero_status_path: str | Path = DEFAULT_ZOTERO_STATUS_PATH,
    ) -> None:
        self.profile_builder = profile_builder or ZoteroProfileBuilder()
        self.ranker = ranker or RelevanceRanker()
        self.selector = selector or ExplorationSelector()
        self.daily_exploration_policy = DEFAULT_DAILY_EXPLORATION_POLICY
        self.report_builder = report_builder or HtmlReportBuilder()
        self.arxiv_client = arxiv_client or ArxivClient()
        self.biorxiv_client = biorxiv_client or BioRxivClient()
        self.medrxiv_client = medrxiv_client or MedRxivClient()
        self.source_bundle_config_path = Path(source_bundle_config_path)
        self.source_snapshot_root = Path(source_snapshot_root)
        self.zotero_export_path = Path(zotero_export_path)
        self.zotero_status_path = Path(zotero_status_path)

    def available_source_bundles(self) -> tuple[SourceBundleDefinition, ...]:
        return list_public_source_bundles(config_path=self.source_bundle_config_path)

    def custom_source_bundles(self) -> tuple[SourceBundleDefinition, ...]:
        return list_custom_source_bundles(config_path=self.source_bundle_config_path)

    def save_custom_source_bundle(
        self,
        *,
        name: str,
        enabled_sources: Sequence[str],
        include_terms: Sequence[str] = (),
        exclude_terms: Sequence[str] = (),
        description: str = '',
        bundle_id: str | None = None,
    ) -> SourceBundleDefinition:
        bundle = build_custom_source_bundle(
            name=name,
            enabled_sources=enabled_sources,
            include_terms=include_terms,
            exclude_terms=exclude_terms,
            description=description,
            bundle_id=bundle_id,
        )
        upsert_custom_source_bundle(bundle, config_path=self.source_bundle_config_path)
        return bundle

    def remove_custom_source_bundle(self, bundle_id: str) -> None:
        delete_custom_source_bundle(bundle_id, config_path=self.source_bundle_config_path)

    def resolve_source_bundle(self, source: str | None) -> SourceBundleDefinition | None:
        return resolve_source_bundle(source, config_path=self.source_bundle_config_path)

    def zotero_library_state(
        self,
        *,
        refresh: bool = False,
        export_path: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> ZoteroLibraryState:
        if refresh:
            return ensure_local_zotero_export(
                export_path=export_path or self.zotero_export_path,
                status_path=self.zotero_status_path,
                db_path=db_path,
                refresh=True,
            )
        return read_local_zotero_state(
            export_path=export_path or self.zotero_export_path,
            status_path=self.zotero_status_path,
        )

    def _load_or_materialize_source_snapshot(
        self,
        *,
        source: str,
        target_date: date,
        refresh: bool = False,
    ) -> tuple[DailySourceSnapshot, bool]:
        if not refresh:
            cached_snapshot = load_daily_source_snapshot(
                target_date,
                source,
                snapshot_root=self.source_snapshot_root,
            )
            if cached_snapshot is not None:
                return cached_snapshot, True
        snapshot = self._fetch_source_snapshot(source=source, target_date=target_date)
        write_daily_source_snapshot(snapshot, snapshot_root=self.source_snapshot_root)
        return snapshot, False

    def _fetch_source_snapshot(
        self,
        *,
        source: str,
        target_date: date,
    ) -> DailySourceSnapshot:
        normalized_source = str(source or "").strip().lower()
        if normalized_source == "arxiv":
            return self._fetch_arxiv_source_snapshot(target_date=target_date)
        if normalized_source == "biorxiv":
            papers, network_seconds, parse_seconds = self.biorxiv_client.fetch_today_with_timings(
                today=target_date,
                subject="all",
                max_results=None,
            )
            fetch_details = _last_feed_fetch_details(self.biorxiv_client)
            return DailySourceSnapshot(
                source="biorxiv",
                requested_date=target_date,
                generated_at=datetime.now(timezone.utc),
                endpoint=(fetch_details.endpoint if fetch_details is not None else self.biorxiv_client.build_feed_url("all")),
                papers=tuple(papers),
                fetched_count=len(papers),
                status="ready" if papers else "empty",
                note=_compose_source_note(
                    "Daily bioRxiv all-subject local snapshot.",
                    fetch_details,
                ),
                network_seconds=network_seconds,
                parse_seconds=parse_seconds,
                metadata={
                    "subject": "all",
                    "contract_mode": fetch_details.contract_mode if fetch_details is not None else "rss",
                },
            )
        if normalized_source == "medrxiv":
            papers, network_seconds, parse_seconds = self.medrxiv_client.fetch_today_with_timings(
                today=target_date,
                subject="all",
                max_results=None,
            )
            fetch_details = _last_feed_fetch_details(self.medrxiv_client)
            return DailySourceSnapshot(
                source="medrxiv",
                requested_date=target_date,
                generated_at=datetime.now(timezone.utc),
                endpoint=(fetch_details.endpoint if fetch_details is not None else self.medrxiv_client.build_feed_url("all")),
                papers=tuple(papers),
                fetched_count=len(papers),
                status="ready" if papers else "empty",
                note=_compose_source_note(
                    "Daily medRxiv all-subject local snapshot.",
                    fetch_details,
                ),
                network_seconds=network_seconds,
                parse_seconds=parse_seconds,
                metadata={
                    "subject": "all",
                    "contract_mode": fetch_details.contract_mode if fetch_details is not None else "rss",
                },
            )
        raise ValueError(f"Unsupported snapshot source: {source}")

    def _fetch_arxiv_source_snapshot(self, *, target_date: date) -> DailySourceSnapshot:
        category_papers, network_seconds, parse_seconds = self.arxiv_client.fetch_today_by_category_with_timings(
            BIOMEDICAL_DISCOVERY_CATEGORIES,
            today=target_date,
            max_results=None,
        )
        papers = merge_category_papers(category_papers)
        fetched_count = sum(len(items) for items in category_papers.values())
        feed_urls = {
            category: self.arxiv_client.build_feed_url(category)
            for category in BIOMEDICAL_DISCOVERY_CATEGORIES
        }
        query_definitions: tuple[ArxivQueryDefinition, ...] = ()
        contract_mode = "rss-category"
        note = "Daily arXiv bundle snapshot across biomedical and AI-adjacent discovery categories."

        if not papers:
            query_definitions = build_biomedical_discovery_queries(categories=BIOMEDICAL_DISCOVERY_CATEGORIES)
            try:
                query_papers, query_network_seconds, query_parse_seconds = self.arxiv_client.fetch_today_by_queries_with_timings(
                    query_definitions,
                    today=target_date,
                    max_results=240,
                )
            except Exception as exc:
                raise RuntimeError(
                    "arXiv daily category feeds returned no same-day entries and the biomedical API fallback failed: "
                    f"{exc}"
                ) from exc
            papers = merge_paper_batches(query_papers)
            fetched_count = sum(len(items) for items in query_papers.values())
            network_seconds = _sum_known_seconds(network_seconds, query_network_seconds)
            parse_seconds = _sum_known_seconds(parse_seconds, query_parse_seconds)
            contract_mode = "rss-category+api-query-fallback"
            if papers:
                note = (
                    "Daily arXiv bundle snapshot across biomedical and AI-adjacent discovery categories. "
                    "The live category Atom feeds returned no same-day entries, so the snapshot reused the "
                    "biomedical discovery API query fallback."
                )
            else:
                note = (
                    "Daily arXiv bundle snapshot across biomedical and AI-adjacent discovery categories. "
                    "The live category Atom feeds returned no same-day entries, and the biomedical discovery "
                    "API fallback also returned no same-day entries."
                )

        metadata: dict[str, Any] = {
            "categories": list(BIOMEDICAL_DISCOVERY_CATEGORIES),
            "feed_urls": feed_urls,
            "contract_mode": contract_mode,
        }
        if query_definitions:
            metadata["search_queries"] = [definition.query for definition in query_definitions]
            metadata["query_profiles"] = list(_query_profile_metadata(query_definitions))

        return DailySourceSnapshot(
            source="arxiv",
            requested_date=target_date,
            generated_at=datetime.now(timezone.utc),
            endpoint=self.arxiv_client.api_url,
            papers=tuple(papers),
            fetched_count=fetched_count,
            status="ready" if papers else "empty",
            note=note,
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            metadata=metadata,
        )

    def run(
        self,
        zotero_items: Sequence[Mapping[str, Any]],
        candidate_papers: Sequence[PaperRecord],
        *,
        limit: int = 10,
    ) -> WorkflowResult:
        profile = self.profile_builder.build(zotero_items)
        ranked = self.ranker.rank(candidate_papers, profile)
        selected = self.selector.select(ranked, limit=limit)
        html = self.report_builder.render(profile, selected)
        return WorkflowResult(profile=profile, ranked=ranked, selected=selected, html=html)

    def build_demo_report(self, *, limit: int = 5) -> WorkflowResult:
        return self.run(self.demo_zotero_items(), self.demo_papers(), limit=limit)

    def write_demo_report(self, output_path: str | Path, *, limit: int = 5) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self.build_demo_report(limit=limit)
        path.write_text(result.html, encoding="utf-8")
        return path

    def build_daily_digest(
        self,
        *,
        category: str = DEFAULT_ARXIV_CATEGORY,
        mode: str | None = None,
        report_mode: str = DEFAULT_REPORT_MODE,
        llm_provider: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        today: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_results: int = DEFAULT_DAILY_LIMIT,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        refresh_sources: bool = False,
        apply_enhanced_report: bool = True,
    ) -> DailyDigest:
        target_date = today or date.today()
        llm_settings = resolve_frontier_report_llm_settings(
            provider=llm_provider,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )
        target_date, start_date, end_date, normalized_fetch_scope = normalize_request_window_inputs(
            requested_date=target_date,
            start_date=start_date,
            end_date=end_date,
            fetch_scope=fetch_scope,
        )
        if normalized_fetch_scope == FETCH_SCOPE_RANGE_FULL or start_date is not None or end_date is not None:
            resolved_start = start_date or target_date
            resolved_end = end_date or resolved_start
            digest = self._build_range_digest(
                category=category,
                mode=mode,
                report_mode=report_mode,
                start_date=resolved_start,
                end_date=resolved_end,
                max_results=max_results,
                feed_url=feed_url,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
                refresh_sources=refresh_sources,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )
        selected_source = mode or category
        resolved_bundle = self.resolve_source_bundle(selected_source)
        if resolved_bundle is not None:
            digest = self._build_source_bundle_digest(
                bundle=resolved_bundle,
                target_date=target_date,
                max_results=max_results,
                report_mode=report_mode,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
                refresh_sources=refresh_sources,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )
        resolved_mode = normalize_fixed_daily_mode(mode)
        if resolved_mode == BIOMEDICAL_LATEST_MODE:
            digest = self._build_biomedical_latest_digest(
                target_date=target_date,
                max_results=max_results,
                report_mode=report_mode,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )
        if resolved_mode == BIOMEDICAL_DISCOVERY_MODE:
            digest = self._build_biomedical_discovery_digest(
                target_date=target_date,
                max_results=max_results,
                report_mode=report_mode,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )
        if resolved_mode == BIOMEDICAL_DAILY_MODE:
            digest = self._build_biomedical_daily_digest(
                target_date=target_date,
                max_results=max_results,
                report_mode=report_mode,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )
        if resolved_mode == BIOMEDICAL_MULTISOURCE_MODE:
            digest = self._build_biomedical_multisource_digest(
                target_date=target_date,
                max_results=max_results,
                report_mode=report_mode,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
            )
            return (
                self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
                if apply_enhanced_report
                else digest
            )

        digest = self._build_single_category_digest(
            category=category,
            target_date=target_date,
            max_results=max_results,
            report_mode=report_mode,
            fetch_scope=normalized_fetch_scope,
            profile_source=profile_source,
            feed_url=feed_url,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        return (
            self._digest_for_report_mode(digest, report_mode=report_mode, llm_settings=llm_settings)
            if apply_enhanced_report
            else digest
        )

    def write_daily_outputs(
        self,
        *,
        category: str = DEFAULT_ARXIV_CATEGORY,
        mode: str | None = None,
        report_mode: str = DEFAULT_REPORT_MODE,
        llm_provider: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        today: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_results: int = DEFAULT_DAILY_LIMIT,
        cache_path: str | Path | None = None,
        output_path: str | Path | None = None,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        cache_lookup_seconds: float | None = None,
        refresh_sources: bool = False,
    ) -> DailyPreparationResult:
        digest = self.build_daily_digest(
            category=category,
            mode=mode,
            report_mode=report_mode,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            today=today,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            feed_url=feed_url,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
            refresh_sources=refresh_sources,
        )
        resolved_cache_path = (
            Path(cache_path)
            if cache_path is not None
            else self.default_daily_cache_path(
                digest.category,
                digest.target_date,
                end_date=digest.request_window.end_date,
                fetch_scope=digest.fetch_scope,
                profile_source=digest.profile.profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
            )
        )
        resolved_report_path = (
            Path(output_path)
            if output_path is not None
            else (
                self.report_path_for_cache_path(resolved_cache_path)
                if zotero_export_path is not None or zotero_db_path is not None or digest.request_window.kind == "range"
                else self.default_daily_report_path(
                    digest.category,
                    digest.target_date,
                    end_date=digest.request_window.end_date,
                    fetch_scope=digest.fetch_scope,
                    profile_source=digest.profile.profile_source,
                    zotero_export_path=zotero_export_path,
                    zotero_db_path=zotero_db_path,
                )
            )
        )
        digest = self._apply_cache_lookup_timing_to_digest(
            digest,
            cache_lookup_seconds=cache_lookup_seconds,
        )
        resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_daily_report(
            digest=digest,
            output_path=resolved_report_path,
            acquisition_status_label=display_source_label(DISPLAY_SOURCE_FRESH),
        )
        self._write_daily_digest_cache(digest=digest, cache_path=resolved_cache_path)
        return DailyPreparationResult(digest=digest, cache_path=resolved_cache_path, report_path=resolved_report_path)

    def load_daily_digest(self, cache_path: str | Path) -> DailyDigest:
        path = Path(cache_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        had_frontier_report_key = "frontier_report" in payload
        digest = DailyDigest.from_mapping(payload)
        changed = self._backfill_digest_explanations(digest)
        if self._backfill_digest_runtime_contract(digest):
            changed = True
        if self._backfill_digest_source_run_contract(digest):
            changed = True
        if changed and (had_frontier_report_key or digest.frontier_report is not None):
            path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")
        return digest

    def available_daily_caches(self, cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR) -> list[CachedDailyDigest]:
        root = Path(cache_dir)
        if not root.exists():
            return []

        entries: list[CachedDailyDigest] = []
        for path in sorted(root.rglob("frontier_compass_*.json")):
            try:
                digest = self.load_daily_digest(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            entries.append(CachedDailyDigest(digest=digest, cache_path=path))

        entries.sort(
            key=lambda item: (
                item.digest.requested_target_date,
                item.digest.generated_at,
                item.cache_path.name,
            ),
            reverse=True,
        )
        return entries

    def recent_daily_runs(
        self,
        *,
        limit: int | None = 10,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        report_dir: str | Path = DEFAULT_DAILY_REPORT_DIR,
    ) -> list[RunHistoryEntry]:
        return list_recent_daily_runs(
            cache_dir=cache_dir,
            report_dir=report_dir,
            limit=limit,
        )

    def resolve_latest_daily_cache_path(
        self,
        *,
        category: str | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        non_empty_only: bool = False,
    ) -> Path | None:
        cached = self.load_latest_daily_digest(category=category, cache_dir=cache_dir, non_empty_only=non_empty_only)
        if cached is None:
            return None
        return cached.cache_path

    def load_latest_daily_digest(
        self,
        *,
        category: str | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        non_empty_only: bool = False,
    ) -> CachedDailyDigest | None:
        normalized_category = _normalize_category(category) if category else None
        for cached in self.available_daily_caches(cache_dir):
            if non_empty_only and not cached.digest.ranked:
                continue
            if normalized_category is None or _normalize_category(cached.digest.category) == normalized_category:
                return cached
        return None

    def load_requested_daily_digest(
        self,
        *,
        category: str,
        requested_date: date,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        non_empty_only: bool = False,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> CachedDailyDigest | None:
        for cached in self.available_daily_caches(cache_dir):
            if self._is_compatible_cached_daily_digest(
                cached,
                category=category,
                requested_date=requested_date,
                fetch_scope=fetch_scope,
                non_empty_only=non_empty_only,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            ):
                return cached
        return None

    def bootstrap_daily_digest(
        self,
        *,
        selected_source: str,
        requested_date: date,
        max_results: int,
        start_date: date | None = None,
        end_date: date | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        force_fetch: bool = False,
        allow_stale_cache: bool = True,
        report_mode: str = DEFAULT_REPORT_MODE,
        llm_provider: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    ) -> DailyBootstrapResult:
        return self.load_or_materialize_current_digest(
            selected_source=selected_source,
            requested_date=requested_date,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            cache_dir=cache_dir,
            force_fetch=force_fetch,
            allow_stale_cache=allow_stale_cache,
            report_mode=report_mode,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )

    def materialize_daily_digest(
        self,
        *,
        selected_source: str,
        requested_date: date,
        max_results: int,
        start_date: date | None = None,
        end_date: date | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        force_fetch: bool = False,
        cache_path: str | Path | None = None,
        output_path: str | Path | None = None,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        allow_stale_cache: bool = False,
        report_mode: str = DEFAULT_REPORT_MODE,
        llm_provider: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    ) -> DailyBootstrapResult:
        return self.load_or_materialize_current_digest(
            selected_source=selected_source,
            requested_date=requested_date,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            cache_dir=cache_dir,
            force_fetch=force_fetch,
            cache_path=cache_path,
            output_path=output_path,
            feed_url=feed_url,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            allow_stale_cache=allow_stale_cache,
            report_mode=report_mode,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            fetch_scope=fetch_scope,
        )

    def load_or_materialize_current_digest(
        self,
        *,
        selected_source: str,
        requested_date: date,
        max_results: int,
        start_date: date | None = None,
        end_date: date | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        force_fetch: bool = False,
        cache_path: str | Path | None = None,
        output_path: str | Path | None = None,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        allow_stale_cache: bool = True,
        report_mode: str = DEFAULT_REPORT_MODE,
        llm_provider: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    ) -> DailyBootstrapResult:
        llm_settings = resolve_frontier_report_llm_settings(
            provider=llm_provider,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )
        requested_date, start_date, end_date, normalized_fetch_scope = normalize_request_window_inputs(
            requested_date=requested_date,
            start_date=start_date,
            end_date=end_date,
            fetch_scope=fetch_scope,
        )
        resolved_profile_source = _resolve_effective_profile_source(
            profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        if resolved_profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
            zotero_db_path = _resolve_live_zotero_db_path(zotero_db_path)
        resolved_cache_path = (
            Path(cache_path)
            if cache_path is not None
            else Path(cache_dir)
            / self.default_daily_cache_path(
                selected_source,
                requested_date,
                end_date=end_date,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            ).name
        )
        resolved_output_path = (
            Path(output_path)
            if output_path is not None
            else self.report_path_for_cache_path(resolved_cache_path)
        )
        cache_lookup_seconds = 0.0

        def _load_same_day_cached() -> CachedDailyDigest | None:
            if normalized_fetch_scope == FETCH_SCOPE_RANGE_FULL:
                return self._load_matching_cached_daily_digest(
                    resolved_cache_path,
                    category=selected_source,
                    requested_date=requested_date,
                    start_date=start_date or requested_date,
                    end_date=end_date or requested_date,
                    fetch_scope=normalized_fetch_scope,
                    non_empty_only=False,
                    profile_source=profile_source,
                    zotero_export_path=zotero_export_path,
                    zotero_db_path=zotero_db_path,
                    zotero_collections=zotero_collections,
                )
            return self._load_requested_daily_digest_for_materialization(
                category=selected_source,
                requested_date=requested_date,
                cache_dir=cache_dir,
                cache_path=resolved_cache_path,
                non_empty_only=False,
                fetch_scope=normalized_fetch_scope,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            )

        same_day_cached, same_day_cache_seconds = measure_operation(_load_same_day_cached)
        cache_lookup_seconds += same_day_cache_seconds
        if not force_fetch and same_day_cached is not None:
            adjusted_digest = self._digest_for_report_mode(
                same_day_cached.digest,
                report_mode=report_mode,
                llm_settings=llm_settings,
            )
            adjusted_digest = self._apply_cache_lookup_timing_to_digest(
                adjusted_digest,
                cache_lookup_seconds=cache_lookup_seconds,
                preserve_stage_timings=False,
            )
            adjusted_digest = self._apply_cache_story_to_digest(
                adjusted_digest,
                cache_status=CACHE_STATUS_SAME_DAY,
                note="Same-day cache reused for the current run.",
            )
            report_path = self._write_daily_report(
                digest=adjusted_digest,
                output_path=resolved_output_path,
                acquisition_status_label=display_source_label(DISPLAY_SOURCE_CACHE),
            )
            self._write_daily_digest_cache(digest=adjusted_digest, cache_path=resolved_cache_path)
            return DailyBootstrapResult(
                digest=adjusted_digest,
                cache_path=resolved_cache_path,
                report_path=report_path,
                display_source=DISPLAY_SOURCE_CACHE,
            )

        write_kwargs = build_daily_source_kwargs(
            selected_source,
            requested_date=requested_date,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            fetch_scope=normalized_fetch_scope,
        )
        write_kwargs["cache_path"] = resolved_cache_path
        write_kwargs["output_path"] = resolved_output_path
        if feed_url is not None:
            write_kwargs["feed_url"] = feed_url
        if profile_source is not None:
            write_kwargs["profile_source"] = profile_source
        if zotero_export_path is not None:
            write_kwargs["zotero_export_path"] = zotero_export_path
        if zotero_db_path is not None:
            write_kwargs["zotero_db_path"] = zotero_db_path
        if zotero_collections:
            write_kwargs["zotero_collections"] = tuple(zotero_collections)
        if report_mode != DEFAULT_REPORT_MODE:
            write_kwargs["report_mode"] = report_mode
        if llm_provider is not None:
            write_kwargs["llm_provider"] = llm_provider
        if llm_base_url is not None:
            write_kwargs["llm_base_url"] = llm_base_url
        if llm_api_key is not None:
            write_kwargs["llm_api_key"] = llm_api_key
        if llm_model is not None:
            write_kwargs["llm_model"] = llm_model
        write_kwargs["cache_lookup_seconds"] = cache_lookup_seconds
        if force_fetch:
            write_kwargs["refresh_sources"] = True

        try:
            if normalized_fetch_scope == FETCH_SCOPE_RANGE_FULL:
                result = self._materialize_range_outputs(
                    selected_source=selected_source,
                    requested_date=requested_date,
                    start_date=start_date or requested_date,
                    end_date=end_date or requested_date,
                    max_results=max_results,
                    cache_dir=cache_dir,
                    cache_path=resolved_cache_path,
                    output_path=resolved_output_path,
                    force_fetch=force_fetch,
                    allow_stale_cache=allow_stale_cache,
                    report_mode=report_mode,
                    llm_provider=llm_provider,
                    llm_base_url=llm_base_url,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                    feed_url=feed_url,
                    profile_source=profile_source,
                    zotero_export_path=zotero_export_path,
                    zotero_db_path=zotero_db_path,
                    zotero_collections=zotero_collections,
                    cache_lookup_seconds=cache_lookup_seconds,
                )
            else:
                result = self.write_daily_outputs(**write_kwargs)
        except Exception as exc:
            if same_day_cached is None and normalized_fetch_scope != FETCH_SCOPE_RANGE_FULL:
                same_day_cached, retry_lookup_seconds = measure_operation(
                    lambda: self._load_requested_daily_digest_for_materialization(
                        category=selected_source,
                        requested_date=requested_date,
                        cache_dir=cache_dir,
                        cache_path=resolved_cache_path,
                        non_empty_only=False,
                        fetch_scope=normalized_fetch_scope,
                        profile_source=profile_source,
                        zotero_export_path=zotero_export_path,
                        zotero_db_path=zotero_db_path,
                        zotero_collections=zotero_collections,
                    )
                )
                cache_lookup_seconds += retry_lookup_seconds
            if same_day_cached is not None:
                adjusted_digest = self._digest_for_report_mode(
                    same_day_cached.digest,
                    report_mode=report_mode,
                    llm_settings=llm_settings,
                )
                adjusted_digest = self._apply_cache_lookup_timing_to_digest(
                    adjusted_digest,
                    cache_lookup_seconds=cache_lookup_seconds,
                )
                adjusted_digest = self._apply_cache_story_to_digest(
                    adjusted_digest,
                    cache_status=CACHE_STATUS_SAME_DAY,
                    note="Same-day cache reused after a fresh fetch failure.",
                    fetch_error=str(exc),
                )
                report_path = self._write_daily_report(
                    digest=adjusted_digest,
                    output_path=resolved_output_path,
                    acquisition_status_label=display_source_label(DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE),
                    fetch_error=str(exc),
                )
                self._write_daily_digest_cache(digest=adjusted_digest, cache_path=resolved_cache_path)
                return DailyBootstrapResult(
                    digest=adjusted_digest,
                    cache_path=resolved_cache_path,
                    report_path=report_path,
                    display_source=DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE,
                    fetch_error=str(exc),
                )

            stale_cached = None
            if allow_stale_cache and normalized_fetch_scope != FETCH_SCOPE_RANGE_FULL:
                stale_cached, stale_lookup_seconds = measure_operation(
                    lambda: self._load_stale_compatible_daily_digest_for_materialization(
                        category=selected_source,
                        requested_date=requested_date,
                        cache_dir=cache_dir,
                        cache_path=resolved_cache_path,
                        non_empty_only=True,
                        profile_source=profile_source,
                        zotero_export_path=zotero_export_path,
                        zotero_db_path=zotero_db_path,
                        zotero_collections=zotero_collections,
                    )
                )
                cache_lookup_seconds += stale_lookup_seconds
            if stale_cached is None:
                cache_failure_label = "no same-date cache is available"
                if allow_stale_cache:
                    cache_failure_label = "no same-date cache or compatible older cache is available"
                raise RuntimeError(
                    "Fresh source fetch failed for "
                    f"{selected_source} on {requested_date.isoformat()} and {cache_failure_label}: {exc}"
                ) from exc
            stale_digest = self._build_stale_cache_fallback_digest(
                stale_cached.digest,
                requested_date=requested_date,
            )
            stale_digest = self._digest_for_report_mode(
                stale_digest,
                report_mode=report_mode,
                llm_settings=llm_settings,
            )
            stale_digest = self._apply_cache_lookup_timing_to_digest(
                stale_digest,
                cache_lookup_seconds=cache_lookup_seconds,
            )
            stale_digest = self._apply_cache_story_to_digest(
                stale_digest,
                cache_status=CACHE_STATUS_STALE,
                note="Older compatible cache reused after a fresh fetch failure.",
                fetch_error=str(exc),
            )
            report_path = self._write_daily_report(
                digest=stale_digest,
                output_path=resolved_output_path,
                acquisition_status_label=display_source_label(DISPLAY_SOURCE_REUSED_STALE_CACHE),
                fetch_error=str(exc),
            )
            self._write_daily_digest_cache(digest=stale_digest, cache_path=resolved_cache_path)
            return DailyBootstrapResult(
                digest=stale_digest,
                cache_path=resolved_cache_path,
                report_path=report_path,
                display_source=DISPLAY_SOURCE_REUSED_STALE_CACHE,
                fetch_error=str(exc),
            )

        return DailyBootstrapResult(
            digest=result.digest,
            cache_path=result.cache_path,
            report_path=result.report_path,
            display_source=(
                DISPLAY_SOURCE_RANGE_AGGREGATED
                if normalized_fetch_scope == FETCH_SCOPE_RANGE_FULL
                else DISPLAY_SOURCE_FRESH
            ),
        )

    def _materialize_range_outputs(
        self,
        *,
        selected_source: str,
        requested_date: date,
        start_date: date,
        end_date: date,
        max_results: int,
        cache_dir: str | Path,
        cache_path: str | Path,
        output_path: str | Path,
        force_fetch: bool,
        allow_stale_cache: bool,
        report_mode: str,
        llm_provider: str | None,
        llm_base_url: str | None,
        llm_api_key: str | None,
        llm_model: str | None,
        feed_url: str | None,
        profile_source: str | None,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
        zotero_collections: Sequence[str],
        cache_lookup_seconds: float | None,
    ) -> DailyPreparationResult:
        collection = self._collect_range_child_digests(
            category=selected_source,
            start_date=start_date,
            end_date=end_date,
            child_loader=lambda child_date: self.load_or_materialize_current_digest(
                selected_source=selected_source,
                requested_date=child_date,
                max_results=max_results,
                cache_dir=cache_dir,
                force_fetch=force_fetch,
                feed_url=feed_url,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
                allow_stale_cache=allow_stale_cache,
                report_mode=DEFAULT_REPORT_MODE,
                llm_provider=llm_provider,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                fetch_scope=FETCH_SCOPE_DAY_FULL,
            ).digest,
        )
        digest = self._aggregate_range_child_digests(
            start_date=start_date,
            end_date=end_date,
            report_mode=report_mode,
            collection=collection,
        )
        digest = self._apply_cache_lookup_timing_to_digest(
            digest,
            cache_lookup_seconds=cache_lookup_seconds,
        )
        resolved_cache_path = Path(cache_path)
        resolved_report_path = Path(output_path)
        resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_daily_report(
            digest=digest,
            output_path=resolved_report_path,
            acquisition_status_label=display_source_label(DISPLAY_SOURCE_RANGE_AGGREGATED),
        )
        self._write_daily_digest_cache(digest=digest, cache_path=resolved_cache_path)
        return DailyPreparationResult(
            digest=digest,
            cache_path=resolved_cache_path,
            report_path=resolved_report_path,
        )

    def run_daily_workflow(
        self,
        *,
        selected_source: str,
        requested_date: date,
        max_results: int,
        refresh: bool = False,
        start_date: date | None = None,
        end_date: date | None = None,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        cache_path: str | Path | None = None,
        output_path: str | Path | None = None,
        eml_output_path: str | Path | None = None,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        allow_stale_cache: bool = True,
        report_mode: str = DEFAULT_REPORT_MODE,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        generate_dry_run_email: bool = False,
        email_to: str | Sequence[str] | None = None,
        email_from: str | None = None,
    ) -> RunDailyWorkflowResult:
        bootstrap = self.materialize_daily_digest(
            selected_source=selected_source,
            requested_date=requested_date,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            cache_dir=cache_dir,
            force_fetch=refresh,
            cache_path=cache_path,
            output_path=output_path,
            feed_url=feed_url,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            allow_stale_cache=allow_stale_cache,
            report_mode=report_mode,
            fetch_scope=fetch_scope,
        )
        fetch_status_label = display_source_label(bootstrap.display_source)
        artifact_source_label = display_artifact_source_label(bootstrap.display_source)
        delivery_label = ""
        email_subject = ""
        email_to_label = ""
        email_from_label = ""
        resolved_eml_path: Path | None = None

        if generate_dry_run_email:
            from frontier_compass.ui.email_delivery import (
                default_eml_output_path,
                prepare_daily_digest_email,
                write_eml_message,
            )

            prepared_email = prepare_daily_digest_email(
                bootstrap.digest,
                report_path=bootstrap.report_path,
                display_source=bootstrap.display_source,
                fetch_error=bootstrap.fetch_error,
                email_to=email_to,
                email_from=email_from,
            )
            resolved_eml_path = write_eml_message(
                prepared_email.message,
                eml_output_path or default_eml_output_path(bootstrap.report_path),
            )
            fetch_status_label = prepared_email.digest_fetch_status_label
            artifact_source_label = prepared_email.artifact_source_label
            delivery_label = "dry-run .eml written"
            email_subject = prepared_email.subject
            email_to_label = str(prepared_email.message["To"] or "")
            email_from_label = str(prepared_email.message["From"] or "")

        return RunDailyWorkflowResult(
            digest=bootstrap.digest,
            cache_path=bootstrap.cache_path,
            report_path=bootstrap.report_path,
            display_source=bootstrap.display_source,
            fetch_error=bootstrap.fetch_error,
            fetch_status_label=fetch_status_label,
            artifact_source_label=artifact_source_label,
            delivery_label=delivery_label,
            email_subject=email_subject,
            email_to=email_to_label,
            email_from=email_from_label,
            eml_path=resolved_eml_path,
        )

    def render_daily_report_from_cache(
        self,
        cache_path: str | Path,
        output_path: str | Path | None = None,
        *,
        acquisition_status_label: str = "",
        fetch_error: str = "",
    ) -> Path:
        digest = self.load_daily_digest(cache_path)
        resolved_output_path = (
            Path(output_path)
            if output_path is not None
            else self.report_path_for_cache_path(cache_path)
        )
        return self._write_daily_report(
            digest=digest,
            output_path=resolved_output_path,
            acquisition_status_label=acquisition_status_label,
            fetch_error=fetch_error,
        )

    def _backfill_digest_explanations(self, digest: DailyDigest) -> bool:
        changed = False

        updated_ranked: list[RankedPaper] = []
        for item in digest.ranked:
            if item.explanation is not None:
                updated_ranked.append(item)
                continue
            updated_ranked.append(
                RankedPaper(
                    paper=item.paper,
                    score=item.score,
                    reasons=item.reasons,
                    facets=dict(item.facets),
                    recommendation_summary=item.recommendation_summary,
                    explanation=recommendation_explanation_for_ranked_paper(
                        item,
                        profile=digest.profile,
                    ),
                )
            )
            changed = True
        if changed:
            digest.ranked = updated_ranked

        exploration_changed = False
        updated_exploration: list[RankedPaper] = []
        for item in digest.exploration_picks:
            if item.explanation is not None:
                updated_exploration.append(item)
                continue
            updated_exploration.append(
                RankedPaper(
                    paper=item.paper,
                    score=item.score,
                    reasons=item.reasons,
                    facets=dict(item.facets),
                    recommendation_summary=item.recommendation_summary,
                    explanation=recommendation_explanation_for_ranked_paper(
                        item,
                        profile=digest.profile,
                    ),
                )
            )
            exploration_changed = True
        if exploration_changed:
            digest.exploration_picks = updated_exploration
            changed = True

        return changed

    def _backfill_digest_runtime_contract(self, digest: DailyDigest) -> bool:
        if digest.runtime_note and (
            digest.frontier_report is None or digest.frontier_report.runtime_note
        ):
            return False

        runtime_contract = build_report_runtime_contract(digest.requested_report_mode)
        digest.requested_report_mode = str(runtime_contract["requested_report_mode"])
        digest.report_mode = str(runtime_contract["report_mode"])
        digest.cost_mode = str(runtime_contract["cost_mode"])
        digest.enhanced_track = str(runtime_contract["enhanced_track"])
        digest.enhanced_item_count = int(runtime_contract["enhanced_item_count"])
        digest.runtime_note = str(runtime_contract["runtime_note"])
        if digest.frontier_report is not None:
            digest.frontier_report = replace(digest.frontier_report, **runtime_contract)
        return True

    def _backfill_digest_source_run_contract(self, digest: DailyDigest) -> bool:
        changed = False
        expected_sources = _expected_sources_for_digest(digest)
        if not expected_sources:
            return False

        if digest.frontier_report is None and digest.report_status == "ready":
            digest.report_status = "failed"
            if not digest.report_error:
                digest.report_error = "Frontier Report is unavailable in this legacy cache."
            changed = True

        fallback_source_counts = dict(digest.source_counts)
        if not fallback_source_counts and digest.ranked:
            fallback_source_counts = _count_papers_by_source(tuple(item.paper for item in digest.ranked))

        backfilled_source_stats = _backfill_source_run_stats(
            expected_sources=expected_sources,
            source_run_stats=digest.source_run_stats,
            source_counts=fallback_source_counts,
            endpoints=digest.source_endpoints,
        )
        if backfilled_source_stats != digest.source_run_stats:
            digest.source_run_stats = backfilled_source_stats
            changed = True

        if digest.frontier_report is not None:
            backfilled_frontier_stats = _backfill_source_run_stats(
                expected_sources=expected_sources,
                source_run_stats=digest.frontier_report.source_run_stats,
                source_counts=(
                    dict(digest.frontier_report.source_counts)
                    or {row.source: row.displayed_count for row in backfilled_source_stats}
                ),
                endpoints=digest.source_endpoints,
            )
            if backfilled_frontier_stats != digest.frontier_report.source_run_stats:
                digest.frontier_report = replace(
                    digest.frontier_report,
                    source_run_stats=backfilled_frontier_stats,
                )
                changed = True
        return changed

    def _build_biomedical_daily_digest(
        self,
        *,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
    ) -> DailyDigest:
        exploration_policy = self.daily_exploration_policy
        runtime_contract = build_report_runtime_contract(report_mode)
        category_papers, network_seconds, parse_seconds = self.arxiv_client.fetch_today_by_category_with_timings(
            BIOMEDICAL_DAILY_CATEGORIES,
            today=target_date,
            max_results=None,
        )
        papers = merge_category_papers(category_papers)
        profile = self._resolve_daily_profile(
            BIOMEDICAL_DAILY_MODE,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(papers, profile, limit=None, today=target_date)
        ranked = ranked_for_fetch_scope(
            full_ranked,
            max_results=max_results,
            fetch_scope=fetch_scope,
        )
        rank_seconds = perf_counter() - rank_started
        total_fetched = sum(len(category_papers.get(name, ())) for name in BIOMEDICAL_DAILY_CATEGORIES)
        request_window = build_request_window(requested_date=target_date)
        source_endpoints = {"arxiv": self.arxiv_client.api_url}
        source_counts = {"arxiv": len(papers)}
        source_run_stats = build_source_run_stats(
            expected_sources=("arxiv",),
            fetched_counts={"arxiv": total_fetched},
            displayed_counts=source_counts,
            endpoints=source_endpoints,
            timings={
                "arxiv": build_run_timings(
                    network_seconds=network_seconds,
                    parse_seconds=parse_seconds,
                )
            },
        )
        run_timings = build_run_timings(
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            rank_seconds=rank_seconds,
        )
        frontier_report = build_daily_frontier_report(
            paper_pool=papers,
            ranked_papers=full_ranked,
            requested_date=target_date,
            effective_date=target_date,
            source="arxiv",
            mode=BIOMEDICAL_DAILY_MODE,
            mode_label="Biomedical daily",
            mode_kind="bundle",
            **runtime_contract,
            searched_categories=BIOMEDICAL_DAILY_CATEGORIES,
            total_fetched=total_fetched,
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=fetch_scope,
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        exploration_picks = self._daily_exploration_picks(ranked, profile, policy=exploration_policy)
        feed_urls = {name: self.arxiv_client.build_feed_url(name) for name in BIOMEDICAL_DAILY_CATEGORIES}
        per_category_counts = {name: len(category_papers.get(name, ())) for name in BIOMEDICAL_DAILY_CATEGORIES}
        source_metadata = {
            "arxiv": _build_source_contract_metadata(
                mode="bundle",
                native_filters=BIOMEDICAL_DAILY_CATEGORIES,
                native_endpoints=feed_urls,
                search_endpoint=self.arxiv_client.api_url,
            )
        }
        return DailyDigest(
            source="arxiv",
            category=BIOMEDICAL_DAILY_MODE,
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            feed_url="",
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(BIOMEDICAL_DAILY_CATEGORIES),
            per_category_counts=per_category_counts,
            source_counts=source_counts,
            total_fetched=total_fetched,
            feed_urls=feed_urls,
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label="Biomedical daily",
            mode_kind="bundle",
            **runtime_contract,
            fetch_scope=normalize_fetch_scope(fetch_scope),
            mode_notes=(
                "Bundle-based strict same-day q-bio scouting across a fixed biomedical RSS bundle. "
                "Results are filtered locally to the requested date and deduplicated by arXiv identifier before ranking."
            ),
            requested_date=target_date,
            effective_date=target_date,
            strict_same_day_fetched=total_fetched,
            strict_same_day_ranked=len(full_ranked),
            used_latest_available_fallback=False,
        )

    def _build_biomedical_multisource_digest(
        self,
        *,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
    ) -> DailyDigest:
        exploration_policy = self.daily_exploration_policy
        runtime_contract = build_report_runtime_contract(report_mode)
        profile = self._resolve_daily_profile(
            BIOMEDICAL_MULTISOURCE_MODE,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        zotero_query_definitions = build_zotero_retrieval_queries(profile)
        source_errors: dict[str, str] = {}
        source_timings: dict[str, RunTimings] = {}
        source_notes: dict[str, str] = {}
        arxiv_category_papers: dict[str, list[PaperRecord]] = {}
        arxiv_query_papers: dict[str, list[PaperRecord]] = {}
        biorxiv_papers: list[PaperRecord] = []
        medrxiv_papers: list[PaperRecord] = []
        biorxiv_details: FeedFetchDetails | None = None
        medrxiv_details: FeedFetchDetails | None = None
        arxiv_network_seconds: float | None = None
        arxiv_parse_seconds: float | None = None
        try:
            (
                arxiv_category_papers,
                arxiv_category_network_seconds,
                arxiv_category_parse_seconds,
            ) = self.arxiv_client.fetch_today_by_category_with_timings(
                BIOMEDICAL_DAILY_CATEGORIES,
                today=target_date,
                max_results=None,
            )
            arxiv_network_seconds = arxiv_category_network_seconds
            arxiv_parse_seconds = arxiv_category_parse_seconds
        except Exception as exc:
            source_errors["arxiv"] = str(exc)
            arxiv_category_papers = {}
        if zotero_query_definitions:
            try:
                (
                    arxiv_query_papers,
                    arxiv_query_network_seconds,
                    arxiv_query_parse_seconds,
                ) = self.arxiv_client.fetch_today_by_queries_with_timings(
                    zotero_query_definitions,
                    today=target_date,
                    max_results=max(max_results, DEFAULT_DISCOVERY_QUERY_FETCH_LIMIT),
                )
                arxiv_network_seconds = _sum_known_seconds(arxiv_network_seconds, arxiv_query_network_seconds)
                arxiv_parse_seconds = _sum_known_seconds(arxiv_parse_seconds, arxiv_query_parse_seconds)
            except Exception as exc:
                if source_errors.get("arxiv"):
                    source_errors["arxiv"] = f"{source_errors['arxiv']}; {exc}"
                else:
                    source_errors["arxiv"] = str(exc)
                arxiv_query_papers = {}
                if arxiv_category_papers:
                    source_notes["arxiv"] = "Bounded arXiv query augmentation failed; category feeds remained available."
        try:
            biorxiv_papers, biorxiv_network_seconds, biorxiv_parse_seconds = self.biorxiv_client.fetch_today_with_timings(
                today=target_date,
                subject="all",
                max_results=None,
            )
            biorxiv_details = _last_feed_fetch_details(self.biorxiv_client)
            source_timings["biorxiv"] = build_run_timings(
                network_seconds=biorxiv_network_seconds,
                parse_seconds=biorxiv_parse_seconds,
            )
            source_notes["biorxiv"] = _compose_source_note("", biorxiv_details)
        except Exception as exc:
            source_errors["biorxiv"] = str(exc)
            biorxiv_papers = []
        try:
            medrxiv_papers, medrxiv_network_seconds, medrxiv_parse_seconds = self.medrxiv_client.fetch_today_with_timings(
                today=target_date,
                subject="all",
                max_results=None,
            )
            medrxiv_details = _last_feed_fetch_details(self.medrxiv_client)
            source_timings["medrxiv"] = build_run_timings(
                network_seconds=medrxiv_network_seconds,
                parse_seconds=medrxiv_parse_seconds,
            )
            source_notes["medrxiv"] = _compose_source_note("", medrxiv_details)
        except Exception as exc:
            source_errors["medrxiv"] = str(exc)
            medrxiv_papers = []
        source_timings["arxiv"] = build_run_timings(
            network_seconds=arxiv_network_seconds,
            parse_seconds=arxiv_parse_seconds,
        )
        papers = merge_paper_batches(
            {
                **arxiv_category_papers,
                **arxiv_query_papers,
                "biorxiv:all": biorxiv_papers,
                "medrxiv:all": medrxiv_papers,
            }
        )
        if not papers and source_errors:
            raise RuntimeError("; ".join(f"{format_source_label(source)}: {message}" for source, message in source_errors.items()))
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(papers, profile, limit=None, today=target_date)
        ranked = ranked_for_fetch_scope(
            full_ranked,
            max_results=max_results,
            fetch_scope=fetch_scope,
        )
        rank_seconds = perf_counter() - rank_started
        fetched_counts = {
            "arxiv": sum(len(items) for items in arxiv_category_papers.values()) + sum(len(items) for items in arxiv_query_papers.values()),
            "biorxiv": len(biorxiv_papers),
            "medrxiv": len(medrxiv_papers),
        }
        total_fetched = sum(fetched_counts.values())
        request_window = build_request_window(requested_date=target_date)
        source_endpoints = {
            "arxiv": self.arxiv_client.api_url,
            "biorxiv": biorxiv_details.endpoint if biorxiv_details is not None else self.biorxiv_client.build_feed_url("all"),
            "medrxiv": medrxiv_details.endpoint if medrxiv_details is not None else self.medrxiv_client.build_feed_url("all"),
        }
        displayed_counts = {
            **{source: 0 for source in MULTISOURCE_EXPECTED_SOURCES},
            **_count_papers_by_source(papers),
        }
        source_run_stats = build_source_run_stats(
            expected_sources=MULTISOURCE_EXPECTED_SOURCES,
            fetched_counts=fetched_counts,
            displayed_counts=displayed_counts,
            endpoints=source_endpoints,
            errors=source_errors,
            timings=source_timings,
            notes=source_notes,
        )
        run_timings = build_run_timings(
            network_seconds=_sum_known_seconds(
                *(item.timings.network_seconds for item in source_run_stats)
            ),
            parse_seconds=_sum_known_seconds(
                *(item.timings.parse_seconds for item in source_run_stats)
            ),
            rank_seconds=rank_seconds,
        )
        frontier_report = build_daily_frontier_report(
            paper_pool=papers,
            ranked_papers=full_ranked,
            requested_date=target_date,
            effective_date=target_date,
            source="multisource",
            mode=BIOMEDICAL_MULTISOURCE_MODE,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            **runtime_contract,
            searched_categories=BIOMEDICAL_DAILY_CATEGORIES,
            total_fetched=total_fetched,
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=fetch_scope,
            report_status="ready" if not source_errors else "partial",
            report_error="; ".join(source_errors.values()),
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        exploration_picks = self._daily_exploration_picks(ranked, profile, policy=exploration_policy)
        feed_urls = {name: self.arxiv_client.build_feed_url(name) for name in BIOMEDICAL_DAILY_CATEGORIES}
        source_metadata = {
            "arxiv": _build_source_contract_metadata(
                mode="bundle",
                native_filters=BIOMEDICAL_DAILY_CATEGORIES,
                native_endpoints=feed_urls,
                search_endpoint=self.arxiv_client.api_url,
                search_queries=tuple(definition.query for definition in zotero_query_definitions),
                search_profile_label=ZOTERO_RETRIEVAL_PROFILE_LABEL if zotero_query_definitions else "",
                query_profiles=_query_profile_metadata(zotero_query_definitions),
            ),
            "biorxiv": _build_source_contract_metadata(
                mode=biorxiv_details.contract_mode if biorxiv_details is not None else "rss",
                native_filters=("all",),
                native_endpoints={"all": source_endpoints["biorxiv"]},
            ),
            "medrxiv": _build_source_contract_metadata(
                mode=medrxiv_details.contract_mode if medrxiv_details is not None else "rss",
                native_filters=("all",),
                native_endpoints={"all": source_endpoints["medrxiv"]},
            ),
        }
        return DailyDigest(
            source="multisource",
            category=BIOMEDICAL_MULTISOURCE_MODE,
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            feed_url="",
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(BIOMEDICAL_DAILY_CATEGORIES),
            per_category_counts=_count_papers_by_category(papers, BIOMEDICAL_DAILY_CATEGORIES),
            source_counts={row.source: row.displayed_count for row in source_run_stats},
            total_fetched=total_fetched,
            feed_urls=feed_urls,
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            **runtime_contract,
            fetch_scope=normalize_fetch_scope(fetch_scope),
            report_status="ready" if not source_errors else "partial",
            report_error="; ".join(source_errors.values()),
            mode_notes=(
                "Compatibility-only 3-source biomedical scouting across the fixed q-bio arXiv bundle plus "
                "bioRxiv and medRxiv all-subject feeds. This path is no longer the default public release "
                "contract. When a Zotero export is provided, a bounded arXiv query-augmentation layer may "
                "add extra same-day biomedical candidates before deterministic ranking, while source "
                "provenance remains explicit on every surfaced paper."
            ),
            search_profile_label=ZOTERO_RETRIEVAL_PROFILE_LABEL if zotero_query_definitions else "",
            search_queries=tuple(definition.query for definition in zotero_query_definitions),
            requested_date=target_date,
            effective_date=target_date,
            strict_same_day_fetched=total_fetched,
            strict_same_day_ranked=len(full_ranked),
            used_latest_available_fallback=False,
        )

    def _build_biomedical_discovery_digest(
        self,
        *,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
    ) -> DailyDigest:
        profile = self._resolve_daily_profile(
            BIOMEDICAL_DISCOVERY_MODE,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        pool = self._fetch_biomedical_discovery_pool(
            max_results=max_results,
            profile=profile,
            fetch_scope=fetch_scope,
        )
        category_papers = filter_paper_batches_by_date(pool.category_papers, target_date=target_date)
        query_papers = filter_paper_batches_by_date(pool.query_papers, target_date=target_date)
        return self._build_biomedical_hybrid_digest(
            mode=BIOMEDICAL_DISCOVERY_MODE,
            requested_date=target_date,
            effective_date=target_date,
            used_latest_available_fallback=False,
            profile=profile,
            category_papers=category_papers,
            query_papers=query_papers,
            query_definitions=pool.query_definitions,
            search_profile_label=pool.search_profile_label,
            search_queries=pool.search_queries,
            max_results=max_results,
            report_mode=report_mode,
            fetch_scope=fetch_scope,
            mode_label="Biomedical discovery",
            mode_kind="hybrid",
            mode_notes=(
                "Hybrid strict same-day biomedical discovery using the fixed q-bio bundle plus fixed broader arXiv API "
                "searches over selected biomedical, ML, vision, AI, and language categories. When a Zotero export "
                "is present, one or two extra bounded query profiles may add candidate papers before the same "
                "biomedical gating and deterministic ranking are applied."
            ),
            source_run_errors={},
            network_seconds=pool.network_seconds,
            parse_seconds=pool.parse_seconds,
        )

    def _build_biomedical_latest_digest(
        self,
        *,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
    ) -> DailyDigest:
        profile = self._resolve_daily_profile(
            BIOMEDICAL_LATEST_MODE,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        pool = self._fetch_biomedical_discovery_pool(
            max_results=max_results,
            profile=profile,
            fetch_scope=fetch_scope,
        )
        strict_category_papers = filter_paper_batches_by_date(pool.category_papers, target_date=target_date)
        strict_query_papers = filter_paper_batches_by_date(pool.query_papers, target_date=target_date)
        strict_papers = merge_paper_batches({**strict_category_papers, **strict_query_papers})
        strict_ranked = self.ranker.rank(
            strict_papers,
            profile,
            limit=None,
            today=target_date,
        )
        strict_same_day_fetched = self._hybrid_total_fetched(strict_category_papers, strict_query_papers)

        effective_date = target_date
        used_latest_available_fallback = False
        displayed_category_papers = strict_category_papers
        displayed_query_papers = strict_query_papers

        if not strict_papers:
            latest_date = latest_available_paper_date(
                merge_paper_batches({**pool.category_papers, **pool.query_papers}),
                requested_date=target_date,
            )
            if latest_date is not None:
                effective_date = latest_date
                used_latest_available_fallback = latest_date != target_date
                displayed_category_papers = filter_paper_batches_by_date(pool.category_papers, target_date=latest_date)
                displayed_query_papers = filter_paper_batches_by_date(pool.query_papers, target_date=latest_date)

        return self._build_biomedical_hybrid_digest(
            mode=BIOMEDICAL_LATEST_MODE,
            requested_date=target_date,
            effective_date=effective_date,
            used_latest_available_fallback=used_latest_available_fallback,
            profile=profile,
            category_papers=displayed_category_papers,
            query_papers=displayed_query_papers,
            query_definitions=pool.query_definitions,
            search_profile_label=pool.search_profile_label,
            search_queries=pool.search_queries,
            max_results=max_results,
            report_mode=report_mode,
            fetch_scope=fetch_scope,
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            mode_notes=(
                "Hybrid biomedical reviewer mode using the fixed q-bio bundle plus fixed broader arXiv API searches. "
                "It first computes the strict same-day subset for the requested date. If that subset is empty, it "
                "falls back to the most recent non-empty release date present in the fetched candidate pool at or "
                "before the requested date. When a Zotero export is present, one or two extra bounded query "
                "profiles may add candidate papers before the same biomedical gating and deterministic ranking are "
                "applied. Requested date and effective release date remain distinct in the digest."
            ),
            strict_same_day_fetched=strict_same_day_fetched,
            strict_same_day_ranked=len(strict_ranked),
            source_run_errors={},
            network_seconds=pool.network_seconds,
            parse_seconds=pool.parse_seconds,
        )

    def _collect_range_child_digests(
        self,
        *,
        category: str,
        start_date: date,
        end_date: date,
        child_loader,
    ) -> RangeDigestCollection:
        if end_date < start_date:
            raise ValueError("end date must be on or after start date")

        requested_dates = tuple(_iter_requested_dates(start_date, end_date))
        completed_dates: list[date] = []
        child_digests: list[DailyDigest] = []
        failures: list[RequestWindowFailure] = []
        failure_reasons: list[str] = []
        failed_date: date | None = None
        failed_source = ""
        saw_partial_child = False

        for requested_day in requested_dates:
            try:
                child_digest = child_loader(requested_day)
            except Exception as exc:
                reason = str(exc)
                failure_reasons.append(reason)
                _append_request_window_failure(
                    failures,
                    failed_date=requested_day,
                    failed_source=_range_default_failed_source(category),
                    failure_reason=reason,
                )
                if failed_date is None:
                    failed_date = requested_day
                    if not failed_source:
                        failed_source = _range_default_failed_source(category)
                continue

            child_digests.append(child_digest)
            completed_dates.append(requested_day)
            if child_digest.report_status in {"partial", "failed"} or child_digest.request_window.status != "complete":
                saw_partial_child = True
                reason = child_digest.report_error or child_digest.request_window.failure_reason
                if not reason:
                    source_errors = [row.error for row in child_digest.source_run_stats if row.error]
                    reason = " | ".join(source_errors) if source_errors else ""
                if reason:
                    failure_reasons.append(reason)
                if child_digest.request_window.failure_entries:
                    for failure in child_digest.request_window.failure_entries:
                        if failure not in failures:
                            failures.append(failure)
                else:
                    _append_request_window_failure(
                        failures,
                        failed_date=requested_day,
                        failed_source=_infer_failed_source_from_digest(child_digest),
                        failure_reason=reason,
                    )
                if failed_date is None:
                    first_failure = failures[0] if failures else None
                    failed_date = (
                        first_failure.date
                        if first_failure is not None and first_failure.date is not None
                        else requested_day
                    )
                    if not failed_source:
                        failed_source = (
                            first_failure.source
                            if first_failure is not None
                            else _infer_failed_source_from_digest(child_digest)
                        )

        return RangeDigestCollection(
            requested_dates=requested_dates,
            completed_dates=tuple(completed_dates),
            child_digests=tuple(child_digests),
            failures=tuple(failures),
            failure_reasons=tuple(failure_reasons),
            failed_date=failed_date,
            failed_source=failed_source,
            saw_partial_child=saw_partial_child,
        )

    def _aggregate_range_child_digests(
        self,
        *,
        start_date: date,
        end_date: date,
        report_mode: str,
        collection: RangeDigestCollection,
    ) -> DailyDigest:
        if not collection.child_digests:
            raise RuntimeError(
                f"Unable to build any day in requested range {start_date.isoformat()} -> {end_date.isoformat()}: "
                f"{_join_unique_messages(collection.failure_reasons) or 'no data available'}"
            )

        status = (
            "complete"
            if len(collection.completed_dates) == len(collection.requested_dates)
            and not collection.saw_partial_child
            and not collection.failure_reasons
            else "partial"
        )
        failure_reason = _join_unique_messages(collection.failure_reasons)
        request_window = build_request_window(
            requested_date=start_date,
            start_date=start_date,
            end_date=end_date,
            status=status,
            completed_dates=collection.completed_dates,
            failures=collection.failures,
            failed_date=collection.failed_date,
            failed_source=collection.failed_source,
            failure_reason=failure_reason,
        )
        merged_papers = merge_paper_batches(
            {
                f"{digest.requested_target_date.isoformat()}:{index}": [item.paper for item in digest.ranked]
                for index, digest in enumerate(collection.child_digests)
            }
        )
        profile = collection.child_digests[-1].profile
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(merged_papers, profile, limit=None, today=end_date)
        rank_seconds = perf_counter() - rank_started
        ranked = list(full_ranked)
        exploration_picks = self._daily_exploration_picks(
            ranked,
            profile,
            policy=self.daily_exploration_policy,
        )
        per_category_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        feed_urls: dict[str, str] = {}
        source_endpoints: dict[str, str] = {}
        source_metadata: dict[str, dict[str, Any]] = {}
        searched_categories: list[str] = []
        search_queries: list[str] = []
        for digest in collection.child_digests:
            for category_name, count in digest.per_category_counts.items():
                per_category_counts[category_name] = per_category_counts.get(category_name, 0) + int(count)
            for source_name, count in digest.source_counts.items():
                source_counts[source_name] = source_counts.get(source_name, 0) + int(count)
            feed_urls.update(digest.feed_urls)
            source_endpoints.update(digest.source_endpoints)
            source_metadata.update(digest.source_metadata)
            for name in digest.searched_categories:
                if name not in searched_categories:
                    searched_categories.append(name)
            for query in digest.search_queries:
                if query not in search_queries:
                    search_queries.append(query)

        base_digest = collection.child_digests[-1]
        expected_sources = _expected_sources_for_digest(base_digest)
        source_run_stats = _aggregate_range_source_run_stats(
            collection.child_digests,
            expected_sources=expected_sources,
            endpoints=source_endpoints,
            fallback_displayed_counts=source_counts,
        )
        run_timings = build_run_timings(
            cache_seconds=_sum_known_seconds(
                *(digest.run_timings.cache_seconds for digest in collection.child_digests)
            ),
            network_seconds=_sum_known_seconds(
                *(digest.run_timings.network_seconds for digest in collection.child_digests)
            ),
            parse_seconds=_sum_known_seconds(
                *(digest.run_timings.parse_seconds for digest in collection.child_digests)
            ),
            rank_seconds=rank_seconds,
        )
        total_fetched = sum(digest.total_fetched for digest in collection.child_digests)
        runtime_contract = build_report_runtime_contract(report_mode)
        frontier_report = build_daily_frontier_report(
            paper_pool=merged_papers,
            ranked_papers=full_ranked,
            requested_date=start_date,
            effective_date=end_date,
            source=base_digest.source,
            mode=base_digest.category,
            mode_label=f"{base_digest.mode_label} range",
            mode_kind=f"{base_digest.mode_kind}-range",
            **runtime_contract,
            searched_categories=tuple(searched_categories),
            total_fetched=total_fetched,
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=FETCH_SCOPE_RANGE_FULL,
            report_status="ready" if status == "complete" else "partial",
            report_error=failure_reason,
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        return DailyDigest(
            source=base_digest.source,
            category=base_digest.category,
            target_date=start_date,
            generated_at=datetime.now(timezone.utc),
            feed_url=base_digest.feed_url,
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=self.daily_exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(searched_categories),
            per_category_counts=per_category_counts,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
            total_fetched=total_fetched,
            feed_urls=feed_urls,
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label=f"{base_digest.mode_label} range",
            mode_kind=f"{base_digest.mode_kind}-range",
            requested_report_mode=str(runtime_contract["requested_report_mode"]),
            report_mode=str(runtime_contract["report_mode"]),
            cost_mode=str(runtime_contract["cost_mode"]),
            enhanced_track=str(runtime_contract["enhanced_track"]),
            enhanced_item_count=int(runtime_contract["enhanced_item_count"]),
            runtime_note=str(runtime_contract["runtime_note"]),
            llm_requested=bool(runtime_contract["llm_requested"]),
            llm_applied=bool(runtime_contract["llm_applied"]),
            llm_provider=runtime_contract["llm_provider"],
            llm_fallback_reason=runtime_contract["llm_fallback_reason"],
            llm_seconds=runtime_contract["llm_seconds"],
            report_status="ready" if status == "complete" else "partial",
            report_error=failure_reason,
            fetch_scope=FETCH_SCOPE_RANGE_FULL,
            mode_notes=(
                f"{base_digest.mode_notes} Aggregated over requested range "
                f"{start_date.isoformat()} -> {end_date.isoformat()} with day-level execution "
                "and cache-aware reuse."
            ).strip(),
            search_profile_label=base_digest.search_profile_label,
            search_queries=tuple(search_queries),
            requested_date=start_date,
            effective_date=end_date,
            strict_same_day_fetched=None,
            strict_same_day_ranked=None,
            used_latest_available_fallback=any(
                digest.used_latest_available_fallback for digest in collection.child_digests
            ),
            strict_same_day_counts_known=False,
            stale_cache_source_requested_date=None,
            stale_cache_source_effective_date=None,
        )

    def _build_range_digest(
        self,
        *,
        category: str,
        mode: str | None,
        report_mode: str,
        start_date: date,
        end_date: date,
        max_results: int,
        feed_url: str | None,
        profile_source: str,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
        zotero_collections: Sequence[str] = (),
        refresh_sources: bool = False,
    ) -> DailyDigest:
        collection = self._collect_range_child_digests(
            category=category,
            start_date=start_date,
            end_date=end_date,
            child_loader=lambda requested_day: self.build_daily_digest(
                category=category,
                mode=mode,
                report_mode=DEFAULT_REPORT_MODE,
                today=requested_day,
                max_results=max_results,
                feed_url=feed_url,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
                fetch_scope=FETCH_SCOPE_DAY_FULL,
                refresh_sources=refresh_sources,
                apply_enhanced_report=False,
            ),
        )
        return self._aggregate_range_child_digests(
            start_date=start_date,
            end_date=end_date,
            report_mode=report_mode,
            collection=collection,
        )

    def _build_source_bundle_digest(
        self,
        *,
        bundle: SourceBundleDefinition,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str | None,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
        zotero_collections: Sequence[str],
        refresh_sources: bool,
    ) -> DailyDigest:
        exploration_policy = self.daily_exploration_policy
        runtime_contract = build_report_runtime_contract(report_mode)
        request_window = build_request_window(requested_date=target_date)
        profile = self._resolve_daily_profile(
            bundle.bundle_id,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
        )

        loaded_snapshots: list[tuple[str, DailySourceSnapshot, bool]] = []
        source_errors: dict[str, str] = {}
        source_endpoints = {
            source: self._source_endpoint_for(source)
            for source in bundle.enabled_sources
        }
        for source in bundle.enabled_sources:
            try:
                loaded_snapshots.append(
                    (
                        source,
                        *self._load_or_materialize_source_snapshot(
                            source=source,
                            target_date=target_date,
                            refresh=refresh_sources,
                        ),
                    )
                )
            except Exception as exc:
                source_errors[source] = str(exc)

        for source, snapshot, _loaded_from_cache in loaded_snapshots:
            if snapshot.endpoint:
                source_endpoints[source] = snapshot.endpoint

        if not loaded_snapshots and source_errors:
            raise RuntimeError("; ".join(f"{format_source_label(source)}: {message}" for source, message in source_errors.items()))

        merged_papers = merge_paper_batches(
            {
                source: snapshot.papers
                for source, snapshot, _loaded_from_cache in loaded_snapshots
            }
        )
        filtered_papers = filter_papers_for_bundle(merged_papers, bundle)
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(filtered_papers, profile, limit=None, today=target_date)
        ranked = ranked_for_fetch_scope(
            full_ranked,
            max_results=max_results,
            fetch_scope=fetch_scope,
        )
        rank_seconds = perf_counter() - rank_started

        fetched_counts = {
            source: snapshot.fetched_count
            for source, snapshot, _loaded_from_cache in loaded_snapshots
        }
        displayed_counts = {
            **{source: 0 for source in bundle.enabled_sources},
            **_count_papers_by_source(filtered_papers),
        }
        source_notes = {
            source: (
                f"{snapshot.note} Reused existing local day snapshot."
                if loaded_from_cache
                else f"{snapshot.note} Refreshed local day snapshot."
            ).strip()
            for source, snapshot, loaded_from_cache in loaded_snapshots
        }
        source_statuses = {
            source: snapshot.status
            for source, snapshot, _loaded_from_cache in loaded_snapshots
        }
        source_cache_statuses = {
            source: CACHE_STATUS_SAME_DAY if loaded_from_cache else CACHE_STATUS_FRESH
            for source, _snapshot, loaded_from_cache in loaded_snapshots
        }
        source_timings = {
            source: build_run_timings(
                cache_seconds=0.0 if loaded_from_cache else None,
                network_seconds=None if loaded_from_cache else snapshot.network_seconds,
                parse_seconds=None if loaded_from_cache else snapshot.parse_seconds,
            )
            for source, snapshot, loaded_from_cache in loaded_snapshots
        }
        source_run_stats = build_source_run_stats(
            expected_sources=bundle.enabled_sources,
            fetched_counts=fetched_counts,
            displayed_counts=displayed_counts,
            endpoints=source_endpoints,
            errors=source_errors,
            statuses=source_statuses,
            timings=source_timings,
            notes=source_notes,
            cache_statuses=source_cache_statuses,
        )
        run_timings = build_run_timings(
            cache_seconds=_sum_known_seconds(*(item.timings.cache_seconds for item in source_run_stats)),
            network_seconds=_sum_known_seconds(*(item.timings.network_seconds for item in source_run_stats)),
            parse_seconds=_sum_known_seconds(*(item.timings.parse_seconds for item in source_run_stats)),
            rank_seconds=rank_seconds,
        )
        total_fetched = sum(fetched_counts.values())
        report_error = "; ".join(source_errors.values())
        report_status = "ready" if not source_errors else "partial"
        frontier_report = build_daily_frontier_report(
            paper_pool=filtered_papers,
            ranked_papers=full_ranked,
            requested_date=target_date,
            effective_date=target_date,
            source="multisource" if len(bundle.enabled_sources) > 1 else bundle.enabled_sources[0],
            mode=bundle.bundle_id,
            mode_label=bundle.label,
            mode_kind="source-bundle",
            **runtime_contract,
            searched_categories=tuple(BIOMEDICAL_DISCOVERY_CATEGORIES),
            total_fetched=total_fetched,
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=fetch_scope,
            report_status=report_status,
            report_error=report_error,
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        exploration_picks = self._daily_exploration_picks(ranked, profile, policy=exploration_policy)
        source_metadata = self._bundle_source_metadata(
            bundle,
            source_endpoints=source_endpoints,
            source_snapshots={source: snapshot for source, snapshot, _loaded_from_cache in loaded_snapshots},
        )
        return DailyDigest(
            source="multisource" if len(bundle.enabled_sources) > 1 else bundle.enabled_sources[0],
            category=bundle.bundle_id,
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            feed_url="",
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(BIOMEDICAL_DISCOVERY_CATEGORIES),
            per_category_counts=_count_papers_by_category(filtered_papers, BIOMEDICAL_DISCOVERY_CATEGORIES),
            source_counts={row.source: row.displayed_count for row in source_run_stats},
            total_fetched=total_fetched,
            feed_urls={
                category: self.arxiv_client.build_feed_url(category)
                for category in BIOMEDICAL_DISCOVERY_CATEGORIES
            },
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label=bundle.label,
            mode_kind="source-bundle",
            **runtime_contract,
            report_status=report_status,
            report_error=report_error,
            fetch_scope=normalize_fetch_scope(fetch_scope),
            mode_notes=self._bundle_mode_notes(bundle),
            search_profile_label=bundle.label,
            search_queries=tuple(bundle.include_terms),
            requested_date=target_date,
            effective_date=target_date,
            strict_same_day_fetched=total_fetched,
            strict_same_day_ranked=len(full_ranked),
            used_latest_available_fallback=False,
        )

    def _bundle_source_metadata(
        self,
        bundle: SourceBundleDefinition,
        *,
        source_endpoints: Mapping[str, str] | None = None,
        source_snapshots: Mapping[str, DailySourceSnapshot] | None = None,
    ) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        resolved_source_endpoints = dict(source_endpoints or {})
        resolved_snapshots = dict(source_snapshots or {})
        for source in bundle.enabled_sources:
            snapshot = resolved_snapshots.get(source)
            snapshot_metadata = dict(snapshot.metadata or {}) if snapshot is not None else {}
            if source == "arxiv":
                metadata[source] = _build_source_contract_metadata(
                    mode="snapshot",
                    native_filters=BIOMEDICAL_DISCOVERY_CATEGORIES,
                    native_endpoints={
                        category: self.arxiv_client.build_feed_url(category)
                        for category in BIOMEDICAL_DISCOVERY_CATEGORIES
                    },
                    search_endpoint=self.arxiv_client.api_url,
                    search_profile_label=bundle.label,
                    contract_mode=str(snapshot_metadata.get("contract_mode", "")),
                    search_queries=tuple(snapshot_metadata.get("search_queries", ()) or ()),
                    query_profiles=tuple(snapshot_metadata.get("query_profiles", ()) or ()),
                )
                continue
            endpoint = resolved_source_endpoints.get(source) or self._source_endpoint_for(source)
            metadata[source] = _build_source_contract_metadata(
                mode="snapshot",
                native_filters=("all",),
                native_endpoints={"all": endpoint} if endpoint else None,
                search_profile_label=bundle.label,
                contract_mode=str(snapshot_metadata.get("contract_mode", "")),
            )
        return metadata

    def _bundle_mode_notes(self, bundle: SourceBundleDefinition) -> str:
        bits = [
            "Bundle-driven daily scouting over one local per-day source snapshot.",
            f"Enabled sources: {', '.join(format_source_label(source) for source in bundle.enabled_sources)}.",
        ]
        if bundle.include_terms:
            bits.append(f"Include terms: {', '.join(bundle.include_terms)}.")
        if bundle.exclude_terms:
            bits.append(f"Exclude terms: {', '.join(bundle.exclude_terms)}.")
        if bundle.description:
            bits.append(bundle.description)
        return " ".join(bits)

    def _source_endpoint_for(self, source: str) -> str:
        normalized = str(source or "").strip().lower()
        if normalized == "arxiv":
            return self.arxiv_client.api_url
        if normalized == "biorxiv":
            return self.biorxiv_client.build_feed_url("all")
        if normalized == "medrxiv":
            return self.medrxiv_client.build_feed_url("all")
        return ""

    def _fetch_biomedical_discovery_pool(
        self,
        *,
        max_results: int,
        profile: UserInterestProfile,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    ) -> BiomedicalDiscoveryPool:
        baseline_definitions = build_biomedical_discovery_queries()
        zotero_definitions = build_zotero_retrieval_queries(profile)
        search_definitions = (*baseline_definitions, *zotero_definitions)
        category_papers, category_network_seconds, category_parse_seconds = self.arxiv_client.fetch_recent_by_category_with_timings(
            BIOMEDICAL_DAILY_CATEGORIES,
            max_results=None,
        )
        query_papers, query_network_seconds, query_parse_seconds = self.arxiv_client.fetch_recent_by_queries_with_timings(
            search_definitions,
            max_results=(
                max(max_results, DEFAULT_DISCOVERY_QUERY_FETCH_LIMIT)
                if normalize_fetch_scope(fetch_scope) == FETCH_SCOPE_SHORTLIST
                else max(DEFAULT_DISCOVERY_QUERY_FETCH_LIMIT, 240)
            ),
        )
        return BiomedicalDiscoveryPool(
            category_papers=category_papers,
            query_papers=query_papers,
            query_definitions=tuple(search_definitions),
            search_profile_label=_compose_search_profile_label(
                baseline_label=BIOMEDICAL_DISCOVERY_PROFILE_LABEL,
                include_zotero=bool(zotero_definitions),
            ),
            network_seconds=_sum_known_seconds(category_network_seconds, query_network_seconds),
            parse_seconds=_sum_known_seconds(category_parse_seconds, query_parse_seconds),
        )

    def _build_biomedical_hybrid_digest(
        self,
        *,
        mode: str,
        requested_date: date,
        effective_date: date,
        used_latest_available_fallback: bool,
        profile: UserInterestProfile,
        category_papers: Mapping[str, Sequence[PaperRecord]],
        query_papers: Mapping[str, Sequence[PaperRecord]],
        query_definitions: Sequence[ArxivQueryDefinition],
        search_profile_label: str,
        search_queries: Sequence[str],
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        mode_label: str,
        mode_kind: str,
        mode_notes: str,
        strict_same_day_fetched: int | None = None,
        strict_same_day_ranked: int | None = None,
        source_run_errors: Mapping[str, str] | None = None,
        network_seconds: float | None = None,
        parse_seconds: float | None = None,
    ) -> DailyDigest:
        exploration_policy = self.daily_exploration_policy
        runtime_contract = build_report_runtime_contract(report_mode)
        request_window = build_request_window(requested_date=requested_date)
        papers = merge_paper_batches({**category_papers, **query_papers})
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(papers, profile, limit=None, today=effective_date)
        ranked = ranked_for_fetch_scope(
            full_ranked,
            max_results=max_results,
            fetch_scope=fetch_scope,
        )
        rank_seconds = perf_counter() - rank_started
        total_fetched = self._hybrid_total_fetched(category_papers, query_papers)
        source_endpoints = {"arxiv": self.arxiv_client.api_url}
        source_counts = {"arxiv": len(papers)}
        source_run_stats = build_source_run_stats(
            expected_sources=("arxiv",),
            fetched_counts={"arxiv": total_fetched},
            displayed_counts=source_counts,
            endpoints=source_endpoints,
            errors=source_run_errors,
            timings={
                "arxiv": build_run_timings(
                    network_seconds=network_seconds,
                    parse_seconds=parse_seconds,
                )
            },
        )
        run_timings = build_run_timings(
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            rank_seconds=rank_seconds,
        )
        frontier_report = build_daily_frontier_report(
            paper_pool=papers,
            ranked_papers=full_ranked,
            requested_date=requested_date,
            effective_date=effective_date,
            source="arxiv",
            mode=mode,
            mode_label=mode_label,
            mode_kind=mode_kind,
            **runtime_contract,
            searched_categories=BIOMEDICAL_DISCOVERY_CATEGORIES,
            total_fetched=total_fetched,
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=fetch_scope,
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        exploration_picks = self._daily_exploration_picks(ranked, profile, policy=exploration_policy)
        feed_urls = {name: self.arxiv_client.build_feed_url(name) for name in BIOMEDICAL_DAILY_CATEGORIES}
        source_metadata = {
            "arxiv": _build_source_contract_metadata(
                mode=mode_kind,
                native_filters=BIOMEDICAL_DISCOVERY_CATEGORIES,
                native_endpoints=feed_urls,
                search_endpoint=self.arxiv_client.api_url,
                search_queries=search_queries,
                search_profile_label=search_profile_label,
                query_profiles=_query_profile_metadata(query_definitions),
            )
        }
        return DailyDigest(
            source="arxiv",
            category=mode,
            target_date=requested_date,
            generated_at=datetime.now(timezone.utc),
            feed_url=self.arxiv_client.api_url,
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(BIOMEDICAL_DISCOVERY_CATEGORIES),
            per_category_counts=_count_papers_by_category(papers, BIOMEDICAL_DISCOVERY_CATEGORIES),
            source_counts=source_counts,
            total_fetched=total_fetched,
            feed_urls=feed_urls,
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label=mode_label,
            mode_kind=mode_kind,
            **runtime_contract,
            fetch_scope=normalize_fetch_scope(fetch_scope),
            mode_notes=mode_notes,
            search_profile_label=search_profile_label,
            search_queries=tuple(search_queries),
            requested_date=requested_date,
            effective_date=effective_date,
            strict_same_day_fetched=total_fetched if strict_same_day_fetched is None else strict_same_day_fetched,
            strict_same_day_ranked=len(full_ranked) if strict_same_day_ranked is None else strict_same_day_ranked,
            used_latest_available_fallback=used_latest_available_fallback,
        )

    @staticmethod
    def _hybrid_total_fetched(
        category_papers: Mapping[str, Sequence[PaperRecord]],
        query_papers: Mapping[str, Sequence[PaperRecord]],
    ) -> int:
        return sum(len(items) for items in category_papers.values()) + sum(len(items) for items in query_papers.values())

    def _build_single_category_digest(
        self,
        *,
        category: str,
        target_date: date,
        max_results: int,
        report_mode: str,
        fetch_scope: str,
        profile_source: str,
        feed_url: str | None,
        zotero_export_path: str | Path | None,
        zotero_db_path: str | Path | None,
    ) -> DailyDigest:
        exploration_policy = self.daily_exploration_policy
        runtime_contract = build_report_runtime_contract(report_mode)
        normalized_category = (category or DEFAULT_ARXIV_CATEGORY).strip()
        papers, network_seconds, parse_seconds = self.arxiv_client.fetch_today_with_timings(
            normalized_category,
            today=target_date,
            max_results=None,
            feed_url=feed_url,
        )
        profile = self._resolve_daily_profile(
            normalized_category,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        rank_started = perf_counter()
        full_ranked = self.ranker.rank(papers, profile, limit=None, today=target_date)
        ranked = ranked_for_fetch_scope(
            full_ranked,
            max_results=max_results,
            fetch_scope=fetch_scope,
        )
        rank_seconds = perf_counter() - rank_started
        request_window = build_request_window(requested_date=target_date)
        resolved_feed_url = feed_url or self.arxiv_client.build_feed_url(normalized_category)
        source_endpoints = {"arxiv": resolved_feed_url}
        source_counts = {"arxiv": len(papers)}
        source_run_stats = build_source_run_stats(
            expected_sources=("arxiv",),
            fetched_counts={"arxiv": len(papers)},
            displayed_counts=source_counts,
            endpoints=source_endpoints,
            timings={
                "arxiv": build_run_timings(
                    network_seconds=network_seconds,
                    parse_seconds=parse_seconds,
                )
            },
        )
        run_timings = build_run_timings(
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            rank_seconds=rank_seconds,
        )
        frontier_report = build_daily_frontier_report(
            paper_pool=papers,
            ranked_papers=full_ranked,
            requested_date=target_date,
            effective_date=target_date,
            source="arxiv",
            mode=normalized_category,
            mode_label=f"{normalized_category} feed",
            mode_kind="category-feed",
            **runtime_contract,
            searched_categories=(normalized_category,),
            total_fetched=len(papers),
        )
        frontier_report = apply_frontier_run_contract(
            frontier_report,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            fetch_scope=fetch_scope,
        )
        frontier_report = replace(
            frontier_report,
            source_counts={row.source: row.displayed_count for row in source_run_stats},
        )
        exploration_picks = self._daily_exploration_picks(ranked, profile, policy=exploration_policy)
        source_metadata = {
            "arxiv": _build_source_contract_metadata(
                mode="category-feed",
                native_filters=(normalized_category,),
                native_endpoints={normalized_category: resolved_feed_url},
                search_endpoint=resolved_feed_url,
            )
        }
        return DailyDigest(
            source="arxiv",
            category=normalized_category,
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            feed_url=resolved_feed_url,
            profile=profile,
            ranked=ranked,
            request_window=request_window,
            source_run_stats=source_run_stats,
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=(normalized_category,),
            per_category_counts={normalized_category: len(papers)},
            source_counts=source_counts,
            total_fetched=len(papers),
            feed_urls={normalized_category: resolved_feed_url},
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label=f"{normalized_category} feed",
            mode_kind="category-feed",
            **runtime_contract,
            fetch_scope=normalize_fetch_scope(fetch_scope),
            mode_notes="Single-category strict same-day arXiv RSS feed filtered locally to the requested date before ranking.",
            requested_date=target_date,
            effective_date=target_date,
            strict_same_day_fetched=len(papers),
            strict_same_day_ranked=len(full_ranked),
            used_latest_available_fallback=False,
        )

    def _daily_exploration_picks(
        self,
        ranked_papers: Sequence[RankedPaper],
        profile: UserInterestProfile,
        *,
        policy=None,
    ) -> list[RankedPaper]:
        return select_daily_exploration_picks(ranked_papers, profile, policy=policy or self.daily_exploration_policy)

    def _resolve_daily_profile(
        self,
        category: str,
        *,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> UserInterestProfile:
        resolved_bundle = self.resolve_source_bundle(category)
        baseline = self._bundle_daily_profile(resolved_bundle) if resolved_bundle is not None else self.daily_profile(category)
        normalized_source = resolve_requested_profile_source(
            profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
        )
        if normalized_source == PROFILE_SOURCE_BASELINE:
            return baseline
        if normalized_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
            resolved_db_path = _resolve_live_zotero_db_path(zotero_db_path)
            filtered_items = filter_items_by_collections(
                load_sqlite_library(resolved_db_path),
                zotero_collections,
            )
            return self.profile_builder.build_augmented_profile_from_items(
                baseline,
                items=filtered_items,
                profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
                profile_label="Live Zotero DB",
                profile_path=str(resolved_db_path.resolve()),
                db_name=resolved_db_path.name,
                selected_collections=zotero_collections,
            )
        export_state = ensure_local_zotero_export(
            export_path=zotero_export_path or self.zotero_export_path,
            status_path=self.zotero_status_path,
            db_path=zotero_db_path,
            refresh=False,
        )
        if not export_state.ready:
            if zotero_export_path is not None:
                raise ValueError(f"Zotero export not found: {zotero_export_path}")
            if export_state.error:
                raise ValueError(export_state.error)
            raise ValueError("No local Zotero library was discovered and no reusable export is available.")
        filtered_items = filter_items_by_collections(
            load_csl_json_export(export_state.export_path),
            zotero_collections,
        )
        return self.profile_builder.build_augmented_profile_from_items(
            baseline,
            items=filtered_items,
            profile_source=PROFILE_SOURCE_ZOTERO_EXPORT,
            profile_label="Zotero Export",
            profile_path=str(export_state.export_path.resolve()),
            export_name=export_state.export_path.name,
            selected_collections=zotero_collections,
        )

    def _bundle_daily_profile(self, bundle: SourceBundleDefinition) -> UserInterestProfile:
        profile = self.daily_profile(bundle.bundle_id)
        if not bundle.include_terms:
            return profile
        return replace(
            profile,
            keywords=tuple(dict.fromkeys((*profile.keywords, *bundle.include_terms))),
            notes=(f"{profile.notes} Bundle focus: {', '.join(bundle.include_terms)}.").strip(),
        )

    @staticmethod
    def daily_profile(category: str = DEFAULT_ARXIV_CATEGORY) -> UserInterestProfile:
        normalized_category = _normalize_category(category or DEFAULT_ARXIV_CATEGORY)
        category_weights = {
            "q-bio": 0.35,
            "q-bio.gn": 0.42,
            "q-bio.qm": 0.40,
            "q-bio.bm": 0.30,
            "q-bio.cb": 0.32,
            "q-bio.sc": 0.30,
            "q-bio.to": 0.24,
            "cs.lg": 0.06,
            "cs.cv": 0.06,
            "stat.ml": 0.05,
            "cs.ai": 0.03,
            "cs.cl": 0.03,
            "eess.iv": 0.02,
        }
        keywords = (
            "bioinformatics",
            "genomics",
            "transcriptomics",
            "proteomics",
            "multi-omics",
            "single-cell",
            "spatial transcriptomics",
            "cell atlas",
            "perturbation",
            "pathology",
            "histopathology",
            "histology",
            "microscopy",
            "biomedical imaging",
            "medical imaging",
            "radiology",
            "whole-slide",
            "biomedical",
            "medical",
            "clinical",
        )
        notes = (
            "Deterministic biomedical scouting profile for bioinformatics, genomics, "
            "transcriptomics, proteomics, same-day single-cell and spatial biology, "
            "pathology/histology, microscopy, biomedical imaging, radiology, perturbation, "
            "and reviewer-safe clinical biomedical papers."
        )
        if normalized_category == SOURCE_BUNDLE_AI_FOR_MEDICINE:
            category_weights.update(
                {
                    "cs.lg": 0.24,
                    "cs.ai": 0.22,
                    "cs.cv": 0.18,
                    "cs.cl": 0.18,
                    "stat.ml": 0.16,
                    "eess.iv": 0.12,
                    "q-bio.gn": 0.18,
                    "q-bio.bm": 0.16,
                    "q-bio.cb": 0.14,
                }
            )
            keywords = (
                "medical ai",
                "clinical ai",
                "biomedical ai",
                "medical imaging",
                "radiology",
                "pathology",
                "histopathology",
                "ehr",
                "patient",
                "language model",
                "multimodal",
                "foundation model",
                "machine learning",
                "deep learning",
                "drug discovery",
                "protein structure",
                "clinical prediction",
                "clinical decision support",
            )
            notes = (
                "Deterministic AI-for-medicine scouting profile for clinically grounded ML, multimodal medical imaging, "
                "pathology, radiology, EHR modeling, biomedical language models, and drug-discovery-adjacent foundation models."
            )
        if normalized_category not in FIXED_DAILY_MODES and normalized_category not in category_weights:
            category_weights[normalized_category] = 0.4
        return UserInterestProfile(
            keywords=keywords,
            category_weights=category_weights,
            seed_titles=(
                "Single-Cell Foundation Models for Transcriptomic Representation Learning",
                "Multimodal Modeling of Histopathology and Molecular Profiles",
                "Bioinformatics Methods for Large-Scale Genomics and Perturbation Data",
            ),
            notes=notes,
            basis_label="biomedical baseline",
            profile_basis=ProfileBasis(
                source=PROFILE_SOURCE_BASELINE,
                label="biomedical baseline",
            ),
        )

    @staticmethod
    def default_daily_cache_path(
        category: str,
        target_date: date,
        *,
        end_date: date | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> Path:
        prefix = _artifact_source_prefix(category)
        return DEFAULT_DAILY_CACHE_DIR / (
            f"frontier_compass_{prefix}_{_slug_category(category)}_"
            f"{_artifact_window_suffix(target_date, end_date=end_date, fetch_scope=fetch_scope)}"
            f"{_profile_output_suffix(profile_source, zotero_export_path=zotero_export_path, zotero_db_path=zotero_db_path, zotero_collections=zotero_collections)}.json"
        )

    @staticmethod
    def default_daily_report_path(
        category: str,
        target_date: date,
        *,
        end_date: date | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> Path:
        prefix = _artifact_source_prefix(category)
        return DEFAULT_DAILY_REPORT_DIR / (
            f"frontier_compass_{prefix}_{_slug_category(category)}_"
            f"{_artifact_window_suffix(target_date, end_date=end_date, fetch_scope=fetch_scope)}"
            f"{_profile_output_suffix(profile_source, zotero_export_path=zotero_export_path, zotero_db_path=zotero_db_path, zotero_collections=zotero_collections)}.html"
        )

    @staticmethod
    def report_path_for_cache_path(cache_path: str | Path) -> Path:
        return report_path_for_cache_artifact(
            cache_path,
            cache_dir=DEFAULT_DAILY_CACHE_DIR,
            report_dir=DEFAULT_DAILY_REPORT_DIR,
        )

    @staticmethod
    def demo_zotero_items() -> list[dict[str, object]]:
        return [
            {
                "title": "Agentic literature triage for biomedical question answering",
                "abstractNote": "Retrieval-augmented workflows for monitoring preprints and evidence updates in fast-moving biomedical domains.",
                "tags": [{"tag": "retrieval"}, {"tag": "biomedical nlp"}, {"tag": "agents"}],
                "collections": ["scouting", "evidence surveillance"],
            },
            {
                "title": "Zotero workflows for evidence surveillance and recommendation",
                "abstractNote": "Human-in-the-loop review pipelines for ranking relevant papers from noisy preprint feeds.",
                "tags": [{"tag": "recommendation"}, {"tag": "evidence surveillance"}],
                "collections": ["llm review ops"],
            },
            {
                "title": "Multimodal embeddings for single-cell atlas discovery",
                "abstractNote": "Bioinformatics retrieval systems that match new studies to atlas and perturbation collections.",
                "tags": [{"tag": "bioinformatics"}, {"tag": "single-cell"}, {"tag": "retrieval"}],
                "collections": ["genomics scouting"],
            },
        ]

    @staticmethod
    def demo_papers() -> list[PaperRecord]:
        return [
            PaperRecord(
                source="arxiv",
                identifier="arxiv-demo-001",
                title="Tool-Using Language Models for Biomedical Literature Triage",
                summary="Agentic retrieval and reranking pipeline for biomedical evidence scouting and question answering.",
                authors=("A. Researcher", "B. Curator"),
                categories=("biomedical nlp", "retrieval", "agents"),
                published=date(2026, 3, 20),
                url="https://arxiv.org/abs/2603.00001",
            ),
            PaperRecord(
                source="medrxiv",
                identifier="medrxiv-demo-002",
                title="Clinician-in-the-Loop LLM Summaries for Evidence Surveillance",
                summary="A clinical workflow for preprint surveillance, recommendation, and structured follow-up review.",
                authors=("C. Analyst",),
                categories=("evidence surveillance", "clinical informatics", "language models"),
                published=date(2026, 3, 21),
                url="https://www.medrxiv.org/content/10.1101/2026.03.21.000002v1",
            ),
            PaperRecord(
                source="biorxiv",
                identifier="biorxiv-demo-003",
                title="Single-Cell Atlas Retrieval with Multimodal Embeddings",
                summary="Embedding-based search for single-cell atlas discovery with strong retrieval calibration.",
                authors=("D. Biologist", "E. Engineer"),
                categories=("bioinformatics", "single-cell", "retrieval"),
                published=date(2026, 3, 19),
                url="https://www.biorxiv.org/content/10.1101/2026.03.19.000003v1",
            ),
            PaperRecord(
                source="arxiv",
                identifier="arxiv-demo-004",
                title="Graph Retrieval Agents for Scientific Discovery",
                summary="Graph-guided agents explore citation neighborhoods and rerank candidate papers for discovery tasks.",
                authors=("F. Systems",),
                categories=("retrieval", "agents", "scientific discovery"),
                published=date(2026, 3, 18),
                url="https://arxiv.org/abs/2603.00004",
            ),
            PaperRecord(
                source="biorxiv",
                identifier="biorxiv-demo-005",
                title="Uncertainty-Aware Preprint Recommendation for Wet-Lab Teams",
                summary="Recommendation model that balances novelty, confidence, and lab relevance for experimental teams.",
                authors=("G. Statistician",),
                categories=("recommendation", "bioinformatics"),
                published=date(2026, 2, 28),
                url="https://www.biorxiv.org/content/10.1101/2026.02.28.000005v1",
            ),
            PaperRecord(
                source="arxiv",
                identifier="arxiv-demo-006",
                title="Efficient Compression for Vision Transformers",
                summary="Compression and distillation techniques for image classification models.",
                authors=("H. Vision",),
                categories=("computer vision",),
                published=date(2026, 3, 22),
                url="https://arxiv.org/abs/2603.00006",
            ),
        ]

    def _write_daily_report(
        self,
        *,
        digest: DailyDigest,
        output_path: str | Path,
        acquisition_status_label: str = "",
        fetch_error: str = "",
    ) -> Path:
        resolved_output_path = Path(output_path)
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        report_html, report_seconds = measure_operation(
            lambda: self.report_builder.render_daily_digest(
                digest,
                title=daily_digest_title(digest),
                acquisition_status_label=acquisition_status_label,
                fetch_error=fetch_error,
            )
        )
        resolved_report_status = self._resolve_report_status(digest)
        resolved_report_error = self._resolve_report_error(
            digest,
            resolved_report_status=resolved_report_status,
            fetch_error=fetch_error,
        )
        del report_html
        resolved_report_seconds = (
            digest.run_timings.report_seconds
            if digest.run_timings.report_seconds is not None
            else report_seconds
        )
        digest.run_timings = build_run_timings(
            cache_seconds=digest.run_timings.cache_seconds,
            network_seconds=digest.run_timings.network_seconds,
            parse_seconds=digest.run_timings.parse_seconds,
            rank_seconds=digest.run_timings.rank_seconds,
            report_seconds=resolved_report_seconds,
        )
        digest.report_status = resolved_report_status
        digest.report_error = resolved_report_error
        if digest.frontier_report is not None:
            digest.frontier_report = replace(
                digest.frontier_report,
                run_timings=digest.run_timings,
                report_status=resolved_report_status,
                report_error=resolved_report_error,
            )
        final_report_html = self.report_builder.render_daily_digest(
            digest,
            title=daily_digest_title(digest),
            acquisition_status_label=acquisition_status_label,
            fetch_error=fetch_error,
        )
        resolved_output_path.write_text(final_report_html, encoding="utf-8")
        return resolved_output_path

    def _write_daily_digest_cache(
        self,
        *,
        digest: DailyDigest,
        cache_path: str | Path,
    ) -> Path:
        resolved_cache_path = Path(cache_path)
        resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_cache_path.write_text(json.dumps(digest.to_mapping(), indent=2), encoding="utf-8")
        return resolved_cache_path

    @staticmethod
    def _resolve_report_status(digest: DailyDigest) -> str:
        if digest.report_status and digest.report_status != "ready":
            return digest.report_status
        if digest.frontier_report is None:
            return "failed"
        if digest.frontier_report.total_ranked <= 0:
            return "empty"
        if digest.report_error:
            return "partial"
        return "ready"

    @staticmethod
    def _resolve_report_error(
        digest: DailyDigest,
        *,
        resolved_report_status: str,
        fetch_error: str = "",
    ) -> str:
        if fetch_error:
            return fetch_error
        if digest.report_error:
            return digest.report_error
        if resolved_report_status == "empty":
            return "Frontier Report is empty for the current run."
        if resolved_report_status == "failed":
            return "Frontier Report is unavailable for the current run."
        return ""

    def _ensure_report_for_cached_digest(
        self,
        cached: CachedDailyDigest,
        output_path: str | Path | None = None,
    ) -> Path:
        report_path = (
            Path(output_path)
            if output_path is not None
            else self.report_path_for_cache_path(cached.cache_path)
        )
        return self._write_daily_report(
            digest=cached.digest,
            output_path=report_path,
            acquisition_status_label=display_source_label(DISPLAY_SOURCE_CACHE),
        )

    def _load_requested_daily_digest_for_materialization(
        self,
        *,
        category: str,
        requested_date: date,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        cache_path: str | Path | None = None,
        non_empty_only: bool = False,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> CachedDailyDigest | None:
        if cache_path is not None:
            explicit_match = self._load_matching_cached_daily_digest(
                cache_path,
                category=category,
                requested_date=requested_date,
                fetch_scope=fetch_scope,
                non_empty_only=non_empty_only,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            )
            if explicit_match is not None:
                return explicit_match

        for cached in self._candidate_daily_caches_for_materialization(
            cache_dir=cache_dir,
            cache_path=cache_path,
        ):
            if self._is_compatible_cached_daily_digest(
                cached,
                category=category,
                requested_date=requested_date,
                fetch_scope=fetch_scope,
                non_empty_only=non_empty_only,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            ):
                return cached
        return None

    def _load_stale_compatible_daily_digest_for_materialization(
        self,
        *,
        category: str,
        requested_date: date,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        cache_path: str | Path | None = None,
        non_empty_only: bool = False,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> CachedDailyDigest | None:
        for cached in self._candidate_daily_caches_for_materialization(
            cache_dir=cache_dir,
            cache_path=cache_path,
        ):
            cached_requested_date = cached.digest.requested_target_date
            if cached_requested_date >= requested_date:
                continue
            if self._is_compatible_cached_daily_digest(
                cached,
                category=category,
                non_empty_only=non_empty_only,
                profile_source=profile_source,
                zotero_export_path=zotero_export_path,
                zotero_db_path=zotero_db_path,
                zotero_collections=zotero_collections,
            ):
                return cached
        return None

    def _candidate_daily_caches_for_materialization(
        self,
        *,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        cache_path: str | Path | None = None,
    ) -> list[CachedDailyDigest]:
        candidate_dirs: list[Path] = []
        if cache_path is not None:
            candidate_dirs.append(Path(cache_path).parent)
        candidate_dirs.append(Path(cache_dir))

        seen_dirs: set[Path] = set()
        seen_paths: set[Path] = set()
        entries: list[CachedDailyDigest] = []
        for candidate_dir in candidate_dirs:
            resolved_dir = candidate_dir.resolve()
            if resolved_dir in seen_dirs:
                continue
            seen_dirs.add(resolved_dir)
            for cached in self.available_daily_caches(candidate_dir):
                resolved_path = cached.cache_path.resolve()
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                entries.append(cached)

        entries.sort(
            key=lambda item: (
                item.digest.requested_target_date,
                item.digest.generated_at,
                item.cache_path.name,
            ),
            reverse=True,
        )
        return entries

    def _build_stale_cache_fallback_digest(
        self,
        cached_digest: DailyDigest,
        *,
        requested_date: date,
    ) -> DailyDigest:
        effective_date = cached_digest.effective_display_date
        frontier_report = (
            replace(
                cached_digest.frontier_report,
                requested_date=requested_date,
                effective_date=effective_date,
                request_window=build_request_window(requested_date=requested_date),
                source_run_stats=self._rewrite_source_run_stats_for_cache_story(
                    cached_digest.frontier_report.source_run_stats,
                    cache_status="stale-compatible-cache",
                    note="Older compatible cache reused for the current run.",
                ),
            )
            if cached_digest.frontier_report is not None
            else None
        )
        return DailyDigest(
            source=cached_digest.source,
            category=cached_digest.category,
            target_date=requested_date,
            generated_at=datetime.now(timezone.utc),
            feed_url=cached_digest.feed_url,
            profile=cached_digest.profile,
            ranked=list(cached_digest.ranked),
            request_window=build_request_window(requested_date=requested_date),
            source_run_stats=self._rewrite_source_run_stats_for_cache_story(
                cached_digest.source_run_stats,
                cache_status="stale-compatible-cache",
                note="Older compatible cache reused for the current run.",
            ),
            run_timings=cached_digest.run_timings,
            exploration_picks=list(cached_digest.exploration_picks),
            exploration_policy=cached_digest.exploration_policy,
            frontier_report=frontier_report,
            searched_categories=tuple(cached_digest.searched_categories),
            per_category_counts=dict(cached_digest.per_category_counts),
            source_counts=dict(cached_digest.source_counts),
            total_fetched=cached_digest.total_fetched,
            feed_urls=dict(cached_digest.feed_urls),
            source_endpoints=dict(cached_digest.source_endpoints),
            source_metadata=dict(cached_digest.source_metadata),
            mode_label=cached_digest.mode_label,
            mode_kind=cached_digest.mode_kind,
            requested_report_mode=cached_digest.requested_report_mode,
            report_mode=cached_digest.report_mode,
            cost_mode=cached_digest.cost_mode,
            enhanced_track=cached_digest.enhanced_track,
            enhanced_item_count=cached_digest.enhanced_item_count,
            runtime_note=cached_digest.runtime_note,
            report_status=cached_digest.report_status,
            report_error=cached_digest.report_error,
            fetch_scope=cached_digest.fetch_scope,
            mode_notes=cached_digest.mode_notes,
            search_profile_label=cached_digest.search_profile_label,
            search_queries=tuple(cached_digest.search_queries),
            requested_date=requested_date,
            effective_date=effective_date,
            strict_same_day_fetched=None,
            strict_same_day_ranked=None,
            used_latest_available_fallback=cached_digest.used_latest_available_fallback,
            strict_same_day_counts_known=False,
            stale_cache_source_requested_date=cached_digest.requested_target_date,
            stale_cache_source_effective_date=cached_digest.effective_display_date,
        )

    @staticmethod
    def _apply_cache_lookup_timing_to_digest(
        digest: DailyDigest,
        *,
        cache_lookup_seconds: float | None,
        preserve_stage_timings: bool = True,
    ) -> DailyDigest:
        if cache_lookup_seconds is None:
            return digest
        updated_run_timings = build_run_timings(
            cache_seconds=cache_lookup_seconds,
            network_seconds=digest.run_timings.network_seconds if preserve_stage_timings else None,
            parse_seconds=digest.run_timings.parse_seconds if preserve_stage_timings else None,
            rank_seconds=digest.run_timings.rank_seconds if preserve_stage_timings else None,
            report_seconds=digest.run_timings.report_seconds if preserve_stage_timings else None,
        )
        updated_frontier_report = (
            replace(
                digest.frontier_report,
                run_timings=updated_run_timings,
            )
            if digest.frontier_report is not None
            else None
        )
        return replace(
            digest,
            run_timings=updated_run_timings,
            frontier_report=updated_frontier_report,
        )

    def _digest_for_report_mode(
        self,
        digest: DailyDigest,
        *,
        report_mode: str,
        llm_settings: FrontierReportLLMSettings | None = None,
    ) -> DailyDigest:
        frontier_report = digest.frontier_report
        resolved_settings = llm_settings or resolve_frontier_report_llm_settings()
        deterministic_frontier_report = (
            self._restore_deterministic_frontier_report(frontier_report)
            if frontier_report is not None
            else None
        )
        if report_mode == DEFAULT_REPORT_MODE or deterministic_frontier_report is None:
            runtime_contract = build_report_runtime_contract(report_mode)
            adjusted_frontier_report = (
                replace(deterministic_frontier_report, **runtime_contract)
                if deterministic_frontier_report is not None
                else None
            )
            return replace(
                digest,
                frontier_report=adjusted_frontier_report,
                **runtime_contract,
            )

        if not resolved_settings.configured:
            runtime_contract = build_report_runtime_contract(
                report_mode,
                llm_provider=resolved_settings.provider_label,
                llm_fallback_reason=frontier_report_llm_unavailable_reason(resolved_settings),
            )
            return replace(
                digest,
                frontier_report=replace(deterministic_frontier_report, **runtime_contract),
                **runtime_contract,
            )

        llm_started = perf_counter()
        try:
            llm_result = build_model_assisted_frontier_report(
                deterministic_frontier_report,
                settings=resolved_settings,
            )
        except (FrontierReportLLMConfigurationError, FrontierReportLLMError) as exc:
            runtime_contract = build_report_runtime_contract(
                report_mode,
                llm_provider=resolved_settings.provider_label,
                llm_fallback_reason=str(exc),
            )
            return replace(
                digest,
                frontier_report=replace(deterministic_frontier_report, **runtime_contract),
                **runtime_contract,
            )

        runtime_contract = build_report_runtime_contract(
            report_mode,
            llm_provider=resolved_settings.provider_label,
            llm_applied=True,
            llm_seconds=perf_counter() - llm_started,
            enhanced_item_count=llm_result.enhanced_item_count,
        )
        return replace(
            digest,
            frontier_report=replace(llm_result.report, **runtime_contract),
            **runtime_contract,
        )

    @staticmethod
    def _restore_deterministic_frontier_report(frontier_report):
        if frontier_report is None:
            return None
        deterministic_takeaways = frontier_report.deterministic_takeaways or frontier_report.takeaways
        deterministic_field_highlights = (
            frontier_report.deterministic_field_highlights
            or frontier_report.field_highlights
        )
        return replace(
            frontier_report,
            takeaways=deterministic_takeaways,
            field_highlights=deterministic_field_highlights,
        )

    @staticmethod
    def _rewrite_source_run_stats_for_cache_story(
        source_run_stats: Sequence[SourceRunStats],
        *,
        cache_status: str,
        note: str,
        fetch_error: str = "",
    ) -> tuple[SourceRunStats, ...]:
        if not source_run_stats:
            return ()
        single_source = len(source_run_stats) == 1
        rewritten_rows: list[SourceRunStats] = []
        for item in source_run_stats:
            normalized_item = _normalize_source_run_outcome(item)
            row_error = item.error or (fetch_error if single_source else "")
            live_outcome = normalized_item.live_outcome or SOURCE_OUTCOME_UNKNOWN_LEGACY
            rewritten_rows.append(
                replace(
                    normalized_item,
                    cache_status=cache_status,
                    outcome=_derive_current_source_outcome(
                        cache_status=cache_status,
                        live_outcome=live_outcome,
                    ),
                    live_outcome=live_outcome,
                    status=(
                        "partial"
                        if row_error and (item.fetched_count > 0 or item.displayed_count > 0)
                        else "failed"
                        if row_error
                        else normalized_item.status
                    ),
                    error=row_error,
                    note=_merge_cache_story_note(normalized_item.note, note, fetch_error=fetch_error),
                    timings=normalized_item.timings,
                )
            )
        return tuple(rewritten_rows)

    def _apply_cache_story_to_digest(
        self,
        digest: DailyDigest,
        *,
        cache_status: str,
        note: str,
        fetch_error: str = "",
    ) -> DailyDigest:
        rewritten_source_stats = self._rewrite_source_run_stats_for_cache_story(
            digest.source_run_stats,
            cache_status=cache_status,
            note=note,
            fetch_error=fetch_error,
        )
        rewritten_frontier_report = (
            replace(
                digest.frontier_report,
                source_run_stats=self._rewrite_source_run_stats_for_cache_story(
                    digest.frontier_report.source_run_stats,
                    cache_status=cache_status,
                    note=note,
                    fetch_error=fetch_error,
                ),
            )
            if digest.frontier_report is not None
            else None
        )
        report_status = digest.report_status
        if fetch_error and report_status == "ready":
            report_status = "partial"
        current_run_timings = build_run_timings(
            cache_seconds=digest.run_timings.cache_seconds,
            network_seconds=digest.run_timings.network_seconds,
            parse_seconds=digest.run_timings.parse_seconds,
            rank_seconds=digest.run_timings.rank_seconds,
            report_seconds=digest.run_timings.report_seconds,
        )
        rewritten_frontier_report = (
            replace(
                rewritten_frontier_report,
                run_timings=current_run_timings,
            )
            if rewritten_frontier_report is not None
            else None
        )
        return replace(
            digest,
            generated_at=datetime.now(timezone.utc),
            source_run_stats=rewritten_source_stats,
            run_timings=current_run_timings,
            frontier_report=rewritten_frontier_report,
            report_status=report_status,
            report_error=fetch_error or digest.report_error,
        )

    def _is_compatible_cached_daily_digest(
        self,
        cached: CachedDailyDigest,
        *,
        category: str,
        requested_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        non_empty_only: bool = False,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> bool:
        if non_empty_only and not cached.digest.ranked:
            return False
        if _normalize_category(cached.digest.category) != _normalize_category(category):
            return False
        normalized_fetch_scope = normalize_fetch_scope(fetch_scope)
        cached_fetch_scope = normalize_fetch_scope(
            cached.digest.fetch_scope,
            default=FETCH_SCOPE_DAY_FULL,
        )
        if normalized_fetch_scope == FETCH_SCOPE_RANGE_FULL:
            if cached.digest.request_window.kind != "range":
                return False
            if start_date is not None and cached.digest.request_window.start_date != start_date:
                return False
            if end_date is not None and cached.digest.request_window.end_date != end_date:
                return False
            if cached_fetch_scope != FETCH_SCOPE_RANGE_FULL:
                return False
        else:
            if cached.digest.request_window.kind == "range":
                return False
            if requested_date is not None and cached.digest.requested_target_date != requested_date:
                return False
            if normalized_fetch_scope == FETCH_SCOPE_DAY_FULL and cached_fetch_scope != FETCH_SCOPE_DAY_FULL:
                return False
            if normalized_fetch_scope == FETCH_SCOPE_SHORTLIST and cached_fetch_scope != FETCH_SCOPE_SHORTLIST:
                return False
        if cached.digest.frontier_report is None:
            return False
        return _cache_matches_profile_compatibility(
            cached.cache_path,
            cached.digest,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
        )

    def _load_matching_cached_daily_digest(
        self,
        cache_path: str | Path,
        *,
        category: str,
        requested_date: date,
        start_date: date | None = None,
        end_date: date | None = None,
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        non_empty_only: bool = False,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
    ) -> CachedDailyDigest | None:
        path = Path(cache_path)
        if not path.exists():
            return None
        try:
            digest = self.load_daily_digest(path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        cached = CachedDailyDigest(digest=digest, cache_path=path)
        if not self._is_compatible_cached_daily_digest(
            cached,
            category=category,
            requested_date=requested_date,
            start_date=start_date,
            end_date=end_date,
            fetch_scope=fetch_scope,
            non_empty_only=non_empty_only,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
        ):
            return None
        return cached


def build_daily_run_summary(
    digest: DailyDigest,
    *,
    cache_path: str | Path | None = None,
    report_path: str | Path | None = None,
    display_source: str = DISPLAY_SOURCE_CACHE,
) -> DailyRunSummary:
    searched_categories = digest.searched_categories or ((digest.category,) if digest.category else ())
    total_fetched = max(int(digest.total_fetched), digest.total_ranked_count)
    total_displayed = digest.total_displayed_count
    per_category_counts = dict(digest.per_category_counts)
    if not per_category_counts and searched_categories:
        per_category_counts = {searched_categories[0]: total_fetched}
    source_counts = dict(digest.source_counts) or _count_papers_by_source((item.paper for item in digest.ranked))
    feed_urls = dict(digest.feed_urls)
    if not feed_urls and digest.feed_url and digest.category:
        feed_urls = {digest.category: digest.feed_url}
    source_endpoints = dict(digest.source_endpoints)
    return DailyRunSummary(
        requested_date=digest.requested_target_date,
        effective_date=digest.effective_display_date,
        request_window=digest.request_window,
        source_run_stats=tuple(digest.source_run_stats),
        run_timings=digest.run_timings,
        used_latest_available_fallback=digest.used_latest_available_fallback,
        strict_same_day_counts_known=digest.strict_same_day_counts_known,
        strict_same_day_fetched=(
            digest.strict_same_day_fetched_count if digest.strict_same_day_counts_known else None
        ),
        strict_same_day_ranked=(
            digest.strict_same_day_ranked_count if digest.strict_same_day_counts_known else None
        ),
        stale_cache_fallback_used=digest.stale_cache_fallback_used,
        stale_cache_source_requested_date=digest.stale_cache_source_requested_date,
        stale_cache_source_effective_date=digest.stale_cache_source_effective_date,
        displayed_fetched=total_fetched,
        displayed_ranked=digest.total_ranked_count,
        category=digest.category,
        mode_label=digest.mode_label or digest.category,
        mode_kind=digest.mode_kind or _default_mode_kind(digest.category),
        requested_report_mode=digest.requested_report_mode,
        report_mode=digest.report_mode,
        cost_mode=digest.cost_mode,
        enhanced_track=digest.enhanced_track,
        enhanced_item_count=digest.enhanced_item_count,
        runtime_note=digest.runtime_note,
        llm_requested=digest.llm_requested,
        llm_applied=digest.llm_applied,
        llm_provider=digest.llm_provider,
        llm_fallback_reason=digest.llm_fallback_reason,
        llm_seconds=digest.llm_seconds,
        report_status=digest.report_status,
        report_error=digest.report_error,
        fetch_scope=digest.fetch_scope,
        profile_source=digest.profile.profile_source,
        mode_notes=digest.mode_notes,
        search_profile_label=digest.search_profile_label,
        search_queries=digest.search_queries,
        ranked_count=digest.total_ranked_count,
        frontier_report_present=digest.frontier_report is not None,
        report_artifact_aligned=True,
        searched_categories=searched_categories,
        per_category_counts=per_category_counts,
        source_counts=source_counts,
        total_fetched=total_fetched,
        total_displayed=total_displayed,
        cache_path=str(Path(cache_path)) if cache_path is not None else "",
        report_path=(
            str(Path(report_path))
            if report_path is not None
            else (
                str(FrontierCompassApp.report_path_for_cache_path(cache_path))
                if cache_path is not None
                else str(FrontierCompassApp.default_daily_report_path(digest.category, digest.target_date))
            )
        ),
        display_source=display_source,
        feed_url=digest.feed_url,
        feed_urls=feed_urls,
        source_endpoints=source_endpoints,
    )


def build_daily_source_kwargs(
    selected_source: str,
    *,
    requested_date: date,
    max_results: int,
    start_date: date | None = None,
    end_date: date | None = None,
    report_mode: str = DEFAULT_REPORT_MODE,
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "today": requested_date,
        "max_results": max_results,
    }
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if report_mode != DEFAULT_REPORT_MODE:
        kwargs["report_mode"] = report_mode
    if normalize_fetch_scope(fetch_scope) != FETCH_SCOPE_DAY_FULL:
        kwargs["fetch_scope"] = normalize_fetch_scope(fetch_scope)
    fixed_mode = normalize_fixed_daily_mode(selected_source)
    if fixed_mode is not None:
        kwargs["mode"] = fixed_mode
    else:
        kwargs["category"] = selected_source
    return kwargs


def format_daily_source_label(selected_source: str) -> str:
    bundle = resolve_source_bundle(selected_source, config_path=DEFAULT_SOURCE_BUNDLES_PATH)
    if bundle is not None:
        return bundle.label
    labels = {
        BIOMEDICAL_LATEST_MODE: "Legacy latest-available biomedical mode",
        BIOMEDICAL_MULTISOURCE_MODE: "Compatibility 3-source run",
        BIOMEDICAL_DISCOVERY_MODE: "Biomedical discovery brief",
        BIOMEDICAL_DAILY_MODE: "Biomedical q-bio bundle",
    }
    if selected_source in labels:
        return labels[selected_source]
    return f"{selected_source} single-category feed"


def display_source_label(display_source: str) -> str:
    labels = {
        DISPLAY_SOURCE_FRESH: "fresh source fetch",
        DISPLAY_SOURCE_CACHE: "same-day cache",
        DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE: "same-date cache reused after fetch failure",
        DISPLAY_SOURCE_REUSED_STALE_CACHE: "older compatible cache reused after fetch failure",
        DISPLAY_SOURCE_RANGE_AGGREGATED: "range aggregated from day artifacts",
    }
    return labels.get(display_source, display_source)


def display_artifact_source_label(display_source: str) -> str:
    labels = {
        DISPLAY_SOURCE_FRESH: "fresh source fetch",
        DISPLAY_SOURCE_CACHE: "same-day cache",
        DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE: "same-day cache",
        DISPLAY_SOURCE_REUSED_STALE_CACHE: "older compatible cache",
        DISPLAY_SOURCE_RANGE_AGGREGATED: "range aggregated from day artifacts",
    }
    return labels.get(display_source, display_source)


def build_local_file_url(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


def build_existing_local_file_url(path: str | Path | None) -> str:
    if path is None:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    return resolved.resolve().as_uri()


def resolve_default_profile_selection(
    *,
    profile_source: str | None = None,
    explicit_zotero_export_path: str | Path | None = None,
    explicit_zotero_db_path: str | Path | None = None,
    default_zotero_export_path: str | Path | None = None,
    default_zotero_db_path: str | Path | None = None,
    reusable_zotero_export_path: str | Path = DEFAULT_ZOTERO_EXPORT_PATH,
) -> ResolvedProfileSelection:
    normalized_profile_source = normalize_profile_source(profile_source)
    if profile_source is not None and normalized_profile_source is None:
        raise ValueError(f"Unsupported profile_source: {profile_source}")

    explicit_export = Path(explicit_zotero_export_path) if explicit_zotero_export_path is not None else None
    explicit_db = Path(explicit_zotero_db_path) if explicit_zotero_db_path is not None else None
    default_export = Path(default_zotero_export_path) if default_zotero_export_path is not None else None
    default_db = Path(default_zotero_db_path) if default_zotero_db_path is not None else None
    reusable_export = Path(reusable_zotero_export_path)

    if normalized_profile_source is not None:
        if normalized_profile_source == PROFILE_SOURCE_BASELINE:
            return ResolvedProfileSelection(profile_source=PROFILE_SOURCE_BASELINE)
        if normalized_profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
            return ResolvedProfileSelection(
                profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
                zotero_db_path=explicit_db or default_db,
            )
        return ResolvedProfileSelection(
            profile_source=PROFILE_SOURCE_ZOTERO_EXPORT,
            zotero_export_path=(
                explicit_export
                or _preferred_reusable_zotero_export_path(
                    default_export_path=default_export,
                    reusable_export_path=reusable_export,
                )
            ),
            zotero_db_path=explicit_db,
        )

    if explicit_export is not None and explicit_db is not None:
        resolve_requested_profile_source(
            None,
            zotero_export_path=explicit_export,
            zotero_db_path=explicit_db,
        )
    if explicit_db is not None:
        return ResolvedProfileSelection(
            profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
            zotero_db_path=explicit_db,
        )
    if explicit_export is not None:
        return ResolvedProfileSelection(
            profile_source=PROFILE_SOURCE_ZOTERO_EXPORT,
            zotero_export_path=explicit_export,
        )

    readable_default_db = _readable_zotero_db_path(default_db)
    if readable_default_db is not None:
        return ResolvedProfileSelection(
            profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
            zotero_db_path=readable_default_db,
        )

    readable_export = _preferred_reusable_zotero_export_path(
        default_export_path=default_export,
        reusable_export_path=reusable_export,
    )
    if readable_export is not None:
        return ResolvedProfileSelection(
            profile_source=PROFILE_SOURCE_ZOTERO_EXPORT,
            zotero_export_path=readable_export,
        )

    return ResolvedProfileSelection(profile_source=PROFILE_SOURCE_BASELINE)


def normalize_request_window_inputs(
    *,
    requested_date: date,
    start_date: date | None = None,
    end_date: date | None = None,
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> tuple[date, date | None, date | None, str]:
    normalized_fetch_scope = normalize_fetch_scope(fetch_scope)
    resolved_start = start_date
    resolved_end = end_date
    if resolved_start is not None and resolved_end is None:
        resolved_end = resolved_start
    if resolved_end is not None and resolved_start is None:
        resolved_start = requested_date
    if resolved_start is not None and resolved_end is not None and resolved_end < resolved_start:
        resolved_start, resolved_end = resolved_end, resolved_start
    if resolved_start is not None and resolved_end is not None and resolved_start == resolved_end:
        return resolved_start, None, None, FETCH_SCOPE_DAY_FULL
    if resolved_start is not None or resolved_end is not None:
        normalized_fetch_scope = FETCH_SCOPE_RANGE_FULL
        return resolved_start or requested_date, resolved_start, resolved_end, normalized_fetch_scope
    return requested_date, resolved_start, resolved_end, normalized_fetch_scope


def build_ranked_paper_cards(
    ranked_papers: Sequence[RankedPaper],
    *,
    profile: UserInterestProfile | None = None,
    recommended_threshold: float = DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    why_label: str = "Why this paper",
    why_overrides: Mapping[str, str] | None = None,
) -> list[RankedPaperCard]:
    return [
        _build_ranked_paper_card(
            item,
            profile=profile,
            recommended_threshold=recommended_threshold,
            why_label=why_label,
            why_override=(
                why_overrides.get(item.paper.display_id, "")
                if why_overrides is not None and item.paper.display_id in why_overrides
                else None
            ),
        )
        for item in ranked_papers
    ]


def build_exploration_cards(
    ranked_papers: Sequence[RankedPaper],
    *,
    ranked_pool: Sequence[RankedPaper],
    profile: UserInterestProfile,
    policy=None,
) -> list[RankedPaperCard]:
    why_overrides = {
        item.paper.display_id: daily_exploration_note(
            item,
            ranked_papers=ranked_pool,
            profile=profile,
            policy=policy,
        )
        for item in ranked_papers
    }
    return build_ranked_paper_cards(
        ranked_papers,
        profile=profile,
        why_label="Why it's exploratory",
        why_overrides=why_overrides,
    )


def build_profile_inspector_lines(profile: UserInterestProfile) -> tuple[str, ...]:
    return profile.inspector_lines()


def format_author_summary(authors: Sequence[str]) -> str:
    if not authors:
        return "Unknown authors"
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} +{len(authors) - 1} more"


def _preferred_reusable_zotero_export_path(
    *,
    default_export_path: Path | None,
    reusable_export_path: Path,
) -> Path | None:
    readable_default_export = _readable_zotero_export_path(default_export_path)
    if readable_default_export is not None:
        return readable_default_export
    readable_reusable_export = _readable_zotero_export_path(reusable_export_path)
    if readable_reusable_export is not None:
        return readable_reusable_export
    return default_export_path if default_export_path is not None else None


def _readable_zotero_export_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        load_csl_json_export(path)
    except (OSError, ValueError):
        return None
    return path


def _readable_zotero_db_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    discovered_path, _error, _candidate_paths = discover_local_zotero_db_details(db_path=path)
    return discovered_path


def _build_ranked_paper_card(
    item: RankedPaper,
    *,
    profile: UserInterestProfile | None,
    recommended_threshold: float,
    why_label: str,
    why_override: str | None,
) -> RankedPaperCard:
    explanation = recommendation_explanation_for_ranked_paper(item, profile=profile)
    why_line = why_override if why_override is not None else why_this_paper_line(explanation)
    return RankedPaperCard(
        title=item.paper.title,
        source_label=format_source_label(item.paper.source),
        theme_label=theme_label_for_ranked_paper(item),
        authors_text=format_author_summary(item.paper.authors),
        published_text=(item.paper.published or item.paper.updated).isoformat()
        if (item.paper.published or item.paper.updated)
        else "unknown",
        categories=tuple(item.paper.categories[:6]),
        score=item.score,
        status_label=priority_label_for_score(item.score),
        is_recommended=item.score >= recommended_threshold,
        why_label=why_label,
        why_it_surfaced=why_line,
        score_explanation=score_explanation_line(explanation),
        relevance_explanation=interest_relevance_line(explanation),
        zotero_effect_label=zotero_effect_badge_text(explanation.zotero_effect),
        score_breakdown=explanation_breakdown_rows(explanation),
        score_detail_lines=explanation_detail_lines(explanation),
        recommendation_summary=item.recommendation_summary or item.paper.summary or "No summary provided.",
        url=item.paper.url,
    )


def _compact_reason_line(reasons: Sequence[str], *, limit: int = 2) -> str:
    selected = [reason.strip() for reason in reasons[:limit] if reason and reason.strip()]
    if not selected:
        return ""
    return "; ".join(selected)


def _slug_category(category: str) -> str:
    return category.lower().replace(".", "-").replace("+", "-").replace("/", "-")


def _artifact_source_prefix(category: str) -> str:
    normalized = _normalize_category(category)
    if (
        resolve_source_bundle(normalized, config_path=DEFAULT_SOURCE_BUNDLES_PATH) is not None
        or (normalized not in FIXED_DAILY_MODES and "." not in normalized and normalized != DEFAULT_ARXIV_CATEGORY)
    ):
        return "bundle"
    if normalized == BIOMEDICAL_MULTISOURCE_MODE:
        return "multisource"
    return "arxiv"


def _artifact_window_suffix(
    target_date: date,
    *,
    end_date: date | None = None,
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> str:
    if normalize_fetch_scope(fetch_scope) == FETCH_SCOPE_RANGE_FULL and end_date is not None:
        return f"{target_date.isoformat()}_to_{end_date.isoformat()}"
    return target_date.isoformat()


def _profile_output_suffix(
    profile_source: str | None,
    *,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
) -> str:
    normalized_source = _resolve_effective_profile_source(
        profile_source,
        zotero_export_path=zotero_export_path,
        zotero_db_path=zotero_db_path,
    )
    if normalized_source == PROFILE_SOURCE_BASELINE:
        return ""
    if normalized_source == PROFILE_SOURCE_ZOTERO_EXPORT:
        return _zotero_export_profile_output_suffix(
            zotero_export_path=zotero_export_path,
            zotero_collections=zotero_collections,
        )
    return _live_zotero_profile_output_suffix(
        zotero_db_path=_resolve_live_zotero_db_path(zotero_db_path),
        zotero_collections=zotero_collections,
    )


def _zotero_export_profile_output_suffix(
    *,
    zotero_export_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
) -> str:
    selection_suffix = _zotero_collection_suffix(zotero_collections)
    resolved_export_path = Path(zotero_export_path) if zotero_export_path is not None else DEFAULT_ZOTERO_EXPORT_PATH
    stem = _slug_category(resolved_export_path.stem)
    digest = sha1(str(resolved_export_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"_profile-zotero-export-{stem}-{digest}{selection_suffix}"


def _legacy_zotero_export_profile_output_suffix(
    *,
    zotero_export_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
) -> str:
    selection_suffix = _zotero_collection_suffix(zotero_collections)
    resolved_export_path = Path(zotero_export_path) if zotero_export_path is not None else DEFAULT_ZOTERO_EXPORT_PATH
    stem = _slug_category(resolved_export_path.stem)
    digest = sha1(str(resolved_export_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"_zotero-{stem}-{digest}{selection_suffix}"


def _live_zotero_profile_output_suffix(
    *,
    zotero_db_path: str | Path,
    zotero_collections: Sequence[str] = (),
) -> str:
    selection_suffix = _zotero_collection_suffix(zotero_collections)
    path = Path(zotero_db_path)
    stem = _slug_category(path.stem)
    digest = sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"_profile-live-zotero-db-{stem}-{digest}{selection_suffix}"


def _cache_matches_profile_compatibility(
    cache_path: str | Path,
    digest: DailyDigest,
    *,
    profile_source: str | None = None,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
) -> bool:
    cache_name = Path(cache_path).stem
    normalized_source = _resolve_effective_profile_source(
        profile_source,
        zotero_export_path=zotero_export_path,
        zotero_db_path=zotero_db_path,
    )
    candidate_is_profile_specific = (
        "_zotero-" in cache_name
        or "_profile-zotero-export-" in cache_name
        or "_profile-live-zotero-db-" in cache_name
    )
    if normalized_source == PROFILE_SOURCE_ZOTERO_EXPORT:
        resolved_export_path = Path(zotero_export_path) if zotero_export_path is not None else DEFAULT_ZOTERO_EXPORT_PATH
        expected_suffix = _zotero_export_profile_output_suffix(
            zotero_export_path=resolved_export_path,
            zotero_collections=zotero_collections,
        )
        legacy_suffix = _legacy_zotero_export_profile_output_suffix(
            zotero_export_path=resolved_export_path,
            zotero_collections=zotero_collections,
        )
        if (
            cache_name.endswith(expected_suffix) or cache_name.endswith(legacy_suffix)
        ) and digest.profile.profile_source == PROFILE_SOURCE_ZOTERO_EXPORT and (
            digest.profile.zotero_export_name == resolved_export_path.name
            and _normalize_collection_selection(digest.profile.zotero_selected_collections)
            == _normalize_collection_selection(zotero_collections)
        ):
            return True
        return False
    if normalized_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
        resolved_db_path = _resolve_live_zotero_db_path(zotero_db_path)
        expected_suffix = _live_zotero_profile_output_suffix(
            zotero_db_path=resolved_db_path,
            zotero_collections=zotero_collections,
        )
        return (
            cache_name.endswith(expected_suffix)
            and digest.profile.profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB
            and digest.profile.zotero_db_name == resolved_db_path.name
            and _normalize_collection_selection(digest.profile.zotero_selected_collections)
            == _normalize_collection_selection(zotero_collections)
        )
    return not candidate_is_profile_specific and digest.profile.profile_source == PROFILE_SOURCE_BASELINE


def _resolve_effective_profile_source(
    profile_source: str | None,
    *,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
) -> str:
    return resolve_requested_profile_source(
        profile_source,
        zotero_export_path=zotero_export_path,
        zotero_db_path=zotero_db_path,
    )


def _resolve_live_zotero_db_path(zotero_db_path: str | Path | None) -> Path:
    resolved_db_path, error, _candidate_paths = discover_local_zotero_db_details(
        db_path=zotero_db_path,
    )
    if resolved_db_path is None:
        raise ValueError(error or "No readable local Zotero library was discovered.")
    return resolved_db_path


def _normalize_collection_selection(values: Sequence[str] | None) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        normalized = str(value).strip()
        canonical = normalized.lower()
        if not normalized or canonical in seen:
            continue
        selected.append(normalized)
        seen.add(canonical)
    return tuple(selected)


def _zotero_collection_suffix(values: Sequence[str] | None) -> str:
    normalized = _normalize_collection_selection(values)
    if not normalized:
        return ""
    digest = sha1("|".join(value.lower() for value in normalized).encode("utf-8")).hexdigest()[:8]
    return f"_collections-{digest}"


def _count_papers_by_category(papers: Sequence[PaperRecord], categories: Sequence[str]) -> dict[str, int]:
    counts = {category: 0 for category in categories}
    for paper in papers:
        normalized_categories = {value.lower() for value in paper.categories if value}
        for category in categories:
            key = category.lower()
            if key in normalized_categories or any(value.startswith(f"{key}.") for value in normalized_categories):
                counts[category] += 1
    return counts


def _default_mode_kind(category: str) -> str:
    normalized = _normalize_category(category)
    if resolve_source_bundle(normalized, config_path=DEFAULT_SOURCE_BUNDLES_PATH) is not None:
        return "source-bundle"
    if normalized == BIOMEDICAL_LATEST_MODE:
        return "latest-available-hybrid"
    if normalized == BIOMEDICAL_MULTISOURCE_MODE:
        return "multisource"
    if normalized == BIOMEDICAL_DISCOVERY_MODE:
        return "hybrid"
    if normalized == BIOMEDICAL_DAILY_MODE:
        return "bundle"
    return "category-feed"


def _normalize_category(category: str) -> str:
    return (category or DEFAULT_ARXIV_CATEGORY).strip().lower()


def format_source_label(source: str) -> str:
    labels = {
        "arxiv": "arXiv",
        "biorxiv": "bioRxiv",
        "medrxiv": "medRxiv",
        "multisource": "Multisource",
    }
    normalized = (source or "unknown").strip().lower()
    return labels.get(normalized, normalized or "unknown")


def format_source_outcome_label(outcome: str) -> str:
    labels = {
        SOURCE_OUTCOME_LIVE_SUCCESS: "live-success",
        SOURCE_OUTCOME_LIVE_ZERO: "live-zero",
        SOURCE_OUTCOME_LIVE_FAILED: "live-failed",
        SOURCE_OUTCOME_SAME_DAY_CACHE: "same-day-cache",
        SOURCE_OUTCOME_STALE_CACHE: "stale-cache",
        SOURCE_OUTCOME_UNKNOWN_LEGACY: "unknown-legacy",
    }
    normalized = (outcome or SOURCE_OUTCOME_UNKNOWN_LEGACY).strip().lower()
    return labels.get(normalized, normalized or SOURCE_OUTCOME_UNKNOWN_LEGACY)


def summarize_source_counts(source_counts: Mapping[str, int]) -> tuple[str, ...]:
    return tuple(
        f"{format_source_label(source)}: {count}"
        for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        if source
    )


def normalize_fetch_scope(
    fetch_scope: str | None,
    *,
    default: str = FETCH_SCOPE_DAY_FULL,
) -> str:
    return normalize_schema_fetch_scope(fetch_scope, default=default)


def build_request_window(
    *,
    requested_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    status: str = "complete",
    completed_dates: Sequence[date] = (),
    failures: Sequence[RequestWindowFailure] = (),
    failed_date: date | None = None,
    failed_source: str = "",
    failure_reason: str = "",
) -> RequestWindow:
    resolved_failures = tuple(failures)
    if start_date is not None or end_date is not None:
        resolved_start = start_date or requested_date
        resolved_end = end_date or resolved_start
        return RequestWindow(
            kind="range",
            requested_date=requested_date or resolved_start,
            start_date=resolved_start,
            end_date=resolved_end,
            status=status,
            completed_dates=tuple(completed_dates),
            failures=resolved_failures,
            failed_date=failed_date,
            failed_source=failed_source,
            failure_reason=failure_reason,
        )
    return RequestWindow(
        kind="day",
        requested_date=requested_date,
        status=status,
        completed_dates=tuple(completed_dates),
        failures=resolved_failures,
        failed_date=failed_date,
        failed_source=failed_source,
        failure_reason=failure_reason,
    )


def _join_unique_messages(messages: Sequence[str]) -> str:
    unique_messages: list[str] = []
    for message in messages:
        normalized = " ".join(str(message).split())
        if normalized and normalized not in unique_messages:
            unique_messages.append(normalized)
    return " | ".join(unique_messages)


def _range_default_failed_source(category: str) -> str:
    normalized = _normalize_category(category)
    bundle = resolve_source_bundle(normalized, config_path=DEFAULT_SOURCE_BUNDLES_PATH)
    if normalized == BIOMEDICAL_MULTISOURCE_MODE or (bundle is not None and len(bundle.enabled_sources) > 1):
        return ""
    return "arxiv"


def _build_request_window_failure(
    *,
    failed_date: date | None,
    failed_source: str = "",
    failure_reason: str = "",
) -> RequestWindowFailure | None:
    normalized_source = str(failed_source or "").strip().lower()
    normalized_reason = " ".join(str(failure_reason or "").split())
    if failed_date is None and not normalized_source and not normalized_reason:
        return None
    return RequestWindowFailure(
        date=failed_date,
        source=normalized_source,
        reason=normalized_reason,
    )


def _append_request_window_failure(
    failures: list[RequestWindowFailure],
    *,
    failed_date: date | None,
    failed_source: str = "",
    failure_reason: str = "",
) -> None:
    failure = _build_request_window_failure(
        failed_date=failed_date,
        failed_source=failed_source,
        failure_reason=failure_reason,
    )
    if failure is not None and failure not in failures:
        failures.append(failure)


def _infer_failed_source_from_digest(digest: DailyDigest) -> str:
    if digest.source and digest.source != "multisource" and digest.report_status != "ready":
        return digest.source
    failing_rows = [
        row.source
        for row in digest.source_run_stats
        if row.source and (row.status in {"partial", "failed"} or row.error)
    ]
    unique_sources: list[str] = []
    for source in failing_rows:
        if source not in unique_sources:
            unique_sources.append(source)
    if len(unique_sources) == 1:
        return unique_sources[0]
    # For multisource: if multiple sources show status-based failures but only
    # one has a non-empty error, attribute the failure to that specific source.
    if len(unique_sources) != 1:
        error_sources: list[str] = []
        for row in digest.source_run_stats:
            if row.source and row.error and row.source not in error_sources:
                error_sources.append(row.source)
        if len(error_sources) == 1:
            return error_sources[0]
    return ""


def _aggregate_range_source_run_stats(
    child_digests: Sequence[DailyDigest],
    *,
    expected_sources: Sequence[str],
    endpoints: Mapping[str, str] | None = None,
    fallback_displayed_counts: Mapping[str, int] | None = None,
) -> tuple[SourceRunStats, ...]:
    endpoint_lookup = {str(key).strip().lower(): str(value) for key, value in (endpoints or {}).items()}
    displayed_fallback = {str(key).strip().lower(): int(value) for key, value in (fallback_displayed_counts or {}).items()}
    per_source_rows: dict[str, list[SourceRunStats]] = {}
    for digest in child_digests:
        for row in digest.source_run_stats:
            normalized = (row.source or "unknown").strip().lower() or "unknown"
            per_source_rows.setdefault(normalized, []).append(_normalize_source_run_outcome(row))

    aggregated_rows: list[SourceRunStats] = []
    for source in expected_sources:
        normalized_source = (source or "unknown").strip().lower() or "unknown"
        rows = per_source_rows.get(normalized_source, [])
        fetched_count = sum(row.fetched_count for row in rows)
        displayed_count = sum(row.displayed_count for row in rows)
        if displayed_count == 0:
            displayed_count = displayed_fallback.get(normalized_source, 0)
        requested = any(row.requested for row in rows) if rows else True
        statuses = [row.status for row in rows if row.status]
        errors = [row.error for row in rows if row.error]
        notes = [row.note for row in rows if row.note]
        outcomes = [row.outcome for row in rows if row.outcome]
        live_outcomes = [row.live_outcome for row in rows if row.live_outcome]
        # Preserve diagnostic notes from error-bearing child rows: if a child
        # has an error but its own note is empty, carry the error text forward
        # as a diagnostic note so context like "Same-day cache reused after a
        # fresh fetch failure" is never silently dropped.
        for row in rows:
            if row.error and not row.note and row.error not in notes:
                notes.append(row.error)
        cache_statuses = [row.cache_status for row in rows if row.cache_status]
        timings = merge_run_timings(*(row.timings for row in rows)) if rows else RunTimings()
        aggregated_rows.append(
            SourceRunStats(
                source=normalized_source,
                requested=requested,
                fetched_count=fetched_count,
                displayed_count=displayed_count,
                status=_aggregate_range_source_status(
                    statuses,
                    fetched_count=fetched_count,
                    displayed_count=displayed_count,
                    errors=errors,
                ),
                outcome=_summarize_range_field(outcomes) or SOURCE_OUTCOME_UNKNOWN_LEGACY,
                live_outcome=_summarize_range_field(live_outcomes) or SOURCE_OUTCOME_UNKNOWN_LEGACY,
                cache_status=_summarize_range_field(cache_statuses) or "unknown",
                error=_join_unique_messages(errors),
                endpoint=endpoint_lookup.get(normalized_source, ""),
                note=_join_unique_messages(notes),
                timings=timings,
            )
        )
    return tuple(aggregated_rows)


def _aggregate_range_source_status(
    statuses: Sequence[str],
    *,
    fetched_count: int,
    displayed_count: int,
    errors: Sequence[str],
) -> str:
    normalized_statuses = {str(status).strip().lower() for status in statuses if str(status).strip()}
    has_error = any(str(error).strip() for error in errors)
    if has_error:
        if fetched_count > 0 or displayed_count > 0 or "partial" in normalized_statuses or "ready" in normalized_statuses:
            return "partial"
        return "failed"
    if "partial" in normalized_statuses:
        return "partial"
    if "failed" in normalized_statuses:
        if fetched_count > 0 or displayed_count > 0 or "ready" in normalized_statuses:
            return "partial"
        return "failed"
    if "ready" in normalized_statuses:
        return "ready"
    if "empty" in normalized_statuses:
        return "empty"
    if normalized_statuses:
        return sorted(normalized_statuses)[0]
    return "unknown"


def _summarize_range_field(values: Sequence[str]) -> str:
    unique_values: list[str] = []
    for value in values:
        normalized = " ".join(str(value).split())
        if normalized and normalized not in unique_values:
            unique_values.append(normalized)
    if not unique_values:
        return ""
    if len(unique_values) == 1:
        return unique_values[0]
    return "mixed: " + " / ".join(unique_values)


def _derive_live_source_outcome(
    *,
    fetched_count: int,
    displayed_count: int,
    error: str,
    status: str,
) -> str:
    normalized_status = (status or "").strip().lower()
    has_content = fetched_count > 0 or displayed_count > 0
    if has_content:
        return SOURCE_OUTCOME_LIVE_SUCCESS
    if error:
        return SOURCE_OUTCOME_LIVE_FAILED
    if normalized_status in {"empty", "ready"} or fetched_count == 0:
        return SOURCE_OUTCOME_LIVE_ZERO
    return SOURCE_OUTCOME_UNKNOWN_LEGACY


def _normalize_cache_status(cache_status: str | None, *, default: str = CACHE_STATUS_FRESH) -> str:
    normalized = str(cache_status or default).strip().lower()
    aliases = {
        "same-day cache": CACHE_STATUS_SAME_DAY,
        "same-date-cache": CACHE_STATUS_SAME_DAY,
        "same-date cache": CACHE_STATUS_SAME_DAY,
        "stale-cache": CACHE_STATUS_STALE,
        "stale compatible cache": CACHE_STATUS_STALE,
        CACHE_STATUS_STALE: CACHE_STATUS_STALE,
    }
    return aliases.get(normalized, normalized or default)


def _derive_current_source_outcome(
    *,
    cache_status: str,
    live_outcome: str,
) -> str:
    normalized_cache_status = _normalize_cache_status(cache_status)
    normalized_live_outcome = (live_outcome or "").strip().lower()
    if normalized_cache_status == CACHE_STATUS_SAME_DAY:
        return (
            SOURCE_OUTCOME_UNKNOWN_LEGACY
            if normalized_live_outcome == SOURCE_OUTCOME_UNKNOWN_LEGACY
            else SOURCE_OUTCOME_SAME_DAY_CACHE
        )
    if normalized_cache_status == CACHE_STATUS_STALE:
        return (
            SOURCE_OUTCOME_UNKNOWN_LEGACY
            if normalized_live_outcome == SOURCE_OUTCOME_UNKNOWN_LEGACY
            else SOURCE_OUTCOME_STALE_CACHE
        )
    if normalized_live_outcome:
        return normalized_live_outcome
    return SOURCE_OUTCOME_UNKNOWN_LEGACY


def _normalize_source_run_outcome(row: SourceRunStats) -> SourceRunStats:
    normalized_live_outcome = (row.live_outcome or "").strip().lower()
    normalized_outcome = (row.outcome or "").strip().lower()
    normalized_cache_status = _normalize_cache_status(row.cache_status)
    if not normalized_live_outcome:
        if normalized_outcome in {
            SOURCE_OUTCOME_LIVE_SUCCESS,
            SOURCE_OUTCOME_LIVE_ZERO,
            SOURCE_OUTCOME_LIVE_FAILED,
            SOURCE_OUTCOME_UNKNOWN_LEGACY,
        }:
            normalized_live_outcome = normalized_outcome
        elif normalized_cache_status == CACHE_STATUS_FRESH:
            normalized_live_outcome = _derive_live_source_outcome(
                fetched_count=row.fetched_count,
                displayed_count=row.displayed_count,
                error=row.error,
                status=row.status,
            )
        else:
            normalized_live_outcome = SOURCE_OUTCOME_UNKNOWN_LEGACY
    if not normalized_outcome:
        normalized_outcome = _derive_current_source_outcome(
            cache_status=row.cache_status,
            live_outcome=normalized_live_outcome,
        )
    if normalized_outcome == row.outcome and normalized_live_outcome == row.live_outcome:
        return row
    return replace(
        row,
        cache_status=normalized_cache_status,
        outcome=normalized_outcome,
        live_outcome=normalized_live_outcome,
    )


def build_run_timings(
    *,
    cache_seconds: float | None = None,
    network_seconds: float | None = None,
    parse_seconds: float | None = None,
    rank_seconds: float | None = None,
    report_seconds: float | None = None,
) -> RunTimings:
    normalized_cache = _normalize_seconds(cache_seconds)
    normalized_network = _normalize_seconds(network_seconds)
    normalized_parse = _normalize_seconds(parse_seconds)
    normalized_rank = _normalize_seconds(rank_seconds)
    normalized_report = _normalize_seconds(report_seconds)
    known_stages = [
        value
        for value in (
            normalized_cache,
            normalized_network,
            normalized_parse,
            normalized_rank,
            normalized_report,
        )
        if value is not None
    ]
    return RunTimings(
        cache_seconds=normalized_cache,
        network_seconds=normalized_network,
        parse_seconds=normalized_parse,
        rank_seconds=normalized_rank,
        report_seconds=normalized_report,
        total_seconds=round(sum(known_stages), 4) if known_stages else None,
    )


def build_source_run_stats(
    *,
    expected_sources: Sequence[str],
    fetched_counts: Mapping[str, int] | None = None,
    displayed_counts: Mapping[str, int] | None = None,
    endpoints: Mapping[str, str] | None = None,
    errors: Mapping[str, str] | None = None,
    statuses: Mapping[str, str] | None = None,
    timings: Mapping[str, RunTimings] | None = None,
    notes: Mapping[str, str] | None = None,
    cache_statuses: Mapping[str, str] | None = None,
    cache_status: str = "fresh",
) -> tuple[SourceRunStats, ...]:
    fetched_lookup = {str(key).lower(): int(value) for key, value in (fetched_counts or {}).items()}
    displayed_lookup = {str(key).lower(): int(value) for key, value in (displayed_counts or {}).items()}
    endpoint_lookup = {str(key).lower(): str(value) for key, value in (endpoints or {}).items()}
    error_lookup = {str(key).lower(): str(value) for key, value in (errors or {}).items()}
    status_lookup = {str(key).lower(): str(value) for key, value in (statuses or {}).items()}
    timing_lookup = {str(key).lower(): value for key, value in (timings or {}).items()}
    note_lookup = {str(key).lower(): str(value) for key, value in (notes or {}).items()}
    cache_status_lookup = {str(key).lower(): str(value) for key, value in (cache_statuses or {}).items()}
    rows: list[SourceRunStats] = []
    for source in expected_sources:
        normalized = (source or "unknown").strip().lower() or "unknown"
        fetched_count = fetched_lookup.get(normalized, 0)
        displayed_count = displayed_lookup.get(normalized, 0)
        error = error_lookup.get(normalized, "")
        status = status_lookup.get(normalized, "")
        if not status:
            if error:
                status = "partial" if fetched_count > 0 or displayed_count > 0 else "failed"
            elif fetched_count <= 0 and displayed_count <= 0:
                status = "empty"
            else:
                status = "ready"
        live_outcome = _derive_live_source_outcome(
            fetched_count=fetched_count,
            displayed_count=displayed_count,
            error=error,
            status=status,
        )
        resolved_cache_status = _normalize_cache_status(
            cache_status_lookup.get(normalized, cache_status)
        )
        rows.append(
            SourceRunStats(
                source=normalized,
                requested=True,
                fetched_count=fetched_count,
                displayed_count=displayed_count,
                status=status,
                outcome=_derive_current_source_outcome(
                    cache_status=resolved_cache_status,
                    live_outcome=live_outcome,
                ),
                live_outcome=live_outcome,
                cache_status=resolved_cache_status,
                error=error,
                endpoint=endpoint_lookup.get(normalized, ""),
                note=note_lookup.get(normalized, ""),
                timings=timing_lookup.get(normalized, RunTimings()),
            )
        )
    return tuple(rows)


def merge_run_timings(*timings: RunTimings) -> RunTimings:
    return build_run_timings(
        cache_seconds=_sum_known_seconds(*(item.cache_seconds for item in timings)),
        network_seconds=_sum_known_seconds(*(item.network_seconds for item in timings)),
        parse_seconds=_sum_known_seconds(*(item.parse_seconds for item in timings)),
        rank_seconds=_sum_known_seconds(*(item.rank_seconds for item in timings)),
        report_seconds=_sum_known_seconds(*(item.report_seconds for item in timings)),
    )


def _normalize_seconds(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(float(value), 0.0), 4)


def _sum_known_seconds(*values: float | None) -> float | None:
    known_values = [value for value in values if value is not None]
    if not known_values:
        return None
    return sum(known_values)


def _merge_cache_story_note(existing_note: str, cache_note: str, *, fetch_error: str = "") -> str:
    normalized_existing = _dedupe_note_clauses(existing_note)
    normalized_cache_note = _dedupe_note_clauses(cache_note)
    parts: list[str] = []
    if normalized_existing:
        parts.append(normalized_existing)
    if normalized_cache_note and normalized_cache_note not in normalized_existing:
        parts.append(normalized_cache_note)
    if fetch_error:
        fetch_note = f"Fresh fetch error: {fetch_error}"
        if fetch_note not in " ".join(parts):
            parts.append(fetch_note)
    return " ".join(parts)


def _dedupe_note_clauses(note: str) -> str:
    clauses: list[str] = []
    for raw_clause in note.split(". "):
        clause = raw_clause.strip()
        if not clause:
            continue
        normalized_clause = clause if clause.endswith(".") else f"{clause}."
        if normalized_clause not in clauses:
            clauses.append(normalized_clause)
    return " ".join(clauses).strip()


def _expected_sources_for_digest(digest: DailyDigest) -> tuple[str, ...]:
    resolved_bundle = resolve_source_bundle(digest.category, config_path=DEFAULT_SOURCE_BUNDLES_PATH)
    if resolved_bundle is not None:
        return resolved_bundle.enabled_sources
    if digest.category == BIOMEDICAL_MULTISOURCE_MODE or digest.source == "multisource":
        return MULTISOURCE_EXPECTED_SOURCES
    if digest.source:
        return ((digest.source or "arxiv").strip().lower() or "arxiv",)
    return ()


def _backfill_source_run_stats(
    *,
    expected_sources: Sequence[str],
    source_run_stats: Sequence[SourceRunStats],
    source_counts: Mapping[str, int] | None = None,
    endpoints: Mapping[str, str] | None = None,
) -> tuple[SourceRunStats, ...]:
    existing = {item.source.strip().lower(): item for item in source_run_stats if item.source.strip()}
    count_lookup = {str(key).strip().lower(): int(value) for key, value in (source_counts or {}).items()}
    endpoint_lookup = {str(key).strip().lower(): str(value) for key, value in (endpoints or {}).items()}
    rows: list[SourceRunStats] = []
    changed = False
    for source in expected_sources:
        normalized = (source or "unknown").strip().lower() or "unknown"
        if normalized in existing:
            normalized_row = _normalize_source_run_outcome(existing[normalized])
            if normalized_row != existing[normalized]:
                changed = True
            rows.append(normalized_row)
            continue
        changed = True
        displayed_count = count_lookup.get(normalized, 0)
        rows.append(
            SourceRunStats(
                source=normalized,
                requested=True,
                fetched_count=displayed_count,
                displayed_count=displayed_count,
                status="unknown",
                outcome=SOURCE_OUTCOME_UNKNOWN_LEGACY,
                live_outcome=SOURCE_OUTCOME_UNKNOWN_LEGACY,
                cache_status="unknown",
                endpoint=endpoint_lookup.get(normalized, ""),
                note="Legacy artifact is missing source-level observability for this source.",
                timings=RunTimings(),
            )
        )
    if not changed and len(rows) == len(source_run_stats):
        return tuple(source_run_stats)
    return tuple(rows)


def _iter_requested_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    days: list[date] = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return tuple(days)


def apply_frontier_run_contract(
    frontier_report,
    *,
    request_window: RequestWindow,
    source_run_stats: Sequence[SourceRunStats],
    run_timings: RunTimings,
    fetch_scope: str,
    report_status: str = "ready",
    report_error: str = "",
):
    return replace(
        frontier_report,
        request_window=request_window,
        source_run_stats=tuple(source_run_stats),
        run_timings=run_timings,
        fetch_scope=normalize_fetch_scope(fetch_scope),
        report_status=report_status,
        report_error=report_error,
    )


def ranked_for_fetch_scope(
    ranked_papers: Sequence[RankedPaper],
    *,
    max_results: int,
    fetch_scope: str,
) -> list[RankedPaper]:
    normalized_scope = normalize_fetch_scope(fetch_scope)
    if normalized_scope == FETCH_SCOPE_SHORTLIST:
        return list(ranked_papers[: max(max_results, 0)])
    return list(ranked_papers)


def _count_papers_by_source(papers: Sequence[PaperRecord] | Sequence[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for paper in papers:
        source = getattr(paper, "source", "")
        normalized = (str(source) or "unknown").strip().lower() or "unknown"
        counts[normalized] = counts.get(normalized, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _last_feed_fetch_details(client: object) -> FeedFetchDetails | None:
    details = getattr(client, "last_fetch_details", None)
    if isinstance(details, FeedFetchDetails):
        return details
    return None


def _compose_source_note(base_note: str, details: FeedFetchDetails | None) -> str:
    return _join_unique_messages(
        [
            str(base_note or "").strip(),
            details.note if details is not None else "",
        ]
    )


def _build_source_contract_metadata(
    *,
    mode: str,
    native_filters: Sequence[str] = (),
    native_endpoints: Mapping[str, str] | None = None,
    contract_mode: str = "",
    search_endpoint: str = "",
    search_queries: Sequence[str] = (),
    search_profile_label: str = "",
    query_profiles: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "mode": mode,
    }
    if native_filters:
        metadata["native_filters"] = list(native_filters)
    if native_endpoints:
        metadata["native_endpoints"] = {
            str(key): str(value)
            for key, value in native_endpoints.items()
            if str(key) and str(value)
        }
    if contract_mode:
        metadata["contract_mode"] = contract_mode
    if search_endpoint:
        metadata["search_endpoint"] = search_endpoint
    if search_queries:
        metadata["search_queries"] = list(search_queries)
    if search_profile_label:
        metadata["search_profile_label"] = search_profile_label
    if query_profiles:
        metadata["query_profiles"] = [
            {
                str(key): value
                for key, value in profile.items()
                if str(key)
            }
            for profile in query_profiles
            if isinstance(profile, Mapping)
        ]
    return metadata


def _query_profile_metadata(query_definitions: Sequence[ArxivQueryDefinition]) -> tuple[dict[str, Any], ...]:
    profiles: list[dict[str, Any]] = []
    for definition in query_definitions:
        metadata: dict[str, Any] = {
            "label": definition.label,
            "origin": definition.origin,
        }
        if definition.terms:
            metadata["terms"] = list(definition.terms)
        profiles.append(metadata)
    return tuple(profiles)


def _compose_search_profile_label(*, baseline_label: str, include_zotero: bool) -> str:
    if include_zotero:
        return f"{baseline_label} + {ZOTERO_RETRIEVAL_PROFILE_LABEL}"
    return baseline_label


def normalize_fixed_daily_mode(mode: str | None) -> str | None:
    normalized = _normalize_category(mode or "")
    for fixed_mode in FIXED_DAILY_MODES:
        if normalized == fixed_mode:
            return fixed_mode
    return None


def is_fixed_daily_mode(mode: str | None) -> bool:
    return normalize_fixed_daily_mode(mode) is not None
