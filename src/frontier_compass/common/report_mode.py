"""Helpers for explicit FrontierCompass report runtime and cost modes."""

from __future__ import annotations


DEFAULT_REPORT_MODE = "deterministic"
ENHANCED_REPORT_MODE = "enhanced"
REPORT_MODE_CHOICES = (DEFAULT_REPORT_MODE, ENHANCED_REPORT_MODE)

ZERO_TOKEN_COST_MODE = "zero-token"
MODEL_ASSISTED_COST_MODE = "model-assisted"

ENHANCED_TRACK_FRONTIER_REPORT = "frontier-report"

ZERO_TOKEN_RUNTIME_NOTE = (
    "Zero-token run: fetching, ranking, recommendation summaries, exploration picks, and the current "
    "Frontier Report all use deterministic local logic only."
)
ENHANCED_NOT_CONFIGURED_NOTE = (
    "Enhanced Frontier Report mode was requested, but no model-assisted Frontier Report is configured in "
    "this build. The run stayed deterministic and zero-token."
)


def normalize_report_mode(value: str | None) -> str:
    candidate = (value or DEFAULT_REPORT_MODE).strip().lower()
    if candidate in REPORT_MODE_CHOICES:
        return candidate
    raise ValueError(
        f"Unsupported report mode: {value!r}. Expected one of: {', '.join(REPORT_MODE_CHOICES)}"
    )


def build_report_runtime_contract(requested_mode: str | None) -> dict[str, object]:
    normalized_mode = normalize_report_mode(requested_mode)
    if normalized_mode == ENHANCED_REPORT_MODE:
        return {
            "requested_report_mode": ENHANCED_REPORT_MODE,
            "report_mode": DEFAULT_REPORT_MODE,
            "cost_mode": ZERO_TOKEN_COST_MODE,
            "enhanced_track": "",
            "enhanced_item_count": 0,
            "runtime_note": ENHANCED_NOT_CONFIGURED_NOTE,
        }
    return {
        "requested_report_mode": DEFAULT_REPORT_MODE,
        "report_mode": DEFAULT_REPORT_MODE,
        "cost_mode": ZERO_TOKEN_COST_MODE,
        "enhanced_track": "",
        "enhanced_item_count": 0,
        "runtime_note": ZERO_TOKEN_RUNTIME_NOTE,
    }


def format_report_mode_label(mode: str) -> str:
    normalized_mode = normalize_report_mode(mode)
    labels = {
        DEFAULT_REPORT_MODE: "Deterministic",
        ENHANCED_REPORT_MODE: "Enhanced",
    }
    return labels[normalized_mode]


def format_report_mode_option(mode: str) -> str:
    normalized_mode = normalize_report_mode(mode)
    labels = {
        DEFAULT_REPORT_MODE: "Deterministic (zero-token)",
        ENHANCED_REPORT_MODE: "Enhanced Frontier Report (opt-in)",
    }
    return labels[normalized_mode]


def format_cost_mode_label(cost_mode: str) -> str:
    normalized_cost_mode = (cost_mode or ZERO_TOKEN_COST_MODE).strip().lower()
    labels = {
        ZERO_TOKEN_COST_MODE: "Zero-token",
        MODEL_ASSISTED_COST_MODE: "Model-assisted",
    }
    return labels.get(normalized_cost_mode, cost_mode or ZERO_TOKEN_COST_MODE)


def format_runtime_status(report_mode: str, cost_mode: str) -> str:
    return f"{format_report_mode_label(report_mode)} / {format_cost_mode_label(cost_mode)}"
