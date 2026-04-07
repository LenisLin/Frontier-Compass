"""Model-assisted Frontier Report helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from json import JSONDecoder
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from frontier_compass.storage.schema import DailyFrontierReport, FrontierReportHighlight


FRONTIER_COMPASS_LLM_PROVIDER_ENV = "FRONTIER_COMPASS_LLM_PROVIDER"
FRONTIER_COMPASS_LLM_BASE_URL_ENV = "FRONTIER_COMPASS_LLM_BASE_URL"
FRONTIER_COMPASS_LLM_API_KEY_ENV = "FRONTIER_COMPASS_LLM_API_KEY"
FRONTIER_COMPASS_LLM_MODEL_ENV = "FRONTIER_COMPASS_LLM_MODEL"

DEFAULT_FRONTIER_REPORT_PROVIDER = "openai-compatible"
DEFAULT_FRONTIER_REPORT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TAKEAWAYS = 4

SUPPORTED_FRONTIER_REPORT_PROVIDERS = frozenset(
    {
        DEFAULT_FRONTIER_REPORT_PROVIDER,
        "openai",
    }
)


class FrontierReportLLMError(RuntimeError):
    """Raised when model-assisted Frontier Report generation fails."""


class FrontierReportLLMConfigurationError(FrontierReportLLMError):
    """Raised when model-assisted Frontier Report settings are incomplete."""


@dataclass(slots=True, frozen=True)
class FrontierReportLLMSettings:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: float = DEFAULT_FRONTIER_REPORT_TIMEOUT_SECONDS

    @property
    def provider_label(self) -> str | None:
        return self.provider or (
            DEFAULT_FRONTIER_REPORT_PROVIDER
            if any((self.base_url, self.api_key, self.model))
            else None
        )

    @property
    def configured(self) -> bool:
        return bool(self.provider_label and self.base_url and self.api_key and self.model)


@dataclass(slots=True, frozen=True)
class FrontierReportLLMResult:
    report: DailyFrontierReport
    enhanced_item_count: int


def resolve_frontier_report_llm_settings(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    env: Mapping[str, str] | None = None,
) -> FrontierReportLLMSettings:
    resolved_env = os.environ if env is None else env
    resolved_provider = _normalize_optional_text(provider) or _normalize_optional_text(
        resolved_env.get(FRONTIER_COMPASS_LLM_PROVIDER_ENV)
    )
    resolved_base_url = _normalize_optional_text(base_url) or _normalize_optional_text(
        resolved_env.get(FRONTIER_COMPASS_LLM_BASE_URL_ENV)
    )
    resolved_api_key = _normalize_optional_text(api_key) or _normalize_optional_text(
        resolved_env.get(FRONTIER_COMPASS_LLM_API_KEY_ENV)
    )
    resolved_model = _normalize_optional_text(model) or _normalize_optional_text(
        resolved_env.get(FRONTIER_COMPASS_LLM_MODEL_ENV)
    )
    if resolved_provider is None and any((resolved_base_url, resolved_api_key, resolved_model)):
        resolved_provider = DEFAULT_FRONTIER_REPORT_PROVIDER
    return FrontierReportLLMSettings(
        provider=resolved_provider,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        model=resolved_model,
    )


def frontier_report_llm_unavailable_reason(settings: FrontierReportLLMSettings) -> str:
    provider = settings.provider_label
    if provider is None:
        return "No model-assisted provider is configured for this run."

    missing_bits: list[str] = []
    if not settings.base_url:
        missing_bits.append("base URL")
    if not settings.api_key:
        missing_bits.append("API key")
    if not settings.model:
        missing_bits.append("model")
    if missing_bits:
        joined = ", ".join(missing_bits[:-1]) + (" and " if len(missing_bits) > 1 else "") + missing_bits[-1]
        return f"Model-assisted provider {provider} is configured, but the {joined} is missing for this run."

    if provider not in SUPPORTED_FRONTIER_REPORT_PROVIDERS:
        return f"Model-assisted provider {provider} is not supported by this build."

    return f"Model-assisted provider {provider} is configured, but the request could not be prepared for this run."


def build_model_assisted_frontier_report(
    frontier_report: DailyFrontierReport,
    *,
    settings: FrontierReportLLMSettings,
) -> FrontierReportLLMResult:
    if not settings.configured:
        raise FrontierReportLLMConfigurationError(frontier_report_llm_unavailable_reason(settings))

    provider = settings.provider_label
    if provider not in SUPPORTED_FRONTIER_REPORT_PROVIDERS:
        raise FrontierReportLLMConfigurationError(frontier_report_llm_unavailable_reason(settings))

    request_payload = {
        "model": settings.model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write concise field-level Frontier Report summaries for daily biomedical literature "
                    "scouting. Always return valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": _build_frontier_report_prompt(frontier_report),
            },
        ],
    }
    raw_response = _openai_compatible_chat_completion(
        settings=settings,
        payload=request_payload,
    )
    parsed_response = _parse_frontier_report_response(raw_response)
    takeaways = _normalize_takeaways(parsed_response.get("takeaways"))
    highlight_updates = _normalize_highlight_updates(parsed_response.get("field_highlights"))

    rewritten_highlights: list[FrontierReportHighlight] = []
    changed_highlights = 0
    for item in frontier_report.field_highlights:
        rewritten_why = highlight_updates.get(item.identifier)
        if rewritten_why and rewritten_why != item.why:
            rewritten_highlights.append(replace(item, why=rewritten_why))
            changed_highlights += 1
        else:
            rewritten_highlights.append(item)

    if not takeaways and changed_highlights == 0:
        raise FrontierReportLLMError("Model-assisted Frontier Report response did not include usable content.")

    resolved_takeaways = takeaways or frontier_report.takeaways
    enhanced_item_count = changed_highlights + len(resolved_takeaways)
    return FrontierReportLLMResult(
        report=replace(
            frontier_report,
            takeaways=resolved_takeaways,
            field_highlights=tuple(rewritten_highlights),
        ),
        enhanced_item_count=enhanced_item_count,
    )


def _build_frontier_report_prompt(frontier_report: DailyFrontierReport) -> str:
    payload = {
        "task": (
            "Read the field summary and highlight abstracts. Return JSON with keys "
            "`takeaways` and `field_highlights`. `takeaways` must be a list of 2-4 concise strings. "
            "`field_highlights` must be a list of objects with `identifier` and `why`. "
            "Do not invent new identifiers."
        ),
        "frontier_report": {
            "requested_date": frontier_report.requested_date.isoformat(),
            "effective_date": frontier_report.effective_date.isoformat(),
            "source": frontier_report.source,
            "mode": frontier_report.mode_label or frontier_report.mode,
            "searched_categories": list(frontier_report.searched_categories),
            "repeated_themes": [
                {"label": item.label, "count": item.count}
                for item in frontier_report.repeated_themes
            ],
            "salient_topics": [
                {"label": item.label, "count": item.count}
                for item in frontier_report.salient_topics
            ],
            "adjacent_themes": [
                {"label": item.label, "count": item.count}
                for item in frontier_report.adjacent_themes
            ],
            "highlights": [
                {
                    "identifier": item.identifier,
                    "source": item.source,
                    "title": item.title,
                    "theme_label": item.theme_label,
                    "summary": item.summary,
                    "categories": list(item.categories),
                    "published": item.published.isoformat() if item.published is not None else None,
                }
                for item in frontier_report.field_highlights
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def _openai_compatible_chat_completion(
    *,
    settings: FrontierReportLLMSettings,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    endpoint = _chat_completions_endpoint(str(settings.base_url))
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise FrontierReportLLMError(
            f"Model-assisted Frontier Report request failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except URLError as exc:
        raise FrontierReportLLMError(f"Model-assisted Frontier Report request failed: {exc.reason}") from exc
    except OSError as exc:
        raise FrontierReportLLMError(f"Model-assisted Frontier Report request failed: {exc}") from exc

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise FrontierReportLLMError("Model-assisted Frontier Report response was not valid JSON.") from exc
    if not isinstance(parsed, Mapping):
        raise FrontierReportLLMError("Model-assisted Frontier Report response must be a JSON object.")
    return parsed


def _chat_completions_endpoint(base_url: str) -> str:
    stripped = str(base_url).rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped.endswith("/v1"):
        return f"{stripped}/chat/completions"
    return f"{stripped}/chat/completions"


def _parse_frontier_report_response(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, Sequence) or not choices:
        raise FrontierReportLLMError("Model-assisted Frontier Report response did not include any choices.")
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise FrontierReportLLMError("Model-assisted Frontier Report response choice payload was invalid.")
    message = first_choice.get("message")
    if not isinstance(message, Mapping):
        raise FrontierReportLLMError("Model-assisted Frontier Report response did not include a message.")
    content = _message_content_text(message.get("content"))
    if not content:
        raise FrontierReportLLMError("Model-assisted Frontier Report response content was empty.")
    parsed = _extract_json_object(content)
    if not isinstance(parsed, Mapping):
        raise FrontierReportLLMError("Model-assisted Frontier Report response content must be a JSON object.")
    return parsed


def _message_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                chunks.append(item.strip())
                continue
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        return "\n".join(chunks).strip()
    return ""


def _extract_json_object(content: str) -> Any:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    decoder = JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    raise FrontierReportLLMError("Model-assisted Frontier Report response did not contain a valid JSON object.")


def _normalize_takeaways(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    takeaways: list[str] = []
    for item in value:
        normalized = _normalize_optional_text(item)
        if normalized:
            takeaways.append(normalized)
        if len(takeaways) >= DEFAULT_MAX_TAKEAWAYS:
            break
    return tuple(takeaways)


def _normalize_highlight_updates(value: Any) -> dict[str, str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return {}
    updates: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        identifier = _normalize_optional_text(item.get("identifier"))
        why = _normalize_optional_text(item.get("why"))
        if identifier and why:
            updates[identifier] = why
    return updates


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    candidate = " ".join(str(value).split()).strip()
    return candidate or None
