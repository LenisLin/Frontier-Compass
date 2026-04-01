"""Frozen public API for the supported FrontierCompass local scouting workflow.

The package root intentionally re-exports the same objects from this module so
callers can pick either ``frontier_compass`` or ``frontier_compass.api``
without changing the supported public contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from frontier_compass.common.report_mode import DEFAULT_REPORT_MODE
from frontier_compass.storage.schema import DailyDigest, RequestWindow, RunHistoryEntry
from frontier_compass.ui.app import (
    DEFAULT_DAILY_CACHE_DIR,
    DEFAULT_DAILY_REPORT_DIR,
    DEFAULT_REVIEWER_SOURCE,
    FETCH_SCOPE_DAY_FULL,
    FETCH_SCOPE_RANGE_FULL,
    FrontierCompassApp,
    display_artifact_source_label,
    display_source_label,
)


def _extend_optional_run_kwargs(
    kwargs: dict[str, object],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    report_mode: str = DEFAULT_REPORT_MODE,
    profile_source: str | None = None,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> dict[str, object]:
    resolved_fetch_scope = fetch_scope
    if (
        fetch_scope == FETCH_SCOPE_DAY_FULL
        and (start_date is not None or end_date is not None)
    ):
        resolved_fetch_scope = FETCH_SCOPE_RANGE_FULL
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if report_mode != DEFAULT_REPORT_MODE:
        kwargs["report_mode"] = report_mode
    if profile_source is not None:
        kwargs["profile_source"] = profile_source
    if zotero_export_path is not None:
        kwargs["zotero_export_path"] = zotero_export_path
    if zotero_db_path is not None:
        kwargs["zotero_db_path"] = zotero_db_path
    if zotero_collections:
        kwargs["zotero_collections"] = tuple(zotero_collections)
    if resolved_fetch_scope != FETCH_SCOPE_DAY_FULL:
        kwargs["fetch_scope"] = resolved_fetch_scope
    return kwargs


@dataclass(slots=True, frozen=True)
class DailyRunResult:
    """Stable result contract for the current digest, report, and provenance."""

    digest: DailyDigest
    cache_path: Path
    report_path: Path
    display_source: str
    fetch_error: str = ""
    fetch_status_label: str = ""
    artifact_source_label: str = ""

    @property
    def request_window(self) -> RequestWindow:
        return self.digest.request_window

    @property
    def fetch_scope(self) -> str:
        return self.digest.fetch_scope

    @property
    def total_fetched(self) -> int:
        return self.digest.total_fetched

    @property
    def total_displayed(self) -> int:
        return self.digest.total_displayed_count


@dataclass(slots=True, frozen=True)
class LocalUISession:
    """Stable session contract used by the local UI and Python callers."""

    current_run: DailyRunResult
    recent_history: tuple[RunHistoryEntry, ...] = ()
    recent_history_error: str = ""

    @property
    def digest(self) -> DailyDigest:
        return self.current_run.digest

    @property
    def cache_path(self) -> Path:
        return self.current_run.cache_path

    @property
    def report_path(self) -> Path:
        return self.current_run.report_path

    @property
    def display_source(self) -> str:
        return self.current_run.display_source

    @property
    def fetch_error(self) -> str:
        return self.current_run.fetch_error

    @property
    def fetch_status_label(self) -> str:
        return self.current_run.fetch_status_label

    @property
    def artifact_source_label(self) -> str:
        return self.current_run.artifact_source_label

    @property
    def requested_report_mode(self) -> str:
        return self.digest.requested_report_mode

    @property
    def report_mode(self) -> str:
        return self.digest.report_mode

    @property
    def cost_mode(self) -> str:
        return self.digest.cost_mode

    @property
    def runtime_note(self) -> str:
        return self.digest.runtime_note

    @property
    def requested_date(self) -> date:
        return self.digest.requested_target_date

    @property
    def effective_date(self) -> date:
        return self.digest.effective_display_date

    @property
    def profile_basis_label(self) -> str:
        return self.digest.profile.basis_label or "n/a"

    @property
    def zotero_export_name(self) -> str:
        return self.digest.profile.zotero_export_name

    @property
    def profile_source(self) -> str:
        return self.digest.profile.profile_source

    @property
    def request_window(self) -> RequestWindow:
        return self.digest.request_window

    @property
    def fetch_scope(self) -> str:
        return self.digest.fetch_scope

    @property
    def total_fetched(self) -> int:
        return self.digest.total_fetched

    @property
    def total_displayed(self) -> int:
        return self.digest.total_displayed_count


class FrontierCompassRunner:
    """Reusable object-oriented entrypoint for the supported local workflow."""

    def __init__(self, app: FrontierCompassApp | None = None) -> None:
        self.app = app or FrontierCompassApp()

    def run_daily(
        self,
        *,
        source: str = DEFAULT_REVIEWER_SOURCE,
        requested_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_results: int = 80,
        refresh: bool = False,
        allow_stale_cache: bool = True,
        report_mode: str = DEFAULT_REPORT_MODE,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        cache_path: str | Path | None = None,
        report_path: str | Path | None = None,
        feed_url: str | None = None,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    ) -> DailyRunResult:
        """Materialize or reuse the current local digest and report artifacts."""

        materialize_kwargs = _extend_optional_run_kwargs(
            {
            "selected_source": source,
            "requested_date": requested_date or date.today(),
            "max_results": max(int(max_results), 1),
            "cache_dir": cache_dir,
            "force_fetch": refresh,
            "cache_path": cache_path,
            "output_path": report_path,
            "feed_url": feed_url,
            "allow_stale_cache": allow_stale_cache,
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=report_mode,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        bootstrap = self.app.materialize_daily_digest(**materialize_kwargs)
        return DailyRunResult(
            digest=bootstrap.digest,
            cache_path=bootstrap.cache_path,
            report_path=bootstrap.report_path,
            display_source=bootstrap.display_source,
            fetch_error=bootstrap.fetch_error,
            fetch_status_label=display_source_label(bootstrap.display_source),
            artifact_source_label=display_artifact_source_label(bootstrap.display_source),
        )

    def load_recent_history(
        self,
        *,
        limit: int = 10,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        report_dir: str | Path = DEFAULT_DAILY_REPORT_DIR,
    ) -> list[RunHistoryEntry]:
        """Return recent persisted runs for the supporting inspection surfaces."""

        return self.app.recent_daily_runs(
            limit=limit,
            cache_dir=cache_dir,
            report_dir=report_dir,
        )

    def prepare_ui_session(
        self,
        *,
        source: str = DEFAULT_REVIEWER_SOURCE,
        requested_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_results: int = 80,
        refresh: bool = False,
        allow_stale_cache: bool = True,
        report_mode: str = DEFAULT_REPORT_MODE,
        cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
        report_dir: str | Path = DEFAULT_DAILY_REPORT_DIR,
        profile_source: str | None = None,
        zotero_export_path: str | Path | None = None,
        zotero_db_path: str | Path | None = None,
        zotero_collections: Sequence[str] = (),
        fetch_scope: str = FETCH_SCOPE_DAY_FULL,
        history_limit: int = 6,
    ) -> LocalUISession:
        """Prepare the stable session object used by the local UI."""

        run_kwargs = _extend_optional_run_kwargs(
            {
            "source": source,
            "requested_date": requested_date,
            "max_results": max_results,
            "refresh": refresh,
            "allow_stale_cache": allow_stale_cache,
            "cache_dir": cache_dir,
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=report_mode,
            profile_source=profile_source,
            zotero_export_path=zotero_export_path,
            zotero_db_path=zotero_db_path,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        current_run = self.run_daily(**run_kwargs)
        try:
            recent_history = tuple(
                self.load_recent_history(
                    limit=history_limit,
                    cache_dir=cache_dir,
                    report_dir=report_dir,
                )
            )
            recent_history_error = ""
        except Exception as exc:  # pragma: no cover - exercised through UI/manual validation.
            recent_history = ()
            recent_history_error = str(exc)
        return LocalUISession(
            current_run=current_run,
            recent_history=recent_history,
            recent_history_error=recent_history_error,
        )


_DEFAULT_RUNNER: FrontierCompassRunner | None = None


def run_daily(
    *,
    source: str = DEFAULT_REVIEWER_SOURCE,
    requested_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_results: int = 80,
    refresh: bool = False,
    allow_stale_cache: bool = True,
    report_mode: str = DEFAULT_REPORT_MODE,
    cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
    cache_path: str | Path | None = None,
    report_path: str | Path | None = None,
    feed_url: str | None = None,
    profile_source: str | None = None,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> DailyRunResult:
    """Shortest public Python path for the current daily digest workflow."""

    return _default_runner().run_daily(
        source=source,
        requested_date=requested_date,
        start_date=start_date,
        end_date=end_date,
        max_results=max_results,
        refresh=refresh,
        allow_stale_cache=allow_stale_cache,
        report_mode=report_mode,
        cache_dir=cache_dir,
        cache_path=cache_path,
        report_path=report_path,
        feed_url=feed_url,
        profile_source=profile_source,
        zotero_export_path=zotero_export_path,
        zotero_db_path=zotero_db_path,
        zotero_collections=zotero_collections,
        fetch_scope=fetch_scope,
    )


def load_recent_history(
    *,
    limit: int = 10,
    cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
    report_dir: str | Path = DEFAULT_DAILY_REPORT_DIR,
) -> list[RunHistoryEntry]:
    """Public helper for reading recent persisted run history."""

    return _default_runner().load_recent_history(
        limit=limit,
        cache_dir=cache_dir,
        report_dir=report_dir,
    )


def prepare_ui_session(
    *,
    source: str = DEFAULT_REVIEWER_SOURCE,
    requested_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_results: int = 80,
    refresh: bool = False,
    allow_stale_cache: bool = True,
    report_mode: str = DEFAULT_REPORT_MODE,
    cache_dir: str | Path = DEFAULT_DAILY_CACHE_DIR,
    report_dir: str | Path = DEFAULT_DAILY_REPORT_DIR,
    profile_source: str | None = None,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
    history_limit: int = 6,
) -> LocalUISession:
    """Public helper for the runner-backed local UI session contract."""

    return _default_runner().prepare_ui_session(
        source=source,
        requested_date=requested_date,
        start_date=start_date,
        end_date=end_date,
        max_results=max_results,
        refresh=refresh,
        allow_stale_cache=allow_stale_cache,
        report_mode=report_mode,
        cache_dir=cache_dir,
        report_dir=report_dir,
        profile_source=profile_source,
        zotero_export_path=zotero_export_path,
        zotero_db_path=zotero_db_path,
        zotero_collections=zotero_collections,
        fetch_scope=fetch_scope,
        history_limit=history_limit,
    )


def _default_runner() -> FrontierCompassRunner:
    global _DEFAULT_RUNNER
    if _DEFAULT_RUNNER is None:
        _DEFAULT_RUNNER = FrontierCompassRunner()
    return _DEFAULT_RUNNER


__all__ = [
    "FrontierCompassRunner",
    "DailyRunResult",
    "LocalUISession",
    "run_daily",
    "prepare_ui_session",
    "load_recent_history",
]
