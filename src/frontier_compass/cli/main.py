"""Command line entry points for FrontierCompass."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Sequence

from frontier_compass.api import (
    DailyRunResult,
    FrontierCompassRunner,
    LocalUISession,
    load_recent_history,
)
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    REPORT_MODE_CHOICES,
    ZERO_TOKEN_COST_MODE,
)
from frontier_compass.common.user_defaults import (
    DEFAULT_USER_DEFAULTS_PATH,
    LoadedUserDefaults,
    ResolvedSetting,
    load_user_defaults,
    resolve_setting,
)
from frontier_compass.reporting.daily_brief import summarize_category_counts
from frontier_compass.storage.schema import DailyDigest, RunTimings, SourceRunStats
from frontier_compass.ui.app import (
    BIOMEDICAL_DAILY_MODE,
    BIOMEDICAL_DISCOVERY_MODE,
    BIOMEDICAL_LATEST_MODE,
    BIOMEDICAL_MULTISOURCE_MODE,
    DEFAULT_ARXIV_CATEGORY,
    FETCH_SCOPE_DAY_FULL,
    FETCH_SCOPE_OPTIONS,
    FETCH_SCOPE_RANGE_FULL,
    PROFILE_SOURCE_BASELINE,
    PROFILE_SOURCE_LIVE_ZOTERO_DB,
    PROFILE_SOURCE_ZOTERO,
    PROFILE_SOURCE_ZOTERO_EXPORT,
    DEFAULT_REVIEWER_SOURCE,
    FrontierCompassApp,
    display_artifact_source_label,
    display_source_label,
    format_source_outcome_label,
    is_fixed_daily_mode,
)
from frontier_compass.ui.history import (
    build_history_artifact_rows,
    build_history_summary_bits,
    format_history_requested_effective_label,
)
from frontier_compass.ui.email_delivery import (
    default_eml_output_path,
    prepare_daily_digest_email,
    resolve_smtp_settings,
    send_email_message,
    write_eml_message,
)


DEFAULT_DEMO_REPORT_PATH = Path("reports/daily/frontier_compass_demo.html")
CLI_DEFAULT_MAX_RESULTS = 80


class FrontierCompassCliFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Preserve helpful line breaks while still showing argument defaults."""


def _resolve_ui_app_path() -> Path:
    return Path(str(resource_files("frontier_compass.ui").joinpath("streamlit_app.py"))).resolve()


def _build_ui_launch_command(
    *,
    port: int | None = None,
    headless: bool = False,
    startup_args: Sequence[str] = (),
) -> list[str]:
    command = [sys.executable, "-m", "streamlit", "run", str(_resolve_ui_app_path())]
    if headless:
        command.extend(["--server.headless", "true"])
    if port is not None:
        command.extend(["--server.port", str(port)])
    if startup_args:
        command.extend(["--", *startup_args])
    return command


