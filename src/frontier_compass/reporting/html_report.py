"""HTML report rendering for ranked paper shortlists."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape, unescape
from typing import Sequence

from frontier_compass.common.report_mode import (
    format_llm_bool,
    format_llm_provider,
    format_llm_seconds,
    format_llm_summary,
)
from frontier_compass.common.source_bundles import resolve_source_bundle
from frontier_compass.exploration.selector import (
    daily_exploration_intro,
    daily_exploration_note,
    resolve_daily_exploration_picks,
)
from frontier_compass.ranking.relevance import (
    explanation_breakdown_rows,
    explanation_detail_lines,
    interest_relevance_line,
    priority_label_for_score,
    recommendation_explanation_for_ranked_paper,
    score_explanation_line,
    why_this_paper_line,
    zotero_effect_badge_text,
)
from frontier_compass.reporting.daily_brief import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    DailyBriefSummary,
    build_daily_brief,
    build_reviewer_shortlist,
    summarize_category_counts,
    theme_label_for_ranked_paper,
)
from frontier_compass.storage.schema import (
    DailyDigest,
    DailyFrontierReport,
    FrontierReportHighlight,
    RankedPaper,
    RequestWindow,
    RunTimings,
    SourceRunStats,
    UserInterestProfile,
)


BIOMEDICAL_LATEST_MODE = "biomedical-latest"
BIOMEDICAL_DISCOVERY_MODE = "biomedical-discovery"
BIOMEDICAL_DAILY_MODE = "biomedical-daily"
BIOMEDICAL_MULTISOURCE_MODE = "biomedical-multisource"


class HtmlReportBuilder:
    def render(
        self,
        profile: UserInterestProfile,
        ranked_papers: Sequence[RankedPaper],
        *,
        title: str = "FrontierCompass Report",
    ) -> str:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        shortlist, shortlist_title = _build_shortlist(ranked_papers)
        brief = build_daily_brief(profile, shortlist, total_ranked=len(ranked_papers))
        summary_html = "".join(
            (
                _render_summary_panel("Shown / ranked", f"{brief.shown_count} / {brief.total_ranked}"),
                _render_summary_panel("Recommended", str(brief.recommended_count)),
                _render_summary_panel("Average score", f"{brief.average_score:.3f}"),
                _render_summary_panel(
                    "Visible themes",
                    _signal_text(brief.top_theme_signals) or "No repeated theme signal",
                ),
                _render_summary_panel(
                    "Top category signals",
                    _signal_text(brief.top_category_signals) or "No strong category cluster",
                ),
            )
        )
        today_panel_html = (
            f"<p><strong>Profile mode</strong><br>{escape(profile.basis_summary_label)}</p>"
            f"<p><strong>Profile basis</strong><br>{escape(_profile_basis_text(profile))}</p>"
            f"<p><strong>Profile source</strong><br>{escape(_profile_source_text(profile))}</p>"
            f"<p><strong>Profile items parsed / used</strong><br>{escape(_profile_item_text(profile) or 'n/a')}</p>"
            f"<p><strong>Top profile terms</strong><br>{escape(_profile_terms_text(profile) or 'No dominant profile terms')}</p>"
            f"<p><strong>Top Zotero signals</strong><br>{escape(_zotero_signal_text(profile) or 'No Zotero augmentation in this report')}</p>"
            f"<p><strong>Zotero retrieval hints</strong><br>{escape(_zotero_retrieval_hint_text(profile) or 'No Zotero retrieval augmentation in this report')}</p>"
        )
        audit_html = (
            f"<p><strong>Profile basis</strong><br>{escape(_profile_basis_text(profile))}</p>"
            f"<p><strong>Profile source</strong><br>{escape(_profile_source_text(profile))}</p>"
            f"<p><strong>Profile path</strong><br>{escape(profile.profile_path or 'n/a')}</p>"
            f"<p><strong>Profile items parsed / used</strong><br>{escape(_profile_item_text(profile) or 'n/a')}</p>"
            f"<p><strong>Top profile terms</strong><br>{escape(_profile_terms_text(profile) or 'No dominant profile terms')}</p>"
            f"<p><strong>Biomedical keywords</strong><br>{escape(_signal_text(brief.top_keyword_signals) or 'No repeated keyword signals')}</p>"
            f"<p><strong>Zotero signals</strong><br>{escape(_zotero_signal_text(profile) or 'No Zotero augmentation in this report')}</p>"
            f"<p><strong>Zotero retrieval hints</strong><br>{escape(_zotero_retrieval_hint_text(profile) or 'No Zotero retrieval augmentation in this report')}</p>"
            f"<p><strong>Profile categories</strong><br>{escape(', '.join(profile.top_categories()) or 'No profile categories')}</p>"
            f"<p><strong>Seed titles</strong><br>{escape('; '.join(profile.seed_titles[:3]) or 'No seed titles')}</p>"
        )
        return _render_report_document(
            title=title,
            generated_at=generated_at,
            intro_text=profile.notes or "FrontierCompass research briefing.",
            summary_html=summary_html,
            brief=brief,
            today_panel_html=today_panel_html,
            provenance_html="",
            shortlist_title=shortlist_title,
            shortlist_cards="\n".join(_render_card(item, profile=profile, highlight=True) for item in shortlist)
            or "<p>No shortlist papers met the current threshold.</p>",
            exploration_html="",
            audit_html=audit_html,
            full_cards="\n".join(_render_card(item, profile=profile) for item in ranked_papers) or "<p>No papers matched the current profile.</p>",
            show_full_ranked=bool(ranked_papers) and shortlist != list(ranked_papers),
        )

    def render_daily_digest(
        self,
        digest: DailyDigest,
        *,
        title: str | None = None,
        acquisition_status_label: str = "",
        fetch_error: str = "",
    ) -> str:
        resolved_title = title or daily_digest_title(digest)
        generated_at = digest.generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        shortlist, shortlist_title = _build_shortlist(digest.ranked)
        exploration_picks = resolve_daily_exploration_picks(digest)
        brief = build_daily_brief(digest.profile, shortlist, total_ranked=len(digest.ranked))
        frontier_report = digest.frontier_report
        searched = ", ".join(digest.searched_categories) or digest.category or "n/a"
        category_counts = summarize_category_counts(digest.searched_categories, digest.per_category_counts)
        mode_label = digest.mode_label or digest.category or "n/a"
        source_mix_text = _source_count_summary(digest.source_run_stats, digest.source_counts)
        if not source_mix_text and frontier_report is not None:
            source_mix_text = _source_count_summary(frontier_report.source_run_stats, frontier_report.source_counts)
        summary_sections = []
        if acquisition_status_label:
            summary_sections.append(_render_summary_panel("Fetch status", acquisition_status_label))
        if fetch_error:
            summary_sections.append(_render_summary_panel("Fresh fetch error", fetch_error))
        summary_sections.extend(
            (
                _render_summary_panel(
                    "Request window",
                    _request_window_text(digest.request_window),
                ),
                _render_summary_panel("Display basis", digest.selection_basis_label),
                _render_summary_panel("Mode", f"{mode_label} ({digest.category})" if digest.category else mode_label),
                _render_summary_panel("Fetch scope", digest.fetch_scope or "n/a"),
                _render_summary_panel("Ranked pool", str(digest.total_ranked_count)),
                _render_summary_panel("Personalized shortlist", str(len(shortlist))),
                _render_summary_panel("Recommended", str(brief.recommended_count)),
                _render_summary_panel(
                    "Repeated shortlist themes",
                    _signal_text(brief.top_theme_signals) or "No repeated shortlist theme",
                ),
                _render_summary_panel(
                    "Interest signals",
                    _signal_text(brief.top_keyword_signals) or "No repeated interest signal",
                ),
            )
        )
        if source_mix_text:
            summary_sections.insert(
                min(len(summary_sections), 4),
                _render_summary_panel("Source composition", source_mix_text),
            )
        summary_sections.insert(
            min(len(summary_sections), 5),
            _render_summary_panel(
                "LLM runtime",
                format_llm_summary(
                    llm_requested=digest.llm_requested,
                    llm_applied=digest.llm_applied,
                    llm_provider=digest.llm_provider,
                ),
            ),
        )
        summary_html = "".join(summary_sections)
        today_panel_html = (
            f"<p><strong>Profile mode</strong><br>{escape(digest.profile.basis_summary_label)}</p>"
            f"<p><strong>Profile basis</strong><br>{escape(_profile_basis_text(digest.profile))}</p>"
            f"<p><strong>Profile source</strong><br>{escape(_profile_source_text(digest.profile))}</p>"
            f"<p><strong>Profile items parsed / used</strong><br>{escape(_profile_item_text(digest.profile) or 'n/a')}</p>"
            f"<p><strong>Top profile terms</strong><br>{escape(_profile_terms_text(digest.profile) or 'No dominant profile terms')}</p>"
            f"<p><strong>Top Zotero signals</strong><br>{escape(_zotero_signal_text(digest.profile) or 'No Zotero augmentation in this digest')}</p>"
            f"<p><strong>Zotero retrieval hints</strong><br>{escape(_zotero_retrieval_hint_text(digest.profile) or 'No Zotero retrieval augmentation in this digest')}</p>"
        )
        audit_parts: list[str] = []
        if acquisition_status_label:
            audit_parts.append(_render_audit_paragraph("Fetch status", acquisition_status_label))
        if fetch_error:
            audit_parts.append(_render_audit_paragraph("Fresh fetch error", fetch_error))
        audit_parts.extend(
            (
                _render_audit_paragraph("Requested date", digest.requested_target_date.isoformat()),
                _render_audit_paragraph("Effective release date", digest.effective_display_date.isoformat()),
                _render_audit_paragraph("Request window", _request_window_text(digest.request_window)),
                _render_audit_paragraph(
                    "Latest-available display fallback",
                    "yes" if digest.used_latest_available_fallback else "no",
                ),
                _render_audit_paragraph(
                    "Stale cache fallback",
                    "yes" if digest.stale_cache_fallback_used else "no",
                ),
                _render_audit_paragraph("Selection status", _selection_status_text(digest)),
                _render_audit_paragraph("Display basis", digest.selection_basis_label),
                _render_audit_paragraph("Mode", f"{mode_label} ({digest.category})" if digest.category else mode_label),
                _render_audit_paragraph("Mode kind", digest.mode_kind or "n/a"),
                _render_audit_paragraph("Requested report mode", digest.requested_report_mode),
                _render_audit_paragraph("Frontier Report mode", digest.report_mode),
                _render_audit_paragraph("Cost mode", digest.cost_mode),
                _render_audit_paragraph("Report status", digest.report_status or "ready"),
                _render_audit_paragraph("Fetch scope", digest.fetch_scope or "n/a"),
                _render_audit_paragraph("Enhanced track", digest.enhanced_track or "none"),
                _render_audit_paragraph("Enhanced item count", str(digest.enhanced_item_count)),
                _render_audit_paragraph("Runtime note", digest.runtime_note or "No additional runtime note."),
                _render_audit_paragraph("LLM requested", format_llm_bool(digest.llm_requested)),
                _render_audit_paragraph("LLM applied", format_llm_bool(digest.llm_applied)),
                _render_audit_paragraph("LLM provider", format_llm_provider(digest.llm_provider)),
                _render_audit_paragraph("LLM fallback reason", digest.llm_fallback_reason or "none"),
                _render_audit_paragraph("LLM time", format_llm_seconds(digest.llm_seconds)),
                _render_audit_paragraph("Timings", _timings_text(digest.run_timings) or "n/a"),
                _render_audit_paragraph("Mode notes", digest.mode_notes or "No additional mode notes."),
                _render_audit_paragraph("Searched categories", searched),
                _render_audit_paragraph("Strict same-day fetched / ranked", digest.strict_same_day_counts_label),
                _render_audit_paragraph("Total fetched", str(max(digest.total_fetched, digest.total_ranked_count))),
                _render_audit_paragraph("Total ranked pool", str(digest.total_ranked_count)),
                _render_audit_paragraph("Total displayed", str(digest.total_displayed_count)),
                _render_audit_paragraph(
                    "Display contract",
                    _display_contract_text(digest),
                ),
                _render_audit_paragraph("Per-category counts", " | ".join(category_counts) or "n/a"),
                _render_audit_paragraph(
                    "Source mix",
                    _source_count_summary(digest.source_run_stats, digest.source_counts) or "n/a",
                ),
                _render_audit_paragraph(
                    "Frontier ranked pool",
                    str(frontier_report.total_ranked) if frontier_report is not None else "unavailable",
                ),
                _render_audit_paragraph("Search profile", digest.search_profile_label or "n/a"),
                _render_audit_paragraph("Profile basis", _profile_basis_text(digest.profile)),
                _render_audit_paragraph("Profile source", _profile_source_text(digest.profile)),
                _render_audit_paragraph("Profile path", digest.profile.profile_path or "n/a"),
                _render_audit_paragraph("Profile items parsed / used", _profile_item_text(digest.profile) or "n/a"),
                _render_audit_paragraph("Top profile terms", _profile_terms_text(digest.profile) or "No dominant profile terms"),
                _render_audit_paragraph(
                    "Zotero signals",
                    _zotero_signal_text(digest.profile) or "No Zotero augmentation in this digest",
                ),
                _render_audit_paragraph(
                    "Zotero retrieval hints",
                    _zotero_retrieval_hint_text(digest.profile) or "No Zotero retrieval augmentation in this digest",
                ),
                _render_audit_paragraph("Exploration policy", _exploration_policy_text(digest) or "n/a"),
                _render_audit_paragraph(
                    "Personalized visible themes",
                    _signal_text(brief.top_theme_signals) or "No repeated theme signal",
                ),
                _render_audit_paragraph(
                    "Frontier repeated themes",
                    (
                        _signal_text(frontier_report.repeated_themes) or "No repeated theme signal"
                        if frontier_report is not None
                        else "Legacy cache is missing the frontier report contract."
                    ),
                ),
                _render_audit_paragraph(
                    "Frontier salient topics",
                    (
                        _signal_text(frontier_report.salient_topics) or "No dominant topic bucket"
                        if frontier_report is not None
                        else "Legacy cache is missing the frontier report contract."
                    ),
                ),
                _render_audit_paragraph(
                    "Frontier adjacent themes",
                    (
                        _signal_text(frontier_report.adjacent_themes) or "No notable adjacent theme"
                        if frontier_report is not None
                        else "Legacy cache is missing the frontier report contract."
                    ),
                ),
                _render_audit_paragraph(
                    "Biomedical keywords",
                    _signal_text(brief.top_keyword_signals) or "No repeated keyword signals",
                ),
                _render_audit_paragraph(
                    "Top category signals",
                    _signal_text(brief.top_category_signals) or "No strong category cluster",
                ),
                _render_audit_paragraph("Feed coverage", " | ".join(category_counts) or searched),
            )
        )
        if digest.stale_cache_fallback_used:
            audit_parts.append(
                _render_audit_paragraph(
                    "Stale cache source requested date",
                    (
                        digest.stale_cache_source_requested_date.isoformat()
                        if digest.stale_cache_source_requested_date is not None
                        else "unknown"
                    ),
                )
            )
            audit_parts.append(
                _render_audit_paragraph(
                    "Stale cache source effective date",
                    (
                        digest.stale_cache_source_effective_date.isoformat()
                        if digest.stale_cache_source_effective_date is not None
                        else "unknown"
                    ),
                )
            )
        audit_html = "".join(audit_parts) + (
            f"{_render_source_contract_panel(digest.source_metadata)}"
            f"{_render_source_endpoint_panel(digest.source_endpoints)}"
            f"{_render_query_panel(digest.search_queries)}"
        )
        provenance_html = _render_source_provenance_panel(
            title="Digest source provenance",
            source_run_stats=digest.source_run_stats,
            source_counts=digest.source_counts,
            section_note=(
                "Requested sources stay visible here even when they returned zero papers, failed, or were "
                "served from same-day or stale-compatible cache."
            ),
        )
        personalized_cards_html = (
            "\n".join(_render_card(item, profile=digest.profile, highlight=True) for item in shortlist)
            or "<p>No shortlist papers met the current threshold.</p>"
        )
        personalized_section_html = (
            '<p class="section-note">Personalized reading lane for what to open first after you have scanned the field-wide report.</p>'
            f"<h3>{escape(shortlist_title)}</h3>"
            f"{personalized_cards_html}"
        )
        if exploration_picks:
            personalized_section_html += _render_exploration_section(
                digest,
                exploration_picks=exploration_picks,
                include_section_wrapper=False,
            )
        if digest.total_ranked_count > len(shortlist):
            personalized_section_html += (
                '<div class="panel">'
                f"<p><strong>Ranked pool kept off-page</strong><br>Showing {digest.total_displayed_count} "
                f"reading-first items in this report while preserving a full ranked pool of "
                f"{digest.total_ranked_count} papers in the cache and UI.</p>"
                "</div>"
            )
        frontier_html = (
            _render_frontier_report_section(frontier_report, profile=digest.profile)
            if frontier_report is not None
            else _render_missing_frontier_report_section()
        )
        intro_text = _daily_intro_text(digest)
        run_summary_json = json.dumps(
            _build_report_run_summary(
                digest,
                acquisition_status_label=acquisition_status_label,
                fetch_error=fetch_error,
            ),
            indent=2,
            sort_keys=True,
        )
        return _render_report_document(
            title=resolved_title,
            generated_at=generated_at,
            intro_text=intro_text,
            summary_html=summary_html,
            brief=brief,
            today_panel_html=today_panel_html,
            provenance_html=provenance_html,
            shortlist_title="Most Relevant to Your Zotero",
            shortlist_cards=personalized_section_html,
            exploration_html=frontier_html,
            audit_html=audit_html,
            full_cards="",
            show_full_ranked=False,
            run_summary_json=run_summary_json,
        )


def render_report(profile: UserInterestProfile, ranked_papers: Sequence[RankedPaper], *, title: str = "FrontierCompass Report") -> str:
    return HtmlReportBuilder().render(profile, ranked_papers, title=title)


def extract_report_summary_value(report_html: str, label: str) -> str:
    patterns = (
        rf"<strong>{re.escape(label)}</strong>\s*<p>(.*?)</p>",
        rf"<p><strong>{re.escape(label)}</strong><br>(.*?)</p>",
    )
    for pattern in patterns:
        match = re.search(pattern, report_html, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            continue
        return _normalize_html_text(match.group(1))
    return ""


def _render_report_document(
    *,
    title: str,
    generated_at: str,
    intro_text: str,
    summary_html: str,
    brief: DailyBriefSummary,
    today_panel_html: str,
    provenance_html: str,
    shortlist_title: str,
    shortlist_cards: str,
    exploration_html: str,
    audit_html: str,
    full_cards: str,
    show_full_ranked: bool,
    run_summary_json: str = "",
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f1e7;
      --bg-soft: #efe6d7;
      --panel: rgba(255, 251, 245, 0.92);
      --panel-strong: rgba(255, 255, 252, 0.98);
      --ink: #231813;
      --ink-soft: #45362f;
      --muted: #7a685b;
      --accent: #8d4f24;
      --accent-soft: rgba(141, 79, 36, 0.1);
      --border: rgba(107, 74, 52, 0.16);
      --strong: #1f6d63;
      --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      --sans: "Avenir Next", "Trebuchet MS", "Gill Sans", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: var(--sans); background:
      radial-gradient(circle at top left, rgba(141, 79, 36, 0.12), transparent 24%),
      radial-gradient(circle at top right, rgba(31, 109, 99, 0.12), transparent 20%),
      linear-gradient(180deg, var(--bg) 0%, var(--bg-soft) 100%); color: var(--ink); }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 34px 22px 54px; }}
    header {{ margin-bottom: 28px; padding: 0 0 20px; border-bottom: 1px solid var(--border); }}
    h1, h2, h3 {{ font-family: var(--serif); letter-spacing: -0.025em; }}
    h1 {{ margin: 0 0 10px; max-width: 14ch; font-size: clamp(2.9rem, 6vw, 4.8rem); line-height: 0.98; text-wrap: balance; }}
    h2 {{ margin: 0 0 14px; font-size: 1.6rem; }}
    h3 {{ margin: 0 0 10px; font-size: 1.3rem; }}
    .meta {{ color: var(--muted); font-size: 0.96rem; letter-spacing: 0.02em; }}
    header > p {{ max-width: 64ch; margin: 0; color: var(--ink-soft); font-size: 1.06rem; line-height: 1.85; }}
    .summary {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); margin: 22px 0 16px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 22px; padding: 18px; box-shadow: 0 22px 48px rgba(62, 37, 17, 0.08); backdrop-filter: blur(10px); }}
    .panel p {{ color: var(--ink-soft); line-height: 1.72; }}
    .brief-grid {{ display: grid; gap: 16px; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); margin-bottom: 24px; }}
    .paper {{ background: var(--panel-strong); border: 1px solid var(--border); border-radius: 22px; padding: 22px; margin-bottom: 16px; box-shadow: 0 18px 38px rgba(62, 37, 17, 0.08); }}
    .paper.highlight {{ border-color: rgba(31, 109, 99, 0.26); box-shadow: 0 20px 42px rgba(31, 109, 99, 0.08); }}
    .paper h3 {{ margin: 0 0 10px; font-size: 1.4rem; line-height: 1.18; text-wrap: balance; }}
    .paper p {{ margin: 0 0 14px; color: var(--ink-soft); line-height: 1.8; }}
    .paper .score {{ color: var(--strong); font-weight: bold; }}
    .paper .status {{ display: inline-block; margin-bottom: 8px; border-radius: 999px; padding: 4px 10px; background: var(--accent-soft); color: var(--accent); font-size: 0.84rem; font-weight: 700; }}
    .paper .theme {{ display: inline-block; margin: 0 0 8px 8px; border-radius: 999px; padding: 4px 10px; background: rgba(35, 24, 19, 0.05); color: var(--ink); font-size: 0.82rem; }}
    .section h3 {{ margin: 10px 0 12px; font-size: 1.08rem; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; padding: 0; list-style: none; }}
    .chips li {{ border: 1px solid var(--border); border-radius: 999px; padding: 4px 10px; color: var(--muted); font-size: 0.9rem; background: rgba(255, 250, 243, 0.9); }}
    .takeaways {{ margin: 0; padding-left: 20px; }}
    .takeaways li {{ margin-bottom: 10px; color: var(--ink-soft); line-height: 1.72; }}
    .section {{ margin-top: 30px; }}
    .section-note {{ color: var(--muted); margin: 0 0 14px; max-width: 68ch; line-height: 1.72; }}
    .table-wrap {{ overflow-x: auto; border-radius: 16px; }}
    table.provenance-table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
    table.provenance-table th,
    table.provenance-table td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    table.provenance-table th {{ color: var(--muted); font-weight: 700; font-size: 0.82rem; letter-spacing: 0.02em; }}
    table.provenance-table tr:last-child td {{ border-bottom: none; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; font-weight: 700; color: var(--ink); }}
    .breakdown {{ margin: 10px 0 0; padding-left: 18px; }}
    .breakdown li {{ margin-bottom: 6px; }}
    .badge-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 0; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 4px 10px; background: rgba(35, 24, 19, 0.05); color: var(--ink); font-size: 0.82rem; }}
    .section-kicker {{ margin: 0 0 8px; color: var(--accent); font-size: 0.82rem; font-weight: 700; letter-spacing: 0.04em; }}
    .paper-actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 2px; }}
    .action-link {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.58rem 0.9rem;
      border-radius: 999px;
      background: rgba(141, 79, 36, 0.08);
      border: 1px solid rgba(141, 79, 36, 0.14);
      color: var(--accent);
      font-weight: 700;
      text-decoration: none;
    }}
    .action-link:hover {{ background: rgba(141, 79, 36, 0.14); color: #6d3d1a; }}
    .action-muted {{ color: var(--muted); font-size: 0.92rem; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ color: #6d3d1a; }}
    @media (max-width: 820px) {{
      h1 {{ font-size: 2.2rem; }}
      .brief-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    {f'<script id="frontier-compass-run-summary" type="application/json">{escape(run_summary_json)}</script>' if run_summary_json else ""}
    <header>
      <div class="meta">Generated {escape(generated_at)}</div>
      <h1>{escape(title)}</h1>
      <p>{escape(intro_text)}</p>
    </header>
    <section class="brief-grid">
      <div class="panel">
        <h2>What to read first</h2>
        <ul class="takeaways">{_render_takeaways(brief)}</ul>
      </div>
      <div class="panel">
        <h2>Active profile</h2>
        {today_panel_html}
      </div>
    </section>
    <section class="summary">
      {summary_html}
    </section>
    {provenance_html}
    {exploration_html}
    <section class="section">
      <h2>{escape(shortlist_title)}</h2>
      {shortlist_cards}
    </section>
    <section class="section">
      <h2>Audit trail</h2>
      <details class="panel">
        <summary>Show run details and provenance</summary>
        {audit_html}
      </details>
    </section>
    {f'<section class="section"><details class="panel"><summary>All ranked papers</summary>{full_cards}</details></section>' if show_full_ranked else ""}
  </main>
</body>
</html>
"""


