"""Streamlit UI for FrontierCompass daily reading."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
import sys
from typing import Any, Sequence

import streamlit as st

from frontier_compass.api import FrontierCompassRunner, LocalUISession
from frontier_compass.common.frontier_report_llm import resolve_frontier_report_llm_settings
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    format_cost_mode_label,
    format_llm_bool,
    format_llm_provider,
    format_llm_seconds,
    format_llm_summary,
    format_runtime_status,
)
from frontier_compass.common.user_defaults import load_user_defaults
from frontier_compass.exploration.selector import daily_exploration_intro, resolve_daily_exploration_picks
from frontier_compass.reporting.daily_brief import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    build_daily_brief,
    build_reviewer_shortlist,
    filter_ranked_papers,
    summarize_category_counts,
)
from frontier_compass.storage.schema import RunHistoryEntry, RunTimings, SourceRunStats, resolve_requested_profile_source
from frontier_compass.ui.app import (
    DAILY_SOURCE_OPTIONS,
    DISPLAY_SOURCE_CACHE,
    DISPLAY_SOURCE_FRESH,
    DISPLAY_SOURCE_RANGE_AGGREGATED,
    DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE,
    DISPLAY_SOURCE_REUSED_STALE_CACHE,
    DEFAULT_REVIEWER_SOURCE,
    FETCH_SCOPE_DAY_FULL,
    FETCH_SCOPE_OPTIONS,
    FETCH_SCOPE_RANGE_FULL,
    PROFILE_SOURCE_BASELINE,
    PROFILE_SOURCE_LIVE_ZOTERO_DB,
    PROFILE_SOURCE_ZOTERO_EXPORT,
    build_existing_local_file_url,
    build_daily_run_summary,
    build_exploration_cards,
    build_profile_inspector_lines,
    build_ranked_paper_cards,
    display_source_label,
    format_daily_source_label,
    format_source_label,
    format_source_outcome_label,
    normalize_request_window_inputs,
    resolve_default_profile_selection,
)
from frontier_compass.ui.history import (
    build_history_artifact_rows,
    build_history_summary_bits,
    eml_path_for_report_artifact,
    format_history_compatibility_text,
    format_history_llm_provenance_text,
    format_history_requested_effective_label,
)
from frontier_compass.ui.streamlit_support import render_external_link
from frontier_compass.zotero.export_loader import load_csl_json_export
from frontier_compass.zotero.local_library import (
    DEFAULT_ZOTERO_EXPORT_PATH,
    ZoteroLibraryState,
    available_collections,
    write_local_zotero_state,
)
from frontier_compass.zotero.sqlite_loader import load_sqlite_library


@dataclass(slots=True, frozen=True)
class UIStartupRequest:
    selected_source: str
    requested_date: date
    max_results: int
    start_date: date | None = None
    end_date: date | None = None
    report_mode: str = DEFAULT_REPORT_MODE
    profile_source: str | None = None
    zotero_export_path: Path | None = None
    zotero_db_path: Path | None = None
    zotero_collections: tuple[str, ...] = ()
    fetch_scope: str = FETCH_SCOPE_DAY_FULL
    allow_stale_cache: bool = True
    skip_initial_load: bool = False
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    @property
    def request_key(self) -> str:
        zotero_key = str(self.zotero_export_path) if self.zotero_export_path is not None else ""
        zotero_db_key = str(self.zotero_db_path) if self.zotero_db_path is not None else ""
        collection_key = "|".join(self.zotero_collections)
        return (
            f"{self.selected_source}|{self.requested_date.isoformat()}|{self.max_results}|{self.report_mode}|"
            f"{int(self.allow_stale_cache)}|{self.profile_source or ''}|{self.fetch_scope}|"
            f"{self.start_date.isoformat() if self.start_date is not None else ''}|"
            f"{self.end_date.isoformat() if self.end_date is not None else ''}|"
            f"{zotero_key}|{zotero_db_key}|{collection_key}|{int(self.skip_initial_load)}|"
            f"{self.llm_provider or ''}|{self.llm_base_url or ''}|{self.llm_model or ''}"
        )

    @property
    def effective_profile_source(self) -> str:
        try:
            return resolve_requested_profile_source(
                self.profile_source,
                zotero_export_path=self.zotero_export_path,
                zotero_db_path=self.zotero_db_path,
            )
        except ValueError:
            return PROFILE_SOURCE_BASELINE

    @property
    def auto_profile_source_note(self) -> str:
        if self.profile_source is not None:
            return ""
        if self.zotero_db_path is not None:
            return "Personalization defaults to the configured live Zotero DB."
        if self.zotero_export_path is not None:
            return "Personalization defaults to a reusable Zotero export snapshot."
        return ""

    @property
    def request_label(self) -> str:
        if self.start_date is not None or self.end_date is not None:
            start = self.start_date or self.requested_date
            end = self.end_date or start
            return f"{start.isoformat()} -> {end.isoformat()}"
        return self.requested_date.isoformat()


@dataclass(slots=True, frozen=True)
class PersonalizationPanelState:
    active: bool
    profile_source: str
    label: str
    detail: str
    available_collections: tuple[str, ...] = ()
    item_count: int = 0
    zotero_export_path: Path | None = None
    zotero_db_path: Path | None = None
    note: str = ""
    error: str = ""
    allow_upload: bool = False


LANE_DAILY_FULL_REPORT = "daily-full-report"
LANE_PERSONALIZED = "most-relevant"
LANE_FRONTIER_SIGNALS = "other-frontier-signals"
LANE_OPTIONS = (
    LANE_DAILY_FULL_REPORT,
    LANE_PERSONALIZED,
    LANE_FRONTIER_SIGNALS,
)


def _widget_key(*parts: str) -> str:
    return "fc-" + "-".join(parts)


def render_app() -> None:
    runner = FrontierCompassRunner()
    startup_request = _load_startup_request()
    source_options = _daily_source_options(runner, startup_request.selected_source)

    st.set_page_config(page_title="FrontierCompass Daily", layout="wide")
    _inject_styles()

    st.markdown(
        """
        <section class="fc-hero">
          <div class="fc-kicker">FrontierCompass daily reading desk</div>
          <h1>A readable daily field report for the biomedical frontier.</h1>
          <p>Open one page, scan the full field first, then move into your Zotero-aware reading lane and the peripheral signals still worth keeping in view.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    refresh_note = str(st.session_state.pop("fc_zotero_refresh_note", ""))
    if refresh_note:
        if "saved" in refresh_note.lower() or "updated" in refresh_note.lower():
            st.success(refresh_note)
        else:
            st.info(refresh_note)

    control_row = st.columns((1.65, 0.8, 1.2))
    reading_window = control_row[0].date_input(
        "Reading date",
        value=_reading_window_value(startup_request),
        key=_widget_key("home", "requested-date"),
    )
    requested_date, start_date, end_date = _coerce_reading_window(
        reading_window,
        fallback_date=startup_request.requested_date,
    )
    refresh_requested = control_row[1].button(
        "Refresh",
        type="primary",
        use_container_width=True,
        key=_widget_key("home", "refresh"),
    )
    status_slot = control_row[2].empty()

    with st.expander("Advanced compatibility", expanded=False):
        selected_source = st.selectbox(
            "Source override",
            source_options,
            index=_daily_source_index(startup_request.selected_source, source_options),
            format_func=lambda value: _format_source_bundle_label(runner, value),
            key=_widget_key("advanced", "source-bundle"),
        )
        st.caption("Use the Reading date range above. Matching start/end dates stay single-day; different dates become a range run.")
        max_results = st.slider(
            "Display limit",
            min_value=20,
            max_value=120,
            value=_clamp_fetch_limit(startup_request.max_results),
            step=20,
            key=_widget_key("advanced", "display-limit"),
        )
        report_mode = st.selectbox(
            "Report mode",
            options=(DEFAULT_REPORT_MODE, "enhanced"),
            index=0 if startup_request.report_mode == DEFAULT_REPORT_MODE else 1,
            key=_widget_key("advanced", "report-mode"),
        )
        allow_stale_cache = st.checkbox(
            "Allow stale cache fallback",
            value=startup_request.allow_stale_cache,
            key=_widget_key("advanced", "allow-stale-cache"),
        )
        active_bundle = runner.app.resolve_source_bundle(selected_source)
        if selected_source == DEFAULT_REVIEWER_SOURCE:
            st.caption("Using the default public bundle over arXiv and bioRxiv unless you choose an override here.")
        elif active_bundle is not None and active_bundle.description:
            st.caption(active_bundle.description)
        else:
            st.caption(f"Using advanced source override: {format_daily_source_label(selected_source)}.")
        if startup_request.auto_profile_source_note:
            st.caption(startup_request.auto_profile_source_note)
        with st.expander("Bundle presets", expanded=False):
            _render_custom_bundle_manager(runner)

    base_request, window_note = _normalize_ui_request(
        UIStartupRequest(
            selected_source=selected_source,
            requested_date=requested_date,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            report_mode=report_mode,
            profile_source=startup_request.profile_source,
            zotero_export_path=startup_request.zotero_export_path,
            zotero_db_path=startup_request.zotero_db_path,
            zotero_collections=startup_request.zotero_collections,
            fetch_scope=FETCH_SCOPE_DAY_FULL,
            allow_stale_cache=allow_stale_cache,
            skip_initial_load=startup_request.skip_initial_load,
        )
    )
    personalization_state = _build_personalization_state(base_request)

    with st.expander("Personalization", expanded=False):
        st.caption(personalization_state.detail)
        if personalization_state.note:
            st.caption(personalization_state.note)
        if personalization_state.error:
            st.warning(personalization_state.error)

        if personalization_state.allow_upload:
            st.markdown("#### Use Zotero export instead")
            uploaded_export = st.file_uploader(
                "Local CSL JSON export",
                type=["json"],
                key=_widget_key("personalization", "upload"),
            )
            if st.button(
                "Save export snapshot",
                use_container_width=True,
                disabled=uploaded_export is None,
                key=_widget_key("personalization", "save-upload"),
            ):
                try:
                    saved_state = _persist_uploaded_zotero_export(runner, uploaded_export)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["fc_zotero_refresh_note"] = _format_zotero_refresh_notice(saved_state)
                    st.rerun()

        if personalization_state.available_collections:
            selected_collection_values = tuple(
                st.multiselect(
                    "Collections",
                    options=personalization_state.available_collections,
                    default=list(
                        _collection_multiselect_defaults(
                            base_request.zotero_collections,
                            personalization_state.available_collections,
                        )
                    ),
                    help="Leave all collections selected to keep the whole library in play.",
                    key=_widget_key("personalization", "collections"),
                )
            )
            selected_collections = _normalize_collection_selection(
                selected_collection_values,
                personalization_state.available_collections,
            )
            if selected_collections:
                st.caption("Collection scope: " + ", ".join(selected_collections))
            else:
                st.caption("Collection scope: all collections.")
        else:
            selected_collections = ()

        if personalization_state.zotero_db_path is not None:
            st.caption(f"Configured live DB: {personalization_state.zotero_db_path}")
        if personalization_state.zotero_export_path is not None:
            st.caption(f"Reusable export: {personalization_state.zotero_export_path}")

    active_request = replace(base_request, zotero_collections=selected_collections)
    status_slot.markdown(
        _build_personalization_status_markup(
            personalization_state,
            selected_collections=selected_collections,
        ),
        unsafe_allow_html=True,
    )

    if window_note:
        st.warning(window_note)
    st.caption(f"Current reading window: {active_request.request_label}")

    if _should_skip_initial_load(active_request, force_refresh=refresh_requested):
        active_session = None
        active_error = (
            "The UI was opened without a prewarmed run after a CLI prewarm failure. "
            "Change the date or use Refresh to retry."
        )
    else:
        active_session, active_error = _resolve_active_session(
            runner,
            request=active_request,
            force_refresh=refresh_requested,
        )

    if active_session is None:
        if active_request.skip_initial_load:
            st.warning(active_error)
        else:
            st.error(
                "Unable to load the current run for "
                f"{active_request.selected_source} ({active_request.request_label}): "
                f"{active_error or 'no cache or fetch result available.'}"
            )
        _render_missing_session_shell(active_request, active_error)
        return

    digest = active_session.digest
    cache_path = str(active_session.cache_path)
    report_path = str(active_session.report_path)
    recent_runs = active_session.recent_history
    recent_runs_error = active_session.recent_history_error

    summary = build_daily_run_summary(
        digest,
        cache_path=cache_path,
        report_path=report_path,
        display_source=active_session.display_source,
    )
    frontier_report = digest.frontier_report
    frontier_source_stats = frontier_report.source_run_stats if frontier_report is not None else summary.source_run_stats
    frontier_source_text = _format_source_stats_text(frontier_source_stats)
    frontier_source_count_text = _format_source_mix_counts(
        frontier_source_stats,
        frontier_report.source_counts if frontier_report is not None else getattr(summary, "source_counts", {}),
    )
    frontier_timings = frontier_report.run_timings if frontier_report is not None else summary.run_timings
    frontier_timings_text = _format_run_timings_text(frontier_timings)
    profile_inspector_lines = build_profile_inspector_lines(digest.profile)
    category_labels = summarize_category_counts(summary.searched_categories, summary.per_category_counts)

    if active_session.fetch_error:
        st.warning(f"Fresh source fetch failed: {active_session.fetch_error}")

    short_notice = f"{active_session.fetch_status_label.capitalize()} for {summary.mode_label or summary.category}."
    if active_session.display_source == DISPLAY_SOURCE_FRESH and summary.total_fetched <= 0:
        st.warning(
            "Fresh fetch completed, but no papers matched "
            f"{summary.requested_date.isoformat()} for {summary.mode_label or summary.category}."
        )
    elif active_session.display_source == DISPLAY_SOURCE_FRESH:
        st.success(short_notice)
    elif active_session.display_source in (DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE, DISPLAY_SOURCE_REUSED_STALE_CACHE):
        st.warning(short_notice)
    else:
        st.info(short_notice)
    st.caption(
        " | ".join(
            (
                f"Requested {summary.requested_date.isoformat()}",
                f"Showing {summary.effective_date.isoformat()}",
                f"Source mix {frontier_source_count_text or 'n/a'}",
                f"Profile {active_session.profile_basis_label}",
            )
        )
    )
    zero_result_guidance = _build_zero_result_guidance(
        active_session,
        selected_source=active_request.selected_source,
    )
    if zero_result_guidance:
        _render_guidance_panel(
            "No papers matched this day",
            zero_result_guidance,
            kicker="Availability note",
        )

    reviewer_source = filter_ranked_papers(digest.ranked, sort_mode="score")
    reviewer_shortlist, _reviewer_shortlist_title = build_reviewer_shortlist(
        reviewer_source,
        max_items=min(max(len(digest.ranked), 1), 6),
        recommended_threshold=DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    )
    reviewer_cards = build_ranked_paper_cards(reviewer_shortlist, profile=digest.profile)
    top_cards = _top_recommendation_cards(reviewer_cards)
    top_abstract_url = next((card.url for card in top_cards if card.url), "")
    brief = build_daily_brief(digest.profile, reviewer_shortlist, total_ranked=len(digest.ranked))
    exploration_picks = resolve_daily_exploration_picks(digest)
    exploration_cards = build_exploration_cards(
        exploration_picks,
        ranked_pool=digest.ranked,
        profile=digest.profile,
        policy=digest.exploration_policy,
    )
    lead_frontier_url = ""
    if frontier_report is not None:
        lead_frontier_url = next((item.url for item in frontier_report.field_highlights if item.url), "")

    _render_home_summary_cards(
        summary=summary,
        frontier_report=frontier_report,
        brief=brief,
        reviewer_cards=reviewer_cards,
        exploration_cards=exploration_cards,
        personalization_state=personalization_state,
    )
    with st.container(border=True):
        st.markdown(
            (
                '<section class="fc-reader-shell">'
                '<div class="fc-lane-kicker">Reader card</div>'
                '<h3>Keep one reading surface open and switch lanes inside it.</h3>'
                '<p>The top cards stay as a dashboard summary. Use the tabs on this card to move between the field-wide report, your personalized shortlist, and the frontier-adjacent signals.</p>'
                "</section>"
            ),
            unsafe_allow_html=True,
        )
        reader_tabs = st.tabs(
            (
                "Daily Full Report",
                "Most Relevant to Your Zotero",
                "Other Frontier Signals",
            )
        )
        with reader_tabs[0]:
            _render_daily_full_report_lane(
                summary=summary,
                frontier_report=frontier_report,
                frontier_source_count_text=frontier_source_count_text,
                lead_frontier_url=lead_frontier_url,
                report_path=report_path,
                cache_path=cache_path,
            )
        with reader_tabs[1]:
            _render_personalized_lane(
                digest=digest,
                brief=brief,
                reviewer_cards=reviewer_cards,
                top_cards=top_cards,
                top_abstract_url=top_abstract_url,
                report_path=report_path,
            )
        with reader_tabs[2]:
            _render_frontier_signals_lane(
                digest=digest,
                summary=summary,
                frontier_report=frontier_report,
                exploration_cards=exploration_cards,
            )

    with st.expander("Full ranked pool", expanded=False):
        ranked_cards = build_ranked_paper_cards(
            filter_ranked_papers(
                digest.ranked,
                max_items=min(max(len(digest.ranked), 1), 24),
                sort_mode="score",
            ),
            profile=digest.profile,
        )
        if ranked_cards:
            _render_ranked_cards(ranked_cards)
        else:
            st.info("No ranked papers are available for this run.")

    current_eml_path = eml_path_for_report_artifact(report_path)
    with st.expander("History and provenance", expanded=False):
        st.caption(_display_source_notice(active_session))
        overview_row = st.columns(4)
        overview_row[0].metric("Display source", display_source_label(summary.display_source))
        overview_row[1].metric("Profile basis", active_session.profile_basis_label)
        overview_row[2].metric("Report mode", summary.report_mode)
        overview_row[3].metric("Cost mode", format_cost_mode_label(summary.cost_mode))
        artifact_columns = st.columns(3)
        with artifact_columns[0]:
            if not _render_artifact_action(
                "Open current HTML report",
                report_path,
                missing_label="Current HTML report is missing for this run.",
                key="current-report-link",
                use_container_width=True,
            ):
                st.caption("Current HTML report is missing for this run.")
        with artifact_columns[1]:
            if not _render_artifact_action(
                "Open current cache JSON",
                cache_path,
                missing_label="Current cache JSON is missing for this run.",
                key="current-cache-link",
                use_container_width=True,
            ):
                st.caption("Current cache JSON is missing for this run.")
        with artifact_columns[2]:
            if not _render_artifact_action(
                "Open current .eml",
                current_eml_path if current_eml_path.exists() else None,
                missing_label="No .eml generated for this run.",
                key="current-eml-link",
                use_container_width=True,
            ):
                st.caption("No .eml generated for this run.")
        if recent_runs_error:
            st.info(f"Recent-run history is unavailable: {recent_runs_error}")
        else:
            st.markdown("### Recent runs")
            _render_recent_runs(recent_runs)

    with st.expander("Runtime and compatibility", expanded=False):
        if frontier_source_text:
            st.caption(f"Source stats: {frontier_source_text}")
        if frontier_timings_text:
            st.caption(f"Run timings: {frontier_timings_text}")
        if category_labels:
            st.caption("Category counts: " + " | ".join(category_labels))
        st.caption(
            "LLM: "
            + format_llm_summary(
                llm_requested=summary.llm_requested,
                llm_applied=summary.llm_applied,
                llm_provider=summary.llm_provider,
            )
        )
        st.caption(f"Requested report mode: {summary.requested_report_mode}.")
        st.caption(f"LLM requested: {format_llm_bool(summary.llm_requested)}.")
        st.caption(f"LLM applied: {format_llm_bool(summary.llm_applied)}.")
        st.caption(f"LLM provider: {format_llm_provider(summary.llm_provider)}.")
        st.caption(f"LLM time: {format_llm_seconds(summary.llm_seconds)}.")
        if recent_runs:
            entry = recent_runs[0]
            if isinstance(entry, RunHistoryEntry):
                st.caption(format_history_llm_provenance_text(entry))
                if entry.is_compatibility_entry:
                    st.caption(format_history_compatibility_text(entry))
        if profile_inspector_lines:
            st.markdown("### Profile details")
            for line in profile_inspector_lines:
                st.caption(line)


