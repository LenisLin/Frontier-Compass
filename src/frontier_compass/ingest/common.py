"""Shared utilities for feed-based ingestors."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from time import sleep
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from frontier_compass.storage.schema import PaperRecord
from frontier_compass.common.text_normalization import slugify


_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
_RSS_ITEM = "{http://purl.org/rss/1.0/}item"
_RSS_TITLE = "{http://purl.org/rss/1.0/}title"
_RSS_DESCRIPTION = "{http://purl.org/rss/1.0/}description"
_RSS_LINK = "{http://purl.org/rss/1.0/}link"
_RSS_GUID = "{http://purl.org/rss/1.0/}guid"
_RSS_CATEGORY = "{http://purl.org/rss/1.0/}category"
DEFAULT_FEED_USER_AGENT = "FrontierCompass/0.1 (preprint scouting CLI)"
_T = TypeVar("_T")


class FeedRequestError(RuntimeError):
    """Raised when a bounded RSS request still fails."""


@dataclass(slots=True, frozen=True)
class FeedFetchDetails:
    papers: tuple[PaperRecord, ...]
    network_seconds: float
    parse_seconds: float
    endpoint: str
    contract_mode: str = "rss"
    note: str = ""
    available_dates: tuple[date, ...] = ()


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


def _xml_text_any(node: ET.Element, *tags: str) -> str:
    for tag in tags:
        text = xml_text(node, tag)
        if text:
            return text
    return ""



def parse_rss_feed(
    xml_text_value: str,
    *,
    source: str,
    subject: str | None = None,
    feed_url: str | None = None,
) -> list[PaperRecord]:
    root = ET.fromstring(xml_text_value)
    items = root.findall(".//item")
    if not items:
        items = root.findall(f".//{_RSS_ITEM}")
    papers: list[PaperRecord] = []

    for item in items:
        title = clean_text(_xml_text_any(item, "title", _RSS_TITLE))
        summary = clean_text(_xml_text_any(item, "description", _RSS_DESCRIPTION))
        url = _xml_text_any(item, "link", _RSS_LINK)
        guid = _xml_text_any(item, "guid", _RSS_GUID) or url
        published = parse_date(
            _xml_text_any(item, "pubDate")
            or clean_text(item.findtext(_DC_DATE, default=""))
        )
        categories = tuple(
            category
            for category in (
                clean_text(category.text or "")
                for category in (*item.findall("category"), *item.findall(_RSS_CATEGORY))
            )
            if category
        )
        authors = tuple(
            author
            for author in (clean_text(author.text or "") for author in item.findall(_DC_CREATOR))
            if author
        )
        if not authors:
            fallback = clean_text(_xml_text_any(item, "author"))
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


def parse_recent_listing(
    html_text: str,
    *,
    source: str,
    subject: str | None = None,
    listing_url: str | None = None,
) -> list[PaperRecord]:
    parser = _RecentListingParser(
        source=source,
        subject=subject,
        listing_url=listing_url or "",
    )
    parser.feed(html_text)
    parser.close()
    return parser.papers


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


def build_recent_listing_source_metadata(
    *,
    native_identifier: str,
    native_url: str,
    listing_url: str,
    subject: str | None = None,
    listing_date: date | None = None,
    doi_url: str = "",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "native_identifier": native_identifier,
        "native_url": native_url,
        "feed_kind": "recent-html",
        "listing_url": listing_url,
    }
    if subject:
        metadata["subject"] = subject
    if listing_date is not None:
        metadata["listing_date"] = listing_date.isoformat()
    if doi_url:
        metadata["doi_url"] = doi_url
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


class _RecentListingParser(HTMLParser):
    def __init__(
        self,
        *,
        source: str,
        subject: str | None,
        listing_url: str,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._source = source
        self._subject = subject or ""
        self._listing_url = listing_url
        self._current_listing_date: date | None = None
        self._heading_buffer: list[str] = []
        self._heading_depth = 0
        self._current_entry: dict[str, Any] | None = None
        self._entry_depth = 0
        self._title_buffer: list[str] | None = None
        self._title_depth = 0
        self._author_buffer: list[str] | None = None
        self._author_depth = 0
        self._pages_buffer: list[str] | None = None
        self._pages_depth = 0
        self._doi_buffer: list[str] | None = None
        self._doi_depth = 0
        self.papers: list[PaperRecord] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        class_names = _class_tokens(attr_map.get("class", ""))

        if self._heading_depth:
            self._heading_depth += 1
        if self._current_entry is not None:
            self._entry_depth += 1
            self._bump_capture_depths()

        if tag == "h3" and "highwire-list-title" in class_names:
            self._heading_buffer = []
            self._heading_depth = 1
            return

        if self._current_entry is None and tag == "div" and "highwire-article-citation" in class_names:
            self._current_entry = {
                "url": "",
                "title": "",
                "authors": [],
                "pages": "",
                "doi": "",
                "data_pisa": attr_map.get("data-pisa", ""),
                "data_pisa_master": attr_map.get("data-pisa-master", ""),
                "published": self._current_listing_date,
            }
            self._entry_depth = 1
            return

        if self._current_entry is None:
            return

        if tag == "a" and "highwire-cite-linked-title" in class_names:
            self._current_entry["url"] = urljoin(self._listing_url, attr_map.get("href", ""))
            self._title_buffer = []
            self._title_depth = 1
            return

        if tag == "span" and "highwire-citation-author" in class_names:
            self._author_buffer = []
            self._author_depth = 1
            return

        if tag == "span" and "highwire-cite-metadata-pages" in class_names:
            self._pages_buffer = []
            self._pages_depth = 1
            return

        if tag == "span" and "highwire-cite-metadata-doi" in class_names:
            self._doi_buffer = []
            self._doi_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                self._current_listing_date = _parse_recent_listing_date("".join(self._heading_buffer))

        self._close_capture(tag)

        if self._current_entry is not None:
            self._entry_depth -= 1
            if self._entry_depth == 0:
                self._finalize_entry()

    def handle_data(self, data: str) -> None:
        if self._heading_depth:
            self._heading_buffer.append(data)
        if self._title_depth and self._title_buffer is not None:
            self._title_buffer.append(data)
        if self._author_depth and self._author_buffer is not None:
            self._author_buffer.append(data)
        if self._pages_depth and self._pages_buffer is not None:
            self._pages_buffer.append(data)
        if self._doi_depth and self._doi_buffer is not None:
            self._doi_buffer.append(data)

    def _bump_capture_depths(self) -> None:
        if self._title_depth:
            self._title_depth += 1
        if self._author_depth:
            self._author_depth += 1
        if self._pages_depth:
            self._pages_depth += 1
        if self._doi_depth:
            self._doi_depth += 1

    def _close_capture(self, tag: str) -> None:
        del tag
        if self._title_depth:
            self._title_depth -= 1
            if self._title_depth == 0 and self._title_buffer is not None and self._current_entry is not None:
                self._current_entry["title"] = clean_text("".join(self._title_buffer))
                self._title_buffer = None
        if self._author_depth:
            self._author_depth -= 1
            if self._author_depth == 0 and self._author_buffer is not None and self._current_entry is not None:
                author = clean_text("".join(self._author_buffer))
                if author:
                    self._current_entry["authors"].append(author)
                self._author_buffer = None
        if self._pages_depth:
            self._pages_depth -= 1
            if self._pages_depth == 0 and self._pages_buffer is not None and self._current_entry is not None:
                self._current_entry["pages"] = clean_text("".join(self._pages_buffer)).rstrip(";")
                self._pages_buffer = None
        if self._doi_depth:
            self._doi_depth -= 1
            if self._doi_depth == 0 and self._doi_buffer is not None and self._current_entry is not None:
                self._current_entry["doi"] = clean_text("".join(self._doi_buffer)).removeprefix("doi:").strip()
                self._doi_buffer = None

    def _finalize_entry(self) -> None:
        assert self._current_entry is not None
        url = str(self._current_entry.get("url", "")).strip()
        title = clean_text(str(self._current_entry.get("title", "")))
        published = self._current_entry.get("published")
        native_identifier = source_native_identifier(url)
        if not native_identifier:
            fallback_identifier = str(self._current_entry.get("data_pisa_master") or self._current_entry.get("data_pisa") or "")
            native_identifier = fallback_identifier.split(";", 1)[-1].strip()
        if not native_identifier:
            native_identifier = str(self._current_entry.get("pages", "")).strip()
        doi_url = str(self._current_entry.get("doi", "")).strip()
        source_metadata = build_recent_listing_source_metadata(
            native_identifier=native_identifier,
            native_url=url,
            listing_url=self._listing_url,
            subject=self._subject,
            listing_date=published if isinstance(published, date) else None,
            doi_url=doi_url,
        )
        self.papers.append(
            PaperRecord(
                source=self._source,
                identifier=native_identifier or slugify(title) or "unknown-paper",
                title=title or "Untitled paper",
                summary="",
                authors=tuple(dict.fromkeys(self._current_entry.get("authors", ()))),
                categories=(),
                published=published if isinstance(published, date) else None,
                url=url,
                source_metadata=source_metadata,
            )
        )
        self._current_entry = None
        self._entry_depth = 0


def _class_tokens(value: str) -> set[str]:
    return {token for token in str(value or "").split() if token}


def _parse_recent_listing_date(value: str) -> date | None:
    normalized = clean_text(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%B %d, %Y").date()
    except ValueError:
        return None