def _build_shortlist(ranked_papers: Sequence[RankedPaper]) -> tuple[list[RankedPaper], str]:
    return build_reviewer_shortlist(
        ranked_papers,
        max_items=8,
        recommended_threshold=DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    )


def daily_digest_title(digest: DailyDigest) -> str:
    requested_date = digest.requested_target_date.isoformat()
    requested_window = (
        f"{digest.request_window.resolved_start_date.isoformat()} to {digest.request_window.resolved_end_date.isoformat()}"
        if digest.request_window.is_range
        and digest.request_window.resolved_start_date is not None
        and digest.request_window.resolved_end_date is not None
        else requested_date
    )
    bundle = resolve_source_bundle(digest.category)
    if bundle is not None:
        return f"FrontierCompass {bundle.label} Brief ({requested_window})"
    if digest.category == BIOMEDICAL_DISCOVERY_MODE:
        return f"FrontierCompass Biomedical Discovery arXiv Brief ({requested_window})"
    if digest.category == BIOMEDICAL_LATEST_MODE:
        return f"FrontierCompass Biomedical Latest Available arXiv Brief (requested {requested_window})"
    if digest.category == BIOMEDICAL_DAILY_MODE:
        return f"FrontierCompass Biomedical Daily arXiv Brief ({requested_window})"
    if digest.category == BIOMEDICAL_MULTISOURCE_MODE:
        return f"FrontierCompass Biomedical 3-Source Compatibility Brief ({requested_window})"
    return f"FrontierCompass Daily arXiv Report ({digest.category}, {requested_window})"


