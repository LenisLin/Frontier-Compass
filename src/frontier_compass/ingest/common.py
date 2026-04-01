"""Shared utilities for feed-based ingestors."""

from __future__ import annotations

import re
import socket
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from time import sleep
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from frontier_compass.storage.schema import PaperRecord
from frontier_compass.common.text_normalization import slugify


_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
DEFAULT_FEED_USER_AGENT = "FrontierCompass/0.1 (preprint scouting CLI)"
_T = TypeVar("_T")


class FeedRequestError(RuntimeError):
    """Raised when a bounded RSS request still fails."""


def measure_operation(operation: Callable[[], _T]) -> tuple[_T, float]:
    started = perf_counter()
    result = operation()
    return result, max(perf_counter() - started, 0.0)



def fetch_text(
    url: str,
    *,
    timeout: int = 30,
    max_attempts: int = 2,
    retry_delay_seconds: float = 1.0,
    rate_limit_delay_seconds: float = 2.0,
    user_agent: str = DEFAULT_FEED_USER_AGENT,
    source_label: str = "feed",
) -> str:
    normalized_source = clean_text(source_label) or "feed"
    last_error: FeedRequestError | None = None
    for attempt in range(1, max(max_attempts, 1) + 1):
        request = Request(url, headers={"User-Agent": user_agent})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            retryable = exc.code == 429
            last_error = FeedRequestError(_http_error_message(exc, source_label=normalized_source))
            if retryable and attempt < max(max_attempts, 1):
                sleep(rate_limit_delay_seconds)
                continue
            raise last_error from exc
        except URLError as exc:
            retryable, message = _classify_url_error(exc, source_label=normalized_source)
            last_error = FeedRequestError(message)
            if retryable and attempt < max(max_attempts, 1):
                sleep(retry_delay_seconds)
                continue
            raise last_error from exc
        except (TimeoutError, socket.timeout) as exc:
            last_error = FeedRequestError(f"{normalized_source} request timed out")
            if attempt < max(max_attempts, 1):
                sleep(retry_delay_seconds)
                continue
            raise last_error from exc
    raise last_error or FeedRequestError(f"{normalized_source} request failed")



def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())



def parse_date(value: str | None) -> date | None:
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError, IndexError):
        return None



def last_path_token(value: str) -> str:
    trimmed = value.strip().rstrip("/")
    if not trimmed:
        return ""
    return trimmed.rsplit("/", 1)[-1]



def xml_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()



def parse_rss_feed(
    xml_text_value: str,
    *,
    source: str,
    subject: str | None = None,
    feed_url: str | None = None,
) -> list[PaperRecord]:
    root = ET.fromstring(xml_text_value)
    items = root.findall(".//item")
    papers: list[PaperRecord] = []

    for item in items:
        title = clean_text(xml_text(item, "title"))
        summary = clean_text(xml_text(item, "description"))
        url = xml_text(item, "link")
        guid = xml_text(item, "guid") or url
        published = parse_date(xml_text(item, "pubDate"))
        categories = tuple(
            category
            for category in (clean_text(category.text or "") for category in item.findall("category"))
            if category
        )
        authors = tuple(
            author
            for author in (clean_text(author.text or "") for author in item.findall(_DC_CREATOR))
            if author
        )
        if not authors:
            fallback = clean_text(xml_text(item, "author"))
            if fallback:
                authors = tuple(part.strip() for part in fallback.split(",") if part.strip())
        native_identifier = source_native_identifier(guid or url)
        source_metadata = build_rss_source_metadata(
            native_identifier=native_identifier,
            native_url=url,
            tags=categories,
            guid=guid,
            subject=subject,
            feed_url=feed_url,
        )

        papers.append(
            PaperRecord(
                source=source,
                identifier=native_identifier or last_path_token(guid) or slugify(title),
                title=title or "Untitled paper",
                summary=summary,
                authors=authors,
                categories=categories,
                published=published,
                url=url,
                source_metadata=source_metadata,
            )
        )

    return papers


def source_native_identifier(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    if "/content/" in trimmed:
        return trimmed.split("/content/", 1)[1].strip("/")
    if trimmed.startswith("doi:"):
        return trimmed.removeprefix("doi:").strip()
    if trimmed.startswith("http://") or trimmed.startswith("https://"):
        return last_path_token(trimmed)
    return trimmed


def build_rss_source_metadata(
    *,
    native_identifier: str,
    native_url: str,
    tags: tuple[str, ...],
    guid: str = "",
    subject: str | None = None,
    feed_url: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "native_identifier": native_identifier,
        "native_url": native_url,
        "tags": list(tags),
        "feed_kind": "rss",
    }
    if guid:
        metadata["guid"] = guid
    if subject:
        metadata["subject"] = subject
    if feed_url:
        metadata["feed_url"] = feed_url
    return {key: value for key, value in metadata.items() if value not in ("", [], None)}


def _http_error_message(exc: HTTPError, *, source_label: str) -> str:
    if exc.code == 429:
        return f"{source_label} request failed with HTTP 429 Too Many Requests"
    reason = clean_text(str(getattr(exc, "reason", "")))
    if reason:
        return f"{source_label} request failed with HTTP {exc.code} {reason}"
    return f"{source_label} request failed with HTTP {exc.code}"


def _classify_url_error(exc: URLError, *, source_label: str) -> tuple[bool, str]:
    reason = exc.reason
    reason_text = clean_text(str(reason or exc))
    if _is_timeout_reason(reason):
        return True, f"{source_label} request timed out"
    if _is_transient_url_reason(reason, reason_text):
        return True, f"{source_label} request failed: {reason_text or 'temporary network error'}"
    return False, f"{source_label} request failed: {reason_text or 'network error'}"


def _is_timeout_reason(reason: object) -> bool:
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in clean_text(str(reason)).lower()


def _is_transient_url_reason(reason: object, reason_text: str) -> bool:
    if isinstance(reason, PermissionError):
        return False
    normalized = reason_text.lower()
    transient_markers = ("temporary", "temporarily", "reset", "timed out", "unreachable", "refused")
    return any(marker in normalized for marker in transient_markers)
