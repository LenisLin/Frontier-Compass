"""Small helpers for optional local user defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from frontier_compass.common.report_mode import normalize_report_mode


DEFAULT_USER_DEFAULTS_PATH = Path("configs/user_defaults.json")
SUPPORTED_USER_DEFAULT_KEYS = frozenset(
    {
        "default_mode",
        "default_report_mode",
        "default_max_results",
        "default_zotero_db_path",
        "default_zotero_export_path",
        "default_email_to",
        "default_email_from",
        "default_generate_dry_run_email",
        "default_allow_stale_cache",
        "default_llm_base_url",
        "default_llm_api_key",
        "default_llm_model",
    }
)
SETTING_SOURCE_BUILT_IN = "built-in"
SETTING_SOURCE_CLI = "cli"
SETTING_SOURCE_CONFIG = "config"


@dataclass(slots=True, frozen=True)
class UserDefaults:
    default_mode: str | None = None
    default_report_mode: str | None = None
    default_max_results: int | None = None
    default_zotero_db_path: Path | None = None
    default_zotero_export_path: Path | None = None
    default_email_to: tuple[str, ...] = ()
    default_email_from: str | None = None
    default_generate_dry_run_email: bool | None = None
    default_allow_stale_cache: bool | None = None
    default_llm_base_url: str | None = None
    default_llm_api_key: str | None = None
    default_llm_model: str | None = None


@dataclass(slots=True, frozen=True)
class LoadedUserDefaults:
    path: Path
    defaults: UserDefaults
    loaded: bool
    disabled: bool = False


@dataclass(slots=True, frozen=True)
class ResolvedSetting:
    value: Any
    source: str


def load_user_defaults(
    *,
    config_path: str | Path | None = None,
    use_config: bool = True,
) -> LoadedUserDefaults:
    resolved_path = Path(config_path) if config_path is not None else DEFAULT_USER_DEFAULTS_PATH
    if not use_config:
        return LoadedUserDefaults(path=resolved_path, defaults=UserDefaults(), loaded=False, disabled=True)

    if not resolved_path.exists():
        if config_path is None:
            return LoadedUserDefaults(path=resolved_path, defaults=UserDefaults(), loaded=False)
        raise ValueError(f"User defaults config not found: {resolved_path}")

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read user defaults config {resolved_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in user defaults config {resolved_path}: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError(f"User defaults config {resolved_path} must contain a JSON object.")

    unknown_keys = sorted(set(payload) - SUPPORTED_USER_DEFAULT_KEYS)
    if unknown_keys:
        raise ValueError(
            f"Unsupported keys in user defaults config {resolved_path}: {', '.join(unknown_keys)}"
        )

    defaults = UserDefaults(
        default_mode=_parse_optional_text(payload, "default_mode"),
        default_report_mode=_parse_optional_report_mode(payload, "default_report_mode"),
        default_max_results=_parse_optional_positive_int(payload, "default_max_results"),
        default_zotero_db_path=_parse_optional_path(
            payload,
            "default_zotero_db_path",
            config_dir=resolved_path.parent,
        ),
        default_zotero_export_path=_parse_optional_path(
            payload,
            "default_zotero_export_path",
            config_dir=resolved_path.parent,
        ),
        default_email_to=_parse_optional_email_recipients(payload, "default_email_to"),
        default_email_from=_parse_optional_text(payload, "default_email_from"),
        default_generate_dry_run_email=_parse_optional_bool(payload, "default_generate_dry_run_email"),
        default_allow_stale_cache=_parse_optional_bool(payload, "default_allow_stale_cache"),
        default_llm_base_url=_parse_optional_text(payload, "default_llm_base_url"),
        default_llm_api_key=_parse_optional_text(payload, "default_llm_api_key"),
        default_llm_model=_parse_optional_text(payload, "default_llm_model"),
    )
    return LoadedUserDefaults(path=resolved_path, defaults=defaults, loaded=True)


def normalize_email_recipients(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Sequence):
        recipients: list[str] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, str):
                raise ValueError(f"Email recipient #{index} must be a string.")
            candidate = item.strip()
            if candidate:
                recipients.append(candidate)
        return tuple(recipients)
    raise ValueError("Email recipients must be a comma-delimited string or an array of strings.")


def resolve_setting(
    *,
    cli_value: Any,
    cli_provided: bool,
    config_value: Any,
    config_is_set: bool,
    built_in_value: Any,
) -> ResolvedSetting:
    if cli_provided:
        return ResolvedSetting(value=cli_value, source=SETTING_SOURCE_CLI)
    if config_is_set:
        return ResolvedSetting(value=config_value, source=SETTING_SOURCE_CONFIG)
    return ResolvedSetting(value=built_in_value, source=SETTING_SOURCE_BUILT_IN)


def _parse_optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    candidate = value.strip()
    return candidate or None


def _parse_optional_report_mode(payload: Mapping[str, Any], key: str) -> str | None:
    raw_value = _parse_optional_text(payload, key)
    if raw_value is None:
        return None
    return normalize_report_mode(raw_value)


def _parse_optional_positive_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a positive integer.")
    if value < 1:
        raise ValueError(f"{key} must be a positive integer.")
    return value


def _parse_optional_bool(payload: Mapping[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    return value


def _parse_optional_path(payload: Mapping[str, Any], key: str, *, config_dir: Path) -> Path | None:
    raw_value = _parse_optional_text(payload, key)
    if raw_value is None:
        return None
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    return candidate


def _parse_optional_email_recipients(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    return normalize_email_recipients(value)