def _daily_intro_text(digest: DailyDigest) -> str:
    requested_text = _request_window_text(digest.request_window)
    effective_text = digest.effective_display_date.isoformat()
    bundle = resolve_source_bundle(digest.category)
    if bundle is not None:
        lead = (
            f"Reviewer-ready {bundle.label.lower()} brief using same-day "
            f"{' and '.join(_format_source_label(source) for source in bundle.enabled_sources)} snapshots."
        )
    elif digest.category == BIOMEDICAL_LATEST_MODE:
        lead = "Reviewer-ready biomedical arXiv brief using a fixed q-bio bundle plus fixed broader arXiv searches."
    elif digest.category == BIOMEDICAL_MULTISOURCE_MODE:
        lead = "Compatibility-only biomedical multisource brief using the fixed q-bio arXiv bundle plus same-day bioRxiv and medRxiv feeds."
    elif digest.category == BIOMEDICAL_DISCOVERY_MODE:
        lead = "Broader strict same-day biomedical discovery brief using a fixed q-bio bundle plus fixed broader arXiv searches."
    elif digest.category == BIOMEDICAL_DAILY_MODE:
        lead = "Strict same-day biomedical arXiv brief across a fixed q-bio bundle."
    else:
        lead = "Strict same-day single-category arXiv brief filtered locally to the requested date."
    status = _selection_status_text(digest)
    acquisition_note = ""
    if digest.stale_cache_fallback_used:
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
        acquisition_note = (
            " Fresh fetch failed before a same-date digest could be materialized, so this report reuses "
            f"an older compatible cached digest from requested {source_requested} showing {source_effective}."
        )
    return (
        f"{lead} Requested {requested_text}; showing {effective_text}. "
        f"{status}{acquisition_note} Runtime: {digest.report_mode} / {digest.cost_mode}. "
        f"This report stays useful even without Zotero. Profile lane: {digest.profile.basis_summary_label or digest.profile.basis_label or 'baseline only'}."
    )