def _write_selected_digest(
    runner: FrontierCompassRunner,
    *,
    selected_source: str,
    requested_date: date,
    start_date: date | None = None,
    end_date: date | None = None,
    max_results: int,
    report_mode: str = DEFAULT_REPORT_MODE,
    profile_source: str | None = None,
    zotero_export_path: Path | None = None,
    zotero_db_path: Path | None = None,
    zotero_collections: tuple[str, ...] = (),
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    allow_stale_cache: bool = True,
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
) -> LocalUISession:
    return runner.prepare_ui_session(
        **_build_prepare_ui_session_kwargs(
            source=selected_source,
            requested_date=requested_date,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            refresh=True,
            allow_stale_cache=allow_stale_cache,
            report_mode=report_mode,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
        )
    )


def _build_prepare_ui_session_kwargs(
    *,
    source: str,
    requested_date: date,
    start_date: date | None,
    end_date: date | None,
    max_results: int,
    refresh: bool,
    allow_stale_cache: bool,
    report_mode: str,
    profile_source: str | None,
    zotero_export_path: Path | None,
    zotero_db_path: Path | None,
    zotero_collections: tuple[str, ...],
    fetch_scope: str,
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "source": source,
        "requested_date": requested_date,
        "max_results": max_results,
        "refresh": refresh,
        "allow_stale_cache": allow_stale_cache,
        "report_mode": report_mode,
        "zotero_export_path": zotero_export_path,
    }
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if profile_source is not None:
        kwargs["profile_source"] = profile_source
    if zotero_db_path is not None:
        kwargs["zotero_db_path"] = zotero_db_path
    if zotero_collections:
        kwargs["zotero_collections"] = tuple(zotero_collections)
    if fetch_scope != FETCH_SCOPE_DAY_FULL:
        kwargs["fetch_scope"] = fetch_scope
    if llm_provider is not None:
        kwargs["llm_provider"] = llm_provider
    if llm_base_url is not None:
        kwargs["llm_base_url"] = llm_base_url
    if llm_api_key is not None:
        kwargs["llm_api_key"] = llm_api_key
    if llm_model is not None:
        kwargs["llm_model"] = llm_model
    return kwargs


