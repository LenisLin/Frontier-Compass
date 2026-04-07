"""Filesystem-backed history scanning for persisted FrontierCompass daily runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path

from frontier_compass.common.source_bundles import (
    DEFAULT_SOURCE_BUNDLES_PATH,
    resolve_source_bundle,
)
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    ZERO_TOKEN_COST_MODE,
    backfill_llm_provenance,
    format_llm_bool,
    format_llm_provider,
    format_llm_seconds,
)
from frontier_compass.reporting.html_report import extract_report_summary_value
from frontier_compass.storage.schema import (
    DailyDigest,
    FETCH_SCOPE_DAY_FULL,
    RequestWindow,
    RunHistoryEntry,
    RunTimings,
    SourceRunStats,
    normalize_profile_source,
)


DEFAULT_HISTORY_CACHE_DIR = Path("data/cache")
DEFAULT_HISTORY_REPORT_DIR = Path("reports/daily")
FETCH_STATUS_UNAVAILABLE = "fetch status unavailable (report missing)"

BIOMEDICAL_LATEST_MODE = "biomedical-latest"
BIOMEDICAL_DISCOVERY_MODE = "biomedical-discovery"
BIOMEDICAL_DAILY_MODE = "biomedical-daily"
BIOMEDICAL_MULTISOURCE_MODE = "biomedical-multisource"

_REPORT_GENERATED_AT_PATTERN = re.compile(r"<div class=\"meta\">Generated (.*?)</div>", flags=re.IGNORECASE | re.DOTALL)
_RUN_SUMMARY_PATTERN = re.compile(
    r"<script id=\"frontier-compass-run-summary\" type=\"application/json\">(.*?)</script>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ARTIFACT_DATE_PATTERN = re.compile(
    r"_(\d{4}-\d{2}-\d{2})(?:_to_\d{4}-\d{2}-\d{2})?(?:_(?:zotero|profile-zotero-export|profile-live-zotero-db)-[^.]*)?$"
)
_FRESH_FETCH_STATUS_PATTERN = re.compile(r"^fresh\s+.+\s+fetch$", flags=re.IGNORECASE)
_FETCH_STATUS_ALIASES = {
    "same-day cache reused after fetch failure": "same-date cache reused after fetch failure",
    "older compatible cache": "older compatible cache reused after fetch failure",
}


@dataclass(slots=True, frozen=True)
class ReportHistoryMetadata:
    fetch_status: str = ""
    requested_date: date | None = None
    effective_date: date | None = None
    request_window: RequestWindow = field(default_factory=RequestWindow)
    source_run_stats: tuple[SourceRunStats, ...] = ()
    source_counts: dict[str, int] = field(default_factory=dict)
    run_timings: RunTimings = field(default_factory=RunTimings)
    category: str = ""
    mode_label: str = ""
    mode_kind: str = ""
    requested_report_mode: str = DEFAULT_REPORT_MODE
    report_mode: str = DEFAULT_REPORT_MODE
    cost_mode: str = ZERO_TOKEN_COST_MODE
    enhanced_track: str = ""
    llm_requested: bool = False
    llm_applied: bool = False
    llm_provider: str | None = None
    llm_fallback_reason: str | None = None
    llm_seconds: float | None = None
    profile_basis: str = ""
    profile_source: str = ""
    profile_path: str = ""
    profile_item_count: int = 0
    profile_used_item_count: int = 0
    profile_terms: tuple[str, ...] = ()
    fetch_scope: str = "day-full"
    report_status: str = "ready"
    report_error: str = ""
    ranked_count: int | None = None
    total_fetched: int = 0
    total_displayed: int = 0
    generated_at: datetime | None = None
    frontier_report_present: bool | None = None
    report_artifact_aligned: bool | None = None
    stale_cache_fallback_used: bool = False


def report_path_for_cache_artifact(
    cache_path: str | Path,
    *,
    cache_dir: str | Path = DEFAULT_HISTORY_CACHE_DIR,
    report_dir: str | Path = DEFAULT_HISTORY_REPORT_DIR,
) -> Path:
    return _mirror_artifact_path(
        cache_path,
        source_root=cache_dir,
        target_root=report_dir,
        suffix=".html",
    )


def cache_path_for_report_artifact(
    report_path: str | Path,
    *,
    cache_dir: str | Path = DEFAULT_HISTORY_CACHE_DIR,
    report_dir: str | Path = DEFAULT_HISTORY_REPORT_DIR,
) -> Path:
    return _mirror_artifact_path(
        report_path,
        source_root=report_dir,
        target_root=cache_dir,
        suffix=".json",
    )


def eml_path_for_report_artifact(report_path: str | Path) -> Path:
    return Path(report_path).with_suffix(".eml")


def read_report_history_metadata(report_path: str | Path) -> ReportHistoryMetadata | None:
    path = Path(report_path)
    if not path.exists():
        return None

    try:
        report_html = path.read_text(encoding="utf-8")
    except OSError:
        return None

    run_summary = _extract_run_summary_metadata(report_html)
    if run_summary is not None:
        if run_summary.generated_at is not None:
            return run_summary
        return replace(
            run_summary,
            generated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        )

    mode_label, category = _parse_mode_value(extract_report_summary_value(report_html, "Mode"))
    requested_date = _parse_iso_date(extract_report_summary_value(report_html, "Requested date")) or _parse_artifact_date(path)
    effective_date = _parse_iso_date(extract_report_summary_value(report_html, "Effective release date")) or requested_date
    profile_source = _parse_profile_source_value(extract_report_summary_value(report_html, "Profile source"))
    profile_path = extract_report_summary_value(report_html, "Profile path")
    profile_item_count, profile_used_item_count = _parse_profile_item_counts(
        extract_report_summary_value(report_html, "Profile items parsed / used")
    )
    llm_provenance = backfill_llm_provenance(
        requested_report_mode=(
            extract_report_summary_value(report_html, "Requested report mode") or DEFAULT_REPORT_MODE
        ),
        report_mode=extract_report_summary_value(report_html, "Frontier Report mode") or DEFAULT_REPORT_MODE,
        cost_mode=extract_report_summary_value(report_html, "Cost mode") or ZERO_TOKEN_COST_MODE,
        llm_requested=_parse_optional_bool(extract_report_summary_value(report_html, "LLM requested")),
        llm_applied=_parse_optional_bool(extract_report_summary_value(report_html, "LLM applied")),
        llm_provider=_normalize_optional_text(extract_report_summary_value(report_html, "LLM provider")),
        llm_fallback_reason=_normalize_optional_text(
            extract_report_summary_value(report_html, "LLM fallback reason")
        ),
        llm_seconds=_parse_duration_seconds(extract_report_summary_value(report_html, "LLM time")),
    )
    return ReportHistoryMetadata(
        fetch_status=_normalize_fetch_status(extract_report_summary_value(report_html, "Fetch status")),
        requested_date=requested_date,
        effective_date=effective_date,
        request_window=RequestWindow(kind="day", requested_date=requested_date),
        category=category,
        mode_label=mode_label,
        mode_kind=extract_report_summary_value(report_html, "Mode kind"),
        requested_report_mode=(
            extract_report_summary_value(report_html, "Requested report mode") or DEFAULT_REPORT_MODE
        ),
        report_mode=extract_report_summary_value(report_html, "Frontier Report mode") or DEFAULT_REPORT_MODE,
        cost_mode=extract_report_summary_value(report_html, "Cost mode") or ZERO_TOKEN_COST_MODE,
        enhanced_track=extract_report_summary_value(report_html, "Enhanced track"),
        llm_requested=bool(llm_provenance["llm_requested"]),
        llm_applied=bool(llm_provenance["llm_applied"]),
        llm_provider=llm_provenance["llm_provider"],
        llm_fallback_reason=llm_provenance["llm_fallback_reason"],
        llm_seconds=_parse_float(llm_provenance["llm_seconds"]),
        profile_basis=extract_report_summary_value(report_html, "Profile basis"),
        profile_source=profile_source,
        profile_path=profile_path,
        profile_item_count=profile_item_count,
        profile_used_item_count=profile_used_item_count,
        profile_terms=_parse_profile_terms(extract_report_summary_value(report_html, "Top profile terms")),
        fetch_scope=extract_report_summary_value(report_html, "Fetch scope") or FETCH_SCOPE_DAY_FULL,
        report_status=extract_report_summary_value(report_html, "Report status") or "ready",
        report_error=extract_report_summary_value(report_html, "Fresh fetch error"),
        ranked_count=(
            _parse_summary_int(extract_report_summary_value(report_html, "Total ranked pool"))
            or _parse_ranked_count(extract_report_summary_value(report_html, "Displayed fetched / ranked"))
        ),
        total_fetched=_parse_summary_int(extract_report_summary_value(report_html, "Total fetched")),
        total_displayed=_parse_summary_int(extract_report_summary_value(report_html, "Total displayed")),
        generated_at=_extract_report_generated_at(report_html)
        or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        frontier_report_present=None,
        report_artifact_aligned=None,
        stale_cache_fallback_used=_parse_yes_no(extract_report_summary_value(report_html, "Stale cache fallback")),
    )


def list_recent_daily_runs(
    *,
    cache_dir: str | Path = DEFAULT_HISTORY_CACHE_DIR,
    report_dir: str | Path = DEFAULT_HISTORY_REPORT_DIR,
    limit: int | None = None,
) -> list[RunHistoryEntry]:
    cache_root = Path(cache_dir)
    report_root = Path(report_dir)
    rows = _scan_cached_digest_rows(cache_root=cache_root, report_root=report_root)

    seen_report_paths = {
        Path(row.report_path).resolve()
        for row in rows
        if row.report_path is not None
    }
    rows.extend(
        _scan_orphan_report_rows(
            cache_root=cache_root,
            report_root=report_root,
            seen_report_paths=seen_report_paths,
        )
    )
    rows.sort(
        key=lambda item: (
            item.generated_at,
            item.requested_date,
            item.report_path or item.cache_path or "",
            item.category,
        ),
        reverse=True,
    )
    rows.sort(key=lambda item: 1 if item.is_compatibility_entry else 0)
    if limit is not None:
        return rows[: max(limit, 0)]
    return rows


def format_history_requested_effective_label(entry: RunHistoryEntry) -> str:
    if entry.request_window.kind == "range":
        return entry.request_window.label
    requested = entry.requested_date.isoformat()
    effective = entry.effective_date.isoformat()
    if requested == effective:
        return requested
    return f"{requested} -> {effective}"


def build_history_summary_bits(entry: RunHistoryEntry) -> tuple[str, ...]:
    summary_bits = [
        entry.fetch_status or "n/a",
        f"window {_history_request_window_text(entry)}",
        f"scope {entry.fetch_scope or FETCH_SCOPE_DAY_FULL}",
        f"profile source {entry.profile_source or 'n/a'}",
        f"source composition {_history_source_composition_text(entry)}",
        f"report {entry.report_mode}/{entry.report_status}",
        f"cache story {_history_cache_story_text(entry)}",
        f"ranked {entry.ranked_count}",
    ]
    if entry.profile_label and entry.profile_label != (entry.profile_source or ""):
        summary_bits.append(entry.profile_label)
    summary_bits.append(entry.cost_mode or ZERO_TOKEN_COST_MODE)
    if entry.run_timings.total_seconds is not None:
        summary_bits.append(f"time {entry.run_timings.total_seconds:.2f}s")
    if entry.profile_source and entry.profile_source != "baseline":
        summary_bits.append(entry.profile_source)
    if entry.profile_path_name:
        summary_bits.append(f"profile {entry.profile_path_name}")
    if entry.profile_item_count or entry.profile_used_item_count:
        summary_bits.append(f"profile items {entry.profile_item_count}/{entry.profile_used_item_count}")
    if entry.profile_terms:
        summary_bits.append(f"profile terms {', '.join(entry.profile_terms[:3])}")
    if entry.frontier_report_present is False:
        summary_bits.append("frontier report unavailable")
    if entry.report_artifact_aligned is False:
        summary_bits.append("report artifact not aligned")
    if entry.zotero_export_name:
        summary_bits.append(f"zotero {entry.zotero_export_name}")
    elif entry.zotero_db_name:
        summary_bits.append(f"zotero-db {entry.zotero_db_name}")
    elif entry.zotero_augmented:
        summary_bits.append("zotero enabled")
    if entry.exploration_pick_count:
        summary_bits.append(f"exploration {entry.exploration_pick_count}")
    if entry.is_compatibility_entry:
        summary_bits.append(entry.compatibility_label)
    return tuple(summary_bits)


def format_history_llm_provenance_text(entry: RunHistoryEntry) -> str:
    return (
        f"LLM requested {format_llm_bool(entry.llm_requested)} | "
        f"applied {format_llm_bool(entry.llm_applied)} | "
        f"provider {format_llm_provider(entry.llm_provider)} | "
        f"fallback {entry.llm_fallback_reason or 'none'} | "
        f"time {format_llm_seconds(entry.llm_seconds)}"
    )


def format_history_compatibility_text(entry: RunHistoryEntry) -> str:
    if not entry.is_compatibility_entry:
        return "current-contract artifact"
    if entry.compatibility_reasons:
        return f"{entry.compatibility_label}: {'; '.join(entry.compatibility_reasons)}"
    return entry.compatibility_label


def _history_source_composition_text(entry: RunHistoryEntry) -> str:
    if entry.source_run_stats:
        return ", ".join(
            f"{row.source} {row.displayed_count}/{row.fetched_count}"
            for row in entry.source_run_stats
        )
    if entry.source_counts:
        return ", ".join(f"{source} {count}" for source, count in sorted(entry.source_counts.items()))
    return "n/a"


def _history_request_window_text(entry: RunHistoryEntry) -> str:
    label = entry.request_window.label
    if label and label != "n/a":
        return label
    return format_history_requested_effective_label(entry)


def _history_cache_story_text(entry: RunHistoryEntry) -> str:
    if entry.stale_cache_fallback_used:
        return "older compatible cache reused after fetch failure"
    if entry.same_date_cache_reused:
        return "same-date cache reused after fetch failure"
    if not entry.fetch_status or entry.fetch_status == FETCH_STATUS_UNAVAILABLE:
        return "report missing"
    return entry.fetch_status


def _format_history_source_run_stat(row: SourceRunStats) -> str:
    piece = f"{row.source} {row.fetched_count}/{row.displayed_count} [{row.resolved_outcome}; {row.status}; {row.cache_status}]"
    extra_bits: list[str] = []
    if row.resolved_live_outcome != row.resolved_outcome:
        extra_bits.append(f"live: {row.resolved_live_outcome}")
    if row.error:
        extra_bits.append(f"error: {row.error}")
    if row.note:
        extra_bits.append(f"note: {row.note}")
    if extra_bits:
        piece = f"{piece} ({'; '.join(extra_bits)})"
    return piece


def build_history_artifact_rows(entry: RunHistoryEntry) -> tuple[tuple[str, str], ...]:
    return (
        ("Report", entry.report_path or "none"),
        ("Cache", entry.cache_path or "none"),
        ("EML", entry.eml_path or "none"),
    )


def _scan_cached_digest_rows(*, cache_root: Path, report_root: Path) -> list[RunHistoryEntry]:
    if not cache_root.exists():
        return []

    rows: list[RunHistoryEntry] = []
    for cache_path in sorted(cache_root.rglob("frontier_compass_*.json")):
        if not cache_path.is_file():
            continue
        digest = _load_digest_from_cache(cache_path)
        if digest is None:
            continue

        report_path = report_path_for_cache_artifact(cache_path, cache_dir=cache_root, report_dir=report_root)
        report_metadata = read_report_history_metadata(report_path)
        eml_path = eml_path_for_report_artifact(report_path)
        source_run_stats = (
            report_metadata.source_run_stats
            if report_metadata is not None and report_metadata.source_run_stats
            else tuple(digest.source_run_stats)
        )
        source_counts = (
            dict(report_metadata.source_counts)
            if report_metadata is not None and report_metadata.source_counts
            else dict(digest.source_counts)
        )
        run_timings = (
            report_metadata.run_timings
            if report_metadata is not None and _has_known_timings(report_metadata.run_timings)
            else digest.run_timings
        )
        frontier_report_present = (
            report_metadata.frontier_report_present
            if report_metadata is not None and report_metadata.frontier_report_present is not None
            else digest.frontier_report is not None
        )
        report_artifact_aligned = (
            report_metadata.report_artifact_aligned
            if report_metadata is not None and report_metadata.report_artifact_aligned is not None
            else bool(report_path.exists())
        )
        fetch_status = (
            report_metadata.fetch_status
            if report_metadata is not None and report_metadata.fetch_status
            else _fallback_fetch_status(digest)
        )
        compatibility_status, compatibility_reasons = _classify_history_entry(
            cache_path=str(cache_path),
            report_path=str(report_path) if report_path.exists() else None,
            category=digest.category,
            source_run_stats=source_run_stats,
            frontier_report_present=frontier_report_present,
        )
        rows.append(
            RunHistoryEntry(
                requested_date=digest.requested_target_date,
                effective_date=digest.effective_display_date,
                category=digest.category,
                mode_label=digest.mode_label or digest.category,
                mode_kind=digest.mode_kind or _default_mode_kind(digest.category),
                request_window=digest.request_window,
                source_run_stats=source_run_stats,
                source_counts=source_counts,
                profile_basis=digest.profile.basis_label or "n/a",
                profile_source=digest.profile.profile_source,
                profile_path=digest.profile.profile_path,
                profile_item_count=digest.profile.profile_item_count,
                profile_used_item_count=digest.profile.profile_used_item_count,
                profile_terms=digest.profile.top_profile_terms(limit=4),
                zotero_export_name=digest.profile.zotero_export_name,
                zotero_db_name=digest.profile.zotero_db_name,
                fetch_status=fetch_status,
                requested_report_mode=(
                    report_metadata.requested_report_mode
                    if report_metadata is not None and report_metadata.requested_report_mode
                    else digest.requested_report_mode
                ),
                report_mode=(
                    report_metadata.report_mode
                    if report_metadata is not None and report_metadata.report_mode
                    else digest.report_mode
                ),
                cost_mode=(
                    report_metadata.cost_mode
                    if report_metadata is not None and report_metadata.cost_mode
                    else digest.cost_mode
                ),
                enhanced_track=(
                    report_metadata.enhanced_track
                    if report_metadata is not None and report_metadata.enhanced_track
                    else digest.enhanced_track
                ),
                llm_requested=(
                    report_metadata.llm_requested
                    if report_metadata is not None
                    else digest.llm_requested
                ),
                llm_applied=(
                    report_metadata.llm_applied
                    if report_metadata is not None
                    else digest.llm_applied
                ),
                llm_provider=(
                    report_metadata.llm_provider
                    if report_metadata is not None and report_metadata.llm_provider is not None
                    else digest.llm_provider
                ),
                llm_fallback_reason=(
                    report_metadata.llm_fallback_reason
                    if report_metadata is not None and report_metadata.llm_fallback_reason is not None
                    else digest.llm_fallback_reason
                ),
                llm_seconds=(
                    report_metadata.llm_seconds
                    if report_metadata is not None and report_metadata.llm_seconds is not None
                    else digest.llm_seconds
                ),
                fetch_scope=digest.fetch_scope,
                report_status=(
                    report_metadata.report_status
                    if report_metadata is not None and report_metadata.report_status
                    else digest.report_status
                ),
                run_timings=run_timings,
                total_fetched=(
                    report_metadata.total_fetched
                    if report_metadata is not None and report_metadata.total_fetched > 0
                    else max(digest.total_fetched, digest.total_ranked_count)
                ),
                total_displayed=(
                    report_metadata.total_displayed
                    if report_metadata is not None and report_metadata.total_displayed > 0
                    else digest.total_displayed_count
                ),
                frontier_report_present=frontier_report_present,
                report_artifact_aligned=report_artifact_aligned,
                same_date_cache_reused=_is_same_date_cache_status(fetch_status),
                stale_cache_fallback_used=(
                    digest.stale_cache_fallback_used
                    or _is_stale_cache_status(fetch_status)
                    or (
                        report_metadata.stale_cache_fallback_used
                        if report_metadata is not None
                        else False
                    )
                ),
                ranked_count=digest.displayed_ranked_count,
                exploration_pick_count=len(digest.exploration_picks),
                cache_path=str(cache_path),
                report_path=str(report_path) if report_path.exists() else None,
                eml_path=str(eml_path) if eml_path.exists() else None,
                generated_at=digest.generated_at,
                compatibility_status=compatibility_status,
                compatibility_reasons=compatibility_reasons,
            )
        )
    return rows


def _scan_orphan_report_rows(
    *,
    cache_root: Path,
    report_root: Path,
    seen_report_paths: set[Path],
) -> list[RunHistoryEntry]:
    if not report_root.exists():
        return []

    rows: list[RunHistoryEntry] = []
    for report_path in sorted(report_root.rglob("frontier_compass_*.html")):
        if not report_path.is_file():
            continue

        resolved_report_path = report_path.resolve()
        if resolved_report_path in seen_report_paths:
            continue

        report_metadata = read_report_history_metadata(report_path)
        if report_metadata is None or report_metadata.requested_date is None or report_metadata.generated_at is None:
            continue

        cache_path = cache_path_for_report_artifact(report_path, cache_dir=cache_root, report_dir=report_root)
        eml_path = eml_path_for_report_artifact(report_path)
        fetch_status = report_metadata.fetch_status or (
            "older compatible cache reused after fetch failure"
            if report_metadata.stale_cache_fallback_used
            else FETCH_STATUS_UNAVAILABLE
        )
        report_path_value = str(report_path)
        cache_path_value = str(cache_path) if cache_path.exists() else None
        compatibility_status, compatibility_reasons = _classify_history_entry(
            cache_path=cache_path_value,
            report_path=report_path_value,
            category=report_metadata.category,
            source_run_stats=report_metadata.source_run_stats,
            frontier_report_present=report_metadata.frontier_report_present,
        )
        rows.append(
            RunHistoryEntry(
                requested_date=report_metadata.requested_date,
                effective_date=report_metadata.effective_date or report_metadata.requested_date,
                category=report_metadata.category,
                mode_label=report_metadata.mode_label or report_metadata.category or "n/a",
                mode_kind=report_metadata.mode_kind or _default_mode_kind(report_metadata.category),
                request_window=report_metadata.request_window,
                source_run_stats=report_metadata.source_run_stats,
                source_counts=dict(report_metadata.source_counts),
                profile_basis=report_metadata.profile_basis or "n/a",
                profile_source=report_metadata.profile_source,
                profile_path=report_metadata.profile_path,
                profile_item_count=report_metadata.profile_item_count,
                profile_used_item_count=report_metadata.profile_used_item_count,
                profile_terms=report_metadata.profile_terms,
                fetch_status=fetch_status,
                requested_report_mode=report_metadata.requested_report_mode,
                report_mode=report_metadata.report_mode,
                cost_mode=report_metadata.cost_mode,
                enhanced_track=report_metadata.enhanced_track,
                llm_requested=report_metadata.llm_requested,
                llm_applied=report_metadata.llm_applied,
                llm_provider=report_metadata.llm_provider,
                llm_fallback_reason=report_metadata.llm_fallback_reason,
                llm_seconds=report_metadata.llm_seconds,
                fetch_scope=report_metadata.fetch_scope,
                report_status=report_metadata.report_status,
                run_timings=report_metadata.run_timings,
                total_fetched=report_metadata.total_fetched,
                total_displayed=report_metadata.total_displayed,
                frontier_report_present=report_metadata.frontier_report_present,
                report_artifact_aligned=report_metadata.report_artifact_aligned,
                same_date_cache_reused=_is_same_date_cache_status(fetch_status),
                stale_cache_fallback_used=(
                    report_metadata.stale_cache_fallback_used or _is_stale_cache_status(fetch_status)
                ),
                ranked_count=report_metadata.ranked_count or 0,
                exploration_pick_count=None,
                cache_path=cache_path_value,
                report_path=report_path_value,
                eml_path=str(eml_path) if eml_path.exists() else None,
                generated_at=report_metadata.generated_at,
                compatibility_status=compatibility_status,
                compatibility_reasons=compatibility_reasons,
            )
        )
    return rows


def _mirror_artifact_path(
    source_path: str | Path,
    *,
    source_root: str | Path,
    target_root: str | Path,
    suffix: str,
) -> Path:
    path = Path(source_path)
    relative = _relative_to_root(path, Path(source_root))
    if relative is None:
        return path.with_suffix(suffix)
    return Path(target_root) / relative.with_suffix(suffix)


def _relative_to_root(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        try:
            return path.relative_to(root)
        except ValueError:
            return None


def _load_digest_from_cache(cache_path: Path) -> DailyDigest | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return DailyDigest.from_mapping(payload)
    except (TypeError, ValueError):
        return None


def _fallback_fetch_status(digest: DailyDigest) -> str:
    if digest.stale_cache_fallback_used:
        return "older compatible cache reused after fetch failure"
    if any(row.cache_status == "same-day-cache" for row in digest.source_run_stats):
        return "same-day cache"
    if any(row.cache_status == "stale-compatible-cache" for row in digest.source_run_stats):
        return "older compatible cache reused after fetch failure"
    return FETCH_STATUS_UNAVAILABLE


def _normalize_fetch_status(fetch_status: str) -> str:
    normalized = " ".join(fetch_status.split())
    if not normalized:
        return ""
    if _FRESH_FETCH_STATUS_PATTERN.match(normalized):
        return "fresh source fetch"
    return _FETCH_STATUS_ALIASES.get(normalized.lower(), normalized)


def _parse_mode_value(value: str) -> tuple[str, str]:
    normalized = " ".join(value.split())
    if not normalized:
        return "", ""
    match = re.match(r"^(?P<label>.+?)\s+\((?P<category>[^()]+)\)$", normalized)
    if match is None:
        return normalized, normalized
    return match.group("label"), match.group("category")


def _extract_report_generated_at(report_html: str) -> datetime | None:
    match = _REPORT_GENERATED_AT_PATTERN.search(report_html)
    if match is None:
        return None
    value = " ".join(match.group(1).split())
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_run_summary_metadata(report_html: str) -> ReportHistoryMetadata | None:
    match = _RUN_SUMMARY_PATTERN.search(report_html)
    if match is None:
        return None
    try:
        payload = json.loads(unescape(match.group(1)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    request_window_value = payload.get("request_window")
    request_window = (
        RequestWindow.from_mapping(request_window_value)
        if isinstance(request_window_value, dict)
        else RequestWindow()
    )
    source_run_stats_value = payload.get("source_run_stats", ())
    if not isinstance(source_run_stats_value, list):
        source_run_stats_value = []
    run_timings_value = payload.get("run_timings")
    run_timings = (
        RunTimings.from_mapping(run_timings_value)
        if isinstance(run_timings_value, dict)
        else RunTimings()
    )
    source_counts_value = payload.get("source_counts", {})
    source_counts = (
        {
            str(key): int(value)
            for key, value in source_counts_value.items()
            if str(key)
        }
        if isinstance(source_counts_value, dict)
        else {}
    )
    requested_date = _parse_iso_date(str(payload.get("requested_date", "")))
    effective_date = _parse_iso_date(str(payload.get("effective_date", ""))) or requested_date
    llm_provenance = backfill_llm_provenance(
        requested_report_mode=str(payload.get("requested_report_mode", DEFAULT_REPORT_MODE)),
        report_mode=str(payload.get("report_mode", DEFAULT_REPORT_MODE)),
        cost_mode=str(payload.get("cost_mode", ZERO_TOKEN_COST_MODE)),
        llm_requested=payload.get("llm_requested") if isinstance(payload.get("llm_requested"), bool) else None,
        llm_applied=payload.get("llm_applied") if isinstance(payload.get("llm_applied"), bool) else None,
        llm_provider=_normalize_optional_text(str(payload.get("llm_provider", ""))),
        llm_fallback_reason=_normalize_optional_text(str(payload.get("llm_fallback_reason", ""))),
        llm_seconds=_parse_float(payload.get("llm_seconds")),
    )
    return ReportHistoryMetadata(
        fetch_status=_normalize_fetch_status(str(payload.get("fetch_status", ""))),
        requested_date=requested_date,
        effective_date=effective_date,
        request_window=request_window,
        source_run_stats=tuple(
            SourceRunStats.from_mapping(item)
            for item in source_run_stats_value
            if isinstance(item, dict)
        ),
        source_counts=source_counts,
        run_timings=run_timings,
        category=str(payload.get("category", "")),
        mode_label=str(payload.get("mode_label", "")),
        mode_kind=str(payload.get("mode_kind", "")),
        requested_report_mode=str(payload.get("requested_report_mode", DEFAULT_REPORT_MODE)),
        report_mode=str(payload.get("report_mode", DEFAULT_REPORT_MODE)),
        cost_mode=str(payload.get("cost_mode", ZERO_TOKEN_COST_MODE)),
        enhanced_track=str(payload.get("enhanced_track", "")),
        llm_requested=bool(llm_provenance["llm_requested"]),
        llm_applied=bool(llm_provenance["llm_applied"]),
        llm_provider=llm_provenance["llm_provider"],
        llm_fallback_reason=llm_provenance["llm_fallback_reason"],
        llm_seconds=_parse_float(llm_provenance["llm_seconds"]),
        profile_basis=str(payload.get("profile_basis", "")),
        profile_source=normalize_profile_source(str(payload.get("profile_source", ""))) or "",
        profile_path=str(payload.get("profile_path", "")),
        profile_item_count=_parse_int(payload.get("profile_item_count")) or 0,
        profile_used_item_count=_parse_int(payload.get("profile_used_item_count")) or 0,
        profile_terms=tuple(
            str(value).strip()
            for value in payload.get("profile_terms", ())
            if str(value).strip()
        ),
        fetch_scope=str(payload.get("fetch_scope", FETCH_SCOPE_DAY_FULL)),
        report_status=str(payload.get("report_status", "ready")),
        report_error=str(payload.get("report_error", "") or payload.get("fetch_error", "")),
        ranked_count=_parse_int(payload.get("ranked_count")),
        total_fetched=_parse_int(payload.get("total_fetched")) or 0,
        total_displayed=_parse_int(payload.get("total_displayed")) or 0,
        generated_at=_extract_report_generated_at(report_html),
        frontier_report_present=_parse_optional_bool(payload.get("frontier_report_present")),
        report_artifact_aligned=_parse_optional_bool(payload.get("report_artifact_aligned")),
        stale_cache_fallback_used=_is_stale_cache_status(str(payload.get("fetch_status", ""))),
    )


def _parse_ranked_count(value: str) -> int | None:
    match = re.match(r"^\s*\d+\s*/\s*(\d+)\s*$", value)
    if match is None:
        return None
    return int(match.group(1))


def _parse_summary_int(value: str) -> int:
    try:
        return max(int(value.strip()), 0)
    except (AttributeError, ValueError):
        return 0


def _parse_profile_source_value(value: str) -> str:
    normalized = value.split("(", 1)[0].strip()
    return normalize_profile_source(normalized) or ""


def _parse_profile_item_counts(value: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", value)
    if match is None:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _parse_profile_terms(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _normalize_optional_text(value: str) -> str | None:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized or normalized.lower() in {"n/a", "none"}:
        return None
    return normalized


def _parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_yes_no(value: str) -> bool:
    return value.strip().lower() == "yes"


def _parse_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _parse_duration_seconds(value: str) -> float | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized in {"n/a", "none"}:
        return None
    if normalized.endswith("s"):
        normalized = normalized[:-1]
    try:
        return float(normalized)
    except ValueError:
        return None


def _path_in_history_compatibility_archive(path_value: str | None) -> bool:
    if not path_value:
        return False
    return "live_validation" in Path(path_value).parts


def _path_has_legacy_profile_name(path_value: str | None) -> bool:
    if not path_value:
        return False
    return "_zotero-" in Path(path_value).name


def _classify_history_entry(
    *,
    cache_path: str | None,
    report_path: str | None,
    category: str,
    source_run_stats: tuple[SourceRunStats, ...],
    frontier_report_present: bool | None,
) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []
    normalized_category = category.strip().lower()
    unexpected_bundle_sources = _unexpected_official_bundle_sources(
        category=category,
        source_run_stats=source_run_stats,
    )
    if _path_in_history_compatibility_archive(cache_path) or _path_in_history_compatibility_archive(report_path):
        reasons.append("archived live-validation artifact")
    if normalized_category in {
        BIOMEDICAL_LATEST_MODE,
        BIOMEDICAL_DISCOVERY_MODE,
        BIOMEDICAL_DAILY_MODE,
    }:
        reasons.append(f"legacy compatibility mode id: {normalized_category}")
    if normalized_category == BIOMEDICAL_MULTISOURCE_MODE:
        reasons.append("legacy 3-source compatibility path")
    if unexpected_bundle_sources:
        reasons.append(
            "official bundle artifact contains unexpected source rows: "
            + ", ".join(unexpected_bundle_sources)
        )
    if _path_has_legacy_profile_name(cache_path) or _path_has_legacy_profile_name(report_path):
        reasons.append("legacy profile artifact name")
    if any(
        row.resolved_outcome == "unknown-legacy" or row.resolved_live_outcome == "unknown-legacy"
        for row in source_run_stats
    ):
        reasons.append("legacy source provenance backfill")
    if frontier_report_present is False:
        reasons.append("legacy cache without frontier report")
    if not reasons:
        return "", ()
    status = "archived" if any(reason.startswith("archived") for reason in reasons) else "legacy"
    return status, tuple(reasons)


def _unexpected_official_bundle_sources(
    *,
    category: str,
    source_run_stats: tuple[SourceRunStats, ...],
) -> tuple[str, ...]:
    bundle = resolve_source_bundle(category, config_path=DEFAULT_SOURCE_BUNDLES_PATH)
    if bundle is None or not bundle.official:
        return ()
    expected_sources = {source.strip().lower() for source in bundle.enabled_sources if source.strip()}
    present_sources = {
        str(row.source).strip().lower()
        for row in source_run_stats
        if str(row.source).strip()
    }
    unexpected_sources = sorted(source for source in present_sources if source not in expected_sources)
    return tuple(unexpected_sources)


def _has_known_timings(run_timings: RunTimings) -> bool:
    return any(
        value is not None
        for value in (
            run_timings.cache_seconds,
            run_timings.network_seconds,
            run_timings.parse_seconds,
            run_timings.rank_seconds,
            run_timings.report_seconds,
            run_timings.total_seconds,
        )
    )


def _parse_artifact_date(path: Path) -> date | None:
    match = _ARTIFACT_DATE_PATTERN.search(path.stem)
    if match is None:
        return None
    return _parse_iso_date(match.group(1))


def _default_mode_kind(category: str) -> str:
    normalized = category.strip().lower()
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


def _is_same_date_cache_status(fetch_status: str) -> bool:
    normalized = fetch_status.strip().lower()
    return normalized.startswith("same-day cache") or "same-date cache reused" in normalized


def _is_stale_cache_status(fetch_status: str) -> bool:
    return "older compatible cache" in fetch_status.strip().lower()