def _render_summary_panel(label: str, value: str) -> str:
    return f"""<div class="panel">
        <strong>{escape(label)}</strong>
        <p>{escape(value)}</p>
      </div>"""


def _render_audit_paragraph(label: str, value: str) -> str:
    return f"<p><strong>{escape(label)}</strong><br>{escape(value)}</p>"


def _render_query_panel(search_queries: Sequence[str]) -> str:
    if not search_queries:
        return "<p><strong>Search queries</strong><br>n/a</p>"
    return "<p><strong>Search queries</strong><br>" + "<br><br>".join(escape(query) for query in search_queries) + "</p>"


def _profile_basis_text(profile: UserInterestProfile) -> str:
    return profile.basis_label or "n/a"


def _profile_source_text(profile: UserInterestProfile) -> str:
    return f"{profile.profile_source} ({profile.profile_source_label})"


def _profile_item_text(profile: UserInterestProfile) -> str:
    if not profile.profile_item_count and not profile.profile_used_item_count:
        return ""
    return f"{profile.profile_item_count}/{profile.profile_used_item_count}"


def _profile_terms_text(profile: UserInterestProfile) -> str:
    return ", ".join(profile.top_profile_terms(limit=6))


def _zotero_signal_text(profile: UserInterestProfile) -> str:
    return ", ".join(profile.top_zotero_signals(limit=6))


