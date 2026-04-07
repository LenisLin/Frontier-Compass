"""Helpers for explicit FrontierCompass report runtime and cost modes."""

from __future__ import annotations


DEFAULT_REPORT_MODE = "deterministic"
ENHANCED_REPORT_MODE = "enhanced"
REPORT_MODE_CHOICES = (DEFAULT_REPORT_MODE, ENHANCED_REPORT_MODE)

ZERO_TOKEN_COST_MODE = "zero-token"
MODEL_ASSISTED_COST_MODE = "model-assisted"

ENHANCED_TRACK_FRONTIER_REPORT = "frontier-report"
ENHANCED_NOT_CONFIGURED_FALLBACK_REASON = "No model-assisted provider is configured for this run."
ENHANCED_PROVIDER_DISABLED_FALLBACK_REASON = (
    "A model-assisted provider is configured, but the Frontier Report run stayed deterministic."
)

ZERO_TOKEN_RUNTIME_NOTE = (
    "Zero-token run: fetching, ranking, recommendation summaries, exploration picks, and the current "
    "Frontier Report all use deterministic local logic only."
)
ENHANCED_NOT_CONFIGURED_NOTE = (
    "Enhanced Frontier Report mode was requested, but no model-assisted Frontier Report is configured for "
    "this run. The run stayed deterministic and zero-token."
)
ENHANCED_PROVIDER_DISABLED_NOTE = (
    "Enhanced Frontier Report mode was requested with provider {provider}, but the Frontier Report run "
    "stayed deterministic and zero-token."
)
MODEL_ASSISTED_RUNTIME_NOTE = (
    "Enhanced Frontier Report mode was applied with provider {provider}."
)


def normalize_report_mode(value: str | None) -> str:
    candidate = (value or DEFAULT_REPORT_MODE).strip().lower()
    if candidate in REPORT_MODE_CHOICES:
        return candidate
    raise ValueError(
        f"Unsupported report mode: {value!r}. Expected one of: {', '.join(REPORT_MODE_CHOICES)}"
    )


def _safe_normalize_report_mode(value: str | None) -> str:
    try:
        return normalize_report_mode(value)
    except ValueError:
        return DEFAULT_REPORT_MODE


def _normalize_llm_provider(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def backfill_llm_provenance(
    *,
    requested_report_mode: str | None,
    report_mode: str | None = None,
    cost_mode: str | None = None,
    llm_requested: bool | None = None,
    llm_applied: bool | None = None,
    llm_provider: str | None = None,
    llm_fallback_reason: str | None = None,
    llm_seconds: float | None = None,
) -> dict[str, object]:
    normalized_requested_mode = _safe_normalize_report_mode(requested_report_mode)
    normalized_applied_mode = _safe_normalize_report_mode(report_mode)
    normalized_cost_mode = (cost_mode or ZERO_TOKEN_COST_MODE).strip().lower() or ZERO_TOKEN_COST_MODE
    resolved_provider = _normalize_llm_provider(llm_provider)
    resolved_requested = (
        bool(llm_requested)
        if llm_requested is not None
        else normalized_requested_mode == ENHANCED_REPORT_MODE
    )
    resolved_applied = (
        bool(llm_applied)
        if llm_applied is not None
        else (
            normalized_applied_mode == ENHANCED_REPORT_MODE
            or normalized_cost_mode == MODEL_ASSISTED_COST_MODE
        )
    )
    resolved_fallback_reason = str(llm_fallback_reason or "").strip() or None
    if resolved_fallback_reason is None and resolved_requested and not resolved_applied:
        resolved_fallback_reason = (
            ENHANCED_PROVIDER_DISABLED_FALLBACK_REASON
            if resolved_provider
            else ENHANCED_NOT_CONFIGURED_FALLBACK_REASON
        )
    return {
        "llm_requested": resolved_requested,
        "llm_applied": resolved_applied,
        "llm_provider": resolved_provider,
        "llm_fallback_reason": resolved_fallback_reason,
        "llm_seconds": llm_seconds,
    }


def build_report_runtime_contract(
    requested_mode: str | None,
    *,
    llm_provider: str | None = None,
    llm_applied: bool = False,
    llm_seconds: float | None = None,
    llm_fallback_reason: str | None = None,
    enhanced_item_count: int = 0,
) -> dict[str, object]:
    normalized_mode = normalize_report_mode(requested_mode)
    if normalized_mode == ENHANCED_REPORT_MODE:
        resolved_provider = _normalize_llm_provider(llm_provider)
        resolved_fallback_reason = str(llm_fallback_reason or "").strip() or None
        resolved_report_mode = ENHANCED_REPORT_MODE if llm_applied else DEFAULT_REPORT_MODE
        resolved_cost_mode = MODEL_ASSISTED_COST_MODE if llm_applied else ZERO_TOKEN_COST_MODE
        resolved_track = ENHANCED_TRACK_FRONTIER_REPORT if llm_applied else ""
        runtime_note = (
            MODEL_ASSISTED_RUNTIME_NOTE.format(provider=resolved_provider or "configured-llm")
            if llm_applied
            else (
                ENHANCED_PROVIDER_DISABLED_NOTE.format(provider=resolved_provider)
                if resolved_provider
                else ENHANCED_NOT_CONFIGURED_NOTE
            )
        )
        return {
            "requested_report_mode": ENHANCED_REPORT_MODE,
            "report_mode": resolved_report_mode,
            "cost_mode": resolved_cost_mode,
            "enhanced_track": resolved_track,
            "enhanced_item_count": max(int(enhanced_item_count), 0) if llm_applied else 0,
            "runtime_note": runtime_note,
            **backfill_llm_provenance(
                requested_report_mode=ENHANCED_REPORT_MODE,
                report_mode=resolved_report_mode,
                cost_mode=resolved_cost_mode,
                llm_requested=True,
                llm_applied=llm_applied,
                llm_provider=resolved_provider,
                llm_fallback_reason=resolved_fallback_reason,
                llm_seconds=llm_seconds,
            ),
        }
    return {
        "requested_report_mode": DEFAULT_REPORT_MODE,
        "report_mode": DEFAULT_REPORT_MODE,
        "cost_mode": ZERO_TOKEN_COST_MODE,
        "enhanced_track": "",
        "enhanced_item_count": 0,
        "runtime_note": ZERO_TOKEN_RUNTIME_NOTE,
        **backfill_llm_provenance(
            requested_report_mode=DEFAULT_REPORT_MODE,
            report_mode=DEFAULT_REPORT_MODE,
            cost_mode=ZERO_TOKEN_COST_MODE,
            llm_requested=False,
            llm_applied=False,
        ),
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


def format_llm_bool(value: bool) -> str:
    return "yes" if value else "no"


def format_llm_provider(provider: str | None) -> str:
    return _normalize_llm_provider(provider) or "none"


def format_llm_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds:.2f}s"


def format_llm_summary(
    *,
    llm_requested: bool,
    llm_applied: bool,
    llm_provider: str | None,
) -> str:
    return (
        f"requested {format_llm_bool(llm_requested)} / "
        f"applied {format_llm_bool(llm_applied)} / "
        f"provider {format_llm_provider(llm_provider)}"
    )
