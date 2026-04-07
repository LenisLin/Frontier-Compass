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
from frontier_compass.common.frontier_report_llm import (
    FRONTIER_COMPASS_LLM_API_KEY_ENV,
    FRONTIER_COMPASS_LLM_BASE_URL_ENV,
    FRONTIER_COMPASS_LLM_MODEL_ENV,
    FRONTIER_COMPASS_LLM_PROVIDER_ENV,
)
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    REPORT_MODE_CHOICES,
    ZERO_TOKEN_COST_MODE,
    format_llm_bool,
    format_llm_provider,
    format_llm_seconds,
)
from frontier_compass.common.source_bundles import SOURCE_BUNDLE_AI_FOR_MEDICINE
from frontier_compass.common.source_bundles import SOURCE_BUNDLE_BIOMEDICAL
from frontier_compass.common.user_defaults import (
    DEFAULT_USER_DEFAULTS_PATH,
    LoadedUserDefaults,
    ResolvedSetting,
    load_user_defaults,
    resolve_setting,
)
from frontier_compass.reporting.daily_brief import summarize_category_counts
from frontier_compass.storage.schema import (
    DailyDigest,
    RunTimings,
    SourceRunStats,
    resolve_requested_profile_source,
)
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
    PROFILE_SOURCE_ZOTERO_EXPORT,
    DEFAULT_REVIEWER_SOURCE,
    FrontierCompassApp,
    display_artifact_source_label,
    display_source_label,
    format_source_outcome_label,
    is_fixed_daily_mode,
    resolve_default_profile_selection,
)
from frontier_compass.ui.history import (
    build_history_artifact_rows,
    format_history_requested_effective_label,
    format_history_compatibility_text,
    format_history_llm_provenance_text,
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


def _build_ui_launch_env(
    *,
    llm_provider: ResolvedSetting | None = None,
    llm_base_url: ResolvedSetting | None = None,
    llm_api_key: ResolvedSetting | None = None,
    llm_model: ResolvedSetting | None = None,
) -> dict[str, str]:
    launch_env = dict(os.environ)
    if llm_provider is not None and llm_provider.source == "cli" and llm_provider.value:
        launch_env[FRONTIER_COMPASS_LLM_PROVIDER_ENV] = str(llm_provider.value)
    if llm_base_url is not None and llm_base_url.source == "cli" and llm_base_url.value:
        launch_env[FRONTIER_COMPASS_LLM_BASE_URL_ENV] = str(llm_base_url.value)
    if llm_api_key is not None and llm_api_key.source == "cli" and llm_api_key.value:
        launch_env[FRONTIER_COMPASS_LLM_API_KEY_ENV] = str(llm_api_key.value)
    if llm_model is not None and llm_model.source == "cli" and llm_model.value:
        launch_env[FRONTIER_COMPASS_LLM_MODEL_ENV] = str(llm_model.value)
    return launch_env


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
            "Recommended local CLI path. By default this cache-first wrapper uses the public 2-source bundle "
            "(arXiv + bioRxiv), ensures the HTML report exists, and can optionally write a dry-run .eml "
            "artifact. medRxiv remains available only through explicit compatibility paths."
        ),
    )
    _add_config_arguments(run_daily_parser)
    _add_daily_source_arguments(
        run_daily_parser,
        default_help=(
            "Advanced compatibility source override. If you omit both --mode and --category, run-daily uses "
            "the default public 2-source bundle (arXiv + bioRxiv). Use biomedical-multisource only for the "
            "compatibility 3-source path."
        ),
        category_help=(
            "Strict single-category arXiv RSS compatibility path, for example q-bio, q-bio.GN, or cs.LG. "
            "If you pass --category without --mode, run-daily skips the default 2-source bundle."
        ),
    )
    _add_report_mode_argument(run_daily_parser)
    _add_frontier_report_llm_arguments(run_daily_parser)
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
        help=(
            "Advanced override for a local Zotero CSL JSON export when you want to bypass config-backed "
            "defaults or the reusable snapshot."
        ),
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
            "Recommended local interactive surface. The app resolves config-backed Zotero defaults on its own, "
            "opens the reading-first homepage cache-first, and keeps CLI startup args for explicit overrides "
            "or exact launch reproduction."
        ),
    )
    _add_config_arguments(ui_parser)
    _add_daily_source_arguments(
        ui_parser,
        default_help=(
            "Advanced compatibility source override. If you omit both --mode and --category, ui uses the "
            "default public 2-source bundle (arXiv + bioRxiv). Use biomedical-multisource only for the "
            "compatibility 3-source path."
        ),
        category_help=(
            "Strict single-category arXiv compatibility path, for example q-bio, q-bio.GN, or cs.LG. "
            "If you pass --category without --mode, ui skips the default 2-source bundle."
        ),
    )
    _add_report_mode_argument(ui_parser)
    _add_frontier_report_llm_arguments(ui_parser)
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
        help=(
            "Advanced override for a local Zotero CSL JSON export. The normal UI path is to configure "
            "default_zotero_db_path or default_zotero_export_path in configs/user_defaults.json."
        ),
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
        help="Compatibility explicit build for advanced source paths or a strict single feed.",
        description=(
            "Compatibility build path. If you omit both --mode and --category, this command still uses the "
            "default public 2-source bundle. Pass --mode for an advanced bundle or compatibility mode, or "
            "pass --category for a strict single-category RSS path."
        ),
    )
    _add_config_arguments(daily_parser)
    _add_daily_source_arguments(
        daily_parser,
        default_help=(
            "Advanced compatibility source override. If you omit both --mode and --category, daily uses the "
            "default public 2-source bundle. Use biomedical-discovery for the strict same-day hybrid audit "
            "path, biomedical-daily for the q-bio comparison bundle, or biomedical-multisource for the "
            "compatibility 3-source bundle."
        ),
        category_help=(
            "Strict single-category arXiv RSS compatibility path, for example q-bio, q-bio.GN, or cs.LG. "
            "If you pass --category without --mode, daily skips the default 2-source bundle."
        ),
    )
    _add_report_mode_argument(daily_parser)
    _add_frontier_report_llm_arguments(daily_parser)
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
        help=(
            "Advanced override for a local Zotero CSL JSON export when you want to bypass config-backed "
            "defaults or the reusable snapshot."
        ),
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
            "Advanced compatibility source override. If you omit both --mode and --category, deliver-daily "
            "uses the default public 2-source bundle."
        ),
        category_help=(
            "Strict single-category arXiv RSS compatibility path, for example q-bio, q-bio.GN, or cs.LG. "
            "If you pass --category without --mode, deliver-daily skips the default 2-source bundle."
        ),
    )
    _add_report_mode_argument(deliver_parser)
    _add_frontier_report_llm_arguments(deliver_parser)
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
        help=(
            "Advanced override for a local Zotero CSL JSON export when you want to bypass config-backed "
            "defaults or the reusable snapshot."
        ),
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
        llm_provider = _resolve_llm_provider(args, loaded_defaults)
        llm_base_url = _resolve_llm_base_url(args, loaded_defaults)
        llm_api_key = _resolve_llm_api_key(args, loaded_defaults)
        llm_model = _resolve_llm_model(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source, zotero_export, zotero_db = _resolve_profile_settings(
            args,
            loaded_defaults,
        )
        allow_stale_cache = _resolve_allow_stale_cache(args, loaded_defaults)
        startup_args = _build_ui_startup_args(
            args=args,
            source=selected_source,
            requested_date=requested_date,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            report_mode=report_mode,
            profile_source=profile_source,
            zotero_export_path=zotero_export,
            zotero_db_path=zotero_db,
            zotero_collections=zotero_collections,
            fetch_scope=fetch_scope,
            allow_stale_cache=allow_stale_cache,
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
                llm_provider=llm_provider,
                llm_base_url=llm_base_url,
                llm_model=llm_model,
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
    launch_env = _build_ui_launch_env(
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )
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
            llm_provider=llm_provider.value,
            llm_base_url=llm_base_url.value,
            llm_api_key=llm_api_key.value,
            llm_model=llm_model.value,
            profile_source=profile_source.value,
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
        print("Change the date or use Refresh inside the app to retry.", file=sys.stderr)
    _print_resolution_summary(
        loaded_defaults=loaded_defaults,
        settings=_ui_settings(
            selected_source=selected_source,
            report_mode=report_mode,
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
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
    subprocess_kwargs: dict[str, object] = {"check": False}
    if any(
        resolved.source == "cli" and resolved.value
        for resolved in (llm_provider, llm_base_url, llm_api_key, llm_model)
    ):
        subprocess_kwargs["env"] = launch_env
    completed = subprocess.run(command, **subprocess_kwargs)
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
        llm_provider = _resolve_llm_provider(args, loaded_defaults)
        llm_base_url = _resolve_llm_base_url(args, loaded_defaults)
        llm_api_key = _resolve_llm_api_key(args, loaded_defaults)
        llm_model = _resolve_llm_model(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source, zotero_export, zotero_db = _resolve_profile_settings(
            args,
            loaded_defaults,
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
            llm_provider=llm_provider.value,
            llm_base_url=llm_base_url.value,
            llm_api_key=llm_api_key.value,
            llm_model=llm_model.value,
            profile_source=profile_source.value,
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
            ("LLM provider", llm_provider),
            ("LLM base URL", llm_base_url),
            ("LLM model", llm_model),
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
        llm_provider = _resolve_llm_provider(args, loaded_defaults)
        llm_base_url = _resolve_llm_base_url(args, loaded_defaults)
        llm_api_key = _resolve_llm_api_key(args, loaded_defaults)
        llm_model = _resolve_llm_model(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source, zotero_export, zotero_db = _resolve_profile_settings(
            args,
            loaded_defaults,
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
            llm_provider=llm_provider.value,
            llm_base_url=llm_base_url.value,
            llm_api_key=llm_api_key.value,
            llm_model=llm_model.value,
            profile_source=profile_source.value,
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
            ("LLM provider", llm_provider),
            ("LLM base URL", llm_base_url),
            ("LLM model", llm_model),
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
        llm_provider = _resolve_llm_provider(args, loaded_defaults)
        llm_base_url = _resolve_llm_base_url(args, loaded_defaults)
        llm_api_key = _resolve_llm_api_key(args, loaded_defaults)
        llm_model = _resolve_llm_model(args, loaded_defaults)
        max_results = _resolve_max_results(args, loaded_defaults)
        zotero_collections = _resolve_zotero_collections(args)
        profile_source, zotero_export, zotero_db = _resolve_profile_settings(
            args,
            loaded_defaults,
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
            llm_provider=llm_provider.value,
            llm_base_url=llm_base_url.value,
            llm_api_key=llm_api_key.value,
            llm_model=llm_model.value,
            profile_source=profile_source.value,
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
            llm_provider=llm_provider,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
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

    current_entries = [entry for entry in history_entries if not entry.is_compatibility_entry]
    compatibility_entries = [entry for entry in history_entries if entry.is_compatibility_entry]

    print("Recent runs (current-contract first)")
    print()
    for index, entry in enumerate(current_entries):
        _print_history_entry(entry)
        if index != len(current_entries) - 1:
            print()
    if compatibility_entries:
        if current_entries:
            print()
        print("Compatibility / archived entries")
        print()
        for index, entry in enumerate(compatibility_entries):
            _print_history_entry(entry)
            if index != len(compatibility_entries) - 1:
                print()
    return 0


def _print_history_entry(entry: object) -> None:
    if not hasattr(entry, "requested_date") or not hasattr(entry, "generated_at"):
        return
    print(f"{entry.requested_date.isoformat()} | {entry.mode_label}")
    print(f"Requested -> showing: {format_history_requested_effective_label(entry)}")
    print(f"Request window: {entry.request_window.label}")
    print(f"Fetch scope: {entry.fetch_scope}")
    print(f"Total fetched / displayed: {entry.total_fetched} / {entry.total_displayed}")
    print(f"Generated: {entry.generated_at.isoformat()}")
    print(" | ".join(_build_cli_history_summary_bits(entry)))
    if getattr(entry, "is_compatibility_entry", False):
        print(f"Compatibility: {format_history_compatibility_text(entry)}")
    if hasattr(entry, "llm_requested"):
        print(format_history_llm_provenance_text(entry))
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


def _build_cli_history_summary_bits(entry: object) -> tuple[str, ...]:
    summary_bits = [
        getattr(entry, "fetch_status", "") or "n/a",
        f"ranked {getattr(entry, 'ranked_count', 0)}",
        f"report {getattr(entry, 'report_mode', DEFAULT_REPORT_MODE)}/{getattr(entry, 'report_status', 'ready')}",
        getattr(entry, "cost_mode", "") or ZERO_TOKEN_COST_MODE,
        getattr(entry, "profile_label", "") or "n/a",
    ]
    source_run_stats = getattr(entry, "source_run_stats", ())
    source_counts = getattr(entry, "source_counts", {})
    if source_run_stats:
        summary_bits.append(
            " | ".join(
                _format_cli_history_source_run_stat(row)
                for row in source_run_stats
            )
        )
    elif source_counts:
        summary_bits.append(
            " | ".join(
                f"{source} {count}"
                for source, count in sorted(source_counts.items(), key=lambda item: item[0])
            )
        )
    run_timings = getattr(entry, "run_timings", None)
    total_seconds = getattr(run_timings, "total_seconds", None)
    if total_seconds is not None:
        summary_bits.append(f"time {total_seconds:.2f}s")
    zotero_export_name = getattr(entry, "zotero_export_name", "")
    zotero_db_name = getattr(entry, "zotero_db_name", "")
    if zotero_export_name:
        summary_bits.append(f"zotero {zotero_export_name}")
    elif zotero_db_name:
        summary_bits.append(f"zotero-db {zotero_db_name}")
    elif bool(getattr(entry, "zotero_augmented", False)):
        summary_bits.append("zotero enabled")
    exploration_pick_count = getattr(entry, "exploration_pick_count", None)
    if exploration_pick_count:
        summary_bits.append(f"exploration {exploration_pick_count}")
    if getattr(entry, "frontier_report_present", None) is False:
        summary_bits.append("frontier report unavailable")
    if getattr(entry, "report_artifact_aligned", None) is False:
        summary_bits.append("report artifact not aligned")
    return tuple(summary_bits)


def _format_cli_history_source_run_stat(row: object) -> str:
    piece = (
        f"{getattr(row, 'source', 'unknown')} "
        f"{getattr(row, 'fetched_count', 0)}/{getattr(row, 'displayed_count', 0)} "
        f"[{getattr(row, 'resolved_outcome', '')}; {getattr(row, 'status', '')}; {getattr(row, 'cache_status', '')}]"
    )
    extra_bits: list[str] = []
    resolved_live_outcome = getattr(row, "resolved_live_outcome", "")
    resolved_outcome = getattr(row, "resolved_outcome", "")
    if resolved_live_outcome != resolved_outcome:
        extra_bits.append(f"live: {resolved_live_outcome}")
    error = getattr(row, "error", "")
    if error:
        extra_bits.append(f"error: {error}")
    note = getattr(row, "note", "")
    if note:
        extra_bits.append(f"note: {note}")
    if extra_bits:
        return f"{piece} ({'; '.join(extra_bits)})"
    return piece


def _build_ui_startup_args(
    *,
    args: argparse.Namespace,
    source: ResolvedSetting,
    requested_date: date,
    start_date: date | None,
    end_date: date | None,
    max_results: ResolvedSetting,
    report_mode: ResolvedSetting,
    profile_source: ResolvedSetting,
    zotero_export_path: ResolvedSetting,
    zotero_db_path: ResolvedSetting,
    zotero_collections: Sequence[str] = (),
    fetch_scope: str,
    allow_stale_cache: ResolvedSetting,
) -> list[str]:
    startup_args: list[str] = []
    if hasattr(args, "config"):
        startup_args.extend(["--config", str(args.config)])
    if bool(_optional_attr(args, "no_config")):
        startup_args.append("--no-config")
    if source.source == "cli":
        startup_args.extend(["--source", str(source.value)])
    if hasattr(args, "today"):
        startup_args.extend(["--requested-date", requested_date.isoformat()])
    if max_results.source == "cli":
        startup_args.extend(["--max-results", str(max(int(max_results.value), 1))])
    if report_mode.source == "cli":
        startup_args.extend(["--report-mode", str(report_mode.value)])
    if allow_stale_cache.source == "cli":
        startup_args.append("--allow-stale-cache" if bool(allow_stale_cache.value) else "--no-stale-cache")
    if start_date is not None:
        startup_args.extend(["--start-date", start_date.isoformat()])
    if end_date is not None:
        startup_args.extend(["--end-date", end_date.isoformat()])
    if profile_source.source == "cli":
        startup_args.extend(["--profile-source", str(profile_source.value)])
    if _should_include_fetch_scope(fetch_scope, start_date=start_date, end_date=end_date):
        startup_args.extend(["--fetch-scope", fetch_scope])
    if zotero_export_path.source == "cli" and zotero_export_path.value is not None:
        startup_args.extend(["--zotero-export", str(Path(zotero_export_path.value))])
    if zotero_db_path.source == "cli" and zotero_db_path.value is not None:
        startup_args.extend(["--zotero-db-path", str(Path(zotero_db_path.value))])
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
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
    profile_source: str | None = None,
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
    if llm_provider is not None:
        kwargs["llm_provider"] = llm_provider
    if llm_base_url is not None:
        kwargs["llm_base_url"] = llm_base_url
    if llm_api_key is not None:
        kwargs["llm_api_key"] = llm_api_key
    if llm_model is not None:
        kwargs["llm_model"] = llm_model
    if profile_source is not None:
        kwargs["profile_source"] = profile_source
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
            SOURCE_BUNDLE_BIOMEDICAL,
            SOURCE_BUNDLE_AI_FOR_MEDICINE,
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


def _add_frontier_report_llm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--llm-provider",
        default=argparse.SUPPRESS,
        help="Optional model-assisted Frontier Report provider label. Defaults to openai-compatible when base URL, key, or model is provided.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=argparse.SUPPRESS,
        help="OpenAI-compatible base URL for the Frontier Report model call.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=argparse.SUPPRESS,
        help="API key for the Frontier Report model call. Prefer config or environment variables for local use.",
    )
    parser.add_argument(
        "--llm-model",
        default=argparse.SUPPRESS,
        help="Model id for the Frontier Report model call.",
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
        default=argparse.SUPPRESS,
        help=(
            "Advanced compatibility override for the profile basis. Public choices are baseline, "
            "zotero_export, and live_zotero_db. If omitted, the app resolves the default from "
            "default_zotero_db_path, default_zotero_export_path, and the reusable export snapshot. "
            "Legacy alias zotero remains accepted for compatibility and maps to zotero_export."
        ),
    )
    parser.add_argument(
        "--zotero-db-path",
        type=Path,
        default=argparse.SUPPRESS,
        help=(
            "Advanced override for a live Zotero SQLite path. The normal UI path is to set "
            "default_zotero_db_path in configs/user_defaults.json."
        ),
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
        fetch_scope = FETCH_SCOPE_RANGE_FULL
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


def _resolve_llm_provider(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    del loaded_defaults
    if hasattr(args, "llm_provider"):
        return ResolvedSetting(value=args.llm_provider, source="cli")
    env_value = os.environ.get(FRONTIER_COMPASS_LLM_PROVIDER_ENV, "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_llm_base_url(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    if hasattr(args, "llm_base_url"):
        return ResolvedSetting(value=args.llm_base_url, source="cli")
    if loaded_defaults.defaults.default_llm_base_url is not None:
        return ResolvedSetting(value=loaded_defaults.defaults.default_llm_base_url, source="config")
    env_value = os.environ.get(FRONTIER_COMPASS_LLM_BASE_URL_ENV, "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_llm_api_key(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    if hasattr(args, "llm_api_key"):
        return ResolvedSetting(value=args.llm_api_key, source="cli")
    if loaded_defaults.defaults.default_llm_api_key is not None:
        return ResolvedSetting(value=loaded_defaults.defaults.default_llm_api_key, source="config")
    env_value = os.environ.get(FRONTIER_COMPASS_LLM_API_KEY_ENV, "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_llm_model(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    if hasattr(args, "llm_model"):
        return ResolvedSetting(value=args.llm_model, source="cli")
    if loaded_defaults.defaults.default_llm_model is not None:
        return ResolvedSetting(value=loaded_defaults.defaults.default_llm_model, source="config")
    env_value = os.environ.get(FRONTIER_COMPASS_LLM_MODEL_ENV, "").strip()
    if env_value:
        return ResolvedSetting(value=env_value, source="environment")
    return ResolvedSetting(value=None, source="built-in")


def _resolve_zotero_export(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=_optional_attr(args, "zotero_export"),
        cli_provided=hasattr(args, "zotero_export"),
        config_value=loaded_defaults.defaults.default_zotero_export_path,
        config_is_set=loaded_defaults.defaults.default_zotero_export_path is not None,
        built_in_value=None,
    )


def _resolve_zotero_db_path(args: argparse.Namespace, loaded_defaults: LoadedUserDefaults) -> ResolvedSetting:
    return resolve_setting(
        cli_value=_optional_attr(args, "zotero_db_path"),
        cli_provided=hasattr(args, "zotero_db_path"),
        config_value=loaded_defaults.defaults.default_zotero_db_path,
        config_is_set=loaded_defaults.defaults.default_zotero_db_path is not None,
        built_in_value=None,
    )


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


def _resolve_profile_settings(
    args: argparse.Namespace,
    loaded_defaults: LoadedUserDefaults,
    *,
    zotero_export: ResolvedSetting | None = None,
    zotero_db: ResolvedSetting | None = None,
) -> tuple[ResolvedSetting, ResolvedSetting, ResolvedSetting]:
    explicit_export_path = _optional_attr(args, "zotero_export") if hasattr(args, "zotero_export") else None
    explicit_db_path = _optional_attr(args, "zotero_db_path") if hasattr(args, "zotero_db_path") else None
    selection = resolve_default_profile_selection(
        profile_source=_optional_attr(args, "profile_source") if hasattr(args, "profile_source") else None,
        explicit_zotero_export_path=explicit_export_path,
        explicit_zotero_db_path=explicit_db_path,
        default_zotero_export_path=loaded_defaults.defaults.default_zotero_export_path,
        default_zotero_db_path=loaded_defaults.defaults.default_zotero_db_path,
    )
    resolved_export = zotero_export
    resolved_db = zotero_db
    if resolved_export is None:
        resolved_export = _resolve_zotero_export(args, loaded_defaults)
    if resolved_db is None:
        resolved_db = _resolve_zotero_db_path(args, loaded_defaults)

    if hasattr(args, "profile_source"):
        profile_source = ResolvedSetting(value=selection.profile_source, source="cli")
    elif explicit_export_path is not None or explicit_db_path is not None:
        profile_source = ResolvedSetting(value=selection.profile_source, source="derived")
    elif selection.profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB and selection.zotero_db_path is not None:
        profile_source = ResolvedSetting(value=selection.profile_source, source="config")
    elif selection.profile_source == PROFILE_SOURCE_ZOTERO_EXPORT and selection.zotero_export_path is not None:
        profile_source_source = (
            "config"
            if (
                loaded_defaults.defaults.default_zotero_export_path is not None
                and selection.zotero_export_path == loaded_defaults.defaults.default_zotero_export_path
            )
            else "derived"
        )
        profile_source = ResolvedSetting(value=selection.profile_source, source=profile_source_source)
    else:
        profile_source = ResolvedSetting(value=selection.profile_source, source="built-in")

    export_source = "built-in"
    export_value = None
    if explicit_export_path is not None:
        export_source = "cli"
        export_value = Path(explicit_export_path)
    elif selection.zotero_export_path is not None:
        export_value = selection.zotero_export_path
        if (
            loaded_defaults.defaults.default_zotero_export_path is not None
            and selection.zotero_export_path == loaded_defaults.defaults.default_zotero_export_path
        ):
            export_source = "config"
        else:
            export_source = "derived"

    db_source = "built-in"
    db_value = None
    if explicit_db_path is not None:
        db_source = "cli"
        db_value = Path(explicit_db_path)
    elif selection.zotero_db_path is not None:
        db_value = selection.zotero_db_path
        if (
            loaded_defaults.defaults.default_zotero_db_path is not None
            and selection.zotero_db_path == loaded_defaults.defaults.default_zotero_db_path
        ):
            db_source = "config"
        else:
            db_source = "derived"

    return (
        profile_source,
        ResolvedSetting(value=export_value, source=export_source),
        ResolvedSetting(value=db_value, source=db_source),
    )


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
    llm_provider: ResolvedSetting,
    llm_base_url: ResolvedSetting,
    llm_model: ResolvedSetting,
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
    if (
        report_mode.value != DEFAULT_REPORT_MODE
        or llm_provider.source != "built-in"
        or llm_base_url.source != "built-in"
        or llm_model.source != "built-in"
    ):
        settings.extend(
            [
                ("LLM provider", llm_provider),
                ("LLM base URL", llm_base_url),
                ("LLM model", llm_model),
            ]
        )
    if dry_run_email.value or email_to.source != "built-in" or email_from.source != "built-in":
        settings.append(("Email to", email_to))
        settings.append(("Email from", email_from))
    return tuple(settings)


def _ui_settings(
    *,
    selected_source: ResolvedSetting,
    report_mode: ResolvedSetting,
    llm_provider: ResolvedSetting,
    llm_base_url: ResolvedSetting,
    llm_model: ResolvedSetting,
    max_results: ResolvedSetting,
    profile_source: ResolvedSetting,
    zotero_export: ResolvedSetting,
    zotero_db: ResolvedSetting,
    fetch_scope: ResolvedSetting,
    allow_stale_cache: ResolvedSetting,
) -> tuple[tuple[str, ResolvedSetting], ...]:
    settings = [
        ("Selected source", selected_source),
        ("Report mode", report_mode),
        ("Max results", max_results),
        ("Profile source", profile_source),
        ("Zotero export", zotero_export),
        ("Zotero DB", zotero_db),
        ("Fetch scope", fetch_scope),
        ("Allow stale cache fallback", allow_stale_cache),
    ]
    if (
        report_mode.value != DEFAULT_REPORT_MODE
        or llm_provider.source != "built-in"
        or llm_base_url.source != "built-in"
        or llm_model.source != "built-in"
    ):
        settings.extend(
            [
                ("LLM provider", llm_provider),
                ("LLM base URL", llm_base_url),
                ("LLM model", llm_model),
            ]
        )
    return tuple(settings)


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
    print(f"Current UI run: {_format_public_source_setting(session.digest.category)}")
    print("Tracks: Digest + Frontier Report")
    if session.digest.category != DEFAULT_REVIEWER_SOURCE:
        print(f"Advanced source id: {session.digest.category}")
    if session.digest.frontier_report is None:
        print("Frontier Report status: unavailable in this legacy cache.")
    print(f"Frontier Report present: {'yes' if session.digest.frontier_report is not None else 'no'}")
    print(f"Requested report mode: {session.digest.requested_report_mode}")
    print(f"Frontier Report mode: {session.digest.report_mode}")
    print(f"Report status: {session.digest.report_status}")
    if session.digest.report_error:
        print(f"Report note: {session.digest.report_error}")
    print(f"Cost mode: {session.digest.cost_mode}")
    print(f"LLM requested: {format_llm_bool(session.digest.llm_requested)}")
    print(f"LLM applied: {format_llm_bool(session.digest.llm_applied)}")
    print(f"LLM provider: {format_llm_provider(session.digest.llm_provider)}")
    print(f"LLM fallback reason: {session.digest.llm_fallback_reason or 'none'}")
    print(f"LLM time: {format_llm_seconds(session.digest.llm_seconds)}")
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
    print("Tracks: Digest + Frontier Report")
    if digest.frontier_report is None:
        print("Frontier Report status: unavailable in this legacy cache.")
    print(f"Frontier Report present: {'yes' if digest.frontier_report is not None else 'no'}")
    print(f"Requested report mode: {digest.requested_report_mode}")
    print(f"Frontier Report mode: {digest.report_mode}")
    print(f"Report status: {digest.report_status}")
    if digest.report_error:
        print(f"Report note: {digest.report_error}")
    print(f"Cost mode: {digest.cost_mode}")
    print(f"LLM requested: {format_llm_bool(digest.llm_requested)}")
    print(f"LLM applied: {format_llm_bool(digest.llm_applied)}")
    print(f"LLM provider: {format_llm_provider(digest.llm_provider)}")
    print(f"LLM fallback reason: {digest.llm_fallback_reason or 'none'}")
    print(f"LLM time: {format_llm_seconds(digest.llm_seconds)}")
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
    print(f"Source run: {_format_public_source_setting(digest.category)}")
    if digest.category != DEFAULT_REVIEWER_SOURCE:
        print(f"Advanced source id: {digest.category}")
        print(f"Advanced source label: {digest.mode_label or digest.category}")
        print(f"Advanced source kind: {digest.mode_kind or 'n/a'}")
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
        rendered_label = "Source path" if label == "Selected source" else label
        rendered_value = (
            _format_public_source_setting(resolved.value)
            if label == "Selected source"
            else _format_setting_value(resolved.value)
        )
        print(f"{rendered_label}: {rendered_value} ({resolved.source})")
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


def _format_public_source_setting(value: object) -> str:
    normalized = str(value or "")
    labels = {
        SOURCE_BUNDLE_BIOMEDICAL: "default public bundle (arXiv + bioRxiv)",
        BIOMEDICAL_MULTISOURCE_MODE: "compatibility 3-source run (arXiv + bioRxiv + medRxiv)",
        BIOMEDICAL_LATEST_MODE: "legacy latest-available biomedical mode",
        BIOMEDICAL_DISCOVERY_MODE: "advanced biomedical discovery mode",
        BIOMEDICAL_DAILY_MODE: "advanced q-bio bundle mode",
        "ai-for-medicine": "advanced AI for medicine bundle override",
    }
    return labels.get(normalized, normalized)


def _profile_source_resolution_note(
    *,
    profile_source: ResolvedSetting | None,
    zotero_export: ResolvedSetting | None,
    zotero_db: ResolvedSetting | None,
) -> str:
    if profile_source is None or profile_source.source != "derived":
        return ""
    if profile_source.value == PROFILE_SOURCE_LIVE_ZOTERO_DB:
        return "Profile source auto-selected: live_zotero_db because a Zotero DB path was supplied."
    if profile_source.value == PROFILE_SOURCE_ZOTERO_EXPORT:
        return "Profile source auto-selected: zotero_export because a Zotero export was supplied."
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