def _zotero_retrieval_hint_text(profile: UserInterestProfile) -> str:
    hints = []
    for hint in profile.zotero_retrieval_hints:
        if hint.terms:
            hints.append(" + ".join(hint.terms))
    return "; ".join(hints)


def _exploration_policy_text(digest: DailyDigest) -> str:
    policy = digest.exploration_policy
    if policy is None:
        return ""
    summary = (
        f"{policy.label}: {policy.max_items} picks max, {policy.max_per_theme} per theme, "
        f"minimum score {policy.min_score:.2f}, minimum biomedical keyword {policy.min_biomedical_keyword:.2f}"
    )
    if policy.notes:
        summary += f". {policy.notes}"
    return summary


def _render_takeaways(brief: DailyBriefSummary) -> str:
    return "".join(f"<li>{escape(line)}</li>" for line in brief.takeaways)


def _selection_status_text(digest: DailyDigest) -> str:
    if digest.used_latest_available_fallback:
        return (
            "Showing latest available fallback results because the strict same-day subset for the requested date was empty."
        )
    return "Showing strict same-day results for the requested date."


def _display_contract_text(digest: DailyDigest) -> str:
    frontier_displayed = digest.frontier_displayed_count
    return (
        f"Personalized shortlist surfaces {min(digest.total_ranked_count, 8)} shortlist items and "
        f"{len(digest.exploration_picks)} exploration picks. Frontier Report surfaces "
        f"{frontier_displayed} highlight items. The remaining ranked pool stays in cache and UI instead of "
        "being dumped into the HTML artifact."
    )


def _signal_text(signals: Sequence[object]) -> str:
    values: list[str] = []
    for signal in signals:
        label = getattr(signal, "label", "")
        count = getattr(signal, "count", None)
        if not label:
            continue
        if isinstance(count, int):
            values.append(f"{label} ({count})")
        else:
            values.append(str(label))
    return ", ".join(values)


def _normalize_html_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(without_tags).split())


def _render_exploration_section(
    digest: DailyDigest,
    *,
    exploration_picks: Sequence[RankedPaper],
    include_section_wrapper: bool = True,
) -> str:
    cards_html = "\n".join(
        _render_card(
            item,
            profile=digest.profile,
            reason_label="Why it's exploratory",
            reason_text=daily_exploration_note(
                item,
                ranked_papers=digest.ranked,
                profile=digest.profile,
                policy=digest.exploration_policy,
            ),
        )
        for item in exploration_picks
    )
    content = (
        "<h3>Exploration picks</h3>"
        f'<p class="section-note">{escape(daily_exploration_intro(digest.profile, policy=digest.exploration_policy))}</p>'
        f"{cards_html or '<p>No exploration picks are available for this digest.</p>'}"
    )
    if include_section_wrapper:
        return f'<section class="section">{content}</section>'
    return content