def _resolve_active_session(
    runner: FrontierCompassRunner,
    *,
    request: UIStartupRequest,
    force_refresh: bool,
) -> tuple[LocalUISession | None, str]:
    request_key = request.request_key
    if not force_refresh and st.session_state.get("fc_digest_request_key") == request_key:
        cached_result = st.session_state.get("fc_ui_session")
        cached_error = str(st.session_state.get("fc_digest_error", ""))
        if isinstance(cached_result, LocalUISession) or cached_result is None:
            return cached_result, cached_error

    spinner_label = (
        f"Refreshing {request.selected_source} for {request.request_label}..."
        if force_refresh
        else f"Loading {request.selected_source} for {request.request_label}..."
    )
    try:
        with st.spinner(spinner_label):
            if force_refresh:
                result = _write_selected_digest(
                    runner,
                    selected_source=request.selected_source,
                    requested_date=request.requested_date,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    max_results=request.max_results,
                    report_mode=request.report_mode,
                    profile_source=request.profile_source,
                    zotero_export_path=request.zotero_export_path,
                    zotero_db_path=request.zotero_db_path,
                    zotero_collections=request.zotero_collections,
                    fetch_scope=request.fetch_scope,
                    allow_stale_cache=request.allow_stale_cache,
                    llm_provider=request.llm_provider,
                    llm_base_url=request.llm_base_url,
                    llm_api_key=request.llm_api_key,
                    llm_model=request.llm_model,
                )
            else:
                result = runner.prepare_ui_session(
                    **_build_prepare_ui_session_kwargs(
                        source=request.selected_source,
                        requested_date=request.requested_date,
                        start_date=request.start_date,
                        end_date=request.end_date,
                        max_results=request.max_results,
                        refresh=False,
                        allow_stale_cache=request.allow_stale_cache,
                        report_mode=request.report_mode,
                        profile_source=request.profile_source,
                        zotero_export_path=request.zotero_export_path,
                        zotero_db_path=request.zotero_db_path,
                        zotero_collections=request.zotero_collections,
                        fetch_scope=request.fetch_scope,
                        llm_provider=request.llm_provider,
                        llm_base_url=request.llm_base_url,
                        llm_api_key=request.llm_api_key,
                        llm_model=request.llm_model,
                    )
                )
    except Exception as exc:  # pragma: no cover - exercised via Streamlit tests/manual use.
        error_message = str(exc)
        st.session_state["fc_digest_request_key"] = request_key
        st.session_state["fc_ui_session"] = None
        st.session_state["fc_digest_error"] = error_message
        return None, error_message

    st.session_state["fc_digest_request_key"] = request_key
    st.session_state["fc_ui_session"] = result
    st.session_state["fc_digest_error"] = ""
    return result, ""


def _display_source_notice(session: LocalUISession) -> str:
    digest = session.digest
    base = (
        f"Status: {session.fetch_status_label}. Window {digest.request_window.label}; "
        f"showing {digest.effective_display_date.isoformat()}; fetch scope {digest.fetch_scope}; "
        f"profile basis {session.profile_basis_label}; Frontier Report mode {digest.report_mode}; "
        f"cost mode {digest.cost_mode}."
    )
    if session.display_source == DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE:
        return f"{base} Fresh fetch failed, so this page is reusing the same-date cached digest instead."
    if session.display_source == DISPLAY_SOURCE_REUSED_STALE_CACHE:
        source_requested = (
            digest.stale_cache_source_requested_date.isoformat()
            if digest.stale_cache_source_requested_date is not None
            else "unknown"
        )
        source_effective = (
            digest.stale_cache_source_effective_date.isoformat()
            if digest.stale_cache_source_effective_date is not None
            else "unknown"
        )
        return (
            f"{base} Fresh fetch failed, so this page is reusing an older compatible cached digest "
            f"from requested {source_requested} showing {source_effective}."
        )
    if session.display_source == DISPLAY_SOURCE_RANGE_AGGREGATED:
        return (
            f"{base} This range artifact was materialized by iterating day-level runs and reusing day caches "
            "when they were already available."
        )
    return base


def _build_zero_result_guidance(
    session: LocalUISession,
    *,
    selected_source: str,
) -> tuple[str, ...]:
    if session.total_fetched > 0:
        return ()

    digest = session.digest
    requested_date = digest.requested_target_date.isoformat()
    lines = [
        f"No papers matched {requested_date} in the current source contract.",
    ]
    if session.fetch_error:
        lines.append(f"Latest fetch warning: {session.fetch_error}")
    if selected_source == DEFAULT_REVIEWER_SOURCE:
        lines.append(
            "Try a nearby date, widen the Reading date range above, or inspect the source notes below before assuming the app is broken."
        )
    else:
        lines.append("Try a nearby date or widen the Reading date range above.")

    for row in digest.source_run_stats[:3]:
        detail = str(row.note or row.error or "").strip()
        if not detail:
            detail = f"{format_source_outcome_label(row.outcome)} / {row.status or 'unknown'}"
        lines.append(f"{format_source_label(row.source)}: {detail}")
    return tuple(lines)