def build_parser() -> argparse.ArgumentParser:
    ui_command_hint = format_shell_command(_build_ui_launch_command())
    parser = argparse.ArgumentParser(
        prog="frontier-compass",
        description="Run the FrontierCompass local workflow from the command line.",
        epilog=(
            "Shortest local path: frontier-compass run-daily, then frontier-compass ui.\n"
            "Use frontier-compass history to inspect recent persisted runs.\n"
            "Compatibility commands remain available for explicit builds, email delivery, and demos.\n"
            f"Exact Streamlit launch: {ui_command_hint}"
        ),
        formatter_class=FrontierCompassCliFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    run_daily_parser = subparsers.add_parser(
        "run-daily",
        help="Primary local CLI path: materialize or reuse the current digest and report.",
        description=(
            "Recommended local CLI path. This cache-first wrapper materializes or reuses the current digest, "
            "ensures the HTML report exists, and can optionally write a dry-run .eml artifact."
        ),
    )
    _add_config_arguments(run_daily_parser)
    _add_daily_source_arguments(
        run_daily_parser,
        default_help=(
            "Explicit fixed daily mode. If you omit both --mode and --category, run-daily defaults to "
            f"{DEFAULT_REVIEWER_SOURCE}."
        ),
        category_help=(
            "Strict single-category arXiv RSS path, for example q-bio, q-bio.GN, or cs.LG. "
            f"If you pass --category without --mode, run-daily skips the {DEFAULT_REVIEWER_SOURCE} default."
        ),
    )
    _add_report_mode_argument(run_daily_parser)
    run_daily_parser.add_argument("--today", type=_parse_iso_date, default=argparse.SUPPRESS, help="Override the target date as YYYY-MM-DD.")
    _add_request_window_arguments(run_daily_parser)
    run_daily_parser.add_argument(
        "--feed-url",
        default=argparse.SUPPRESS,
        help="Override the arXiv feed URL. Useful for tests or local Atom files.",
    )
    run_daily_parser.add_argument(
        "--cache",
        type=Path,
        default=argparse.SUPPRESS,
        help="JSON cache path. Defaults to a source-specific file under data/cache/.",
    )
    run_daily_parser.add_argument(
        "--output",
        type=Path,
        default=argparse.SUPPRESS,
        help="HTML output path. Defaults to a source-specific file under reports/daily/.",
    )
    run_daily_parser.add_argument(
        "--max-results",
        type=int,
        default=argparse.SUPPRESS,
        help=(
            "Compatibility display/debug cap. day-full and range-full still fetch the full requested window "
            "for the selected source bundle; this value only constrains shortlist mode and bounded query legs. "
            f"Built-in default: {CLI_DEFAULT_MAX_RESULTS}."
        ),
    )
    run_daily_parser.add_argument(
        "--zotero-export",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local Zotero CSL JSON export used to augment the biomedical baseline profile.",
    )
    _add_profile_source_arguments(run_daily_parser)
    run_daily_parser.add_argument(
        "--email-to",
        default=argparse.SUPPRESS,
        help="Recipient address or comma-separated recipients. Falls back to config, then FRONTIER_COMPASS_EMAIL_TO.",
    )
    run_daily_parser.add_argument(
        "--email-from",
        default=argparse.SUPPRESS,
        help="From address. Falls back to config, then FRONTIER_COMPASS_EMAIL_FROM.",
    )
    run_daily_parser.add_argument(
        "--eml-output",
        type=Path,
        default=argparse.SUPPRESS,
        help="Dry-run .eml output path. Defaults to the HTML report path with a .eml suffix.",
    )
    dry_run_group = run_daily_parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run-email",
        dest="dry_run_email",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Write a dry-run .eml artifact after the report is ready.",
    )
    dry_run_group.add_argument(
        "--no-dry-run-email",
        dest="dry_run_email",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Skip the dry-run .eml artifact even if the config enables it.",
    )
    run_daily_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh source fetch instead of reusing a same-day cache when available.",
    )
    stale_cache_group = run_daily_parser.add_mutually_exclusive_group()
    stale_cache_group.add_argument(
        "--allow-stale-cache",
        dest="allow_stale_cache",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow a last-resort older compatible cache fallback if fresh fetch fails and no same-date cache exists.",
    )
    stale_cache_group.add_argument(
        "--no-stale-cache",
        dest="allow_stale_cache",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Disable the older compatible cache fallback and fail if fresh fetch and same-date cache reuse both fail.",
    )

    ui_parser = subparsers.add_parser(
        "ui",
        help="Primary local UI path: prewarm the current digest and launch Streamlit.",
        description=(
            "Recommended local interactive surface. This command can print the exact Streamlit launch "
            "command or prewarm the current digest before opening the app."
        ),
    )
    _add_config_arguments(ui_parser)
    _add_daily_source_arguments(
        ui_parser,
        default_help=(
            "Explicit fixed daily mode. If you omit both --mode and --category, ui defaults to "
            f"{DEFAULT_REVIEWER_SOURCE}."
        ),
        category_help=(
            "Strict single-category arXiv path, for example q-bio, q-bio.GN, or cs.LG. "
            f"If you pass --category without --mode, ui skips the {DEFAULT_REVIEWER_SOURCE} default."
        ),
    )
    _add_report_mode_argument(ui_parser)
    ui_parser.add_argument(
        "--today",
        type=_parse_iso_date,
        default=argparse.SUPPRESS,
        help="Initial requested day for the UI as YYYY-MM-DD.",
    )
    _add_request_window_arguments(ui_parser)
    ui_parser.add_argument(
        "--max-results",
        type=int,
        default=argparse.SUPPRESS,
        help=(
            "Compatibility display/debug cap. day-full and range-full still materialize the full requested "
            "window for the selected source bundle. "
            f"Built-in default: {CLI_DEFAULT_MAX_RESULTS}."
        ),
    )
    ui_parser.add_argument(
        "--zotero-export",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local Zotero CSL JSON export used to augment the UI's active profile.",
    )
    _add_profile_source_arguments(ui_parser)
    ui_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Prewarm the current UI digest with a fresh source fetch before launching Streamlit.",
    )
    ui_stale_cache_group = ui_parser.add_mutually_exclusive_group()
    ui_stale_cache_group.add_argument(
        "--allow-stale-cache",
        dest="allow_stale_cache",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow an older compatible cache fallback if fresh fetch fails and no same-date cache exists.",
    )
    ui_stale_cache_group.add_argument(
        "--no-stale-cache",
        dest="allow_stale_cache",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Disable the older compatible cache fallback during UI prewarm.",
    )
    ui_parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the exact Streamlit command and app path, then exit.",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Optional Streamlit server port.",
    )
    ui_parser.add_argument(
        "--server-headless",
        action="store_true",
        help="Pass --server.headless true to Streamlit.",
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Local inspection helper: list recent runs and provenance.",
        description="Inspect recent local runs, requested vs shown dates, and saved artifacts.",
        epilog="Latest-first output shows the report, cache JSON, and optional .eml path for each saved run.",
        formatter_class=FrontierCompassCliFormatter,
    )
    history_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of recent runs to print.",
    )

    daily_parser = subparsers.add_parser(
        "daily",
        help="Compatibility explicit build for a fixed mode or strict single feed.",
        description=(
            "Build the reviewer-ready biomedical digest explicitly. If you omit both --mode and "
            f"--category, this command uses {DEFAULT_REVIEWER_SOURCE}. Pass --mode for a fixed biomedical "
            "mode, or pass --category for a strict single-category RSS path."
        ),
    )
    _add_config_arguments(daily_parser)
    _add_daily_source_arguments(
        daily_parser,
        default_help=(
            "Explicit fixed daily mode. If you omit both --mode and --category, daily defaults to "
            f"{DEFAULT_REVIEWER_SOURCE}. Use biomedical-discovery for the strict same-day hybrid audit path, "
            "biomedical-daily for the q-bio comparison bundle, or biomedical-multisource for the strict "
            "same-day arXiv + bioRxiv + medRxiv bundle."
        ),
        category_help=(
            "Strict single-category arXiv RSS path, for example q-bio, q-bio.GN, or cs.LG. "
            f"If you pass --category without --mode, daily skips the {DEFAULT_REVIEWER_SOURCE} default."
        ),
    )
    _add_report_mode_argument(daily_parser)
    daily_parser.add_argument("--today", type=_parse_iso_date, default=argparse.SUPPRESS, help="Override the target date as YYYY-MM-DD.")
    _add_request_window_arguments(daily_parser)
    daily_parser.add_argument(
        "--feed-url",
        default=argparse.SUPPRESS,
        help="Override the arXiv feed URL. Useful for tests or local Atom files.",
    )
    daily_parser.add_argument(
        "--cache",
        type=Path,
        default=argparse.SUPPRESS,
        help="JSON cache path. Defaults to a source-specific file under data/cache/.",
    )
    daily_parser.add_argument(
        "--output",
        type=Path,
        default=argparse.SUPPRESS,
        help="HTML output path. Defaults to a source-specific file under reports/daily/.",
    )
    daily_parser.add_argument(
        "--max-results",
        type=int,
        default=argparse.SUPPRESS,
        help=(
            "Compatibility display/debug cap. day-full and range-full still fetch the full requested window "
            "for the selected source bundle. "
            f"Built-in default: {CLI_DEFAULT_MAX_RESULTS}."
        ),
    )
    daily_parser.add_argument(
        "--zotero-export",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local Zotero CSL JSON export used to augment the biomedical baseline profile.",
    )
    _add_profile_source_arguments(daily_parser)

    deliver_parser = subparsers.add_parser(
        "deliver-daily",
        help="Compatibility email delivery from the current digest and report.",
        description=(
            "Compose an email from the existing daily digest and HTML report. By default this command writes a "
            "reviewable .eml file. Pass --send only after dry-run validation."
        ),
    )
    _add_config_arguments(deliver_parser)
    _add_daily_source_arguments(
        deliver_parser,
        default_help=(
            "Explicit fixed daily mode. If you omit both --mode and --category, deliver-daily defaults to "
            f"{DEFAULT_REVIEWER_SOURCE}."
        ),
        category_help=(
            "Strict single-category arXiv RSS path, for example q-bio, q-bio.GN, or cs.LG. "
            f"If you pass --category without --mode, deliver-daily skips the {DEFAULT_REVIEWER_SOURCE} default."
        ),
    )
    _add_report_mode_argument(deliver_parser)
    deliver_parser.add_argument("--today", type=_parse_iso_date, default=argparse.SUPPRESS, help="Override the target date as YYYY-MM-DD.")
    _add_request_window_arguments(deliver_parser)
    deliver_parser.add_argument(
        "--cache",
        type=Path,
        default=argparse.SUPPRESS,
        help="JSON cache path. Defaults to a source-specific file under data/cache/.",
    )
    deliver_parser.add_argument(
        "--report-path",
        type=Path,
        default=argparse.SUPPRESS,
        help="HTML report path. Defaults to a source-specific file under reports/daily/.",
    )
    deliver_parser.add_argument(
        "--max-results",
        type=int,
        default=argparse.SUPPRESS,
        help=(
            "Compatibility display/debug cap used only if materialization is needed. day-full and range-full "
            "still fetch the full requested window for the selected source bundle. "
            f"Built-in default: {CLI_DEFAULT_MAX_RESULTS}."
        ),
    )
    deliver_parser.add_argument(
        "--zotero-export",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local Zotero CSL JSON export used to augment the biomedical baseline profile.",
    )
    _add_profile_source_arguments(deliver_parser)
    deliver_parser.add_argument(
        "--email-to",
        default=argparse.SUPPRESS,
        help="Recipient address or comma-separated recipients. Falls back to config, then FRONTIER_COMPASS_EMAIL_TO.",
    )
    deliver_parser.add_argument(
        "--email-from",
        default=argparse.SUPPRESS,
        help="From address. Falls back to config, then FRONTIER_COMPASS_EMAIL_FROM.",
    )
    deliver_parser.add_argument(
        "--eml-output",
        type=Path,
        default=argparse.SUPPRESS,
        help="Dry-run .eml output path. Defaults to the HTML report path with a .eml suffix.",
    )
    deliver_parser.add_argument(
        "--attach-report",
        action="store_true",
        help="Attach the current HTML report in addition to using it as the HTML email body.",
    )
    deliver_parser.add_argument(
        "--send",
        action="store_true",
        help="Send via SMTP instead of writing a dry-run .eml file.",
    )

    report_parser = subparsers.add_parser(
        "demo-report",
        help="Secondary demo command: write an HTML report using bundled data.",
    )
    report_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DEMO_REPORT_PATH,
        help="Output path for the generated HTML report.",
    )
    report_parser.add_argument("--limit", type=int, default=5, help="Number of shortlisted papers to include.")

    ranking_parser = subparsers.add_parser(
        "demo-ranking",
        help="Secondary demo command: print a ranked list using bundled data.",
    )
    ranking_parser.add_argument("--limit", type=int, default=5, help="Number of ranked papers to print.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    app = FrontierCompassApp()
    runner = FrontierCompassRunner(app=app)

    if args.command == "demo-ranking":
        result = app.build_demo_report(limit=max(args.limit, 1))
        for index, ranked in enumerate(result.ranked[: max(args.limit, 1)], start=1):
            published = ranked.paper.published.isoformat() if ranked.paper.published else "unknown"
            print(f"{index}. {ranked.paper.title} [{ranked.score:.3f}] ({ranked.paper.source}, {published})")
        return 0

    if args.command == "demo-report":
        output_path = app.write_demo_report(args.output, limit=max(args.limit, 1))
        print(f"Wrote FrontierCompass demo report to {output_path}")
        return 0

    if args.command == "daily":
        return _handle_daily_command(parser, args, runner)

    if args.command == "deliver-daily":
        return _handle_deliver_daily_command(parser, args, runner)

    if args.command == "run-daily":
        return _handle_run_daily_command(parser, args, runner)

    if args.command == "history":
        return _handle_history_command(args)

    if args.command == "ui":
        return _handle_ui_command(parser, args, runner)

    parser.error(f"Unknown command: {args.command}")
    return 2


def main_entry() -> None:
    raise SystemExit(main())


def _handle_ui_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    runner: FrontierCompassRunner,
) -> int:
    del parser
    try:
        loaded_defaults = _load_command_defaults(args)
        requested_date, start_date, end_date, fetch_scope = _resolve_request_window(args)
        selected_source = _resolve_selected_source(args, loaded_defaults)
        report_mode = _resolve_report_mode(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_export = _resolve_zotero_export(args, loaded_defaults)
        zotero_db = _resolve_zotero_db_path(args)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source = _resolve_profile_source(
            args,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
        )
        allow_stale_cache = _resolve_allow_stale_cache(args, loaded_defaults)
        startup_args = _build_ui_startup_args(
            source=str(selected_source.value),
            requested_date=requested_date,
            start_date=start_date,
            end_date=end_date,
            max_results=int(max_results.value),
            report_mode=str(report_mode.value),
            profile_source=profile_source,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
            allow_stale_cache=bool(allow_stale_cache.value),
        )
        streamlit_app_path = _resolve_ui_app_path()
        command = _build_ui_launch_command(
            port=args.port,
            headless=args.server_headless,
            startup_args=startup_args,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.print_command:
        _print_resolution_summary(
            loaded_defaults=loaded_defaults,
            settings=_ui_settings(
                selected_source=selected_source,
                report_mode=report_mode,
                max_results=max_results,
                profile_source=profile_source,
                zotero_export=zotero_export,
                zotero_db=zotero_db,
                fetch_scope=ResolvedSetting(value=fetch_scope, source="cli" if hasattr(args, "fetch_scope") else "derived"),
                allow_stale_cache=allow_stale_cache,
            ),
        )
        print(f"Requested date: {requested_date.isoformat()}")
        print(f"Streamlit app: {streamlit_app_path}")
        print(f"Launch command: {format_shell_command(command)}")
        return 0

    session: LocalUISession | None = None
    prewarm_error = ""
    try:
        session_kwargs = _extend_optional_runner_kwargs(
            {
            "source": str(selected_source.value),
            "requested_date": requested_date,
            "max_results": int(max_results.value),
            "refresh": args.refresh,
            "allow_stale_cache": bool(allow_stale_cache.value),
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=str(report_mode.value),
            profile_source=profile_source,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        session = runner.prepare_ui_session(**session_kwargs)
    except Exception as exc:
        prewarm_error = str(exc)
        startup_args = [*startup_args, "--skip-initial-load"]
        command = _build_ui_launch_command(
            port=args.port,
            headless=args.server_headless,
            startup_args=startup_args,
        )

    if session is not None:
        _print_ui_startup_summary(session)
    elif prewarm_error:
        print("UI prewarm failed; launching Streamlit without an active digest.", file=sys.stderr)
        print(prewarm_error, file=sys.stderr)
        print("Use Load selection or Refresh from sources inside the app to retry.", file=sys.stderr)
    _print_resolution_summary(
        loaded_defaults=loaded_defaults,
        settings=_ui_settings(
            selected_source=selected_source,
            report_mode=report_mode,
            max_results=max_results,
            profile_source=profile_source,
            zotero_export=zotero_export,
            zotero_db=zotero_db,
            fetch_scope=ResolvedSetting(value=fetch_scope, source="cli" if hasattr(args, "fetch_scope") else "derived"),
            allow_stale_cache=allow_stale_cache,
        ),
    )
    print(f"Refresh prewarm: {'yes' if args.refresh else 'no'}")
    print(f"Launching FrontierCompass UI from {streamlit_app_path}")
    print(f"Command: {format_shell_command(command)}")
    completed = subprocess.run(command, check=False)
    return completed.returncode


def _handle_daily_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    runner: FrontierCompassRunner,
) -> int:
    try:
        loaded_defaults = _load_command_defaults(args)
        requested_date, start_date, end_date, fetch_scope = _resolve_request_window(args)
        feed_url = _optional_attr(args, "feed_url")
        selected_source = _resolve_selected_source(args, loaded_defaults, feed_url=feed_url)
        if is_fixed_daily_mode(str(selected_source.value)) and feed_url is not None:
            parser.error(f"--feed-url cannot be combined with --mode {selected_source.value}")
        report_mode = _resolve_report_mode(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_export = _resolve_zotero_export(args, loaded_defaults)
        zotero_db = _resolve_zotero_db_path(args)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source = _resolve_profile_source(
            args,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
        )
        run_kwargs = _extend_optional_runner_kwargs(
            {
            "source": str(selected_source.value),
            "requested_date": requested_date,
            "max_results": int(max_results.value),
            "refresh": True,
            "allow_stale_cache": False,
            "cache_path": _optional_attr(args, "cache"),
            "report_path": _optional_attr(args, "output"),
            "feed_url": feed_url,
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=str(report_mode.value),
            profile_source=profile_source,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        result = runner.run_daily(**run_kwargs)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_daily_digest_summary(
        digest=result.digest,
        cache_path=result.cache_path,
        report_path=result.report_path,
        fetch_status_label=result.fetch_status_label,
        fetch_error=result.fetch_error,
    )
    _print_resolution_summary(
        loaded_defaults=loaded_defaults,
        settings=(
            ("Selected source", selected_source),
            ("Report mode", report_mode),
            ("Max results", max_results),
            ("Profile source", profile_source),
            ("Zotero export", zotero_export),
            ("Zotero DB", zotero_db),
            ("Fetch scope", ResolvedSetting(value=fetch_scope, source="cli" if hasattr(args, "fetch_scope") else "derived")),
        ),
    )
    return 0


def _handle_deliver_daily_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    runner: FrontierCompassRunner,
) -> int:
    try:
        loaded_defaults = _load_command_defaults(args)
        requested_date, start_date, end_date, fetch_scope = _resolve_request_window(args)
        selected_source = _resolve_selected_source(args, loaded_defaults)
        report_mode = _resolve_report_mode(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_export = _resolve_zotero_export(args, loaded_defaults)
        zotero_db = _resolve_zotero_db_path(args)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source = _resolve_profile_source(
            args,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
        )
        email_to = _resolve_email_to(args, loaded_defaults)
        email_from = _resolve_email_from(args, loaded_defaults)
        run_kwargs = _extend_optional_runner_kwargs(
            {
            "source": str(selected_source.value),
            "requested_date": requested_date,
            "max_results": int(max_results.value),
            "refresh": False,
            "allow_stale_cache": False,
            "cache_path": _optional_attr(args, "cache"),
            "report_path": _optional_attr(args, "report_path"),
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=str(report_mode.value),
            profile_source=profile_source,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        result = runner.run_daily(**run_kwargs)
        prepared_email = prepare_daily_digest_email(
            result.digest,
            report_path=result.report_path,
            display_source=result.display_source,
            fetch_error=result.fetch_error,
            email_to=email_to.value,
            email_from=email_from.value,
            attach_report=args.attach_report,
        )
        if args.send:
            send_email_message(prepared_email.message, resolve_smtp_settings())
            eml_output_path = None
            delivery_label = "SMTP sent"
        else:
            eml_output_path = write_eml_message(
                prepared_email.message,
                _optional_attr(args, "eml_output") or default_eml_output_path(result.report_path),
            )
            delivery_label = "dry-run .eml written"
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_daily_digest_summary(
        digest=result.digest,
        cache_path=result.cache_path,
        report_path=result.report_path,
        fetch_status_label=prepared_email.digest_fetch_status_label,
        artifact_source_label=prepared_email.artifact_source_label,
        fetch_error=prepared_email.fresh_fetch_error,
    )
    _print_resolution_summary(
        loaded_defaults=loaded_defaults,
        settings=(
            ("Selected source", selected_source),
            ("Report mode", report_mode),
            ("Max results", max_results),
            ("Profile source", profile_source),
            ("Zotero export", zotero_export),
            ("Zotero DB", zotero_db),
            ("Fetch scope", ResolvedSetting(value=fetch_scope, source="cli" if hasattr(args, "fetch_scope") else "derived")),
            ("Email to", email_to),
            ("Email from", email_from),
        ),
    )
    print(f"Email subject: {prepared_email.subject}")
    print(f"Email to: {prepared_email.message['To']}")
    print(f"Email from: {prepared_email.message['From']}")
    print(f"Delivery: {delivery_label}")
    if eml_output_path is not None:
        print(f"EML: {eml_output_path}")
    return 0


def _handle_run_daily_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    runner: FrontierCompassRunner,
) -> int:
    try:
        loaded_defaults = _load_command_defaults(args)
        requested_date, start_date, end_date, fetch_scope = _resolve_request_window(args)
        feed_url = _optional_attr(args, "feed_url")
        selected_source = _resolve_selected_source(args, loaded_defaults, feed_url=feed_url)
        if is_fixed_daily_mode(str(selected_source.value)) and feed_url is not None:
            parser.error(f"--feed-url cannot be combined with --mode {selected_source.value}")
        report_mode = _resolve_report_mode(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_export = _resolve_zotero_export(args, loaded_defaults)
        zotero_db = _resolve_zotero_db_path(args)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source = _resolve_profile_source(
            args,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
        )
        dry_run_email = _resolve_dry_run_email(args, loaded_defaults)
        allow_stale_cache = _resolve_allow_stale_cache(args, loaded_defaults)
        email_to = _resolve_email_to(args, loaded_defaults)
        email_from = _resolve_email_from(args, loaded_defaults)
        run_kwargs = _extend_optional_runner_kwargs(
            {
            "source": str(selected_source.value),
            "requested_date": requested_date,
            "max_results": int(max_results.value),
            "refresh": args.refresh,
            "allow_stale_cache": bool(allow_stale_cache.value),
            "cache_path": _optional_attr(args, "cache"),
            "report_path": _optional_attr(args, "output"),
            "feed_url": feed_url,
            },
            start_date=start_date,
            end_date=end_date,
            report_mode=str(report_mode.value),
            profile_source=profile_source,
            zotero_export_path=zotero_export.value,
            zotero_db_path=zotero_db.value,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
        )
        result = runner.run_daily(**run_kwargs)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    fetch_status_label = result.fetch_status_label
    artifact_source_label = result.artifact_source_label
    delivery_label = "not requested"
    email_subject = ""
    email_to_value = ""
    email_from_value = ""
    eml_path: Path | None = None

    if bool(dry_run_email.value):
        prepared_email = prepare_daily_digest_email(
            result.digest,
            report_path=result.report_path,
            display_source=result.display_source,
            fetch_error=result.fetch_error,
            email_to=email_to.value,
            email_from=email_from.value,
        )
        eml_path = write_eml_message(
            prepared_email.message,
            _optional_attr(args, "eml_output") or default_eml_output_path(result.report_path),
        )
        fetch_status_label = prepared_email.digest_fetch_status_label
        artifact_source_label = prepared_email.artifact_source_label
        delivery_label = "dry-run .eml written"
        email_subject = prepared_email.subject
        email_to_value = str(prepared_email.message["To"] or "")
        email_from_value = str(prepared_email.message["From"] or "")

    _print_daily_digest_summary(
        digest=result.digest,
        cache_path=result.cache_path,
        report_path=result.report_path,
        fetch_status_label=fetch_status_label,
        artifact_source_label=artifact_source_label,
        fetch_error=result.fetch_error,
    )
    _print_resolution_summary(
        loaded_defaults=loaded_defaults,
        settings=_run_daily_settings(
            selected_source=selected_source,
            report_mode=report_mode,
            max_results=max_results,
            profile_source=profile_source,
            zotero_export=zotero_export,
            zotero_db=zotero_db,
            fetch_scope=ResolvedSetting(value=fetch_scope, source="cli" if hasattr(args, "fetch_scope") else "derived"),
            allow_stale_cache=allow_stale_cache,
            dry_run_email=dry_run_email,
            email_to=email_to,
            email_from=email_from,
        ),
    )
    print(f"Refresh: {'yes' if args.refresh else 'no'}")
    print(f"Delivery: {delivery_label}")
    if email_subject:
        print(f"Email subject: {email_subject}")
        print(f"Email to: {email_to_value}")
        print(f"Email from: {email_from_value}")
    if eml_path is not None:
        print(f"EML: {eml_path}")
    return 0


def _handle_history_command(args: argparse.Namespace) -> int:
    history_entries = load_recent_history(limit=max(int(args.limit), 1))
    if not history_entries:
        print("No recent daily runs found under data/cache.")
        return 0

    print("Recent runs (latest first)")
    print()
    for index, entry in enumerate(history_entries):
        print(f"{entry.requested_date.isoformat()} | {entry.mode_label}")
        print(f"Requested -> showing: {format_history_requested_effective_label(entry)}")
        print(f"Request window: {entry.request_window.label}")
        print(f"Fetch scope: {entry.fetch_scope}")
        print(f"Total fetched / displayed: {entry.total_fetched} / {entry.total_displayed}")
        print(f"Generated: {entry.generated_at.isoformat()}")
        print(" | ".join(build_history_summary_bits(entry)))
        if entry.frontier_report_present is not None:
            print(f"Frontier Report present: {'yes' if entry.frontier_report_present else 'no'}")
        if entry.source_run_stats:
            print(f"Sources: {_format_source_run_stats_text(entry.source_run_stats)}")
        elif entry.source_counts:
            print(
                "Sources: "
                + " | ".join(
                    f"{source} {count}"
                    for source, count in sorted(entry.source_counts.items(), key=lambda item: item[0])
                )
            )
        run_timings_text = _format_run_timings_text(entry.run_timings)
        if run_timings_text:
            print(f"Run timings: {run_timings_text}")
        for label, value in build_history_artifact_rows(entry):
            print(f"{label}: {value}")
        if index != len(history_entries) - 1:
            print()
    return 0


def _build_ui_startup_args(
    *,
    source: str,
    requested_date: date,
    start_date: date | None,
    end_date: date | None,
    max_results: int,
    report_mode: str,
    profile_source: ResolvedSetting,
    zotero_export_path: str | Path | None,
    zotero_db_path: str | Path | None,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str,
    allow_stale_cache: bool,
) -> list[str]:
    startup_args = [
        "--source",
        source,
        "--requested-date",
        requested_date.isoformat(),
        "--max-results",
        str(max(int(max_results), 1)),
        "--report-mode",
        report_mode,
        "--allow-stale-cache" if allow_stale_cache else "--no-stale-cache",
    ]
    if start_date is not None:
        startup_args.extend(["--start-date", start_date.isoformat()])
    if end_date is not None:
        startup_args.extend(["--end-date", end_date.isoformat()])
    if profile_source.source != "built-in" or profile_source.value == PROFILE_SOURCE_BASELINE:
        startup_args.extend(["--profile-source", str(profile_source.value)])
    if _should_include_fetch_scope(fetch_scope, start_date=start_date, end_date=end_date):
        startup_args.extend(["--fetch-scope", fetch_scope])
    if zotero_export_path is not None:
        startup_args.extend(["--zotero-export", str(Path(zotero_export_path))])
    if zotero_db_path is not None:
        startup_args.extend(["--zotero-db-path", str(Path(zotero_db_path))])
    for collection in zotero_collections:
        startup_args.extend(["--zotero-collection", str(collection)])
    return startup_args


def _should_include_fetch_scope(fetch_scope: str, *, start_date: date | None, end_date: date | None) -> bool:
    del start_date, end_date
    return fetch_scope != FETCH_SCOPE_DAY_FULL


def _extend_optional_runner_kwargs(
    kwargs: dict[str, object],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    report_mode: str = DEFAULT_REPORT_MODE,
    profile_source: ResolvedSetting | None = None,
    zotero_export_path: str | Path | None = None,
    zotero_db_path: str | Path | None = None,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str = FETCH_SCOPE_DAY_FULL,
) -> dict[str, object]:
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if report_mode != DEFAULT_REPORT_MODE:
        kwargs["report_mode"] = report_mode
    if profile_source is not None:
        kwargs["profile_source"] = profile_source.value
    kwargs["zotero_export_path"] = zotero_export_path
    if zotero_db_path is not None:
        kwargs["zotero_db_path"] = zotero_db_path
    if zotero_collections:
        kwargs["zotero_collections"] = tuple(zotero_collections)
    if _should_include_fetch_scope(fetch_scope, start_date=start_date, end_date=end_date):
        kwargs["fetch_scope"] = fetch_scope
    return kwargs


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help=f"Optional JSON defaults file. If omitted, checks {DEFAULT_USER_DEFAULTS_PATH}.",
    )
    config_group.add_argument(
        "--no-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help=f"Ignore {DEFAULT_USER_DEFAULTS_PATH} and use only CLI arguments plus built-ins.",
    )


def _add_daily_source_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_help: str,
    category_help: str,
) -> None:
    parser.add_argument(
        "--mode",
        choices=(
            BIOMEDICAL_LATEST_MODE,
            BIOMEDICAL_MULTISOURCE_MODE,
            BIOMEDICAL_DISCOVERY_MODE,
            BIOMEDICAL_DAILY_MODE,
        ),
        default=argparse.SUPPRESS,
        help=default_help,
    )
    parser.add_argument(
        "--category",
        default=argparse.SUPPRESS,
        help=category_help,
    )


def _add_report_mode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report-mode",
        choices=REPORT_MODE_CHOICES,
        default=argparse.SUPPRESS,
        help=(
            "Frontier Report runtime mode. deterministic is the safe default and stays zero-token. "
            "enhanced is opt-in and only targets the Frontier Report track when configured."
        ),
    )


def _add_request_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start-date",
        type=_parse_iso_date,
        default=argparse.SUPPRESS,
        help="Optional start date for a range run as YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-date",
        type=_parse_iso_date,
        default=argparse.SUPPRESS,
        help="Optional end date for a range run as YYYY-MM-DD.",
    )
    parser.add_argument(
        "--fetch-scope",
        choices=FETCH_SCOPE_OPTIONS,
        default=argparse.SUPPRESS,
        help="Fetch contract. day-full is the new default; range-full aggregates a requested date window.",
    )


def _add_profile_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile-source",
        choices=(
            PROFILE_SOURCE_BASELINE,
            PROFILE_SOURCE_ZOTERO,
            PROFILE_SOURCE_ZOTERO_EXPORT,
            PROFILE_SOURCE_LIVE_ZOTERO_DB,
        ),
        default=argparse.SUPPRESS,
        help=(
            "Profile basis contract. baseline is default; zotero is the primary reusable-export workflow. "
            "zotero_export and live_zotero_db remain accepted compatibility aliases."
        ),
    )
    parser.add_argument(
        "--zotero-db-path",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local Zotero SQLite path used to discover or refresh the reusable export snapshot.",
    )
    parser.add_argument(
        "--zotero-collection",
        action="append",
        default=argparse.SUPPRESS,
        help="Optional Zotero collection name to use as the profile topic filter. Repeat to keep multiple collections.",
    )


def _load_command_defaults(args: argparse.Namespace) -> LoadedUserDefaults:
    return load_user_defaults(
        config_path=_optional_attr(args, "config"),
        use_config=not bool(_optional_attr(args, "no_config")),
    )


def _resolve_requested_date(args: argparse.Namespace) -> date:
    return _optional_attr(args, "today") or date.today()


def _resolve_request_window(args: argparse.Namespace) -> tuple[date, date | None, date | None, str]:
    requested_date = _resolve_requested_date(args)
    start_date = _optional_attr(args, "start_date")
    end_date = _optional_attr(args, "end_date")
    fetch_scope = _optional_attr(args, "fetch_scope") or FETCH_SCOPE_DAY_FULL
    if start_date is not None and end_date is None:
        end_date = start_date
    if end_date is not None and start_date is None:
        start_date = requested_date
    if start_date is not None or end_date is not None:
        fetch_scope = FETCH_SCOPE_RANGE_FULL if _optional_attr(args, "fetch_scope") is None else str(fetch_scope)
    return requested_date, start_date, end_date, str(fetch_scope)


def _resolve_selected_source(
    args: argparse.Namespace,
    loaded_defaults: LoadedUserDefaults,
    *,
    feed_url: str | None = None,
) -> ResolvedSetting:
    if hasattr(args, "mode"):
        return ResolvedSetting(value=args.mode, source="cli")
    if hasattr(args, "category"):
        return ResolvedSetting(value=args.category, source="cli")
    if loaded_defaults.defaults.default_mode is not None:
        return ResolvedSetting(value=loaded_defaults.defaults.default_mode, source="config")
    if feed_url is not None:
        return ResolvedSetting(value=DEFAULT_ARXIV_CATEGORY, source="built-in")
    return ResolvedSetting(value=DEFAULT_REVIEWER_SOURCE, source="built-in")


def _resolve_max_results(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    resolved = resolve_setting(
        cli_value=max(int(_optional_attr(args, "max_results") or CLI_DEFAULT_MAX_RESULTS), 1),
        cli_provided=hasattr(args, "max_results"),
        config_value=loaded_defaults.defaults.default_max_results,
        config_is_set=loaded_defaults.defaults.default_max_results is not None,
        built_in_value=CLI_DEFAULT_MAX_RESULTS,
    )
    return ResolvedSetting(value=max(int(resolved.value), 1), source=resolved.source)


def _resolve_report_mode(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=_optional_attr(args, "report_mode") or DEFAULT_REPORT_MODE,
        cli_provided=hasattr(args, "report_mode"),
        config_value=loaded_defaults.defaults.default_report_mode,
        config_is_set=loaded_defaults.defaults.default_report_mode is not None,
        built_in_value=DEFAULT_REPORT_MODE,
    )


def _resolve_zotero_export(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=_optional_attr(args, "zotero_export"),
        cli_provided=hasattr(args, "zotero_export"),
        config_value=loaded_defaults.defaults.default_zotero_export_path,
        config_is_set=loaded_defaults.defaults.default_zotero_export_path is not None,
        built_in_value=None,
    )


def _resolve_zotero_db_path(args: argparse.Namespace) -> ResolvedSetting:
    if hasattr(args, "zotero_db_path"):
        return ResolvedSetting(value=_optional_attr(args, "zotero_db_path"), source="cli")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_zotero_collections(args: argparse.Namespace) -> tuple[str, ...]:
    values = _optional_attr(args, "zotero_collection") or ()
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        canonical = normalized.lower()
        if not normalized or canonical in seen:
            continue
        selected.append(normalized)
        seen.add(canonical)
    return tuple(selected)


def _resolve_profile_source(
    args: argparse.Namespace,
    *,
    zotero_export_path: str | Path | None,
    zotero_db_path: str | Path | None,
) -> ResolvedSetting:
    if hasattr(args, "profile_source"):
        return ResolvedSetting(value=str(args.profile_source), source="cli")
    if zotero_db_path is not None or zotero_export_path is not None:
        return ResolvedSetting(value=PROFILE_SOURCE_ZOTERO, source="derived")
    return ResolvedSetting(value=PROFILE_SOURCE_BASELINE, source="built-in")


def _resolve_email_to(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    if hasattr(args, "email_to"):
        return ResolvedSetting(value=args.email_to, source="cli")
    if loaded_defaults.defaults.default_email_to:
        return ResolvedSetting(value=loaded_defaults.defaults.default_email_to, source="config")
    env_value = os.environ.get("FRONTIER_COMPASS_EMAIL_TO", "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_email_from(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    if hasattr(args, "email_from"):
        return ResolvedSetting(value=args.email_from, source="cli")
    if loaded_defaults.defaults.default_email_from is not None:
        return ResolvedSetting(value=loaded_defaults.defaults.default_email_from, source="config")
    env_value = os.environ.get("FRONTIER_COMPASS_EMAIL_FROM", "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_dry_run_email(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=bool(_optional_attr(args, "dry_run_email")),
        cli_provided=hasattr(args, "dry_run_email"),
        config_value=loaded_defaults.defaults.default_generate_dry_run_email,
        config_is_set=loaded_defaults.defaults.default_generate_dry_run_email is not None,
        built_in_value=False,
    )


def _resolve_allow_stale_cache(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=bool(_optional_attr(args, "allow_stale_cache")),
        cli_provided=hasattr(args, "allow_stale_cache"),
        config_value=loaded_defaults.defaults.default_allow_stale_cache,
        config_is_set=loaded_defaults.defaults.default_allow_stale_cache is not None,
        built_in_value=True,
    )


def _run_daily_settings(
    *,
    selected_source: ResolvedSetting,
    report_mode: ResolvedSetting,
    max_results: ResolvedSetting,
    profile_source: ResolvedSetting,
    zotero_export: ResolvedSetting,
    zotero_db: ResolvedSetting,
    fetch_scope: ResolvedSetting,
    allow_stale_cache: ResolvedSetting,
    dry_run_email: ResolvedSetting,
    email_to: ResolvedSetting,
    email_from: ResolvedSetting,
) -> tuple[tuple[str, ResolvedSetting], ...]:
    settings: list[tuple[str, ResolvedSetting]] = [
        ("Selected source", selected_source),
        ("Report mode", report_mode),
        ("Max results", max_results),
        ("Profile source", profile_source),
        ("Zotero export", zotero_export),
        ("Zotero DB", zotero_db),
        ("Fetch scope", fetch_scope),
        ("Allow stale cache fallback", allow_stale_cache),
        ("Dry-run email", dry_run_email),
    ]
    if dry_run_email.value or email_to.source != "built-in" or email_from.source != "built-in":
        settings.append(("Email to", email_to))
        settings.append(("Email from", email_from))
    return tuple(settings)


def _ui_settings(
    *,
    selected_source: ResolvedSetting,
    report_mode: ResolvedSetting,
    max_results: ResolvedSetting,
    profile_source: ResolvedSetting,
    zotero_export: ResolvedSetting,
    zotero_db: ResolvedSetting,
    fetch_scope: ResolvedSetting,
    allow_stale_cache: ResolvedSetting,
) -> tuple[tuple[str, ResolvedSetting], ...]:
    return (
        ("Selected source", selected_source),
        ("Report mode", report_mode),
        ("Max results", max_results),
        ("Profile source", profile_source),
        ("Zotero export", zotero_export),
        ("Zotero DB", zotero_db),
        ("Fetch scope", fetch_scope),
        ("Allow stale cache fallback", allow_stale_cache),
    )


def _optional_attr(args: argparse.Namespace, name: str):
    return getattr(args, name, None)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def format_shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _print_run_daily_summary(result: DailyRunResult) -> None:
    _print_daily_digest_summary(
        digest=result.digest,
        cache_path=result.cache_path,
        report_path=result.report_path,
        fetch_status_label=result.fetch_status_label,
        artifact_source_label=result.artifact_source_label,
        fetch_error=result.fetch_error,
    )


def _print_ui_startup_summary(session: LocalUISession) -> None:
    print(f"Current UI digest: {session.digest.mode_label or session.digest.category}")
    print("Tracks: Personalized Digest + Frontier Report")
    if session.digest.frontier_report is None:
        print("Frontier Report status: unavailable in this legacy cache.")
    print(f"Frontier Report present: {'yes' if session.digest.frontier_report is not None else 'no'}")
    print(f"Requested report mode: {session.digest.requested_report_mode}")
    print(f"Frontier Report mode: {session.digest.report_mode}")
    print(f"Report status: {session.digest.report_status}")
    if session.digest.report_error:
        print(f"Report note: {session.digest.report_error}")
    print(f"Cost mode: {session.digest.cost_mode}")
    print(f"Enhanced track: {session.digest.enhanced_track or 'none'}")
    print(f"Runtime note: {session.digest.runtime_note}")
    print(f"Fetch status: {session.fetch_status_label}")
    print(f"Request window: {session.digest.request_window.label}")
    print(f"Fetch scope: {session.fetch_scope}")
    print(f"Requested date: {session.requested_date.isoformat()}")
    print(f"Effective displayed date: {session.effective_date.isoformat()}")
    print(f"Total fetched: {session.total_fetched}")
    print(f"Total displayed: {session.total_displayed}")
    print(f"Profile basis: {session.profile_basis_label}")
    print(f"Profile label: {session.digest.profile.profile_label}")
    print(f"Profile source: {session.profile_source} ({session.digest.profile.profile_source_label})")
    if session.digest.profile.profile_path:
        print(f"Profile path: {session.digest.profile.profile_path}")
    if session.digest.profile.profile_item_count or session.digest.profile.profile_used_item_count:
        print(
            "Profile items parsed / used: "
            f"{session.digest.profile.profile_item_count} / {session.digest.profile.profile_used_item_count}"
        )
    top_profile_terms = session.digest.profile.top_profile_terms(limit=6)
    if top_profile_terms:
        print(f"Top profile terms: {', '.join(top_profile_terms)}")
    if session.zotero_export_name:
        print(f"Zotero export: {session.zotero_export_name}")
    if session.digest.profile.zotero_db_name:
        print(f"Live Zotero DB: {session.digest.profile.zotero_db_name}")
    if session.fetch_error:
        print(f"Fresh fetch error: {session.fetch_error}")
    if session.digest.source_run_stats:
        print(f"Source stats: {_format_source_run_stats_text(session.digest.source_run_stats)}")
    elif session.digest.source_counts:
        print(
            "Source counts: "
            + " | ".join(
                f"{source} {count}"
                for source, count in sorted(session.digest.source_counts.items(), key=lambda item: item[0])
            )
        )
    run_timings_text = _format_run_timings_text(session.digest.run_timings)
    if run_timings_text:
        print(f"Run timings: {run_timings_text}")
    if session.recent_history_error:
        print(f"Recent history: unavailable ({session.recent_history_error})")
    else:
        print(f"Recent history entries: {len(session.recent_history)}")
    print(f"Cache: {session.cache_path}")
    print(f"Report: {session.report_path}")


def _print_daily_digest_summary(
    *,
    digest: DailyDigest,
    cache_path: str | Path,
    report_path: str | Path,
    fetch_status_label: str,
    fetch_error: str = "",
    artifact_source_label: str = "",
) -> None:
    print(f"Fetch status: {fetch_status_label}")
    if artifact_source_label and artifact_source_label != fetch_status_label:
        print(f"Artifact source: {artifact_source_label}")
    if fetch_error:
        print(f"Fresh fetch error: {fetch_error}")
    print("Tracks: Personalized Digest + Frontier Report")
    if digest.frontier_report is None:
        print("Frontier Report status: unavailable in this legacy cache.")
    print(f"Frontier Report present: {'yes' if digest.frontier_report is not None else 'no'}")
    print(f"Requested report mode: {digest.requested_report_mode}")
    print(f"Frontier Report mode: {digest.report_mode}")
    print(f"Report status: {digest.report_status}")
    if digest.report_error:
        print(f"Report note: {digest.report_error}")
    print(f"Cost mode: {digest.cost_mode}")
    print(f"Enhanced track: {digest.enhanced_track or 'none'}")
    print(f"Enhanced item count: {digest.enhanced_item_count}")
    print(f"Runtime note: {digest.runtime_note}")
    print(f"Request window: {digest.request_window.label}")
    print(f"Requested date: {digest.requested_target_date.isoformat()}")
    print(f"Effective displayed date: {digest.effective_display_date.isoformat()}")
    print(f"Latest-available display fallback: {'yes' if digest.used_latest_available_fallback else 'no'}")
    print(f"Stale cache fallback: {'yes' if digest.stale_cache_fallback_used else 'no'}")
    if digest.stale_cache_fallback_used:
        print(
            "Stale cache source requested date: "
            + (
                digest.stale_cache_source_requested_date.isoformat()
                if digest.stale_cache_source_requested_date is not None
                else "unknown"
            )
        )
        print(
            "Stale cache source effective date: "
            + (
                digest.stale_cache_source_effective_date.isoformat()
                if digest.stale_cache_source_effective_date is not None
                else "unknown"
            )
        )
    print(f"Display basis: {digest.selection_basis_label}")
    print(f"Mode: {digest.category}")
    print(f"Mode label: {digest.mode_label or digest.category}")
    print(f"Mode kind: {digest.mode_kind or 'n/a'}")
    print(f"Profile basis: {digest.profile.basis_label or 'n/a'}")
    print(f"Profile label: {digest.profile.profile_label}")
    print(f"Profile source: {digest.profile.profile_source} ({digest.profile.profile_source_label})")
    if digest.profile.profile_path:
        print(f"Profile path: {digest.profile.profile_path}")
    if digest.profile.profile_item_count or digest.profile.profile_used_item_count:
        print(
            "Profile items parsed / used: "
            f"{digest.profile.profile_item_count} / {digest.profile.profile_used_item_count}"
        )
    top_profile_terms = digest.profile.top_profile_terms(limit=6)
    if top_profile_terms:
        print(f"Top profile terms: {', '.join(top_profile_terms)}")
    print(f"Fetch scope: {digest.fetch_scope}")
    if digest.profile.zotero_export_name:
        print(f"Zotero export: {digest.profile.zotero_export_name}")
    if digest.profile.zotero_active:
        print(
            f"Zotero items parsed / used: {digest.profile.zotero_item_count} / "
            f"{digest.profile.zotero_used_item_count}"
        )
        top_zotero_signals = tuple((*digest.profile.zotero_keywords[:3], *digest.profile.zotero_concepts[:3]))
        if top_zotero_signals:
            print(f"Top Zotero signals: {', '.join(top_zotero_signals)}")
    if digest.profile.zotero_db_name:
        print(f"Live Zotero DB: {digest.profile.zotero_db_name}")
    print(f"Searched categories: {', '.join(digest.searched_categories)}")
    if digest.search_profile_label:
        print(f"Search profile: {digest.search_profile_label}")
    if digest.mode_notes:
        print(f"Mode notes: {digest.mode_notes}")
    for index, query in enumerate(digest.search_queries, start=1):
        print(f"Query {index}: {query}")
    print(f"Strict same-day fetched: {digest.strict_same_day_fetched_label}")
    print(f"Strict same-day ranked: {digest.strict_same_day_ranked_label}")
    print(f"Total fetched: {max(digest.total_fetched, digest.total_ranked_count)}")
    print(f"Total ranked pool: {digest.total_ranked_count}")
    print(f"Total displayed: {digest.total_displayed_count}")
    if digest.source_run_stats:
        print(f"Source stats: {_format_source_run_stats_text(digest.source_run_stats)}")
    elif digest.source_counts:
        print(
            "Source counts: "
            + " | ".join(
                f"{source} {count}"
                for source, count in sorted(digest.source_counts.items(), key=lambda item: item[0])
            )
        )
    run_timings_text = _format_run_timings_text(digest.run_timings)
    if run_timings_text:
        print(f"Run timings: {run_timings_text}")
    print(
        "Per-category counts: "
        + " | ".join(summarize_category_counts(digest.searched_categories, digest.per_category_counts))
    )
    print(f"Cache: {cache_path}")
    print(f"Report: {report_path}")


def _print_resolution_summary(
    *,
    loaded_defaults: LoadedUserDefaults,
    settings: tuple[tuple[str, ResolvedSetting], ...],
) -> None:
    print(f"Config: {_format_config_status(loaded_defaults)}")
    for label, resolved in settings:
        print(f"{label}: {_format_setting_value(resolved.value)} ({resolved.source})")
    settings_by_label = {label: resolved for label, resolved in settings}
    profile_source_note = _profile_source_resolution_note(
        profile_source=settings_by_label.get("Profile source"),
        zotero_export=settings_by_label.get("Zotero export"),
        zotero_db=settings_by_label.get("Zotero DB"),
    )
    if profile_source_note:
        print(profile_source_note)


def _format_config_status(loaded_defaults: LoadedUserDefaults) -> str:
    if loaded_defaults.disabled:
        return f"disabled by --no-config (default path {loaded_defaults.path})"
    if loaded_defaults.loaded:
        return f"loaded from {loaded_defaults.path}"
    return f"not found at {loaded_defaults.path}; using built-ins"


def _format_setting_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def _profile_source_resolution_note(
    *,
    profile_source: ResolvedSetting | None,
    zotero_export: ResolvedSetting | None,
    zotero_db: ResolvedSetting | None,
) -> str:
    if profile_source is None or profile_source.source != "derived":
        return ""
    if profile_source.value == PROFILE_SOURCE_ZOTERO:
        if zotero_db is not None and zotero_db.value is not None and zotero_export is not None and zotero_export.value is not None:
            return (
                "Profile source auto-selected: zotero because both a Zotero DB path and "
                "a reusable Zotero export were supplied."
            )
        if zotero_db is not None and zotero_db.value is not None:
            return "Profile source auto-selected: zotero because a Zotero DB path was supplied."
        return "Profile source auto-selected: zotero because a Zotero export was supplied."
    return ""


def _format_source_run_stats_text(source_run_stats: Sequence[SourceRunStats]) -> str:
    return " | ".join(
        _format_source_run_stats_row(item)
        for item in source_run_stats
    )


def _format_source_run_stats_row(source_run_stat: SourceRunStats) -> str:
    parts = [
        (
            f"{source_run_stat.source} fetched {source_run_stat.fetched_count} "
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
    if source_run_stat.note:
        parts.append(f"note={source_run_stat.note}")
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


if __name__ == "__main__":
    main_entry()