def _render_frontier_report_section(frontier_report: DailyFrontierReport, *, profile: UserInterestProfile) -> str:
    scope_html = (
        f"<p><strong>Requested date</strong><br>{escape(frontier_report.requested_date.isoformat())}</p>"
        f"<p><strong>Effective release date</strong><br>{escape(frontier_report.effective_date.isoformat())}</p>"
        f"<p><strong>Source run</strong><br>{escape(_format_source_label(frontier_report.source))} / {escape(frontier_report.mode_label or frontier_report.mode)}</p>"
        f"<p><strong>Requested report mode</strong><br>{escape(frontier_report.requested_report_mode)}</p>"
        f"<p><strong>Applied report mode</strong><br>{escape(frontier_report.report_mode)}</p>"
        f"<p><strong>Cost mode</strong><br>{escape(frontier_report.cost_mode)}</p>"
        f"<p><strong>Report status</strong><br>{escape(frontier_report.report_status or 'ready')}</p>"
        f"<p><strong>Enhanced track</strong><br>{escape(frontier_report.enhanced_track or 'none')}</p>"
        f"<p><strong>Runtime note</strong><br>{escape(frontier_report.runtime_note or 'No additional runtime note.')}</p>"
        f"<p><strong>LLM requested</strong><br>{escape(format_llm_bool(frontier_report.llm_requested))}</p>"
        f"<p><strong>LLM applied</strong><br>{escape(format_llm_bool(frontier_report.llm_applied))}</p>"
        f"<p><strong>LLM provider</strong><br>{escape(format_llm_provider(frontier_report.llm_provider))}</p>"
        f"<p><strong>LLM fallback reason</strong><br>{escape(frontier_report.llm_fallback_reason or 'none')}</p>"
        f"<p><strong>LLM time</strong><br>{escape(format_llm_seconds(frontier_report.llm_seconds))}</p>"
        f"<p><strong>Searched categories</strong><br>{escape(', '.join(frontier_report.searched_categories) or 'n/a')}</p>"
        f"<p><strong>Total fetched / ranked pool</strong><br>{frontier_report.total_fetched} / {frontier_report.total_ranked}</p>"
        f"<p><strong>Total displayed</strong><br>{frontier_report.displayed_highlight_count}</p>"
        f"<p><strong>Timings</strong><br>{escape(_timings_text(frontier_report.run_timings) or 'n/a')}</p>"
        f"<p><strong>Profile basis</strong><br>{escape(_profile_basis_text(profile))}</p>"
    )
    status_panel = ""
    if frontier_report.report_status and frontier_report.report_status != "ready":
        status_panel = (
            '<div class="panel">'
            f'<p><strong>Frontier report status</strong><br>{escape(frontier_report.report_status)}</p>'
            f'<p>{escape(frontier_report.report_error or "This run completed with partial or empty report output.")}</p>'
            "</div>"
        )
    field_highlights_html = "".join(
        _render_frontier_highlight_card(item)
        for item in frontier_report.field_highlights
    ) or "<p>No field-wide highlights are available for this run.</p>"
    profile_highlights_html = ""
    if frontier_report.profile_relevant_highlights:
        profile_highlights_html = (
            "<h3>Profile-relevant highlights</h3>"
            '<p class="section-note">This stays secondary inside the frontier view and uses the existing profile-aware ranking only as a highlight signal.</p>'
            + "".join(_render_frontier_highlight_card(item, show_score=True) for item in frontier_report.profile_relevant_highlights)
        )
    return (
        '<section class="section">'
        '<div class="section-kicker">Primary reading lane</div>'
        "<h2>Daily Full Report</h2>"
        '<p class="section-note">Broader field scan built from the requested window&apos;s fetched pool using title, abstract, and categories only. It stays independent from Zotero so you can understand the day&apos;s field-level movement before switching into personalized reading.</p>'
        '<div class="brief-grid">'
        f'<div class="panel"><h2>What happened across the field today</h2><ul class="takeaways">{_render_frontier_takeaways(frontier_report.takeaways)}</ul></div>'
        f'<div class="panel"><h2>Field scope and provenance</h2>{scope_html}</div>'
        "</div>"
        f"{status_panel}"
        '<div class="summary">'
        f'{_render_summary_panel("Request window", _request_window_text(frontier_report.request_window))}'
        f'{_render_summary_panel("Fetch scope", frontier_report.fetch_scope or "n/a")}'
        f'{_render_summary_panel("Source composition", _source_count_summary(frontier_report.source_run_stats, frontier_report.source_counts) or "n/a")}'
        f'{_render_summary_panel("LLM runtime", format_llm_summary(llm_requested=frontier_report.llm_requested, llm_applied=frontier_report.llm_applied, llm_provider=frontier_report.llm_provider))}'
        f'{_render_summary_panel("Repeated themes / hotspots", _signal_text(frontier_report.repeated_themes) or "No repeated theme signal")}'
        f'{_render_summary_panel("Method hotspots", _signal_text(frontier_report.salient_topics) or "No dominant topic bucket")}'
        f'{_render_summary_panel("Adjacent signals", _signal_text(frontier_report.adjacent_themes) or "No notable adjacent theme")}'
        "</div>"
        f"{_render_source_provenance_panel(title='Frontier source provenance', source_run_stats=frontier_report.source_run_stats, source_counts=frontier_report.source_counts, section_note='Requested sources stay listed even when they were empty or lagged upstream, so a quiet bioRxiv day remains visible as zero-result truth rather than looking like a missing source.')}"
        "<h3>Notable highlights</h3>"
        f"{field_highlights_html}"
        f"{profile_highlights_html}"
        "</section>"
    )


def _render_missing_frontier_report_section() -> str:
    return (
        '<section class="section">'
        '<div class="section-kicker">Primary reading lane</div>'
        "<h2>Daily Full Report</h2>"
        '<p class="section-note">This cache predates the split daily artifact contract.</p>'
        '<div class="panel"><p><strong>Field-wide report unavailable</strong><br>'
        "This legacy cache does not contain a full-pool frontier report, so FrontierCompass will not infer one from the personalized slice."
        "</p></div>"
        "</section>"
    )


def _render_frontier_takeaways(lines: Sequence[str]) -> str:
    return "".join(f"<li>{escape(line)}</li>" for line in lines)


def _render_frontier_highlight_card(
    item: FrontierReportHighlight,
    *,
    show_score: bool = False,
) -> str:
    title_html = escape(item.title)
    if item.url:
        title_html = f'<a href="{escape(item.url)}">{title_html}</a>'
    categories = "".join(f"<li>{escape(category)}</li>" for category in item.categories[:6])
    meta_parts: list[str] = []
    if item.published is not None:
        meta_parts.append(escape(f"published {item.published.isoformat()}"))
    if item.identifier:
        meta_parts.append(escape(item.identifier))
    meta = " | ".join(meta_parts)
    badge_row = (
        f'<div class="badge-row"><span class="badge">{escape(_format_source_label(item.source))}</span>'
        f'<span class="badge">{escape(item.theme_label or "Highlight")}</span></div>'
    )
    score_html = (
        f'<p><strong>Score explanation</strong><br>{escape(_frontier_score_explanation_text(item.score))}</p>'
        if show_score and item.score is not None
        else ""
    )
    action_html = (
        f'<div class="paper-actions"><a class="action-link" href="{escape(item.url)}">Open source paper</a></div>'
        if item.url
        else '<div class="paper-actions"><span class="action-muted">No source link is attached to this highlight.</span></div>'
    )
    return f"""<article class="paper highlight">
  <h3>{title_html}</h3>
  {badge_row}
  <p><strong>Why highlighted</strong><br>{escape(item.why)}</p>
  {score_html}
  <p>{escape(item.summary or 'No summary provided.')}</p>
  <div class="meta">{meta}</div>
  <ul class="chips">{categories}</ul>
  {action_html}
</article>"""


def _render_full_ranked_section(
    ranked_papers: Sequence[RankedPaper],
    *,
    profile: UserInterestProfile,
) -> str:
    full_cards = "\n".join(_render_card(item, profile=profile) for item in ranked_papers) or "<p>No papers matched the current profile.</p>"
    return f'<details class="panel"><summary>All ranked papers</summary>{full_cards}</details>'