def _render_guidance_panel(title: str, lines: Sequence[str], *, kicker: str = "") -> None:
    if not lines:
        return
    kicker_html = f'<div class="fc-guidance-kicker">{escape(kicker)}</div>' if kicker else ""
    list_items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    st.markdown(
        (
            '<section class="fc-guidance">'
            f"{kicker_html}"
            f"<h3>{escape(title)}</h3>"
            f"<ul>{list_items}</ul>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _normalize_ui_request(request: UIStartupRequest) -> tuple[UIStartupRequest, str]:
    requested_date, start_date, end_date, fetch_scope = normalize_request_window_inputs(
        requested_date=request.requested_date,
        start_date=request.start_date,
        end_date=request.end_date,
        fetch_scope=request.fetch_scope,
    )
    normalized_request = replace(
        request,
        requested_date=requested_date,
        start_date=start_date,
        end_date=end_date,
        fetch_scope=fetch_scope,
    )
    if (
        request.fetch_scope == FETCH_SCOPE_RANGE_FULL
        and request.start_date is not None
        and request.end_date is not None
        and request.start_date > request.end_date
    ):
        return (
            normalized_request,
            "Date range was reordered so the earlier date stays first: "
            f"{start_date.isoformat()} -> {end_date.isoformat()}.",
        )
    return normalized_request, ""


def _should_skip_initial_load(request: UIStartupRequest, *, force_refresh: bool) -> bool:
    if force_refresh or not request.skip_initial_load:
        return False
    cached_request_key = st.session_state.get("fc_digest_request_key")
    cached_session = st.session_state.get("fc_ui_session")
    return not (
        cached_request_key == request.request_key
        and isinstance(cached_session, LocalUISession)
    )


def _render_artifact_action(
    label: str,
    path: str | Path | None,
    *,
    missing_label: str,
    help: str | None = None,
    key: str | None = None,
    type: str | None = None,
    use_container_width: bool | None = None,
) -> bool:
    artifact_url = build_existing_local_file_url(path)
    if not artifact_url:
        return False
    return render_external_link(
        label,
        artifact_url,
        help=help or missing_label,
        key=key,
        type=type,
        use_container_width=use_container_width,
    )


def _render_missing_session_shell(request: UIStartupRequest, error_message: str) -> None:
    st.markdown(
        (
            '<section class="fc-empty-shell">'
            '<div class="fc-kicker">Daily report unavailable</div>'
            '<h2>The page is ready, but this run could not be loaded yet.</h2>'
            '<p>Keep the date as-is for a cache-first retry, use Refresh for a fresh fetch, or widen the request window if this day looks sparse upstream. '
            'History and runtime details stay available below.</p>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(
            (
                '<section class="fc-reader-shell">'
                '<div class="fc-lane-kicker">Reader card</div>'
                '<h3>The reader card is ready, but this run has no materialized content yet.</h3>'
                '<p>Once a cache or fresh run is available, this card will switch between the daily field report, the personalized lane, and the frontier-adjacent signals.</p>'
                "</section>"
            ),
            unsafe_allow_html=True,
        )
        reader_tabs = st.tabs(
            (
                "Daily Full Report",
                "Most Relevant to Your Zotero",
                "Other Frontier Signals",
            )
        )
        for tab, body in zip(
            reader_tabs,
            (
                "No field-wide daily report is available yet.",
                "No shortlist is loaded yet.",
                "No exploration lane is available yet.",
            ),
            strict=True,
        ):
            with tab:
                st.markdown(
                    (
                        '<div class="fc-empty-panel">'
                        f"<strong>{escape(body)}</strong><br>"
                        f"{escape(error_message or 'No cache or fetch result is available yet.')}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
    with st.expander("History and provenance", expanded=False):
        st.caption(f"Requested window: {request.request_label}")
        st.caption(error_message or "No cache or fetch result is available yet.")


def _load_startup_request(argv: Sequence[str] | None = None) -> UIStartupRequest:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="")
    parser.add_argument("--no-config", action="store_true")
    parser.add_argument("--source", default="")
    parser.add_argument("--requested-date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--max-results", type=int, default=0)
    parser.add_argument("--report-mode", default="")
    parser.add_argument("--profile-source", default="")
    parser.add_argument("--zotero-export", default="")
    parser.add_argument("--zotero-db-path", default="")
    parser.add_argument("--zotero-collection", action="append", default=[])
    parser.add_argument("--fetch-scope", choices=FETCH_SCOPE_OPTIONS, default="")
    parser.add_argument("--skip-initial-load", action="store_true")
    stale_cache_group = parser.add_mutually_exclusive_group()
    stale_cache_group.add_argument("--allow-stale-cache", dest="allow_stale_cache", action="store_true")
    stale_cache_group.add_argument("--no-stale-cache", dest="allow_stale_cache", action="store_false")
    parser.set_defaults(allow_stale_cache=None)

    parsed, _ = parser.parse_known_args(list(argv) if argv is not None else sys.argv[1:])
    loaded_defaults = load_user_defaults(
        config_path=str(parsed.config).strip() or None,
        use_config=not bool(parsed.no_config),
    )

    requested_date = _parse_startup_date(str(parsed.requested_date or ""))
    start_date = _parse_startup_optional_date(str(parsed.start_date or ""))
    end_date = _parse_startup_optional_date(str(parsed.end_date or ""))
    fetch_scope = str(parsed.fetch_scope or FETCH_SCOPE_DAY_FULL).strip() or FETCH_SCOPE_DAY_FULL

    selection = resolve_default_profile_selection(
        profile_source=str(parsed.profile_source).strip() or None,
        explicit_zotero_export_path=Path(parsed.zotero_export) if parsed.zotero_export else None,
        explicit_zotero_db_path=Path(parsed.zotero_db_path) if parsed.zotero_db_path else None,
        default_zotero_export_path=loaded_defaults.defaults.default_zotero_export_path,
        default_zotero_db_path=loaded_defaults.defaults.default_zotero_db_path,
        reusable_zotero_export_path=DEFAULT_ZOTERO_EXPORT_PATH,
    )

    default_report_mode = loaded_defaults.defaults.default_report_mode or DEFAULT_REPORT_MODE
    default_max_results = loaded_defaults.defaults.default_max_results or 80
    default_allow_stale_cache = (
        loaded_defaults.defaults.default_allow_stale_cache
        if loaded_defaults.defaults.default_allow_stale_cache is not None
        else True
    )
    llm_settings = resolve_frontier_report_llm_settings(
        base_url=loaded_defaults.defaults.default_llm_base_url,
        api_key=loaded_defaults.defaults.default_llm_api_key,
        model=loaded_defaults.defaults.default_llm_model,
    )

    request, _ = _normalize_ui_request(
        UIStartupRequest(
            selected_source=str(parsed.source).strip() or loaded_defaults.defaults.default_mode or DEFAULT_REVIEWER_SOURCE,
            requested_date=requested_date,
            max_results=max(int(parsed.max_results or default_max_results), 1),
            start_date=start_date,
            end_date=end_date,
            report_mode=str(parsed.report_mode).strip() or default_report_mode,
            profile_source=str(parsed.profile_source).strip() or None,
            zotero_export_path=selection.zotero_export_path,
            zotero_db_path=selection.zotero_db_path,
            zotero_collections=_normalize_text_selection(parsed.zotero_collection),
            fetch_scope=fetch_scope,
            allow_stale_cache=default_allow_stale_cache if parsed.allow_stale_cache is None else bool(parsed.allow_stale_cache),
            skip_initial_load=bool(parsed.skip_initial_load),
            llm_provider=llm_settings.provider_label,
            llm_base_url=llm_settings.base_url,
            llm_api_key=llm_settings.api_key,
            llm_model=llm_settings.model,
        )
    )
    return request


def _parse_startup_date(value: str) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()


def _parse_startup_optional_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _daily_source_options(runner: FrontierCompassRunner, selected_source: str) -> tuple[str, ...]:
    options = tuple(bundle.bundle_id for bundle in runner.app.available_source_bundles())
    if selected_source in options:
        return options
    if not selected_source:
        return options or DAILY_SOURCE_OPTIONS
    return (*options, selected_source)


def _format_source_bundle_label(runner: FrontierCompassRunner, selected_source: str) -> str:
    bundle = runner.app.resolve_source_bundle(selected_source)
    if bundle is not None:
        return bundle.label
    return format_daily_source_label(selected_source)


def _daily_source_index(selected_source: str, options: Sequence[str]) -> int:
    try:
        return options.index(selected_source)
    except ValueError:
        return 0


def _clamp_fetch_limit(value: int) -> int:
    return min(max(int(value), 20), 120)


def _normalize_text_selection(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        canonical = text.lower()
        if not text or canonical in seen:
            continue
        normalized.append(text)
        seen.add(canonical)
    return tuple(normalized)


def _selected_collection_defaults(
    selected_collections: Sequence[str],
    available_collections_: Sequence[str],
) -> tuple[str, ...]:
    available_lookup = {collection.lower(): collection for collection in available_collections_}
    defaults: list[str] = []
    seen: set[str] = set()
    for collection in selected_collections:
        normalized = str(collection).strip().lower()
        if not normalized or normalized not in available_lookup or normalized in seen:
            continue
        defaults.append(available_lookup[normalized])
        seen.add(normalized)
    return tuple(defaults)


def _collection_multiselect_defaults(
    selected_collections: Sequence[str],
    available_collections_: Sequence[str],
) -> tuple[str, ...]:
    defaults = _selected_collection_defaults(selected_collections, available_collections_)
    if defaults:
        return defaults
    return tuple(available_collections_)


def _normalize_collection_selection(
    selected_collections: Sequence[str],
    available_collections_: Sequence[str],
) -> tuple[str, ...]:
    normalized = _selected_collection_defaults(selected_collections, available_collections_)
    if not normalized or len(normalized) == len(tuple(available_collections_)):
        return ()
    return normalized


def _build_personalization_state(request: UIStartupRequest) -> PersonalizationPanelState:
    effective_source = request.effective_profile_source
    if effective_source == PROFILE_SOURCE_LIVE_ZOTERO_DB and request.zotero_db_path is not None:
        try:
            items = load_sqlite_library(request.zotero_db_path)
        except ValueError as exc:
            return PersonalizationPanelState(
                active=False,
                profile_source=PROFILE_SOURCE_BASELINE,
                label="Personalization off",
                detail="The configured live Zotero DB is unavailable. You can still switch to a saved export.",
                zotero_db_path=request.zotero_db_path,
                error=str(exc),
                allow_upload=True,
            )
        collections = available_collections(items)
        collection_text = f" across {len(collections)} collections" if collections else ""
        return PersonalizationPanelState(
            active=True,
            profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
            label="Personalization on",
            detail=f"Live Zotero DB active: {len(items)} items{collection_text}.",
            available_collections=collections,
            item_count=len(items),
            zotero_db_path=request.zotero_db_path,
            note="Collections are scanned directly from the configured Zotero library on load.",
        )

    if effective_source == PROFILE_SOURCE_ZOTERO_EXPORT and request.zotero_export_path is not None:
        try:
            items = load_csl_json_export(request.zotero_export_path)
        except ValueError as exc:
            return PersonalizationPanelState(
                active=False,
                profile_source=PROFILE_SOURCE_BASELINE,
                label="Personalization off",
                detail="The saved Zotero export could not be read. Upload a fresh CSL JSON export to continue.",
                zotero_export_path=request.zotero_export_path,
                error=str(exc),
                allow_upload=True,
            )
        collections = available_collections(items)
        collection_text = f" across {len(collections)} collections" if collections else ""
        return PersonalizationPanelState(
            active=True,
            profile_source=PROFILE_SOURCE_ZOTERO_EXPORT,
            label="Personalization on",
            detail=f"Reusable Zotero export active: {len(items)} items{collection_text}.",
            available_collections=collections,
            item_count=len(items),
            zotero_export_path=request.zotero_export_path,
            note="Upload a new export here whenever you want to replace the saved local snapshot.",
            allow_upload=True,
        )

    return PersonalizationPanelState(
        active=False,
        profile_source=PROFILE_SOURCE_BASELINE,
        label="Personalization off",
        detail="Baseline reading only. Configure `default_zotero_db_path` once or save a CSL JSON export to personalize the shortlist.",
        allow_upload=True,
    )


def _build_personalization_status_markup(
    state: PersonalizationPanelState,
    *,
    selected_collections: Sequence[str],
) -> str:
    if state.active:
        source_label = "Live Zotero DB" if state.profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB else "Zotero export"
        collection_label = "All collections" if not selected_collections else f"{len(selected_collections)} collections"
        return (
            '<div class="fc-status fc-status-active">'
            '<div class="fc-status-title">Personalization on</div>'
            f'<div class="fc-status-text">{escape(source_label)} · {escape(collection_label)}</div>'
            "</div>"
        )
    return (
        '<div class="fc-status fc-status-idle">'
        '<div class="fc-status-title">Personalization off</div>'
        f'<div class="fc-status-text">{escape(state.detail)}</div>'
        "</div>"
    )


def _persist_uploaded_zotero_export(runner: FrontierCompassRunner, uploaded_file: Any) -> ZoteroLibraryState:
    if uploaded_file is None:
        raise ValueError("Choose a Zotero export before saving the snapshot.")
    payload = uploaded_file.getvalue()
    if not payload:
        raise ValueError("The uploaded Zotero export is empty.")

    export_path = runner.app.zotero_export_path
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_bytes(payload)

    try:
        items = load_csl_json_export(export_path)
    except ValueError as exc:
        export_path.unlink(missing_ok=True)
        raise ValueError(f"Invalid Zotero export: {exc}") from exc

    state = ZoteroLibraryState(
        export_path=export_path,
        status_path=runner.app.zotero_status_path,
        discovered_db_path=None,
        collections=available_collections(items),
        item_count=len(items),
        generated_at=datetime.now(timezone.utc),
        status="ready",
        note=f"Saved local Zotero export snapshot from {getattr(uploaded_file, 'name', 'upload')}.",
        candidate_db_paths=(),
    )
    write_local_zotero_state(state)
    return state


def _format_zotero_refresh_notice(state: ZoteroLibraryState) -> str:
    if state.ready and not state.error:
        collection_text = f" across {len(state.collections)} collections" if state.collections else ""
        return f"Saved Zotero export snapshot with {state.item_count} items{collection_text}."
    if state.ready:
        return f"Reusing existing Zotero export. {state.error or state.note}".strip()
    return f"Zotero export is still unavailable. {state.error or state.note}".strip()


def _render_home_summary_cards(
    *,
    summary: object,
    frontier_report: object | None,
    brief: object,
    reviewer_cards: Sequence[object],
    exploration_cards: Sequence[object],
    personalization_state: PersonalizationPanelState,
) -> None:
    daily_note = (
        getattr(frontier_report, "takeaways", ())[:1][0]
        if frontier_report is not None and getattr(frontier_report, "takeaways", ())
        else f"{getattr(summary, 'total_displayed', 0)} surfaced papers are ready to scan."
    )
    if reviewer_cards:
        reviewer_note = reviewer_cards[0].recommendation_summary
    elif personalization_state.active:
        reviewer_note = "No shortlist items cleared the current ranking pass."
    else:
        reviewer_note = "Add a live Zotero DB or export to turn this lane into a personalized shortlist."
    if exploration_cards:
        exploration_note = exploration_cards[0].title
    elif frontier_report is not None:
        exploration_note = "Field highlights are still available even when the exploration lane is quiet."
    else:
        exploration_note = "No secondary frontier signals are available for this run yet."

    cards = (
        (
            "Daily Full Report",
            (
                f"{getattr(frontier_report, 'displayed_highlight_count', 0)} field highlights"
                if frontier_report is not None
                else "No saved report"
            ),
            daily_note,
            "Switch in the reader card below",
        ),
        (
            "Most Relevant to Your Zotero",
            f"{len(reviewer_cards)} shortlist picks",
            reviewer_note,
            "Switch in the reader card below",
        ),
        (
            "Other Frontier Signals",
            f"{len(exploration_cards)} exploration picks",
            exploration_note,
            "Use the reader card below",
        ),
    )
    cards_markup = "".join(
        (
            '<article class="fc-summary-card">'
            '<div class="fc-summary-label">{label}</div>'
            '<div class="fc-summary-value">{value}</div>'
            '<p>{note}</p>'
            '<span class="fc-summary-link">{footer}</span>'
            "</article>"
        ).format(
            label=escape(label),
            value=escape(value),
            note=escape(note),
            footer=escape(footer),
        )
        for label, value, note, footer in cards
    )
    st.markdown(f'<section class="fc-summary-grid">{cards_markup}</section>', unsafe_allow_html=True)


def _reading_window_value(request: UIStartupRequest) -> tuple[date, date]:
    start = request.start_date or request.requested_date
    end = request.end_date or request.requested_date
    return start, end


def _coerce_reading_window(
    value: Any,
    *,
    fallback_date: date,
) -> tuple[date, date, date]:
    if isinstance(value, date):
        return value, value, value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        selected_dates = [item for item in value if isinstance(item, date)]
        if not selected_dates:
            return fallback_date, fallback_date, fallback_date
        if len(selected_dates) == 1:
            return selected_dates[0], selected_dates[0], selected_dates[0]
        return selected_dates[0], selected_dates[0], selected_dates[1]
    return fallback_date, fallback_date, fallback_date


def _render_lane_banner(
    *,
    kicker: str,
    title: str,
    description: str,
    stats: Sequence[tuple[str, str]],
) -> None:
    stat_markup = "".join(
        (
            '<div class="fc-lane-stat">'
            f'<div class="fc-lane-label">{escape(label)}</div>'
            f'<div class="fc-lane-value">{escape(value)}</div>'
            "</div>"
        )
        for label, value in stats
    )
    st.markdown(
        (
            '<section class="fc-lane-shell">'
            f'<div class="fc-lane-kicker">{escape(kicker)}</div>'
            f'<h3>{escape(title)}</h3>'
            f'<p>{escape(description)}</p>'
            f'<div class="fc-lane-grid">{stat_markup}</div>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_daily_full_report_lane(
    *,
    summary: object,
    frontier_report: object | None,
    frontier_source_count_text: str,
    lead_frontier_url: str,
    report_path: Path | None,
    cache_path: Path | None,
) -> None:
    st.markdown("## Daily Full Report")
    st.caption(
        "Start here when you want the non-personalized read on the day: what moved across the field, which sources contributed, and which original papers deserve the first click."
    )
    _render_lane_banner(
        kicker="Field report",
        title="Read the full field before you narrow it down.",
        description=(
            "This lane stays independent from Zotero. It keeps the broader conversation visible through source composition, repeated themes, method hotspots, and a compact set of notable papers."
        ),
        stats=(
            ("Window", getattr(summary, "request_window").label),
            ("Highlights", str(frontier_report.displayed_highlight_count if frontier_report is not None else 0)),
            ("Fetched", str(getattr(summary, "total_fetched", 0))),
            ("Runtime", format_runtime_status(getattr(summary, "report_mode", DEFAULT_REPORT_MODE), getattr(summary, "cost_mode", "zero-token"))),
        ),
    )
    daily_actions = st.columns((1.1, 1.1, 1.0))
    with daily_actions[0]:
        if not render_external_link(
            "Open lead field paper",
            lead_frontier_url,
            help="Open the first linked paper from today's field-wide highlights.",
            type="primary",
            use_container_width=True,
        ):
            st.caption("No linked highlight is available for the current field-wide lead.")
    with daily_actions[1]:
        if not _render_artifact_action(
            "Open saved HTML report",
            report_path,
            missing_label="Current HTML report is missing for this run.",
            use_container_width=True,
        ):
            st.caption("Current HTML report is missing for this run.")
    with daily_actions[2]:
        if not _render_artifact_action(
            "Open current cache JSON",
            cache_path,
            missing_label="Current cache JSON is missing for this run.",
            use_container_width=True,
        ):
            st.caption("Current cache JSON is missing for this run.")
    st.caption(
        "Requested sources remain visible here even when a source returns zero same-day papers, so a quiet bioRxiv day does not look like a missing source."
    )
    st.metric("Source composition", frontier_source_count_text or "n/a")

    if frontier_report is None:
        st.warning("This cache predates the saved daily full report contract, so only the shortlist is available below.")
        if getattr(summary, "report_error", ""):
            st.caption(getattr(summary, "report_error", ""))
        return

    if frontier_report.takeaways:
        with st.container(border=True):
            for takeaway in frontier_report.takeaways:
                st.markdown(f"- {takeaway}")
    signal_row = st.columns(3)
    signal_row[0].markdown("**Repeated themes**")
    signal_row[1].markdown("**Method hotspots**")
    signal_row[2].markdown("**Adjacent signals**")
    with signal_row[0]:
        _render_signal_row(frontier_report.repeated_themes)
    with signal_row[1]:
        _render_signal_row(frontier_report.salient_topics)
    with signal_row[2]:
        _render_signal_row(frontier_report.adjacent_themes)
    st.markdown("### Notable highlights")
    _render_frontier_highlights(tuple(frontier_report.field_highlights[:4]))


def _render_personalized_lane(
    *,
    digest: object,
    brief: object,
    reviewer_cards: Sequence[object],
    top_cards: Sequence[object],
    top_abstract_url: str,
    report_path: Path | None,
) -> None:
    st.markdown("## Most Relevant to Your Zotero")
    st.caption(
        "The shortlist stays intentionally human-sized: a few papers, why they matched, and the fastest next click."
    )
    _render_lane_banner(
        kicker="Reading lane",
        title="Open the papers most likely to matter first.",
        description=(
            "This lane reuses the existing ranking explanations, but trims the surface to a short reading list instead of a filter-heavy browser."
        ),
        stats=(
            ("Shortlist", str(len(reviewer_cards))),
            ("Recommended", str(getattr(brief, "recommended_count", 0))),
            ("Profile", getattr(getattr(digest, "profile", None), "profile_source_label", "n/a")),
        ),
    )
    if brief.takeaways:
        with st.container(border=True):
            for takeaway in brief.takeaways:
                st.markdown(f"- {takeaway}")
    recommendation_actions = st.columns(2)
    with recommendation_actions[0]:
        if not render_external_link(
            "Open top abstract",
            top_abstract_url,
            help="Open the strongest read-first abstract for this run.",
            type="primary",
            use_container_width=True,
        ):
            st.caption("No abstract link is available for the current lead recommendation.")
    with recommendation_actions[1]:
        if not _render_artifact_action(
            "Open saved HTML report",
            report_path,
            missing_label="Current HTML report is missing for this run.",
            use_container_width=True,
        ):
            st.caption("Current HTML report is missing for this run.")
    if top_cards:
        _render_top_recommendations(top_cards)
    else:
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                "<strong>No personalized shortlist is available for this run.</strong><br>"
                "Try a nearby date, widen the Reading date range above, review the availability note, or add a Zotero export in the Personalization panel."
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def _render_frontier_signals_lane(
    *,
    digest: object,
    summary: object,
    frontier_report: object | None,
    exploration_cards: Sequence[object],
) -> None:
    st.markdown("## Other Frontier Signals")
    st.caption("These are the ideas worth keeping in peripheral vision even when they are not core to your library.")
    _render_lane_banner(
        kicker="Peripheral view",
        title="Keep one eye on the edges of the frontier.",
        description=(
            "Exploration picks and adjacent highlights stay separate from the main shortlist so curiosity does not crowd out the first reading pass."
        ),
        stats=(
            ("Exploration picks", str(len(exploration_cards))),
            ("Fetched", str(getattr(summary, "total_fetched", 0))),
            ("Displayed", str(getattr(summary, "total_displayed", 0))),
        ),
    )
    if exploration_cards:
        st.markdown("### Exploration picks")
        st.caption(daily_exploration_intro(getattr(digest, "profile"), policy=getattr(digest, "exploration_policy", None)))
        _render_exploration_cards(exploration_cards)
    elif frontier_report is not None:
        st.markdown("### Frontier highlights beyond the shortlist")
        _render_frontier_highlights(tuple(frontier_report.field_highlights[:3]))
    else:
        st.info("No secondary frontier signals are available for this run.")


def _parse_bundle_terms(value: str) -> tuple[str, ...]:
    pieces: list[str] = []
    for line in str(value or "").replace("\r", "\n").splitlines():
        for part in line.split(","):
            stripped = part.strip()
            if stripped:
                pieces.append(stripped)
    return _normalize_text_selection(pieces)


def _render_custom_bundle_manager(runner: FrontierCompassRunner) -> None:
    note = str(st.session_state.pop("fc_custom_bundle_note", ""))
    if note:
        st.success(note)

    custom_bundles = runner.app.custom_source_bundles()
    if custom_bundles:
        st.caption("Saved presets: " + ", ".join(bundle.label for bundle in custom_bundles))
    else:
        st.caption("Saved presets live in `configs/source_bundles.json`.")

    create_option = "__create_new_bundle__"
    editor_options = (create_option, *(bundle.bundle_id for bundle in custom_bundles))
    editor_target_key = _widget_key("bundle-manager", "target")
    editor_target = st.selectbox(
        "Preset",
        options=editor_options,
        format_func=lambda value: "Create new preset" if value == create_option else _format_source_bundle_label(runner, value),
        key=editor_target_key,
    )
    editing_bundle = runner.app.resolve_source_bundle(editor_target) if editor_target != create_option else None
    if editing_bundle is not None and editing_bundle.official:
        editing_bundle = None

    name = st.text_input(
        "Preset name",
        value=editing_bundle.label if editing_bundle is not None else "",
        key=_widget_key("bundle-manager", editor_target, "name"),
    )
    enabled_sources = tuple(
        st.multiselect(
            "Enabled sources",
            options=("arxiv", "biorxiv", "medrxiv"),
            default=list(editing_bundle.enabled_sources if editing_bundle is not None else ("arxiv", "biorxiv")),
            key=_widget_key("bundle-manager", editor_target, "sources"),
        )
    )
    description = st.text_input(
        "Short description",
        value=editing_bundle.description if editing_bundle is not None else "",
        key=_widget_key("bundle-manager", editor_target, "description"),
    )
    include_terms = st.text_area(
        "Include terms",
        value="\n".join(editing_bundle.include_terms) if editing_bundle is not None else "",
        key=_widget_key("bundle-manager", editor_target, "include"),
        height=100,
    )
    exclude_terms = st.text_area(
        "Exclude terms",
        value="\n".join(editing_bundle.exclude_terms) if editing_bundle is not None else "",
        key=_widget_key("bundle-manager", editor_target, "exclude"),
        height=100,
    )
    action_columns = st.columns(2)
    save_bundle = action_columns[0].button(
        "Save preset",
        type="primary",
        use_container_width=True,
        key=_widget_key("bundle-manager", editor_target, "save"),
    )
    delete_bundle = action_columns[1].button(
        "Delete preset",
        use_container_width=True,
        disabled=editing_bundle is None,
        key=_widget_key("bundle-manager", editor_target, "delete"),
    )

    if save_bundle:
        try:
            saved_bundle = runner.app.save_custom_source_bundle(
                name=name,
                description=description,
                enabled_sources=enabled_sources,
                include_terms=_parse_bundle_terms(include_terms),
                exclude_terms=_parse_bundle_terms(exclude_terms),
                bundle_id=editing_bundle.bundle_id if editing_bundle is not None else None,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[editor_target_key] = saved_bundle.bundle_id
            st.session_state["fc_custom_bundle_note"] = f"Saved custom preset {saved_bundle.label}."
            st.rerun()

    if delete_bundle and editing_bundle is not None:
        runner.app.remove_custom_source_bundle(editing_bundle.bundle_id)
        st.session_state[editor_target_key] = create_option
        st.session_state["fc_custom_bundle_note"] = f"Deleted custom preset {editing_bundle.label}."
        st.rerun()


def _format_source_label(source: str) -> str:
    labels = {
        "arxiv": "arXiv",
        "biorxiv": "bioRxiv",
        "medrxiv": "medRxiv",
        "multisource": "Multisource",
    }
    normalized = str(source or "").strip().lower()
    return labels.get(normalized, normalized or "unknown")


def _format_source_mix_counts(
    source_run_stats: Sequence[SourceRunStats],
    source_counts: dict[str, int] | None = None,
) -> str:
    source_counts = source_counts or {}
    stats_by_source = {str(item.source).strip().lower(): item for item in source_run_stats if str(item.source).strip()}
    ordered_sources: list[str] = []
    for source in ("arxiv", "biorxiv", "medrxiv"):
        if source in stats_by_source or source in source_counts:
            ordered_sources.append(source)
    remaining_sources = sorted(
        {*(stats_by_source.keys()), *(str(source).strip().lower() for source in source_counts if str(source).strip())}
        - set(ordered_sources)
    )
    ordered_sources.extend(remaining_sources)
    if not ordered_sources:
        return ""
    parts: list[str] = []
    for source in ordered_sources:
        label = _format_source_label(source)
        if source in stats_by_source:
            item = stats_by_source[source]
            parts.append(f"{label} {item.displayed_count} shown / {item.fetched_count} fetched")
        elif source in source_counts:
            parts.append(f"{label} {source_counts[source]}")
    return ", ".join(parts)


def _format_source_stats_text(source_run_stats: Sequence[SourceRunStats]) -> str:
    return " | ".join(_format_source_stats_row(item) for item in source_run_stats)


def _format_source_stats_row(source_run_stat: SourceRunStats) -> str:
    parts = [
        (
            f"{_format_source_label(source_run_stat.source)} fetched {source_run_stat.fetched_count} "
            f"/ retained {source_run_stat.displayed_count}"
        ),
        f"[{format_source_outcome_label(source_run_stat.resolved_outcome)}; {source_run_stat.status}; {source_run_stat.cache_status}]",
    ]
    timing_text = _format_run_timings_text(source_run_stat.timings)
    if timing_text:
        parts.append(f"({timing_text})")
    if source_run_stat.resolved_live_outcome != source_run_stat.resolved_outcome:
        parts.append(f"live={format_source_outcome_label(source_run_stat.resolved_live_outcome)}")
    if source_run_stat.error:
        parts.append(f"error={source_run_stat.error}")
    return " ".join(parts)


def _format_run_timings_text(run_timings: RunTimings) -> str:
    parts: list[str] = []
    if run_timings.cache_seconds is not None:
        parts.append(f"cache {run_timings.cache_seconds:.2f}s")
    if run_timings.network_seconds is not None:
        parts.append(f"network {run_timings.network_seconds:.2f}s")
    if run_timings.parse_seconds is not None:
        parts.append(f"parse {run_timings.parse_seconds:.2f}s")
    if run_timings.rank_seconds is not None:
        parts.append(f"rank {run_timings.rank_seconds:.2f}s")
    if run_timings.report_seconds is not None:
        parts.append(f"report {run_timings.report_seconds:.2f}s")
    if run_timings.total_seconds is not None:
        parts.append(f"total {run_timings.total_seconds:.2f}s")
    return " | ".join(parts)


def _render_recent_runs(entries: Sequence[object]) -> None:
    if not entries:
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                "<strong>No recent runs found under data/cache.</strong><br>"
                "Run a digest once and this lane will turn into an artifact log."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        return

    current_entries = [entry for entry in entries if not getattr(entry, "is_compatibility_entry", False)]
    compatibility_entries = [entry for entry in entries if getattr(entry, "is_compatibility_entry", False)]

    if current_entries:
        for index, entry in enumerate(current_entries, start=1):
            _render_recent_run_entry(entry, index=index, group_key="current")
    if compatibility_entries:
        st.markdown("### Compatibility / archived runs")
        st.caption(
            "Legacy-readable and archived artifacts stay accessible here, but they are kept separate from the primary lane."
        )
        for index, entry in enumerate(compatibility_entries, start=1):
            _render_recent_run_entry(entry, index=index, group_key="compat")


def _render_recent_run_entry(entry: object, *, index: int, group_key: str) -> None:
    requested_date = getattr(entry, "requested_date", None)
    effective_date = getattr(entry, "effective_date", None)
    mode_label = getattr(entry, "mode_label", "") or getattr(entry, "category", "n/a")
    fetch_status = getattr(entry, "fetch_status", "") or "n/a"
    ranked_count = getattr(entry, "ranked_count", 0)
    profile_basis = getattr(entry, "profile_basis", "") or "n/a"
    cache_path = getattr(entry, "cache_path", None)
    report_path = getattr(entry, "report_path", None)
    eml_path = getattr(entry, "eml_path", None)
    generated_at = getattr(entry, "generated_at", None)

    with st.container(border=True):
        st.markdown(f"**{requested_date.isoformat() if requested_date is not None else 'Recent run'} | {mode_label}**")
        if requested_date is not None and effective_date is not None:
            st.caption(f"Requested -> showing: {format_history_requested_effective_label(entry)}")
        st.caption(f"Generated: {generated_at.isoformat() if generated_at is not None else 'unknown'}")
        if isinstance(entry, RunHistoryEntry):
            st.caption(" | ".join(build_history_summary_bits(entry)))
            st.caption(format_history_llm_provenance_text(entry))
            if entry.is_compatibility_entry:
                st.caption(format_history_compatibility_text(entry))
            artifact_bits = [
                f"{label}: {path}"
                for label, path in build_history_artifact_rows(entry)
            ]
            if artifact_bits:
                st.caption(" | ".join(artifact_bits))
        else:
            st.caption(" | ".join((fetch_status, f"ranked {ranked_count}", profile_basis)))

        link_columns = st.columns(3)
        with link_columns[0]:
            if not _render_artifact_action(
                "Open recent report",
                report_path,
                missing_label="Report missing",
                key=f"{group_key}-recent-report-{index}",
                use_container_width=True,
            ):
                st.caption("Report missing")
        with link_columns[1]:
            if not _render_artifact_action(
                "Open recent cache",
                cache_path,
                missing_label="Cache missing",
                key=f"{group_key}-recent-cache-{index}",
                use_container_width=True,
            ):
                st.caption("Cache missing")
        with link_columns[2]:
            if not _render_artifact_action(
                "Open recent .eml",
                eml_path,
                missing_label="No .eml",
                key=f"{group_key}-recent-eml-{index}",
                use_container_width=True,
            ):
                st.caption("No .eml")


def _top_recommendation_cards(cards: Sequence[object]) -> list[object]:
    return list(cards[:3])


def _render_top_recommendations(cards: Sequence[object]) -> None:
    columns = st.columns(max(1, len(cards)))
    for index, (column, card) in enumerate(zip(columns, cards, strict=False), start=1):
        with column:
            with st.container(border=True):
                badge_class = "fc-badge fc-badge-strong" if card.is_recommended else "fc-badge"
                st.markdown(f'<div class="fc-card-title">{escape(card.title)}</div>', unsafe_allow_html=True)
                st.markdown(
                    (
                        '<div class="fc-top-header">'
                        f'<div class="fc-top-rank">Top {index}</div>'
                        f'<div class="fc-theme">{escape(card.theme_label)}</div>'
                        f'<div class="{badge_class}">{escape(card.status_label)}</div>'
                        f'<div class="fc-score-secondary">Score {card.score:.3f}</div>'
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                if card.url:
                    render_external_link(
                        "Open abstract",
                        card.url,
                        key=f"top-link-{index}",
                        use_container_width=True,
                    )
                if card.why_it_surfaced:
                    st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
                if card.score_explanation:
                    st.markdown(f"**Score explanation**: {card.score_explanation}")
                if card.relevance_explanation:
                    st.markdown(f"**Relevant to your interests**: {card.relevance_explanation}")
                st.write(card.recommendation_summary)
                st.caption(f"Published {card.published_text} | Authors {card.authors_text}")
                with st.expander("Score details", expanded=False):
                    _render_score_breakdown(card)


def _render_exploration_cards(cards: Sequence[object]) -> None:
    columns = st.columns(max(1, len(cards)))
    for index, (column, card) in enumerate(zip(columns, cards, strict=False), start=1):
        with column:
            with st.container(border=True):
                st.markdown(f"#### {card.title}")
                st.markdown(
                    (
                        '<div class="fc-top-header">'
                        f'<div class="fc-top-rank">Explore {index}</div>'
                        f'<div class="fc-theme">{escape(card.theme_label)}</div>'
                        f'<div class="fc-badge">{escape(card.status_label)}</div>'
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                if card.why_it_surfaced:
                    st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
                if card.score_explanation:
                    st.markdown(f"**Score explanation**: {card.score_explanation}")
                st.write(card.recommendation_summary)
                st.caption(f"Published {card.published_text} | Authors {card.authors_text}")
                if card.url:
                    render_external_link(
                        "Open abstract",
                        card.url,
                        key=f"explore-link-{index}",
                        use_container_width=True,
                    )
                with st.expander("Score details", expanded=False):
                    _render_score_breakdown(card)


def _render_ranked_cards(cards: Sequence[object]) -> None:
    for index, card in enumerate(cards, start=1):
        with st.container(border=True):
            st.markdown(f"### {index}. {card.title}")
            st.caption(f"Score {card.score:.3f} | {card.status_label} | {card.theme_label}")
            if card.why_it_surfaced:
                st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
            if card.score_explanation:
                st.markdown(f"**Score explanation**: {card.score_explanation}")
            if card.relevance_explanation:
                st.markdown(f"**Relevant to your interests**: {card.relevance_explanation}")
            st.write(card.recommendation_summary)
            if card.url:
                render_external_link(
                    "Open abstract",
                    card.url,
                    key=f"ranked-link-{index}",
                    use_container_width=True,
                )
            with st.expander("Score details", expanded=False):
                _render_score_breakdown(card)


def _render_frontier_highlights(items: Sequence[object], *, show_score: bool = False) -> None:
    if not items:
        st.info("No frontier highlights are available for the current run.")
        return
    for index, item in enumerate(items, start=1):
        with st.container(border=True):
            st.markdown(f"### {index}. {getattr(item, 'title', '')}")
            badge_bits = []
            source_label = getattr(item, "source", "")
            if source_label:
                badge_bits.append(_format_source_label(source_label))
            theme_label = getattr(item, "theme_label", "")
            if theme_label:
                badge_bits.append(theme_label)
            published = getattr(item, "published", None)
            if published is not None and hasattr(published, "isoformat"):
                badge_bits.append(f"Published {published.isoformat()}")
            if badge_bits:
                badges_markup = "".join(
                    f'<span class="fc-badge">{escape(bit)}</span>'
                    for bit in badge_bits
                )
                st.markdown(f'<div class="fc-highlight-badges">{badges_markup}</div>', unsafe_allow_html=True)
            why = getattr(item, "why", "")
            if why:
                st.markdown(f"**Why highlighted**: {why}")
            if show_score and isinstance(getattr(item, "score", None), (int, float)):
                st.markdown(f"**Score explanation**: Secondary profile overlay {getattr(item, 'score'):.3f}.")
            summary = getattr(item, "summary", "")
            if summary:
                st.write(summary)
            url = getattr(item, "url", "")
            if url:
                key_seed = getattr(item, "identifier", "") or getattr(item, "title", "") or str(index)
                key_slug = "".join(character if character.isalnum() else "-" for character in str(key_seed).lower()).strip("-")
                render_external_link(
                    "Open source paper",
                    url,
                    key=f"frontier-highlight-link-{key_slug or index}",
                    use_container_width=True,
                )
            else:
                st.caption("No source link is attached to this highlight.")


def _render_signal_row(signals: Sequence[object]) -> None:
    if not signals:
        st.caption("No repeated signals in the current view.")
        return
    badges = []
    for signal in signals:
        label = getattr(signal, "label", "")
        count = getattr(signal, "count", None)
        if not label:
            continue
        suffix = f" ({count})" if isinstance(count, int) else ""
        badges.append(f'<span class="fc-signal">{escape(label)}{suffix}</span>')
    st.markdown(" ".join(badges), unsafe_allow_html=True)


def _render_score_breakdown(card: object) -> None:
    rows = getattr(card, "score_breakdown", ())
    detail_lines = getattr(card, "score_detail_lines", ())
    st.markdown(f"**Total score:** {getattr(card, 'score', 0.0):.3f}")
    for label, value in rows:
        st.markdown(f"- {label}: {value:+.3f}")
    for line in detail_lines:
        st.caption(line)


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --fc-paper: #eef4f6;
          --fc-panel: rgba(248, 252, 253, 0.9);
          --fc-panel-strong: #fdfefe;
          --fc-border: rgba(11, 24, 32, 0.12);
          --fc-grid: rgba(10, 84, 99, 0.07);
          --fc-ink: #0b1820;
          --fc-muted: #4c5f69;
          --fc-accent: #0f6e7f;
          --fc-accent-soft: rgba(15, 110, 127, 0.1);
          --fc-warm: #8d5a12;
          --fc-warm-soft: rgba(141, 90, 18, 0.1);
          --fc-shadow: 0 28px 56px rgba(6, 25, 37, 0.08);
          --fc-display: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
          --fc-sans: "Avenir Next", "Segoe UI", "Trebuchet MS", sans-serif;
          --fc-label: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
          --fc-mono: "IBM Plex Mono", "SFMono-Regular", "Consolas", "Menlo", monospace;
        }
        .stApp {
          background:
            radial-gradient(circle at 10% 10%, rgba(15, 110, 127, 0.15), transparent 24%),
            radial-gradient(circle at 90% 8%, rgba(141, 90, 18, 0.1), transparent 22%),
            repeating-linear-gradient(90deg, transparent 0 88px, var(--fc-grid) 88px 89px),
            repeating-linear-gradient(180deg, transparent 0 88px, rgba(10, 84, 99, 0.05) 88px 89px),
            linear-gradient(180deg, #f8fbfc 0%, #eef4f6 42%, #e5edf1 100%);
          color: var(--fc-ink);
          font-family: var(--fc-sans);
          font-feature-settings: "tnum" 1;
        }
        .main .block-container {
          max-width: 84rem;
          padding-top: 2rem;
          padding-bottom: 3.5rem;
        }
        .fc-hero {
          position: relative;
          overflow: hidden;
          padding: 2rem 2rem 2.1rem;
          border-radius: 1.8rem;
          border: 1px solid rgba(11, 24, 32, 0.12);
          background:
            linear-gradient(135deg, rgba(255, 255, 255, 0.96) 0%, rgba(243, 249, 250, 0.88) 56%, rgba(227, 239, 243, 0.9) 100%);
          box-shadow: var(--fc-shadow);
          margin-bottom: 1.35rem;
        }
        .fc-hero::before {
          content: "";
          position: absolute;
          inset: 0;
          background:
            linear-gradient(120deg, rgba(15, 110, 127, 0.12), transparent 34%),
            repeating-linear-gradient(135deg, transparent 0 18px, rgba(15, 110, 127, 0.04) 18px 19px);
          pointer-events: none;
        }
        .fc-hero > * {
          position: relative;
          z-index: 1;
        }
        .fc-kicker,
        .fc-lane-kicker,
        .fc-summary-label,
        .fc-lane-label,
        .fc-status-title,
        .fc-top-rank,
        .fc-theme,
        .fc-badge,
        .fc-score-secondary,
        .fc-signal {
          display: inline-block;
          font-family: var(--fc-label);
          color: var(--fc-accent);
          letter-spacing: 0.04em;
          font-size: 0.76rem;
          font-weight: 600;
        }
        .fc-hero h1 {
          margin: 0.75rem 0 0.55rem;
          max-width: 52rem;
          font-family: var(--fc-display);
          font-size: clamp(2.8rem, 5.8vw, 4.6rem);
          letter-spacing: -0.025em;
          line-height: 1.02;
          color: var(--fc-ink);
        }
        .fc-hero p,
        .fc-lane-shell p {
          margin: 0;
          max-width: 54rem;
          color: var(--fc-muted);
          font-size: 1rem;
          line-height: 1.75;
        }
        .fc-status {
          position: relative;
          padding: 0.95rem 1rem 0.95rem 3rem;
          border-radius: 1.25rem;
          border: 1px solid var(--fc-border);
          background: var(--fc-panel);
          box-shadow: var(--fc-shadow);
        }
        .fc-status::before {
          content: "";
          position: absolute;
          left: 1rem;
          top: 50%;
          width: 0.82rem;
          height: 0.82rem;
          border-radius: 999px;
          transform: translateY(-50%);
          background: var(--fc-warm);
          box-shadow: 0 0 0 6px rgba(141, 90, 18, 0.08);
        }
        .fc-status-active {
          border-color: rgba(22, 76, 87, 0.24);
          background: linear-gradient(180deg, rgba(15, 110, 127, 0.1), rgba(248, 252, 253, 0.95));
        }
        .fc-status-active::before {
          background: var(--fc-accent);
          box-shadow: 0 0 0 6px rgba(15, 110, 127, 0.08);
        }
        .fc-status-idle {
          border-color: rgba(141, 90, 18, 0.18);
          background: linear-gradient(180deg, rgba(141, 90, 18, 0.08), rgba(248, 252, 253, 0.95));
        }
        .fc-status-title {
          color: var(--fc-accent);
        }
        .fc-status-text {
          margin-top: 0.22rem;
          font-size: 0.94rem;
          color: var(--fc-muted);
          line-height: 1.55;
        }
        .fc-summary-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 1rem;
          margin: 1.25rem 0 1.8rem;
        }
        .fc-summary-card {
          position: relative;
          overflow: hidden;
          display: flex;
          flex-direction: column;
          min-height: 100%;
          padding: 1.25rem 1.25rem 1.35rem;
          border-radius: 1.35rem;
          border: 1px solid var(--fc-border);
          background: linear-gradient(180deg, rgba(253, 254, 254, 0.96), rgba(245, 250, 251, 0.9));
          box-shadow: var(--fc-shadow);
          color: inherit;
          text-decoration: none;
          transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
          animation: fc-rise 500ms ease both;
        }
        .fc-summary-card::before {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 0.3rem;
          background: linear-gradient(180deg, var(--fc-accent), rgba(15, 110, 127, 0.08));
          border-radius: 1.35rem 0 0 1.35rem;
        }
        .fc-summary-card:hover {
          transform: translateY(-4px);
          border-color: rgba(15, 110, 127, 0.28);
          box-shadow: 0 34px 64px rgba(6, 25, 37, 0.12);
        }
        .fc-summary-label {
          color: var(--fc-muted);
        }
        .fc-summary-value {
          margin-top: 0.55rem;
          font-family: var(--fc-display);
          font-size: 2rem;
          line-height: 1;
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        .fc-summary-card p {
          margin: 0.7rem 0 1rem;
          color: var(--fc-muted);
          line-height: 1.6;
        }
        .fc-summary-link {
          margin-top: auto;
          font-family: var(--fc-label);
          letter-spacing: 0.04em;
          font-size: 0.78rem;
          color: var(--fc-accent);
          font-weight: 600;
        }
        .fc-reader-shell {
          margin-bottom: 0.9rem;
        }
        .fc-reader-shell h3 {
          margin: 0.45rem 0 0.4rem;
          font-family: var(--fc-display);
          font-size: 1.9rem;
          line-height: 1.08;
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        .fc-reader-shell p {
          margin: 0;
          max-width: 54rem;
          color: var(--fc-muted);
          font-size: 0.98rem;
          line-height: 1.72;
        }
        .stTabs [data-baseweb="tab-list"] {
          gap: 0.55rem;
          padding: 0.38rem;
          margin-bottom: 0.85rem;
          border-radius: 999px;
          background: rgba(15, 110, 127, 0.06);
        }
        .stTabs [data-baseweb="tab"] {
          height: auto;
          padding: 0.62rem 1rem;
          border-radius: 999px;
          color: var(--fc-muted);
          font-family: var(--fc-label);
          font-size: 0.82rem;
          font-weight: 600;
          letter-spacing: 0.02em;
        }
        .stTabs [aria-selected="true"] {
          background: linear-gradient(180deg, rgba(15, 110, 127, 0.14), rgba(255, 255, 255, 0.92));
          color: var(--fc-accent);
          box-shadow: 0 10px 24px rgba(6, 25, 37, 0.08);
        }
        .fc-lane-shell {
          margin: 0.8rem 0 1rem;
          padding: 1.25rem 1.25rem 1.2rem;
          border-radius: 1.4rem;
          border: 1px solid var(--fc-border);
          background:
            linear-gradient(180deg, rgba(253, 254, 254, 0.95), rgba(245, 250, 251, 0.9));
          box-shadow: var(--fc-shadow);
        }
        .fc-lane-shell h3 {
          margin: 0.45rem 0 0.4rem;
          font-family: var(--fc-display);
          font-size: 1.9rem;
          line-height: 1.02;
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        .fc-lane-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 0.7rem;
          margin-top: 0.95rem;
        }
        .fc-lane-stat {
          padding: 0.85rem 0.9rem;
          border-radius: 1rem;
          background: rgba(15, 110, 127, 0.05);
          border: 1px solid rgba(15, 110, 127, 0.1);
        }
        .fc-lane-label {
          color: var(--fc-muted);
        }
        .fc-lane-value {
          margin-top: 0.35rem;
          font-family: var(--fc-display);
          font-size: 1.35rem;
          line-height: 1;
          letter-spacing: -0.01em;
          color: var(--fc-ink);
        }
        .fc-card-title {
          font-family: var(--fc-display);
          font-size: 1.7rem;
          line-height: 1.18;
          margin-bottom: 0.55rem;
          letter-spacing: -0.01em;
          color: var(--fc-ink);
        }
        .fc-top-header {
          display: flex;
          flex-wrap: wrap;
          gap: 0.42rem;
          margin-bottom: 0.2rem;
        }
        .fc-top-rank,
        .fc-theme,
        .fc-badge {
          display: inline-block;
          padding: 0.22rem 0.56rem;
          border-radius: 999px;
          font-size: 0.72rem;
        }
        .fc-top-rank {
          background: var(--fc-warm-soft);
          color: var(--fc-warm);
        }
        .fc-theme {
          background: var(--fc-accent-soft);
          color: var(--fc-accent);
        }
        .fc-badge {
          background: rgba(31, 37, 39, 0.06);
          color: var(--fc-ink);
          border: 1px solid rgba(31, 37, 39, 0.08);
        }
        .fc-highlight-badges {
          display: flex;
          flex-wrap: wrap;
          gap: 0.45rem;
          margin-bottom: 0.55rem;
        }
        .fc-badge-strong {
          background: rgba(15, 110, 127, 0.12);
          color: var(--fc-accent);
          border-color: rgba(15, 110, 127, 0.14);
        }
        .fc-score-secondary {
          color: var(--fc-accent);
        }
        .fc-signal {
          margin: 0 0.35rem 0.35rem 0;
          padding: 0.32rem 0.65rem;
          border-radius: 999px;
          background: var(--fc-accent-soft);
          color: var(--fc-accent);
          border: 1px solid rgba(15, 110, 127, 0.1);
        }
        .fc-empty-shell {
          margin: 1rem 0 1.2rem;
          padding: 1.2rem 1.25rem;
          border-radius: 1.35rem;
          border: 1px solid var(--fc-border);
          background: var(--fc-panel);
          box-shadow: var(--fc-shadow);
        }
        .fc-empty-shell h2 {
          margin: 0.45rem 0 0.35rem;
          font-family: var(--fc-display);
          font-size: 2.2rem;
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        .fc-guidance {
          margin: 1rem 0 1.35rem;
          padding: 1.15rem 1.25rem 1.15rem 1.35rem;
          border-radius: 1.3rem;
          border: 1px solid rgba(15, 110, 127, 0.16);
          background:
            linear-gradient(135deg, rgba(255, 252, 247, 0.96), rgba(244, 249, 250, 0.94));
          box-shadow: var(--fc-shadow);
        }
        .fc-guidance-kicker {
          font-family: var(--fc-label);
          font-size: 0.75rem;
          letter-spacing: 0.05em;
          color: var(--fc-warm);
          font-weight: 600;
        }
        .fc-guidance h3 {
          margin: 0.35rem 0 0.55rem;
          font-family: var(--fc-display);
          font-size: 1.6rem;
          line-height: 1.08;
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        .fc-guidance ul {
          margin: 0;
          padding-left: 1.1rem;
          color: var(--fc-muted);
        }
        .fc-guidance li {
          margin: 0.34rem 0;
          line-height: 1.6;
        }
        .fc-empty-shell p,
        .fc-empty-panel {
          color: var(--fc-muted);
          line-height: 1.7;
        }
        .fc-empty-panel {
          padding: 0.95rem 1rem;
          border-radius: 1rem;
          border: 1px dashed rgba(15, 110, 127, 0.18);
          background: rgba(248, 252, 253, 0.74);
        }
        .fc-link-fallback-link {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 2.6rem;
          padding: 0.6rem 1rem;
          border-radius: 1rem;
          border: 1px solid var(--fc-border);
          background: var(--fc-panel);
          color: var(--fc-ink);
          font-family: var(--fc-label);
          font-size: 0.85rem;
          letter-spacing: 0.03em;
          font-weight: 600;
          text-decoration: none;
          transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
        }
        .fc-link-fallback-link:hover {
          transform: translateY(-1px);
          border-color: rgba(15, 110, 127, 0.26);
          background: rgba(240, 248, 249, 0.98);
        }
        .fc-link-primary {
          background: rgba(15, 110, 127, 0.1);
          border-color: rgba(15, 110, 127, 0.18);
          color: var(--fc-accent);
        }
        div.stButton > button {
          border-radius: 1rem;
          border: 1px solid var(--fc-border);
          background: rgba(248, 252, 253, 0.92);
          color: var(--fc-ink);
          font-family: var(--fc-label);
          font-size: 0.88rem;
          font-weight: 600;
          letter-spacing: 0.02em;
          min-height: 3rem;
          box-shadow: 0 18px 32px rgba(6, 25, 37, 0.06);
          transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
        }
        div.stButton > button:hover {
          transform: translateY(-1px);
          border-color: rgba(15, 110, 127, 0.26);
          background: rgba(241, 248, 249, 0.96);
          color: var(--fc-accent);
        }
        div.stButton > button[kind="primary"] {
          border-color: rgba(15, 110, 127, 0.24);
          background: linear-gradient(180deg, rgba(15, 110, 127, 0.14), rgba(15, 110, 127, 0.08));
          color: var(--fc-accent);
        }
        [data-testid="stSelectbox"] label,
        [data-testid="stDateInput"] label,
        [data-testid="stSlider"] label,
        [data-testid="stRadio"] label,
        [data-testid="stCheckbox"] label,
        [data-testid="stMultiSelect"] label,
        [data-testid="stFileUploader"] label,
        [data-testid="stTextInput"] label,
        [data-testid="stTextArea"] label {
          font-family: var(--fc-label);
          letter-spacing: 0.02em;
          color: var(--fc-muted);
        }
        [data-testid="stMetric"] {
          background: var(--fc-panel);
          border: 1px solid var(--fc-border);
          border-radius: 1rem;
          padding: 0.7rem 0.8rem;
        }
        [data-testid="stMetricValue"] {
          color: var(--fc-ink);
          font-family: var(--fc-display);
          letter-spacing: -0.02em;
        }
        [data-testid="stMetricLabel"] {
          font-family: var(--fc-label);
          letter-spacing: 0.03em;
          color: var(--fc-muted);
        }
        [data-testid="stExpander"] details {
          background: rgba(248, 252, 253, 0.88);
          border: 1px solid var(--fc-border);
          border-radius: 1.1rem;
          box-shadow: 0 12px 24px rgba(6, 25, 37, 0.05);
        }
        [data-testid="stExpander"] summary {
          font-family: var(--fc-label);
          letter-spacing: 0.03em;
        }
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stCaptionContainer"] {
          line-height: 1.7;
        }
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {
          font-family: var(--fc-display);
          letter-spacing: -0.02em;
          color: var(--fc-ink);
        }
        @keyframes fc-rise {
          from {
            opacity: 0;
            transform: translateY(10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_app()
