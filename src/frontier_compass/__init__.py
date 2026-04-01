"""Frozen package-root API for the supported FrontierCompass local workflow.

This module intentionally mirrors :mod:`frontier_compass.api` so callers can
choose either the shortest package-root imports or the explicit module path
without changing the supported public surface.
"""

from frontier_compass.api import (
    DailyRunResult,
    FrontierCompassRunner,
    LocalUISession,
    load_recent_history,
    prepare_ui_session,
    run_daily,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "FrontierCompassRunner",
    "DailyRunResult",
    "LocalUISession",
    "run_daily",
    "prepare_ui_session",
    "load_recent_history",
]