def _render_card(
    item: RankedPaper,
    *,
    profile: UserInterestProfile | None,
    highlight: bool = False,
    reason_label: str = "Why this paper",
    reason_text: str | None = None,
) -> str:
    paper = item.paper
    authors = ", ".join(paper.authors) or "Unknown authors"
    summary = item.recommendation_summary or paper.summary or "No summary provided."
    explanation = recommendation_explanation_for_ranked_paper(item, profile=profile)
    why_it_surfaced = reason_text if reason_text is not None else why_this_paper_line(explanation)
    score_explanation = score_explanation_line(explanation)
    relevance_explanation = interest_relevance_line(explanation)
    categories = "".join(f"<li>{escape(category)}</li>" for category in paper.categories[:6])
    title_html = escape(paper.title)
    if paper.url:
        title_html = f'<a href="{escape(paper.url)}">{title_html}</a>'

    meta_parts = [escape(authors)]
    if paper.published:
        meta_parts.insert(0, escape(f"published {paper.published.isoformat()}"))
    if paper.updated and paper.updated != paper.published:
        meta_parts.insert(1, escape(f"updated {paper.updated.isoformat()}"))
    if paper.source_identifier:
        meta_parts.append(escape(paper.source_identifier))
    meta = " | ".join(meta_parts)
    status = priority_label_for_score(item.score)
    class_name = "paper highlight" if highlight else "paper"
    retrieval_badge = '<span class="badge">Zotero retrieval</span>' if explanation.retrieval_support_origin == "zotero" else ""
    badge_row = (
        f'<div class="badge-row"><span class="badge">{escape(_format_source_label(paper.source))}</span>'
        f'<span class="badge">{escape(status)}</span>'
        f'<span class="badge">{escape(theme_label_for_ranked_paper(item))}</span>'
        f'<span class="badge">{escape(zotero_effect_badge_text(explanation.zotero_effect))}</span>'
        f"{retrieval_badge}</div>"
    )
    breakdown_rows = "".join(
        f"<li>{escape(label)}: {value:+.3f}</li>"
        for label, value in explanation_breakdown_rows(explanation)
    )
    detail_lines = "".join(
        f"<li>{escape(line)}</li>"
        for line in explanation_detail_lines(explanation)
    )
    score_details_html = (
        "<details>"
        "<summary>Score details</summary>"
        f"<p><strong>Total score</strong><br>{item.score:.3f}</p>"
        f'<ul class="breakdown">{breakdown_rows}{detail_lines}</ul>'
        "</details>"
    )

    return f"""<article class="{class_name}">
  <h3>{title_html}</h3>
  {badge_row}
  <div class="score">Score {item.score:.3f}</div>
  {f'<p><strong>{escape(reason_label)}</strong><br>{escape(why_it_surfaced)}</p>' if why_it_surfaced else ''}
  <p><strong>Score explanation</strong><br>{escape(score_explanation)}</p>
  <p><strong>Relevant to your interests</strong><br>{escape(relevance_explanation)}</p>
  <p>{escape(summary)}</p>
  <div class="meta">{meta}</div>
  <ul class="chips">{categories}</ul>
  {score_details_html}
</article>"""


def _frontier_score_explanation_text(score: float | None) -> str:
    if score is None:
        return "No profile-aware score is attached to this highlight."
    return f"Secondary profile overlay from the existing digest ranking: {score:.3f}."


def _compact_reason_line(reasons: Sequence[str], *, limit: int = 2) -> str:
    selected = [reason.strip() for reason in reasons[:limit] if reason and reason.strip()]
    if not selected:
        return ""
    return "; ".join(selected)


def _format_source_label(source: str) -> str:
    labels = {
        "arxiv": "arXiv",
        "biorxiv": "bioRxiv",
        "medrxiv": "medRxiv",
        "multisource": "Multisource",
    }
    normalized = (source or "unknown").strip().lower()
    return labels.get(normalized, normalized or "unknown")


def _source_text(source_counts: dict[str, int]) -> str:
    if not source_counts:
        return ""
    return ", ".join(
        f"{_format_source_label(source)} ({count})"
        for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        if source
    )


def _ordered_source_ids(
    source_run_stats: Sequence[SourceRunStats],
    source_counts: dict[str, int],
) -> list[str]:
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
    return ordered_sources


def _source_count_summary(
    source_run_stats: Sequence[SourceRunStats],
    source_counts: dict[str, int],
) -> str:
    stats_by_source = {str(item.source).strip().lower(): item for item in source_run_stats if str(item.source).strip()}
    ordered_sources = _ordered_source_ids(source_run_stats, source_counts)
    if not ordered_sources:
        return ""
    parts: list[str] = []
    for source in ordered_sources:
        label = _format_source_label(source)
        if source in stats_by_source:
            item = stats_by_source[source]
            parts.append(f"{label} {item.displayed_count} shown / {item.fetched_count} fetched")
        else:
            parts.append(f"{label} ({source_counts[source]})")
    return ", ".join(parts)


def _source_stats_text(source_run_stats: Sequence[SourceRunStats]) -> str:
    if not source_run_stats:
        return ""
    parts: list[str] = []
    for item in source_run_stats:
        piece = (
            f"{_format_source_label(item.source)} fetched {item.fetched_count} / retained {item.displayed_count}"
            f" [{item.resolved_outcome}; {item.status}; {item.cache_status}]"
        )
        extra_bits: list[str] = []
        if item.resolved_live_outcome != item.resolved_outcome:
            extra_bits.append(f"live: {item.resolved_live_outcome}")
        if item.error:
            extra_bits.append(f"error: {item.error}")
        if item.note:
            extra_bits.append(f"note: {item.note}")
        if extra_bits:
            piece = f"{piece} ({'; '.join(extra_bits)})"
        parts.append(piece)
    return " | ".join(parts)


def _source_issue_text(source_run_stat: SourceRunStats) -> str:
    details: list[str] = []
    if source_run_stat.error:
        details.append(source_run_stat.error)
    if source_run_stat.note:
        details.append(source_run_stat.note)
    return " | ".join(details) or "none"


def _source_requested_label(source_run_stat: SourceRunStats | None) -> str:
    if source_run_stat is None:
        return "n/a"
    return "yes"


def _source_included_label(*, displayed_count: int) -> str:
    return "yes" if displayed_count > 0 else "no"


