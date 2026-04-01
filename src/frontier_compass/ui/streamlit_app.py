"""Streamlit UI for inspecting FrontierCompass daily local scouting runs."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from datetime import date, timezone
from html import escape
from pathlib import Path
from typing import Sequence

import streamlit as st

from frontier_compass.api import FrontierCompassRunner, LocalUISession
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    format_cost_mode_label,
    format_runtime_status,
)
from frontier_compass.exploration.selector import daily_exploration_intro, resolve_daily_exploration_picks
from frontier_compass.reporting.daily_brief import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    build_daily_brief,
    build_reviewer_shortlist,
    filter_ranked_papers,
    summarize_category_counts,
)
from frontier_compass.storage.schema import RunHistoryEntry, RunTimings, SourceRunStats
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
    PROFILE_SOURCE_ZOTERO,
    build_existing_local_file_url,
    build_profile_inspector_lines,
    build_daily_run_summary,
    build_exploration_cards,
    build_ranked_paper_cards,
    display_source_label,
    format_daily_source_label,
    format_source_outcome_label,
    normalize_request_window_inputs,
)
from frontier_compass.ui.history import (
    build_history_summary_bits,
    eml_path_for_report_artifact,
    format_history_requested_effective_label,
)
from frontier_compass.ui.streamlit_support import render_external_link
from frontier_compass.zotero.local_library import ZoteroLibraryState


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
            f"{zotero_key}|{zotero_db_key}|{collection_key}|{int(self.skip_initial_load)}"
        )

    @property
    def effective_profile_source(self) -> str:
        if self.profile_source is not None:
            normalized = str(self.profile_source).strip().lower()
            if normalized == PROFILE_SOURCE_BASELINE:
                return PROFILE_SOURCE_BASELINE
            return PROFILE_SOURCE_ZOTERO
        if self.zotero_db_path is not None or self.zotero_export_path is not None:
            return PROFILE_SOURCE_ZOTERO
        return PROFILE_SOURCE_BASELINE

    @property
    def auto_profile_source_note(self) -> str:
        if self.profile_source is not None:
            return ""
        if self.zotero_db_path is not None and self.zotero_export_path is not None:
            return "Zotero is auto-selected because a reusable export and a local Zotero library are both available."
        if self.zotero_db_path is not None:
            return "Zotero is auto-selected because a local Zotero library is available."
        if self.zotero_export_path is not None:
            return "Zotero is auto-selected because a reusable Zotero export is available."
        return ""

    @property
    def request_label(self) -> str:
        if self.start_date is not None or self.end_date is not None:
            start = self.start_date or self.requested_date
            end = self.end_date or start
            return f"{start.isoformat()} -> {end.isoformat()}"
        return self.requested_date.isoformat()


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
        <div class="fc-hero">
          <div class="fc-kicker">FrontierCompass Local UI</div>
          <h1>Read the right papers first.</h1>
          <p><strong>Personalized Digest</strong> starts with the papers worth your attention now. <strong>Frontier Report</strong> stays available for the broader field scan from the same local run. Provenance, runtime, and artifact details stay in secondary panels so the first screen can stay focused on reading.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    zotero_state = runner.app.zotero_library_state(
        export_path=startup_request.zotero_export_path,
        db_path=startup_request.zotero_db_path,
    )
    launcher_columns = st.columns((1.45, 1.0))
    with launcher_columns[0]:
        setup_row = st.columns(3)
        selected_source = setup_row[0].selectbox(
            "Source bundle",
            source_options,
            index=_daily_source_index(startup_request.selected_source, source_options),
            format_func=lambda value: _format_source_bundle_label(runner, value),
            key=_widget_key("launch", "source-bundle"),
        )
        requested_date = setup_row[1].date_input(
            "Reading date",
            value=startup_request.requested_date,
            key=_widget_key("launch", "requested-date"),
        )
        profile_source = setup_row[2].selectbox(
            "Profile",
            options=(PROFILE_SOURCE_BASELINE, PROFILE_SOURCE_ZOTERO),
            index=0 if startup_request.effective_profile_source == PROFILE_SOURCE_BASELINE else 1,
            format_func=_format_profile_source_label,
            key=_widget_key("launch", "profile-basis"),
        )
        active_bundle = runner.app.resolve_source_bundle(selected_source)
        if active_bundle is not None and active_bundle.description:
            st.caption(active_bundle.description)
        if startup_request.auto_profile_source_note:
            st.caption(startup_request.auto_profile_source_note)

        with st.expander("Request window", expanded=False):
            fetch_scope = st.radio(
                "Time scope",
                options=(FETCH_SCOPE_DAY_FULL, FETCH_SCOPE_RANGE_FULL),
                index=0 if startup_request.fetch_scope == FETCH_SCOPE_DAY_FULL else 1,
                format_func=lambda value: "Single day" if value == FETCH_SCOPE_DAY_FULL else "Date range",
                key=_widget_key("launch", "time-scope"),
            )
            if fetch_scope == FETCH_SCOPE_RANGE_FULL:
                date_columns = st.columns(2)
                start_date = date_columns[0].date_input(
                    "Start date",
                    value=startup_request.start_date or startup_request.requested_date,
                    key=_widget_key("launch", "start-date"),
                )
                end_date = date_columns[1].date_input(
                    "End date",
                    value=startup_request.end_date or startup_request.requested_date,
                    key=_widget_key("launch", "end-date"),
                )
                requested_date = start_date
            else:
                start_date = None
                end_date = None
            fetch_max_results = st.slider(
                "Display limit",
                min_value=20,
                max_value=120,
                value=_clamp_fetch_limit(startup_request.max_results),
                step=20,
                key=_widget_key("launch", "display-limit"),
            )

        with st.expander("Custom bundles", expanded=False):
            _render_custom_bundle_manager(runner)

    with launcher_columns[1]:
        st.markdown("### Daily launcher")
        st.caption(
            f"Active bundle: {_format_source_bundle_label(runner, selected_source)}. "
            "Daily Recommendation and Daily Report both read from the same saved day snapshot."
        )
        if profile_source == PROFILE_SOURCE_ZOTERO:
            st.caption(_format_zotero_state_summary(zotero_state))
            if zotero_state.collections:
                selected_collections = tuple(
                    st.multiselect(
                        "Zotero collections",
                        options=zotero_state.collections,
                        default=list(_selected_collection_defaults(startup_request.zotero_collections, zotero_state.collections)),
                        key=_widget_key("launch", "zotero-collections"),
                    )
                )
            else:
                selected_collections = ()
                st.caption("No saved Zotero collections are available yet. The whole exported library will be used.")
        else:
            selected_collections = ()

        with st.container(border=True):
            st.markdown("#### Data refresh")
            allow_stale_cache = st.checkbox(
                "Allow stale cache fallback",
                value=startup_request.allow_stale_cache,
                key=_widget_key("launch", "allow-stale-cache"),
            )
            update_snapshot = st.button(
                "Update daily snapshot",
                use_container_width=True,
                key=_widget_key("launch", "refresh-sources"),
            )
            update_zotero = st.button(
                "Update Zotero export",
                use_container_width=True,
                disabled=profile_source != PROFILE_SOURCE_ZOTERO,
                key=_widget_key("launch", "refresh-zotero"),
            )

        action_columns = st.columns(2)
        launch_recommendation = action_columns[0].button(
            "Daily Recommendation",
            type="primary",
            use_container_width=True,
            key=_widget_key("launch", "daily-recommendation"),
        )
        launch_report = action_columns[1].button(
            "Daily Report",
            use_container_width=True,
            key=_widget_key("launch", "daily-report"),
        )
        st.caption("Switching bundle, profile mode, or Zotero collections reuses the saved day snapshot unless you explicitly refresh data.")

    if update_zotero:
        refreshed_zotero_state = runner.app.zotero_library_state(
            refresh=True,
            export_path=startup_request.zotero_export_path,
            db_path=startup_request.zotero_db_path,
        )
        st.session_state["fc_zotero_refresh_note"] = _format_zotero_refresh_notice(refreshed_zotero_state)
        st.rerun()

    if refresh_note := str(st.session_state.pop("fc_zotero_refresh_note", "")):
        if "updated" in refresh_note.lower() or "exported" in refresh_note.lower():
            st.success(refresh_note)
        else:
            st.warning(refresh_note)

    if launch_recommendation:
        st.session_state["fc_launch_focus"] = "digest"
    elif launch_report:
        st.session_state["fc_launch_focus"] = "frontier"

    staged_request = UIStartupRequest(
        selected_source=selected_source,
        requested_date=requested_date,
        max_results=fetch_max_results,
        start_date=start_date,
        end_date=end_date,
        report_mode=startup_request.report_mode,
        profile_source=profile_source,
        zotero_export_path=startup_request.zotero_export_path,
        zotero_db_path=startup_request.zotero_db_path,
        zotero_collections=selected_collections,
        fetch_scope=fetch_scope,
        allow_stale_cache=allow_stale_cache,
    )
    normalized_staged_request, staged_window_note = _normalize_ui_request(staged_request)
    active_request, staged_changes_pending = _resolve_active_request(
        startup_request=startup_request,
        staged_request=normalized_staged_request,
        apply_requested=launch_recommendation or launch_report,
        refresh_requested=update_snapshot,
    )

    if staged_window_note:
        st.warning(staged_window_note)
    elif staged_changes_pending:
        st.info("Launcher choices are staged. Use Daily Recommendation or Daily Report to reopen the current day with the new bundle or profile settings.")
    st.caption(f"Current session request: {active_request.request_label}")

    if _should_skip_initial_load(active_request, force_refresh=update_snapshot):
        active_session = None
        active_error = (
            "The UI was opened without a prewarmed digest after a CLI prewarm failure. "
            "Use Daily Recommendation, Daily Report, or Update daily snapshot to retry."
        )
    else:
        active_session, active_error = _resolve_active_session(
            runner,
            request=active_request,
            force_refresh=update_snapshot,
        )
    if active_session is None:
        if active_request.skip_initial_load:
            st.warning(active_error)
        else:
            st.error(
                "Unable to load a reviewer digest for "
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
    profile_inspector_lines = build_profile_inspector_lines(digest.profile)
    frontier_report = digest.frontier_report
    frontier_source_stats = frontier_report.source_run_stats if frontier_report is not None else summary.source_run_stats
    frontier_source_text = _format_source_stats_text(frontier_source_stats)
    frontier_source_count_text = _format_source_mix_counts(
        frontier_source_stats,
        frontier_report.source_counts if frontier_report is not None else getattr(summary, "source_counts", {}),
    )
    frontier_timings = frontier_report.run_timings if frontier_report is not None else summary.run_timings
    frontier_timings_text = _format_run_timings_text(frontier_timings)

    if recent_runs_error:
        st.caption(f"Recent-run history is unavailable: {recent_runs_error}")

    if active_session.fetch_error:
        st.error(f"Fresh source fetch failed: {active_session.fetch_error}")

    _short_notice = f"Status: {active_session.fetch_status_label}."
    if active_session.display_source == DISPLAY_SOURCE_FRESH:
        st.success(_short_notice)
    elif active_session.display_source in (DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE, DISPLAY_SOURCE_REUSED_STALE_CACHE):
        st.warning(_short_notice)
    else:
        st.info(_short_notice)
    provenance_parts = []
    provenance_parts.append(f"Source mix: {frontier_source_count_text or 'n/a'}")
    provenance_parts.append(f"Report availability: {'present' if summary.frontier_report_present else 'absent'}")
    provenance_parts.append(f"Active profile basis: {active_session.profile_basis_label}")
    st.caption(" | ".join(provenance_parts))
    with st.expander("Full run details", expanded=False):
        st.caption(_display_source_notice(active_session))
    _render_run_briefing(
        summary=summary,
        digest=digest,
        session=active_session,
        frontier_report=frontier_report,
    )

    current_eml_path = eml_path_for_report_artifact(report_path)
    digest_tab, frontier_tab, history_tab = st.tabs(["Digest", "Frontier Report", "History"])

    with digest_tab:
        st.markdown("## Personalized Digest")
        st.caption("Profile-aware reading-first track for what you should look at first.")
        with st.container(border=True):
            context_columns = st.columns(4)
            context_columns[0].metric("Requested date", summary.requested_date.isoformat())
            context_columns[1].metric("Showing", summary.effective_date.isoformat())
            context_columns[2].metric("Source bundle", summary.mode_label or summary.category)
            context_columns[3].metric("Profile source", digest.profile.profile_source_label)
            if digest.profile.profile_path_name:
                st.caption(f"Active profile path: {digest.profile.profile_path_name}")
            top_profile_terms = digest.profile.top_profile_terms(limit=5)
            if top_profile_terms:
                st.caption(f"Top profile terms: {', '.join(top_profile_terms)}")

        with st.expander("Filter papers", expanded=False):
            _filter_cols = st.columns((1.2, 1.0, 1.0, 1.0))
            with _filter_cols[0]:
                view_mode = st.radio(
                    "View",
                    ("Recommended only", "Show all ranked"),
                    index=0,
                    key=_widget_key("digest", "view-mode"),
                )
            with _filter_cols[1]:
                min_score = st.slider(
                    "Minimum score",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.0,
                    step=0.05,
                    key=_widget_key("digest", "minimum-score"),
                )
            with _filter_cols[2]:
                available_card_limit = min(len(digest.ranked), 40)
                if available_card_limit <= 1:
                    max_cards = 1
                    if available_card_limit == 1:
                        st.caption("Maximum cards is fixed at 1 because the current digest has a single ranked paper.")
                    else:
                        st.caption("Maximum cards will expand once the current digest contains ranked papers.")
                else:
                    max_cards = st.slider(
                        "Maximum cards",
                        min_value=1,
                        max_value=available_card_limit,
                        value=min(available_card_limit, 12),
                        step=1,
                        key=_widget_key("digest", "maximum-cards"),
                    )
            with _filter_cols[3]:
                sort_choice = st.selectbox(
                    "Sort order",
                    ("Top score", "Newest first"),
                    index=0,
                    key=_widget_key("digest", "sort-order"),
                )
            st.caption(f"Recommended threshold: {DEFAULT_RECOMMENDED_SCORE_THRESHOLD:.2f}+")

        reviewer_source = filter_ranked_papers(
            digest.ranked,
            min_score=min_score,
            sort_mode="score",
        )
        reviewer_shortlist, reviewer_shortlist_title = build_reviewer_shortlist(
            reviewer_source,
            max_items=min(max_cards, 8),
            recommended_threshold=DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
        )
        filtered_ranked = filter_ranked_papers(
            digest.ranked,
            min_score=min_score,
            max_items=max_cards,
            recommended_only=view_mode == "Recommended only",
            sort_mode="newest" if sort_choice == "Newest first" else "score",
        )
        brief = build_daily_brief(digest.profile, reviewer_shortlist, total_ranked=len(digest.ranked))
        reviewer_cards = build_ranked_paper_cards(reviewer_shortlist, profile=digest.profile)
        exploration_picks = resolve_daily_exploration_picks(digest)
        exploration_cards = build_exploration_cards(
            exploration_picks,
            ranked_pool=digest.ranked,
            profile=digest.profile,
            policy=digest.exploration_policy,
        )
        cards = build_ranked_paper_cards(filtered_ranked, profile=digest.profile)
        top_cards = _top_recommendation_cards(reviewer_cards)
        top_abstract_url = next((card.url for card in top_cards if card.url), "")

        with st.container(border=True):
            st.markdown("### What to read first")
            for takeaway in brief.takeaways:
                st.markdown(f"- {takeaway}")
        st.markdown("**Quick actions**")
        action_columns = st.columns(2)
        with action_columns[0]:
            if not _render_artifact_action(
                "Open current HTML report",
                report_path,
                missing_label="Current HTML report is missing for this run.",
                help="Open the saved HTML briefing for this digest.",
                type="primary",
                use_container_width=True,
            ):
                st.caption("Current HTML report is missing for this run.")
        with action_columns[1]:
            if not render_external_link(
                "Open top arXiv abstract",
                top_abstract_url,
                help="Open the highest-priority arXiv abstract in the current view.",
                use_container_width=True,
            ):
                st.caption("No arXiv abstract is available for the current top recommendation.")

        st.markdown("### Top recommendations")
        st.caption("Start here for the balanced, score-first reading pass through the current digest.")
        if top_cards:
            _render_top_recommendations(top_cards)
        else:
            st.markdown(
                (
                    '<div class="fc-empty-panel">'
                    "<strong>No papers matched the current review filters.</strong><br>"
                    "Lower the score threshold, switch to all ranked papers, or widen the requested window."
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        if exploration_cards:
            st.markdown("### Exploration")
            st.caption(daily_exploration_intro(digest.profile, policy=digest.exploration_policy))
            _render_exploration_cards(exploration_cards)

        with st.expander("All ranked papers", expanded=False):
            if not cards:
                st.info("No papers match the current review filters. Lower the score threshold or switch to all ranked papers.")
            else:
                _render_ranked_cards(cards)

    with frontier_tab:
        st.markdown("## Frontier Report")
        st.caption("Broader field scan built from the requested window’s fetched pool using title, abstract, and categories only. Profile relevance stays secondary here.")
        if frontier_report is None:
            with st.container(border=True):
                st.warning(
                    "This legacy cache does not include the frontier-report contract. "
                    "FrontierCompass will not reconstruct a field-wide report from the personalized slice."
                )
                if summary.report_error:
                    st.caption(summary.report_error)
        else:
            if summary.report_status == "empty":
                st.info(summary.report_error or "Frontier Report is empty for the current run.")
            elif summary.report_status == "partial":
                st.warning(summary.report_error or "Frontier Report is partial for the current run.")
            st.caption(f"Current fetch status: {active_session.fetch_status_label}.")
            st.caption(f"Report availability in this session: {'yes' if summary.frontier_report_present else 'no'}.")
            if summary.report_error:
                st.caption(f"Report note: {summary.report_error}")
            st.caption(f"Request window: {summary.request_window.label}; fetch scope: {summary.fetch_scope}.")
            with st.container(border=True):
                st.markdown("### What happened today in the field")
                if frontier_report.takeaways:
                    for takeaway in frontier_report.takeaways:
                        st.markdown(f"- {takeaway}")
                else:
                    st.write("No field-wide takeaways were produced for this run.")
            frontier_columns = st.columns((1.35, 1.0))
            with frontier_columns[0]:
                with st.container(border=True):
                    overview_row = st.columns(2)
                    overview_row[0].metric("Request window", frontier_report.request_window.label)
                    overview_row[1].metric("Fetch scope", frontier_report.fetch_scope)
                    scope_row = st.columns(2)
                    scope_row[0].metric("Fetched / ranked", f"{frontier_report.total_fetched} / {frontier_report.total_ranked}")
                    scope_row[1].metric("Report runtime", format_runtime_status(frontier_report.report_mode, frontier_report.cost_mode))
                    st.caption(
                        f"Total displayed highlights: {frontier_report.displayed_highlight_count}. "
                        "The broader ranked pool stays in cache and UI instead of being dumped into the page."
                    )
                    if frontier_source_count_text:
                        st.caption(f"Source mix counts: {frontier_source_count_text}")
                    with st.expander("Runtime and profile context", expanded=False):
                        if frontier_source_text:
                            st.caption(f"Source stats: {frontier_source_text}")
                        if frontier_timings_text:
                            st.caption(f"Run timings: {frontier_timings_text}")
                        st.caption(f"Searched categories: {', '.join(frontier_report.searched_categories) or 'n/a'}")
                        st.caption(f"Requested report mode: {frontier_report.requested_report_mode}.")
                        st.caption(f"Enhanced track: {frontier_report.enhanced_track or 'none'}.")
                        st.caption(frontier_report.runtime_note)
            with frontier_columns[1]:
                if not _render_artifact_action(
                    "Open current HTML report",
                    report_path,
                    missing_label="Current HTML report is missing for this run.",
                    key="frontier-report-link",
                    type="primary",
                    use_container_width=True,
                ):
                    st.caption("Current HTML report is missing for this run.")
                st.caption(
                    f"Requested {summary.requested_date.isoformat()} | showing {summary.effective_date.isoformat()} | "
                    f"source bundle {summary.mode_label or summary.category}"
                )

            signal_row = st.columns(3)
            signal_row[0].markdown("**Repeated themes**")
            signal_row[1].markdown("**Salient methods / topics**")
            signal_row[2].markdown("**Adjacent themes**")
            with signal_row[0]:
                _render_signal_row(frontier_report.repeated_themes)
            with signal_row[1]:
                _render_signal_row(frontier_report.salient_topics)
            with signal_row[2]:
                _render_signal_row(frontier_report.adjacent_themes)

            st.markdown("### Field-wide highlights")
            _render_frontier_highlights(frontier_report.field_highlights)

            if frontier_report.profile_relevant_highlights:
                st.markdown("### Potentially relevant to your current profile")
                st.caption("This subsection uses the existing profile-aware ranking only as a secondary highlight signal.")
                _render_frontier_highlights(frontier_report.profile_relevant_highlights, show_score=True)

    with history_tab:
        st.markdown("## History")
        st.markdown("### Recent runs")
        if recent_runs_error:
            st.info(f"Recent-run history is unavailable: {recent_runs_error}")
        else:
            _render_recent_runs(recent_runs)

        with st.container(border=True):
            st.markdown("### Current run")
            overview_row = st.columns(5)
            overview_row[0].metric("Display source", display_source_label(summary.display_source))
            overview_row[1].metric("Profile basis", active_session.profile_basis_label)
            overview_row[2].metric("Report mode", summary.report_mode)
            overview_row[3].metric("Cost mode", format_cost_mode_label(summary.cost_mode))
            overview_row[4].metric(
                "Latest-available fallback",
                "yes" if summary.used_latest_available_fallback else "no",
            )
            st.caption(f"Requested -> showing: {summary.requested_date.isoformat()} -> {summary.effective_date.isoformat()}")
            st.caption(
                f"Fetch scope: {summary.fetch_scope}; total fetched: {summary.total_fetched}; "
                f"total displayed: {summary.total_displayed}; ranked pool: {summary.ranked_count}"
            )
            st.caption(
                f"Frontier Report: {'present' if summary.frontier_report_present else 'absent'}; "
                f"status {summary.report_status}; artifact {'aligned' if summary.report_artifact_aligned else 'not aligned'}."
            )
            if frontier_source_count_text:
                st.caption(f"Source mix counts: {frontier_source_count_text}")
            if frontier_timings_text:
                st.caption(f"Run timings: {frontier_timings_text}")
            st.caption(f"View mode: {view_mode}; sort: {sort_choice.lower()}; minimum score: {min_score:.2f}")

            artifact_columns = st.columns(3)
            with artifact_columns[0]:
                if not _render_artifact_action(
                    "Open current HTML report",
                    report_path,
                    missing_label="Current HTML report is missing for this run.",
                    key="current-history-report",
                    use_container_width=True,
                ):
                    st.caption("Report missing")
            with artifact_columns[1]:
                if not _render_artifact_action(
                    "Open current cache JSON",
                    cache_path,
                    missing_label="Current cache artifact is missing for this run.",
                    key="current-history-cache",
                    use_container_width=True,
                ):
                    st.caption("Cache missing")
            with artifact_columns[2]:
                if _render_artifact_action(
                        "Open current .eml",
                        current_eml_path,
                        missing_label="No current .eml",
                        key="current-history-eml",
                        use_container_width=True,
                    ):
                    pass
                else:
                    st.caption("No current .eml")

        with st.expander("Run details and provenance", expanded=False):
            fallback_row = st.columns(3)
            fallback_row[0].metric("Stale cache fallback", "yes" if summary.stale_cache_fallback_used else "no")
            fallback_row[1].metric("Request window", summary.request_window.label)
            fallback_row[2].metric("Enhanced track", summary.enhanced_track or "none")

            signal_row = st.columns(3)
            signal_row[0].markdown("**Personalized themes**")
            signal_row[1].markdown("**Top category signals**")
            signal_row[2].markdown("**Matched biomedical keywords**")
            with signal_row[0]:
                _render_signal_row(brief.top_theme_signals)
            with signal_row[1]:
                _render_signal_row(brief.top_category_signals)
            with signal_row[2]:
                _render_signal_row(brief.top_keyword_signals)
            st.caption(f"Average shown score: {brief.average_score:.3f}")
            st.caption(f"Current HTML report: {summary.report_path}")
            st.caption(f"Current cache: {summary.cache_path}")
            for line in profile_inspector_lines:
                st.caption(line)
            st.markdown(f"**Requested report mode:** {summary.requested_report_mode}")
            st.markdown(f"**Applied report mode:** {summary.report_mode}")
            st.markdown(f"**Cost mode:** {summary.cost_mode}")
            st.markdown(f"**Enhanced track:** {summary.enhanced_track or 'none'}")
            st.markdown(f"**Enhanced item count:** {summary.enhanced_item_count}")
            st.markdown(f"**Runtime note:** {summary.runtime_note}")
            st.markdown(f"**Mode id:** {summary.category}")
            st.markdown(f"**Mode kind:** {summary.mode_kind or 'n/a'}")
            st.markdown(
                "**Display basis:** "
                + (
                    "latest available fallback results"
                    if summary.used_latest_available_fallback
                    else "strict same-day results"
                )
            )
            st.markdown(
                "**Strict same-day fetched / ranked:** "
                + (
                    f"{summary.strict_same_day_fetched} / {summary.strict_same_day_ranked}"
                    if summary.strict_same_day_counts_known
                    else "unavailable / unavailable"
                )
            )
            st.markdown(f"**Total fetched:** {summary.total_fetched}")
            st.markdown(f"**Total displayed:** {summary.total_displayed}")
            st.markdown(f"**Ranked pool size:** {summary.ranked_count}")
            if not summary.strict_same_day_counts_known:
                st.markdown(
                    "**Strict same-day status:** unavailable because fresh fetch failed before same-day counts were computed."
                )
            if summary.stale_cache_fallback_used:
                st.markdown(
                    "**Stale cache source requested date:** "
                    + (
                        summary.stale_cache_source_requested_date.isoformat()
                        if summary.stale_cache_source_requested_date is not None
                        else "unknown"
                    )
                )
                st.markdown(
                    "**Stale cache source effective date:** "
                    + (
                        summary.stale_cache_source_effective_date.isoformat()
                        if summary.stale_cache_source_effective_date is not None
                        else "unknown"
                    )
                )
            st.markdown(f"**Searched categories:** {', '.join(summary.searched_categories) or 'n/a'}")
            st.markdown(
                "**Per-category counts:** "
                + (" | ".join(summarize_category_counts(summary.searched_categories, summary.per_category_counts)) or "n/a")
            )
            if summary.search_profile_label:
                st.markdown(f"**Search profile:** {summary.search_profile_label}")
            st.markdown(f"**Profile basis:** {digest.profile.basis_label or 'n/a'}")
            st.markdown(
                f"**Profile source:** {digest.profile.profile_source} ({digest.profile.profile_source_label})"
            )
            if digest.profile.profile_path:
                st.markdown(f"**Profile path:** {digest.profile.profile_path}")
            if digest.profile.profile_item_count or digest.profile.profile_used_item_count:
                st.markdown(
                    f"**Profile items parsed / used:** {digest.profile.profile_item_count} / "
                    f"{digest.profile.profile_used_item_count}"
                )
            top_profile_terms = digest.profile.top_profile_terms(limit=6)
            if top_profile_terms:
                st.markdown("**Top profile terms:** " + ", ".join(top_profile_terms))
            top_zotero_signals = tuple((*digest.profile.zotero_keywords[:3], *digest.profile.zotero_concepts[:3]))
            if top_zotero_signals:
                st.markdown("**Top Zotero signals:** " + ", ".join(top_zotero_signals))
            if summary.mode_notes:
                st.markdown(f"**Mode notes:** {summary.mode_notes}")
            if summary.feed_url:
                endpoint_label = "Search endpoint" if "hybrid" in summary.mode_kind or summary.mode_kind == "search" else "Feed"
                st.markdown(f"**{endpoint_label}:** {summary.feed_url}")
            if summary.search_queries:
                st.markdown("**Fixed search queries**")
                st.code("\n\n".join(summary.search_queries), language="text")
            if summary.feed_urls:
                st.markdown("**Source feeds**")
                st.code(
                    "\n".join(
                        f"{category_name}: {feed_url}" for category_name, feed_url in sorted(summary.feed_urls.items())
                    ),
                    language="text",
                )


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
                    )
                )
    except Exception as exc:  # pragma: no cover - exercised through manual validation.
        error_message = str(exc)
        st.session_state["fc_digest_request_key"] = request_key
        st.session_state["fc_ui_session"] = None
        st.session_state["fc_digest_error"] = error_message
        return None, error_message

    st.session_state["fc_digest_request_key"] = request_key
    st.session_state["fc_ui_session"] = result
    st.session_state["fc_digest_error"] = ""
    return result, ""


def _resolve_active_request(
    *,
    startup_request: UIStartupRequest,
    staged_request: UIStartupRequest,
    apply_requested: bool,
    refresh_requested: bool,
) -> tuple[UIStartupRequest, bool]:
    active_request = st.session_state.get("fc_active_request")
    if not isinstance(active_request, UIStartupRequest):
        active_request = startup_request
    if apply_requested or refresh_requested:
        active_request = staged_request
    st.session_state["fc_active_request"] = active_request
    return active_request, staged_request.request_key != active_request.request_key


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


def _render_run_briefing(
    *,
    summary: object,
    digest: object,
    session: LocalUISession,
    frontier_report: object | None,
) -> None:
    status_tone = "caution" if session.display_source in (
        DISPLAY_SOURCE_REUSED_SAME_DATE_CACHE,
        DISPLAY_SOURCE_REUSED_STALE_CACHE,
    ) else "stable" if session.display_source == DISPLAY_SOURCE_CACHE else "live"
    display_basis = "latest available fallback" if getattr(summary, "used_latest_available_fallback", False) else "strict same-day"
    frontier_count = frontier_report.displayed_highlight_count if frontier_report is not None else 0
    report_status = getattr(summary, "report_status", "ready") or "ready"
    cards = (
        (
            "Reading window",
            getattr(summary, "request_window").label,
            f"Requested {getattr(summary, 'requested_date').isoformat()} and showing {getattr(summary, 'effective_date').isoformat()}.",
        ),
        (
            "Digest lane",
            f"{getattr(summary, 'ranked_count')} ranked / {getattr(summary, 'total_displayed')} shown",
            f"Profile source {getattr(digest, 'profile').profile_source_label}; display basis {display_basis}.",
        ),
        (
            "Frontier lane",
            f"{frontier_count} highlights",
            f"Report status {report_status}; runtime {getattr(summary, 'report_mode')} / {getattr(summary, 'cost_mode')}.",
        ),
    )
    chip_rows = (
        ("Status", session.fetch_status_label, status_tone),
        ("Source bundle", getattr(summary, "mode_label") or getattr(summary, "category"), "neutral"),
        ("Profile", session.profile_basis_label, "neutral"),
        ("Fetch scope", getattr(summary, "fetch_scope"), "neutral"),
    )
    card_markup = "".join(
        (
            '<article class="fc-overview-card">'
            f'<div class="fc-overview-label">{escape(label)}</div>'
            f'<div class="fc-overview-value">{escape(value)}</div>'
            f'<p>{escape(note)}</p>'
            "</article>"
        )
        for label, value, note in cards
    )
    chip_markup = "".join(
        f'<span class="fc-chip fc-chip-{escape(tone)}"><strong>{escape(label)}:</strong> {escape(value)}</span>'
        for label, value, tone in chip_rows
    )
    st.markdown(
        (
            '<section class="fc-overview-shell">'
            '<div class="fc-overview-head">'
            '<div>'
            '<div class="fc-overview-kicker">Session briefing</div>'
            '<h2>One run, three reading surfaces.</h2>'
            '<p>The digest, frontier scan, and history trail all point at the same local artifact family.</p>'
            "</div>"
            "</div>"
            f'<div class="fc-chip-row">{chip_markup}</div>'
            f'<div class="fc-overview-grid">{card_markup}</div>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_missing_session_shell(request: UIStartupRequest, error_message: str) -> None:
    st.markdown(
        (
            '<section class="fc-overview-shell">'
            '<div class="fc-overview-head">'
            '<div>'
            '<div class="fc-overview-kicker">No active digest</div>'
            '<h2>Open the workspace first, then materialize a run.</h2>'
            '<p>Use Daily Recommendation or Daily Report to reopen the current day from the saved snapshot. Use Update daily snapshot only when you want a fresh network refresh.</p>'
            "</div>"
            "</div>"
            '<div class="fc-chip-row">'
            f'<span class="fc-chip fc-chip-caution"><strong>Request:</strong> {escape(request.request_label)}</span>'
            f'<span class="fc-chip"><strong>Source bundle:</strong> {escape(request.selected_source)}</span>'
            f'<span class="fc-chip"><strong>Fetch scope:</strong> {escape(request.fetch_scope)}</span>'
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )
    digest_tab, frontier_tab, history_tab = st.tabs(["Digest", "Frontier Report", "History"])
    with digest_tab:
        st.markdown("## Personalized Digest")
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                "<strong>No digest is loaded yet.</strong><br>"
                "Try a cache-first load first. If live sources are rate-limiting, switch to another date or wait for the rate limit window to clear."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    with frontier_tab:
        st.markdown("## Frontier Report")
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                "<strong>No frontier report is available yet.</strong><br>"
                "The field-wide track appears after a digest is successfully materialized."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    with history_tab:
        st.markdown("## History")
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                f"<strong>Startup note.</strong><br>{escape(error_message or 'No cache or fetch result is available yet.')}"
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def _load_startup_request(argv: Sequence[str] | None = None) -> UIStartupRequest:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", default=DEFAULT_REVIEWER_SOURCE)
    parser.add_argument("--requested-date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--max-results", type=int, default=80)
    parser.add_argument("--report-mode", default=DEFAULT_REPORT_MODE)
    parser.add_argument("--profile-source", default="")
    parser.add_argument("--zotero-export", default="")
    parser.add_argument("--zotero-db-path", default="")
    parser.add_argument("--zotero-collection", action="append", default=[])
    parser.add_argument("--fetch-scope", choices=FETCH_SCOPE_OPTIONS, default=FETCH_SCOPE_DAY_FULL)
    parser.add_argument("--skip-initial-load", action="store_true")
    stale_cache_group = parser.add_mutually_exclusive_group()
    stale_cache_group.add_argument("--allow-stale-cache", dest="allow_stale_cache", action="store_true")
    stale_cache_group.add_argument("--no-stale-cache", dest="allow_stale_cache", action="store_false")
    parser.set_defaults(allow_stale_cache=True)
    parsed, _ = parser.parse_known_args(list(argv) if argv is not None else sys.argv[1:])
    requested_date = _parse_startup_date(str(parsed.requested_date or ""))
    start_date = _parse_startup_optional_date(str(parsed.start_date or ""))
    end_date = _parse_startup_optional_date(str(parsed.end_date or ""))
    fetch_scope = str(parsed.fetch_scope or FETCH_SCOPE_DAY_FULL)
    if (start_date is not None or end_date is not None) and fetch_scope == FETCH_SCOPE_DAY_FULL:
        fetch_scope = FETCH_SCOPE_RANGE_FULL
    zotero_export_path = Path(parsed.zotero_export) if parsed.zotero_export else None
    zotero_db_path = Path(parsed.zotero_db_path) if parsed.zotero_db_path else None
    request, _ = _normalize_ui_request(
        UIStartupRequest(
            selected_source=str(parsed.source or DEFAULT_REVIEWER_SOURCE),
            requested_date=requested_date,
            max_results=max(int(parsed.max_results or 80), 1),
            start_date=start_date,
            end_date=end_date,
            report_mode=str(parsed.report_mode or DEFAULT_REPORT_MODE),
            profile_source=str(parsed.profile_source).strip() or None,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=_normalize_text_selection(parsed.zotero_collection),
            fetch_scope=fetch_scope,
            allow_stale_cache=bool(parsed.allow_stale_cache),
            skip_initial_load=bool(parsed.skip_initial_load),
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


def _format_profile_source_label(profile_source: str) -> str:
    if profile_source == PROFILE_SOURCE_ZOTERO:
        return "Zotero"
    return "Baseline"


def _selected_collection_defaults(
    selected_collections: Sequence[str],
    available_collections: Sequence[str],
) -> tuple[str, ...]:
    available_lookup = {collection.lower(): collection for collection in available_collections}
    defaults: list[str] = []
    seen: set[str] = set()
    for collection in selected_collections:
        normalized = str(collection).strip().lower()
        if not normalized or normalized not in available_lookup or normalized in seen:
            continue
        defaults.append(available_lookup[normalized])
        seen.add(normalized)
    return tuple(defaults)


def _format_zotero_state_summary(state: ZoteroLibraryState) -> str:
    pieces: list[str] = []
    if state.ready:
        collection_text = f" across {len(state.collections)} collections" if state.collections else ""
        pieces.append(f"Reusable export ready: {state.item_count} items{collection_text}.")
    else:
        pieces.append("Reusable export is not ready yet.")
    if state.generated_at is not None:
        pieces.append(f"Last updated {state.generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.")
    if state.discovered_db_path is not None:
        pieces.append(f"Using local library {state.discovered_db_path.name} for refresh discovery.")
    elif state.candidate_db_paths:
        pieces.append(f"Checked {len(state.candidate_db_paths)} standard Zotero library locations.")
    if state.note:
        pieces.append(state.note)
    if state.error:
        pieces.append(f"Status: {state.error}")
    return " ".join(piece for piece in pieces if piece)


def _format_zotero_refresh_notice(state: ZoteroLibraryState) -> str:
    if state.ready and not state.error:
        collection_text = f" across {len(state.collections)} collections" if state.collections else ""
        return f"Updated Zotero export with {state.item_count} items{collection_text}."
    if state.ready:
        return f"Reusing existing Zotero export. {state.error or state.note}".strip()
    return f"Zotero export is still unavailable. {state.error or state.note}".strip()


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
        st.caption("Saved custom presets: " + ", ".join(bundle.label for bundle in custom_bundles))
    else:
        st.caption("Saved custom presets live in configs/source_bundles.json. Create one to keep a local topic bundle around.")

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

    name_key = _widget_key("bundle-manager", editor_target, "name")
    description_key = _widget_key("bundle-manager", editor_target, "description")
    sources_key = _widget_key("bundle-manager", editor_target, "sources")
    include_key = _widget_key("bundle-manager", editor_target, "include")
    exclude_key = _widget_key("bundle-manager", editor_target, "exclude")

    header_columns = st.columns((1.05, 0.95))
    name = header_columns[0].text_input(
        "Preset name",
        value=editing_bundle.label if editing_bundle is not None else "",
        key=name_key,
    )
    enabled_sources = tuple(
        header_columns[1].multiselect(
            "Enabled sources",
            options=("arxiv", "biorxiv", "medrxiv"),
            default=list(editing_bundle.enabled_sources if editing_bundle is not None else ("arxiv", "biorxiv", "medrxiv")),
            key=sources_key,
        )
    )
    description = st.text_input(
        "Short description",
        value=editing_bundle.description if editing_bundle is not None else "",
        key=description_key,
    )

    term_columns = st.columns(2)
    include_terms = term_columns[0].text_area(
        "Include terms",
        value="\n".join(editing_bundle.include_terms) if editing_bundle is not None else "",
        help="One term per line or comma-separated. Papers matching these terms stay in the preset.",
        key=include_key,
        height=140,
    )
    exclude_terms = term_columns[1].text_area(
        "Exclude terms",
        value="\n".join(editing_bundle.exclude_terms) if editing_bundle is not None else "",
        help="Optional negative terms to filter out locally after the daily snapshot is loaded.",
        key=exclude_key,
        height=140,
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


def _format_source_stats_text(source_run_stats: Sequence[SourceRunStats]) -> str:
    return " | ".join(
        _format_source_stats_row(item)
        for item in source_run_stats
    )


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


def _format_timing_metric_value(run_timings: RunTimings) -> str:
    if run_timings.total_seconds is not None:
        return f"{run_timings.total_seconds:.2f}s"
    if run_timings.report_seconds is not None:
        return f"{run_timings.report_seconds:.2f}s"
    return "n/a"


def _render_recent_runs(entries: Sequence[object]) -> None:
    if not entries:
        st.markdown(
            (
                '<div class="fc-empty-panel">'
                "<strong>No recent runs found under data/cache.</strong><br>"
                "Run a digest once and this lane will turn into an artifact log with report, cache, and provenance links."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        return

    for index, entry in enumerate(entries, start=1):
        requested_date = getattr(entry, "requested_date", None)
        effective_date = getattr(entry, "effective_date", None)
        mode_label = getattr(entry, "mode_label", "") or getattr(entry, "category", "n/a")
        fetch_status = getattr(entry, "fetch_status", "") or "n/a"
        ranked_count = getattr(entry, "ranked_count", 0)
        profile_basis = getattr(entry, "profile_basis", "") or "n/a"
        zotero_export_name = getattr(entry, "zotero_export_name", "")
        zotero_augmented = bool(getattr(entry, "zotero_augmented", False))
        exploration_pick_count = getattr(entry, "exploration_pick_count", None)
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
            else:
                summary_bits = [fetch_status, f"ranked {ranked_count}", profile_basis]
                if zotero_export_name:
                    summary_bits.append(f"zotero {zotero_export_name}")
                elif zotero_augmented:
                    summary_bits.append("zotero enabled")
                if isinstance(exploration_pick_count, int) and exploration_pick_count > 0:
                    summary_bits.append(f"exploration {exploration_pick_count}")
                st.caption(" | ".join(summary_bits))

            link_columns = st.columns(3)
            with link_columns[0]:
                if _render_artifact_action(
                        "Open recent report",
                        report_path,
                        missing_label="Report missing",
                        key=f"recent-report-{index}",
                        use_container_width=True,
                    ):
                    pass
                else:
                    st.caption("Report missing")
            with link_columns[1]:
                if _render_artifact_action(
                        "Open recent cache",
                        cache_path,
                        missing_label="Cache missing",
                        key=f"recent-cache-{index}",
                        use_container_width=True,
                    ):
                    pass
                else:
                    st.caption("Cache missing")
            with link_columns[2]:
                if _render_artifact_action(
                        "Open recent .eml",
                        eml_path,
                        missing_label="No .eml",
                        key=f"recent-eml-{index}",
                        use_container_width=True,
                    ):
                    pass
                else:
                    st.caption("No .eml")


def _top_recommendation_cards(cards: list[object]) -> list[object]:
    return cards[:3]


def _render_top_recommendations(cards: Sequence[object]) -> None:
    columns = st.columns(max(1, len(cards)))
    for index, (column, card) in enumerate(zip(columns, cards, strict=False), start=1):
        with column:
            with st.container(border=True):
                badge_class = "fc-badge fc-badge-strong" if card.is_recommended else "fc-badge"
                st.markdown(f'<div class="fc-card-title">{card.title}</div>', unsafe_allow_html=True)
                if card.url:
                    st.markdown(f"[Open on arXiv]({card.url})")
                st.markdown(
                    (
                        '<div class="fc-top-header">'
                        f'<div class="fc-top-rank">Top {index}</div>'
                        f'<div class="fc-theme">{card.theme_label}</div>'
                        f'<div class="{badge_class}">{card.status_label}</div>'
                        f'<div class="fc-score-secondary">Score {card.score:.3f}</div>'
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    " ".join(
                        part
                        for part in (
                            f"`{card.source_label}`",
                            f"`{card.zotero_effect_label}`" if card.zotero_effect_label else "",
                        )
                        if part
                    )
                )
                st.markdown(
                    (
                        '<div class="fc-meta-line">'
                        f"<strong>Published</strong> {card.published_text}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    (
                        '<div class="fc-meta-line">'
                        f"<strong>Authors</strong> {card.authors_text}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(" ".join(f"`{category_name}`" for category_name in card.categories) or "_uncategorized_")
                if card.why_it_surfaced:
                    st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
                st.write(card.recommendation_summary)
                with st.expander("Score details", expanded=False):
                    _render_score_breakdown(card)
                if card.url:
                    render_external_link(
                        "Open arXiv abstract",
                        card.url,
                        key=f"top-link-{index}",
                        use_container_width=True,
                    )
            st.markdown("")


def _render_exploration_cards(cards: Sequence[object]) -> None:
    columns = st.columns(max(1, len(cards)))
    for index, (column, card) in enumerate(zip(columns, cards, strict=False), start=1):
        with column:
            with st.container(border=True):
                badge_class = "fc-badge" if card.is_recommended else "fc-badge"
                st.markdown(
                    (
                        '<div class="fc-top-header">'
                        f'<div class="fc-top-rank">Explore {index}</div>'
                        f'<div class="fc-theme">{card.theme_label}</div>'
                        f'<div class="{badge_class}">{card.status_label}</div>'
                        "</div>"
                        f'<div class="fc-score fc-score-tight">Score {card.score:.3f}</div>'
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(f"#### {card.title if not card.url else f'[{card.title}]({card.url})'}")
                st.markdown(
                    " ".join(
                        part
                        for part in (
                            f"`{card.source_label}`",
                            f"`{card.zotero_effect_label}`" if card.zotero_effect_label else "",
                        )
                        if part
                    )
                )
                st.markdown(
                    (
                        '<div class="fc-meta-line">'
                        f"<strong>Published</strong> {card.published_text}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    (
                        '<div class="fc-meta-line">'
                        f"<strong>Authors</strong> {card.authors_text}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                st.markdown(" ".join(f"`{category_name}`" for category_name in card.categories) or "_uncategorized_")
                if card.why_it_surfaced:
                    st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
                st.write(card.recommendation_summary)
                with st.expander("Score details", expanded=False):
                    _render_score_breakdown(card)
                if card.url:
                    render_external_link(
                        "Open arXiv abstract",
                        card.url,
                        key=f"explore-link-{index}",
                        use_container_width=True,
                    )


def _render_ranked_cards(cards: Sequence[object]) -> None:
    for index, card in enumerate(cards, start=1):
        with st.container(border=True):
            badge_class = "fc-badge fc-badge-strong" if card.is_recommended else "fc-badge"
            st.markdown(f"### {index}. {card.title if not card.url else f'[{card.title}]({card.url})'}")
            st.markdown(
                (
                    f'<div class="fc-meta-line">'
                    f'<span class="fc-score-secondary">Score {card.score:.3f}</span>'
                    f' &middot; <span class="{badge_class}" style="margin:0">{card.status_label}</span>'
                    f' &middot; {card.theme_label}'
                    f'</div>'
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                " ".join(
                    part
                    for part in (
                        f"`{card.source_label}`",
                        f"`{card.zotero_effect_label}`" if card.zotero_effect_label else "",
                    )
                    if part
                )
            )
            meta_columns = st.columns((2.2, 1.1))
            meta_columns[0].markdown(f"**Authors**\n\n{card.authors_text}")
            meta_columns[1].markdown(f"**Published**\n\n{card.published_text}")
            st.markdown(" ".join(f"`{category_name}`" for category_name in card.categories) or "_uncategorized_")
            if card.why_it_surfaced:
                st.markdown(f"**{card.why_label}**: {card.why_it_surfaced}")
            st.write(card.recommendation_summary)
            with st.expander("Score details", expanded=False):
                _render_score_breakdown(card)
            if card.url:
                st.markdown(f"[Open abstract on arXiv]({card.url})")
        st.markdown("")


def _render_frontier_highlights(items: Sequence[object], *, show_score: bool = False) -> None:
    if not items:
        st.info("No frontier highlights are available for the current run.")
        return

    for index, item in enumerate(items, start=1):
        with st.container(border=True):
            title = getattr(item, "title", "")
            url = getattr(item, "url", "")
            published = getattr(item, "published", None)
            categories = getattr(item, "categories", ())
            theme_label = getattr(item, "theme_label", "")
            why = getattr(item, "why", "")
            summary = getattr(item, "summary", "")
            identifier = getattr(item, "identifier", "")
            score = getattr(item, "score", None)
            title_markup = title if not url else f"[{title}]({url})"
            badge_class = "fc-badge fc-badge-strong" if show_score and isinstance(score, (int, float)) else "fc-badge"
            badge_label = theme_label or f"Highlight {index}"
            st.markdown(f'<div class="{badge_class}">{badge_label}</div>', unsafe_allow_html=True)
            if show_score and isinstance(score, (int, float)):
                st.markdown(f'<div class="fc-score">Profile score {score:.3f}</div>', unsafe_allow_html=True)
            st.markdown(f"### {index}. {title_markup}")
            meta_bits = []
            if published is not None:
                meta_bits.append(f"published {published.isoformat()}")
            if identifier:
                meta_bits.append(identifier)
            if meta_bits:
                st.caption(" | ".join(meta_bits))
            st.markdown(" ".join(f"`{category_name}`" for category_name in categories) or "_uncategorized_")
            if why:
                st.markdown(f"**Why it matters**: {why}")
            if summary:
                st.write(summary)


def _render_signal_row(signals: tuple[object, ...]) -> None:
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
        badges.append(f'<span class="fc-signal">{label}{suffix}</span>')
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
          --fc-bg: #120f0d;
          --fc-bg-soft: #1c1715;
          --fc-panel: rgba(28, 22, 19, 0.88);
          --fc-panel-strong: rgba(20, 15, 12, 0.96);
          --fc-border: rgba(209, 180, 132, 0.18);
          --fc-ink: #f6efe4;
          --fc-muted: #bca996;
          --fc-accent: #d3a35f;
          --fc-accent-cool: #7ec2b3;
          --fc-danger: #f2a07c;
          --fc-shadow: 0 22px 48px rgba(0, 0, 0, 0.28);
          --fc-serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
          --fc-sans: "Avenir Next", "Trebuchet MS", "Gill Sans", sans-serif;
        }
        .stApp {
          background:
            radial-gradient(circle at top left, rgba(211, 163, 95, 0.18), transparent 24%),
            radial-gradient(circle at top right, rgba(126, 194, 179, 0.14), transparent 22%),
            linear-gradient(180deg, #120f0d 0%, #171210 48%, #211914 100%);
          color: var(--fc-ink);
          font-family: var(--fc-sans);
        }
        .fc-hero {
          padding: 0.3rem 0 1.4rem 0;
          border-bottom: 1px solid var(--fc-border);
          margin-bottom: 1.1rem;
        }
        .fc-kicker {
          display: inline-block;
          padding: 0.24rem 0.72rem;
          border-radius: 999px;
          background: rgba(211, 163, 95, 0.14);
          border: 1px solid rgba(211, 163, 95, 0.24);
          color: #f2c588;
          font-size: 0.78rem;
          font-weight: 700;
          letter-spacing: 0.14em;
          text-transform: uppercase;
        }
        .fc-hero h1 {
          color: var(--fc-ink);
          font-family: var(--fc-serif);
          margin: 0.65rem 0 0.4rem 0;
          font-size: 2.6rem;
          letter-spacing: -0.04em;
        }
        .fc-hero p {
          color: var(--fc-muted);
          margin: 0;
          max-width: 52rem;
          line-height: 1.6;
        }
        .fc-overview-shell {
          margin: 1rem 0 1.4rem 0;
          padding: 1rem 1rem 1.1rem;
          border: 1px solid var(--fc-border);
          border-radius: 1.2rem;
          background:
            linear-gradient(135deg, rgba(211, 163, 95, 0.08), rgba(126, 194, 179, 0.05)),
            var(--fc-panel);
          box-shadow: var(--fc-shadow);
        }
        .fc-overview-head h2 {
          margin: 0.18rem 0 0.25rem 0;
          color: var(--fc-ink);
          font-family: var(--fc-serif);
          font-size: 1.7rem;
        }
        .fc-overview-head p {
          margin: 0;
          color: var(--fc-muted);
          max-width: 44rem;
        }
        .fc-overview-kicker {
          color: var(--fc-accent-cool);
          text-transform: uppercase;
          letter-spacing: 0.16em;
          font-size: 0.72rem;
          font-weight: 700;
        }
        .fc-overview-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
          gap: 0.85rem;
          margin-top: 1rem;
        }
        .fc-overview-card {
          padding: 0.95rem 1rem;
          border-radius: 1rem;
          border: 1px solid rgba(209, 180, 132, 0.14);
          background: rgba(17, 13, 11, 0.74);
          min-height: 8.2rem;
        }
        .fc-overview-label {
          color: var(--fc-muted);
          text-transform: uppercase;
          letter-spacing: 0.12em;
          font-size: 0.72rem;
          font-weight: 700;
        }
        .fc-overview-value {
          color: var(--fc-ink);
          font-family: var(--fc-serif);
          font-size: 1.4rem;
          line-height: 1.15;
          margin-top: 0.35rem;
        }
        .fc-overview-card p {
          color: var(--fc-muted);
          margin: 0.5rem 0 0;
          line-height: 1.55;
        }
        .fc-chip-row {
          display: flex;
          flex-wrap: wrap;
          gap: 0.55rem;
          margin-top: 0.9rem;
        }
        .fc-chip {
          display: inline-flex;
          align-items: center;
          gap: 0.25rem;
          padding: 0.34rem 0.7rem;
          border-radius: 999px;
          border: 1px solid rgba(209, 180, 132, 0.16);
          background: rgba(246, 239, 228, 0.04);
          color: var(--fc-ink);
          font-size: 0.83rem;
        }
        .fc-chip strong {
          color: var(--fc-muted);
        }
        .fc-chip-live {
          border-color: rgba(126, 194, 179, 0.28);
          color: #b8ebdf;
        }
        .fc-chip-stable {
          border-color: rgba(211, 163, 95, 0.26);
          color: #f2c588;
        }
        .fc-chip-caution {
          border-color: rgba(242, 160, 124, 0.3);
          color: #ffc4ab;
        }
        .fc-badge {
          display: inline-block;
          margin: 0 0 0.5rem 0;
          padding: 0.25rem 0.58rem;
          border-radius: 999px;
          background: rgba(246, 239, 228, 0.06);
          border: 1px solid rgba(209, 180, 132, 0.16);
          color: #f2ddc0;
          font-size: 0.78rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.03em;
        }
        .fc-badge-strong {
          background: rgba(126, 194, 179, 0.16);
          border: 1px solid rgba(126, 194, 179, 0.28);
          color: #c4f4e9;
        }
        .fc-score {
          color: var(--fc-accent-cool);
          font-weight: 700;
          margin-bottom: 0.25rem;
        }
        .fc-score-tight {
          margin-bottom: 0.6rem;
        }
        .fc-top-header {
          display: flex;
          align-items: center;
          gap: 0.45rem;
          flex-wrap: wrap;
          margin-bottom: 0.15rem;
        }
        .fc-top-rank {
          display: inline-block;
          padding: 0.2rem 0.5rem;
          border-radius: 999px;
          background: rgba(211, 163, 95, 0.14);
          color: #f2ddc0;
          font-size: 0.78rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.03em;
        }
        .fc-theme {
          display: inline-block;
          padding: 0.2rem 0.55rem;
          border-radius: 999px;
          background: rgba(246, 239, 228, 0.06);
          color: var(--fc-ink);
          font-size: 0.78rem;
        }
        .fc-meta-line {
          color: var(--fc-muted);
          font-size: 0.92rem;
          margin-bottom: 0.3rem;
        }
        .fc-signal {
          display: inline-block;
          margin: 0 0.35rem 0.35rem 0;
          padding: 0.2rem 0.55rem;
          border-radius: 999px;
          background: rgba(126, 194, 179, 0.1);
          border: 1px solid rgba(126, 194, 179, 0.18);
          color: #b8ebdf;
          font-size: 0.85rem;
        }
        .fc-link-fallback-link {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 2.6rem;
          padding: 0.55rem 0.9rem;
          border-radius: 0.9rem;
          border: 1px solid rgba(209, 180, 132, 0.18);
          background: rgba(246, 239, 228, 0.05);
          color: var(--fc-ink);
          font-weight: 700;
          text-decoration: none;
        }
        .fc-link-primary {
          border-color: rgba(211, 163, 95, 0.28);
          background: rgba(211, 163, 95, 0.16);
          color: #fff0d7;
        }
        .fc-card-title {
          color: var(--fc-ink);
          font-family: var(--fc-serif);
          font-size: 1.2rem;
          line-height: 1.2;
          margin-bottom: 0.45rem;
        }
        .fc-empty-panel {
          padding: 0.95rem 1rem;
          border-radius: 1rem;
          border: 1px dashed rgba(209, 180, 132, 0.24);
          background: rgba(246, 239, 228, 0.04);
          color: var(--fc-muted);
          line-height: 1.6;
        }
        [data-testid="stMetricValue"] {
          color: var(--fc-ink);
          font-family: var(--fc-serif);
        }
        [data-testid="stMetricLabel"] {
          color: var(--fc-muted);
        }
        [data-testid="stSidebar"] {
          background: rgba(20, 15, 12, 0.74);
          border-right: 1px solid var(--fc-border);
        }
        [data-testid="stExpander"] details {
          background: rgba(28, 22, 19, 0.76);
          border: 1px solid var(--fc-border);
          border-radius: 0.9rem;
        }
        [data-testid="stTabs"] button {
          font-family: var(--fc-sans);
          letter-spacing: 0.05em;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
          color: #fff0d7;
        }
        div[data-baseweb="slider"] > div {
          color: var(--fc-accent-cool);
        }
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stCaptionContainer"] {
          line-height: 1.65;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_app()