def _render_source_provenance_panel(
    *,
    title: str,
    source_run_stats: Sequence[SourceRunStats],
    source_counts: dict[str, int],
    section_note: str = "",
) -> str:
    stats_by_source = {str(item.source).strip().lower(): item for item in source_run_stats if str(item.source).strip()}
    ordered_sources = _ordered_source_ids(source_run_stats, source_counts)
    if not ordered_sources:
        return ""

    rows: list[str] = []
    for source in ordered_sources:
        item = stats_by_source.get(source)
        if item is None:
            displayed_count = int(source_counts.get(source, 0))
            rows.append(
                "<tr>"
                f"<td>{escape(_format_source_label(source))}</td>"
                "<td>yes</td>"
                f"<td>{escape(_source_included_label(displayed_count=displayed_count))}</td>"
                "<td>n/a</td>"
                "<td>n/a</td>"
                "<td>n/a</td>"
                "<td>n/a</td>"
                "<td>n/a</td>"
                f"<td>{displayed_count}</td>"
                "<td>none</td>"
                "<td>n/a</td>"
                "</tr>"
            )
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(_format_source_label(item.source))}</td>"
            f"<td>{escape(_source_requested_label(item))}</td>"
            f"<td>{escape(_source_included_label(displayed_count=item.displayed_count))}</td>"
            f"<td>{escape(item.resolved_outcome)}</td>"
            f"<td>{escape(item.resolved_live_outcome)}</td>"
            f"<td>{escape(item.status or 'n/a')}</td>"
            f"<td>{escape(item.cache_status or 'n/a')}</td>"
            f"<td>{item.fetched_count}</td>"
            f"<td>{item.displayed_count}</td>"
            f"<td>{escape(_source_issue_text(item))}</td>"
            f"<td>{escape(_timings_text(item.timings) or 'n/a')}</td>"
            "</tr>"
        )
    note_html = f'<p class="section-note">{escape(section_note)}</p>' if section_note else ""
    return (
        '<section class="section">'
        '<div class="panel">'
        f"<h3>{escape(title)}</h3>"
        f"{note_html}"
        '<div class="table-wrap">'
        '<table class="provenance-table">'
        "<thead><tr>"
        "<th>Source</th>"
        "<th>Requested</th>"
        "<th>Included</th>"
        "<th>Outcome</th>"
        "<th>Live outcome</th>"
        "<th>Status</th>"
        "<th>Cache</th>"
        "<th>Fetched</th>"
        "<th>Displayed</th>"
        "<th>Error / note</th>"
        "<th>Timings</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
        "</div>"
        "</section>"
    )


def _request_window_text(request_window: RequestWindow) -> str:
    return request_window.label


def _timings_text(run_timings: RunTimings) -> str:
    timing_parts: list[str] = []
    if run_timings.cache_seconds is not None:
        timing_parts.append(f"cache {run_timings.cache_seconds:.2f}s")
    if run_timings.network_seconds is not None:
        timing_parts.append(f"network {run_timings.network_seconds:.2f}s")
    if run_timings.parse_seconds is not None:
        timing_parts.append(f"parse {run_timings.parse_seconds:.2f}s")
    if run_timings.rank_seconds is not None:
        timing_parts.append(f"rank {run_timings.rank_seconds:.2f}s")
    if run_timings.report_seconds is not None:
        timing_parts.append(f"report {run_timings.report_seconds:.2f}s")
    if not timing_parts:
        return ""
    if run_timings.total_seconds is not None:
        timing_parts.append(f"total {run_timings.total_seconds:.2f}s")
    return " | ".join(timing_parts)


def _build_report_run_summary(
    digest: DailyDigest,
    *,
    acquisition_status_label: str,
    fetch_error: str,
) -> dict[str, object]:
    return {
        "schema_version": 4,
        "category": digest.category,
        "requested_date": digest.requested_target_date.isoformat(),
        "effective_date": digest.effective_display_date.isoformat(),
        "request_window": digest.request_window.to_mapping(),
        "source_run_stats": [item.to_mapping() for item in digest.source_run_stats],
        "run_timings": digest.run_timings.to_mapping(),
        "fetch_status": acquisition_status_label,
        "fetch_error": fetch_error,
        "report_status": digest.report_status,
        "report_error": digest.report_error,
        "requested_report_mode": digest.requested_report_mode,
        "report_mode": digest.report_mode,
        "cost_mode": digest.cost_mode,
        "enhanced_track": digest.enhanced_track,
        "llm_requested": digest.llm_requested,
        "llm_applied": digest.llm_applied,
        "llm_provider": digest.llm_provider,
        "llm_fallback_reason": digest.llm_fallback_reason,
        "llm_seconds": digest.llm_seconds,
        "fetch_scope": digest.fetch_scope,
        "mode_label": digest.mode_label or digest.category,
        "mode_kind": digest.mode_kind,
        "total_fetched": max(digest.total_fetched, digest.total_ranked_count),
        "total_displayed": digest.total_displayed_count,
        "profile_basis": digest.profile.basis_label or "n/a",
        "profile_source": digest.profile.profile_source,
        "profile_path": digest.profile.profile_path,
        "profile_item_count": digest.profile.profile_item_count,
        "profile_used_item_count": digest.profile.profile_used_item_count,
        "profile_terms": list(digest.profile.top_profile_terms(limit=6)),
        "frontier_report_present": digest.frontier_report is not None,
        "report_artifact_aligned": True,
        "ranked_count": digest.total_ranked_count,
        "source_counts": dict(digest.source_counts),
    }


def _render_source_endpoint_panel(source_endpoints: dict[str, str]) -> str:
    if not source_endpoints:
        return "<p><strong>Source endpoints</strong><br>n/a</p>"
    return "<p><strong>Source endpoints</strong><br>" + "<br>".join(
        f"{escape(_format_source_label(source))}: {escape(url)}"
        for source, url in sorted(source_endpoints.items())
    ) + "</p>"


def _render_source_contract_panel(source_metadata: dict[str, dict[str, object]]) -> str:
    if not source_metadata:
        return "<p><strong>Source contract</strong><br>n/a</p>"

    rows: list[str] = []
    for source, metadata in sorted(source_metadata.items()):
        parts: list[str] = []
        mode = metadata.get("mode")
        if isinstance(mode, str) and mode:
            parts.append(f"mode={mode}")
        contract_mode = metadata.get("contract_mode")
        if isinstance(contract_mode, str) and contract_mode:
            parts.append(f"contract={contract_mode}")
        native_filters = metadata.get("native_filters")
        if isinstance(native_filters, list) and native_filters:
            parts.append("filters=" + ", ".join(str(value) for value in native_filters))
        native_endpoints = metadata.get("native_endpoints")
        if isinstance(native_endpoints, dict) and native_endpoints:
            parts.append(f"endpoints={len(native_endpoints)}")
        search_queries = metadata.get("search_queries")
        if isinstance(search_queries, list) and search_queries:
            parts.append(f"queries={len(search_queries)}")
        search_profile_label = metadata.get("search_profile_label")
        if isinstance(search_profile_label, str) and search_profile_label:
            parts.append(f"profile={search_profile_label}")
        query_profiles = metadata.get("query_profiles")
        if isinstance(query_profiles, list) and query_profiles:
            zotero_query_count = sum(
                1
                for item in query_profiles
                if isinstance(item, dict) and str(item.get("origin", "")).strip().lower() == "zotero"
            )
            if zotero_query_count > 0:
                parts.append(f"zotero_queries={zotero_query_count}")
        rows.append(f"{escape(_format_source_label(source))}: {escape(' | '.join(parts) or 'metadata recorded')}")

    return "<p><strong>Source contract</strong><br>" + "<br>".join(rows) + "</p>"
