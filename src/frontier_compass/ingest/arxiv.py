"""arXiv ingestion via the public Atom API and daily RSS feed."""

from __future__ import annotations

import socket
from dataclasses import dataclass, replace
import re
from datetime import date
from time import sleep
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

from frontier_compass.common.text_normalization import slugify
from frontier_compass.ingest.common import clean_text, last_path_token, measure_operation, parse_date
from frontier_compass.storage.schema import PaperRecord, UserInterestProfile


_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_ABSTRACT_PREFIX_RE = re.compile(
    r"^arXiv:[^\s]+\s+Announce Type:\s+\w+\s+Abstract:\s*",
    re.IGNORECASE,
)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)

BIOMEDICAL_DAILY_CATEGORIES = (
    "q-bio",
    "q-bio.GN",
    "q-bio.QM",
    "q-bio.BM",
    "q-bio.CB",
    "q-bio.SC",
)
BIOMEDICAL_DISCOVERY_CATEGORIES = (
    *BIOMEDICAL_DAILY_CATEGORIES,
    "cs.LG",
    "cs.CV",
    "cs.AI",
    "cs.CL",
    "stat.ML",
    "eess.IV",
)
BIOMEDICAL_DISCOVERY_PROFILE_LABEL = "broader-biomedical-discovery-v1"
ZOTERO_RETRIEVAL_PROFILE_LABEL = "zotero-biomedical-augmentation-v1"
ARXIV_REQUEST_TIMEOUT_SECONDS = 15
ARXIV_REQUEST_MAX_ATTEMPTS = 2
ARXIV_REQUEST_USER_AGENT = "FrontierCompass/0.1 (arXiv scouting CLI)"
ARXIV_RETRY_DELAY_SECONDS = 1.0
ARXIV_RATE_LIMIT_DELAY_SECONDS = 2.0
ARXIV_BATCH_PACING_SECONDS = 0.5
BIOMEDICAL_DISCOVERY_KEYWORD_GROUPS = (
    (
        "omics-and-single-cell",
        (
            "bioinformatics",
            "genomics",
            "transcriptomics",
            "proteomics",
            "single-cell",
            "single cell",
            "spatial transcriptomics",
            "cell atlas",
            "multi-omics",
            "perturbation",
        ),
    ),
    (
        "biomedical-imaging-and-clinical-ai",
        (
            "biomedical",
            "medical",
            "clinical",
            "pathology",
            "histopathology",
            "radiology",
            "microscopy",
        ),
    ),
)


@dataclass(slots=True, frozen=True)
class ArxivQueryDefinition:
    label: str
    query: str
    origin: str = "baseline"
    terms: tuple[str, ...] = ()


class ArxivRequestError(RuntimeError):
    """Raised when a bounded arXiv request still fails."""


class ArxivClient:
    api_url = "https://export.arxiv.org/api/query"
    rss_url_template = "https://rss.arxiv.org/atom/{category}"

    def build_url(self, query: str, *, max_results: int = 25, start: int = 0) -> str:
        params = {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        return f"{self.api_url}?{urlencode(params)}"

    def build_feed_url(self, category: str = "cs") -> str:
        normalized_category = (category or "cs").strip()
        return self.rss_url_template.format(category=quote(normalized_category, safe="+.-"))

    def fetch_recent(self, query: str, *, max_results: int = 25) -> list[PaperRecord]:
        papers, _, _ = self.fetch_recent_with_timings(query, max_results=max_results)
        return papers

    def fetch_recent_with_timings(
        self,
        query: str,
        *,
        max_results: int = 25,
    ) -> tuple[list[PaperRecord], float, float]:
        xml_text, network_seconds = measure_operation(
            lambda: _fetch_arxiv_text(self.build_url(query, max_results=max_results))
        )
        papers, parse_seconds = measure_operation(lambda: self.parse_feed(xml_text))
        return papers, network_seconds, parse_seconds

    def fetch_feed(self, category: str = "cs", *, feed_url: str | None = None) -> list[PaperRecord]:
        papers, _, _ = self.fetch_feed_with_timings(category, feed_url=feed_url)
        return papers

    def fetch_feed_with_timings(
        self,
        category: str = "cs",
        *,
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        xml_text, network_seconds = measure_operation(
            lambda: _fetch_arxiv_text(feed_url or self.build_feed_url(category))
        )
        papers, parse_seconds = measure_operation(lambda: self.parse_feed(xml_text))
        return papers, network_seconds, parse_seconds

    def fetch_recent_by_category(
        self,
        categories: Sequence[str],
        *,
        max_results: int | None = None,
        feed_urls: Mapping[str, str] | None = None,
    ) -> dict[str, list[PaperRecord]]:
        category_papers, _, _ = self.fetch_recent_by_category_with_timings(
            categories,
            max_results=max_results,
            feed_urls=feed_urls,
        )
        return category_papers

    def fetch_recent_by_category_with_timings(
        self,
        categories: Sequence[str],
        *,
        max_results: int | None = None,
        feed_urls: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, list[PaperRecord]], float, float]:
        category_papers: dict[str, list[PaperRecord]] = {}
        total_network_seconds = 0.0
        total_parse_seconds = 0.0
        for index, category in enumerate(categories):
            if index > 0:
                _pace_arxiv_batch_requests()
            papers, network_seconds, parse_seconds = self.fetch_feed_with_timings(
                category,
                feed_url=feed_urls.get(category) if feed_urls is not None else None,
            )
            total_network_seconds += network_seconds
            total_parse_seconds += parse_seconds
            if max_results is not None:
                papers = papers[: max(max_results, 0)]
            category_papers[category] = papers
        return category_papers, total_network_seconds, total_parse_seconds

    def fetch_today(
        self,
        category: str = "cs",
        *,
        today: date | None = None,
        max_results: int | None = 120,
        feed_url: str | None = None,
    ) -> list[PaperRecord]:
        papers, _, _ = self.fetch_today_with_timings(
            category,
            today=today,
            max_results=max_results,
            feed_url=feed_url,
        )
        return papers

    def fetch_today_with_timings(
        self,
        category: str = "cs",
        *,
        today: date | None = None,
        max_results: int | None = 120,
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        target_date = today or date.today()
        papers, network_seconds, parse_seconds = self.fetch_feed_with_timings(
            category,
            feed_url=feed_url,
        )
        papers = filter_papers_by_date(papers, target_date=target_date)
        if max_results is None:
            return papers, network_seconds, parse_seconds
        return papers[: max(max_results, 0)], network_seconds, parse_seconds

    def fetch_today_by_category(
        self,
        categories: Sequence[str],
        *,
        today: date | None = None,
        max_results: int | None = None,
        feed_urls: Mapping[str, str] | None = None,
    ) -> dict[str, list[PaperRecord]]:
        category_papers, _, _ = self.fetch_today_by_category_with_timings(
            categories,
            today=today,
            max_results=max_results,
            feed_urls=feed_urls,
        )
        return category_papers

    def fetch_today_by_category_with_timings(
        self,
        categories: Sequence[str],
        *,
        today: date | None = None,
        max_results: int | None = None,
        feed_urls: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, list[PaperRecord]], float, float]:
        category_papers, network_seconds, parse_seconds = self.fetch_recent_by_category_with_timings(
            categories,
            max_results=None,
            feed_urls=feed_urls,
        )
        filtered = filter_paper_batches_by_date(category_papers, target_date=today or date.today())
        if max_results is None:
            return filtered, network_seconds, parse_seconds
        return (
            {
                label: papers[: max(max_results, 0)]
                for label, papers in filtered.items()
            },
            network_seconds,
            parse_seconds,
        )

    def fetch_recent_by_queries(
        self,
        query_definitions: Sequence[ArxivQueryDefinition],
        *,
        max_results: int = 120,
    ) -> dict[str, list[PaperRecord]]:
        query_papers, _, _ = self.fetch_recent_by_queries_with_timings(
            query_definitions,
            max_results=max_results,
        )
        return query_papers

    def fetch_recent_by_queries_with_timings(
        self,
        query_definitions: Sequence[ArxivQueryDefinition],
        *,
        max_results: int = 120,
    ) -> tuple[dict[str, list[PaperRecord]], float, float]:
        query_papers: dict[str, list[PaperRecord]] = {}
        total_network_seconds = 0.0
        total_parse_seconds = 0.0
        for index, definition in enumerate(query_definitions):
            if index > 0:
                _pace_arxiv_batch_requests()
            papers, network_seconds, parse_seconds = self.fetch_recent_with_timings(
                definition.query,
                max_results=max_results,
            )
            total_network_seconds += network_seconds
            total_parse_seconds += parse_seconds
            query_papers[definition.label] = _annotate_query_results(papers, definition)
        return query_papers, total_network_seconds, total_parse_seconds

    def fetch_today_by_queries(
        self,
        query_definitions: Sequence[ArxivQueryDefinition],
        *,
        today: date | None = None,
        max_results: int = 120,
    ) -> dict[str, list[PaperRecord]]:
        query_papers, _, _ = self.fetch_today_by_queries_with_timings(
            query_definitions,
            today=today,
            max_results=max_results,
        )
        return query_papers

    def fetch_today_by_queries_with_timings(
        self,
        query_definitions: Sequence[ArxivQueryDefinition],
        *,
        today: date | None = None,
        max_results: int = 120,
    ) -> tuple[dict[str, list[PaperRecord]], float, float]:
        query_papers, network_seconds, parse_seconds = self.fetch_recent_by_queries_with_timings(
            query_definitions,
            max_results=max_results,
        )
        return (
            filter_paper_batches_by_date(
                query_papers,
                target_date=today or date.today(),
            ),
            network_seconds,
            parse_seconds,
        )

    def parse_feed(self, xml_text: str) -> list[PaperRecord]:
        root = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", _ATOM_NS)
        if not entries:
            entries = root.findall("entry")

        papers: list[PaperRecord] = []
        for entry in entries:
            title = clean_text(_atom_text(entry, "atom:title") or _atom_text(entry, "title"))
            raw_summary = clean_text(_atom_text(entry, "atom:summary") or _atom_text(entry, "summary"))
            summary = _strip_announce_prefix(raw_summary)
            identifier_source = _atom_text(entry, "atom:id") or _atom_text(entry, "id")
            identifier = _normalize_identifier(identifier_source) or slugify(title or summary or "arxiv-paper")
            url = _alternate_link_href(entry) or _build_abs_url(identifier)
            published = parse_date(_atom_text(entry, "atom:published") or _atom_text(entry, "published"))
            updated = parse_date(_atom_text(entry, "atom:updated") or _atom_text(entry, "updated"))
            categories = tuple(dict.fromkeys(_category_term(node) for node in _category_nodes(entry) if _category_term(node)))
            authors = tuple(dict.fromkeys(name for name in _author_names(entry) if name))
            announce_type = clean_text(_atom_text(entry, "arxiv:announce_type"))
            source_metadata = {
                "native_identifier": identifier,
                "native_url": url,
                "tags": list(categories),
                "feed_kind": "atom",
            }
            if identifier_source:
                source_metadata["atom_id"] = identifier_source.strip()
            if announce_type:
                source_metadata["announce_type"] = announce_type
            if categories:
                source_metadata["primary_category"] = categories[0]

            papers.append(
                PaperRecord(
                    source="arxiv",
                    identifier=identifier,
                    title=title or "Untitled paper",
                    summary=summary,
                    authors=authors,
                    categories=categories,
                    published=published,
                    updated=updated,
                    url=url,
                    source_metadata=source_metadata,
                )
            )

        return papers


def build_biomedical_discovery_queries(
    *,
    categories: Sequence[str] = BIOMEDICAL_DISCOVERY_CATEGORIES,
) -> tuple[ArxivQueryDefinition, ...]:
    category_clause = " OR ".join(f"cat:{category}" for category in categories if category)
    if not category_clause:
        raise ValueError("biomedical discovery search requires at least one category")

    query_definitions: list[ArxivQueryDefinition] = []
    for label, keywords in BIOMEDICAL_DISCOVERY_KEYWORD_GROUPS:
        keyword_terms = [_format_query_term(keyword) for keyword in keywords]
        keyword_clause = " OR ".join(term for term in keyword_terms if term)
        if not keyword_clause:
            continue
        query_definitions.append(
            ArxivQueryDefinition(
                label=label,
                query=f"(({category_clause}) AND ({keyword_clause}))",
            )
        )
    return tuple(query_definitions)


def build_zotero_retrieval_queries(
    profile: UserInterestProfile,
    *,
    categories: Sequence[str] = BIOMEDICAL_DISCOVERY_CATEGORIES,
) -> tuple[ArxivQueryDefinition, ...]:
    if not profile.zotero_retrieval_hints:
        return ()

    category_clause = " OR ".join(f"cat:{category}" for category in categories if category)
    if not category_clause:
        raise ValueError("zotero biomedical search requires at least one category")

    query_definitions: list[ArxivQueryDefinition] = []
    for hint in profile.zotero_retrieval_hints:
        terms = tuple(term for term in hint.terms if clean_text(term))
        if not terms:
            continue
        term_clause = " OR ".join(_format_query_term(term) for term in terms)
        if not term_clause:
            continue
        query_definitions.append(
            ArxivQueryDefinition(
                label=hint.label,
                query=f"(({category_clause}) AND ({term_clause}))",
                origin="zotero",
                terms=terms,
            )
        )
    return tuple(query_definitions)


def merge_paper_batches(paper_batches: Mapping[str, Sequence[PaperRecord]]) -> list[PaperRecord]:
    merged: dict[str, PaperRecord] = {}
    for papers in paper_batches.values():
        for paper in papers:
            key = _dedup_key(paper)
            if key not in merged:
                merged[key] = paper
                continue
            merged[key] = replace(
                merged[key],
                categories=_merge_categories(merged[key].categories, paper.categories),
                source_metadata=_merge_source_metadata(merged[key].source_metadata, paper.source_metadata),
            )
    return list(merged.values())


def merge_category_papers(category_papers: Mapping[str, Sequence[PaperRecord]]) -> list[PaperRecord]:
    return merge_paper_batches(category_papers)


def filter_papers_by_date(papers: Sequence[PaperRecord], *, target_date: date) -> list[PaperRecord]:
    return [paper for paper in papers if _paper_date(paper) == target_date]


def filter_paper_batches_by_date(
    paper_batches: Mapping[str, Sequence[PaperRecord]],
    *,
    target_date: date,
) -> dict[str, list[PaperRecord]]:
    return {
        label: filter_papers_by_date(papers, target_date=target_date)
        for label, papers in paper_batches.items()
    }


def latest_available_paper_date(papers: Sequence[PaperRecord], *, requested_date: date) -> date | None:
    available_dates = sorted(
        {
            paper_date
            for paper in papers
            for paper_date in (_paper_date(paper),)
            if paper_date is not None and paper_date <= requested_date
        },
        reverse=True,
    )
    if not available_dates:
        return None
    return available_dates[0]


def _fetch_arxiv_text(url: str) -> str:
    last_error: ArxivRequestError | None = None
    for attempt in range(1, ARXIV_REQUEST_MAX_ATTEMPTS + 1):
        request = Request(url, headers={"User-Agent": ARXIV_REQUEST_USER_AGENT})
        try:
            with urlopen(request, timeout=ARXIV_REQUEST_TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            retryable = exc.code == 429
            last_error = ArxivRequestError(_http_error_message(exc))
            if retryable and attempt < ARXIV_REQUEST_MAX_ATTEMPTS:
                sleep(ARXIV_RATE_LIMIT_DELAY_SECONDS)
                continue
            raise last_error from exc
        except URLError as exc:
            retryable, message = _classify_url_error(exc)
            last_error = ArxivRequestError(message)
            if retryable and attempt < ARXIV_REQUEST_MAX_ATTEMPTS:
                sleep(ARXIV_RETRY_DELAY_SECONDS)
                continue
            raise last_error from exc
        except (TimeoutError, socket.timeout) as exc:
            last_error = ArxivRequestError("arXiv request timed out")
            if attempt < ARXIV_REQUEST_MAX_ATTEMPTS:
                sleep(ARXIV_RETRY_DELAY_SECONDS)
                continue
            raise last_error from exc
    raise last_error or ArxivRequestError("arXiv request failed")


def _pace_arxiv_batch_requests() -> None:
    sleep(ARXIV_BATCH_PACING_SECONDS)


def _http_error_message(exc: HTTPError) -> str:
    if exc.code == 429:
        return "arXiv request failed with HTTP 429 Too Many Requests"
    reason = clean_text(str(getattr(exc, "reason", "")))
    if reason:
        return f"arXiv request failed with HTTP {exc.code} {reason}"
    return f"arXiv request failed with HTTP {exc.code}"


def _classify_url_error(exc: URLError) -> tuple[bool, str]:
    reason = exc.reason
    reason_text = clean_text(str(reason or exc))
    if _is_timeout_reason(reason):
        return True, "arXiv request timed out"
    if _is_transient_url_reason(reason, reason_text):
        return True, f"arXiv request failed: {reason_text or 'temporary network error'}"
    return False, f"arXiv request failed: {reason_text or 'network error'}"


def _is_timeout_reason(reason: object) -> bool:
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in clean_text(str(reason)).lower()


def _is_transient_url_reason(reason: object, reason_text: str) -> bool:
    if isinstance(reason, PermissionError):
        return False
    if isinstance(reason, (ConnectionError, OSError)) and not isinstance(reason, FileNotFoundError):
        lowered = reason_text.lower()
        return any(
            marker in lowered
            for marker in (
                "temporary",
                "temporarily unavailable",
                "connection reset",
                "connection refused",
                "connection aborted",
                "name or service not known",
                "nodename nor servname",
                "network is unreachable",
            )
        ) or isinstance(
            reason,
            (ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError),
        )
    return False


def _atom_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag, _ATOM_NS)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _alternate_link_href(entry: ET.Element) -> str:
    links = entry.findall("atom:link", _ATOM_NS)
    if not links:
        links = entry.findall("link")
    for link in links:
        href = (link.attrib.get("href") or "").strip()
        rel = (link.attrib.get("rel") or "alternate").strip()
        if href and rel == "alternate":
            return href
    for link in links:
        href = (link.attrib.get("href") or "").strip()
        if href:
            return href
    return ""


def _author_names(entry: ET.Element) -> list[str]:
    names = []
    author_nodes = entry.findall("atom:author", _ATOM_NS)
    if not author_nodes:
        author_nodes = entry.findall("author")
    for author in author_nodes:
        name = author.find("atom:name", _ATOM_NS)
        if name is None:
            name = author.find("name")
        if name is not None and name.text:
            names.append(clean_text(name.text))
    if names:
        return names

    creator = _atom_text(entry, "dc:creator")
    if not creator:
        return []
    return [clean_text(part) for part in creator.split(",") if clean_text(part)]


def _category_nodes(entry: ET.Element) -> list[ET.Element]:
    nodes = entry.findall("atom:category", _ATOM_NS)
    if nodes:
        return nodes
    return entry.findall("category")


def _category_term(node: ET.Element) -> str:
    return clean_text(node.attrib.get("term", ""))


def _strip_announce_prefix(summary: str) -> str:
    if not summary:
        return ""
    stripped = _ABSTRACT_PREFIX_RE.sub("", summary).strip()
    if stripped != summary:
        return stripped
    if "Abstract:" in summary:
        return summary.split("Abstract:", 1)[1].strip()
    return summary


def _format_query_term(term: str) -> str:
    normalized = clean_text(term)
    if not normalized:
        return ""
    if "-" in normalized or " " in normalized:
        return f'all:"{normalized}"'
    return f"all:{normalized}"


def _normalize_identifier(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    if trimmed.startswith("oai:arXiv.org:"):
        return trimmed.removeprefix("oai:arXiv.org:")
    if "/abs/" in trimmed:
        return trimmed.split("/abs/", 1)[1].strip("/")
    if trimmed.startswith("http://") or trimmed.startswith("https://"):
        return last_path_token(trimmed)
    return trimmed


def _build_abs_url(identifier: str) -> str:
    if not identifier:
        return ""
    base_identifier = _versionless_identifier(identifier)
    return f"https://arxiv.org/abs/{base_identifier}"


def _paper_date(paper: PaperRecord) -> date | None:
    return paper.published or paper.updated


def _versionless_identifier(identifier: str) -> str:
    if not identifier:
        return ""
    return _ARXIV_VERSION_RE.sub("", identifier)


def _dedup_key(paper: PaperRecord) -> str:
    source = (paper.source or "unknown").strip().lower()
    identifier = _versionless_identifier((paper.identifier or paper.display_id).strip()).lower()
    return f"{source}::{identifier}"


def _merge_categories(existing: Sequence[str], incoming: Sequence[str]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in (*existing, *incoming):
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
    return tuple(merged)


def _annotate_query_results(
    papers: Sequence[PaperRecord],
    definition: ArxivQueryDefinition,
) -> list[PaperRecord]:
    if definition.origin == "baseline" and not definition.terms:
        return list(papers)

    support: dict[str, Any] = {
        "label": definition.label,
        "origin": definition.origin,
    }
    if definition.terms:
        support["terms"] = list(definition.terms)

    annotated: list[PaperRecord] = []
    for paper in papers:
        source_metadata = dict(paper.source_metadata)
        existing_support = source_metadata.get("retrieval_support", [])
        if not isinstance(existing_support, list):
            existing_support = []
        source_metadata["retrieval_support"] = _merge_metadata_lists(existing_support, [support])
        annotated.append(replace(paper, source_metadata=source_metadata))
    return annotated


def _merge_source_metadata(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        str(key): _copy_metadata_value(value)
        for key, value in existing.items()
        if str(key)
    }
    for key, value in incoming.items():
        normalized_key = str(key)
        if not normalized_key:
            continue
        if normalized_key not in merged:
            merged[normalized_key] = _copy_metadata_value(value)
            continue
        current = merged[normalized_key]
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[normalized_key] = _merge_source_metadata(current, value)
            continue
        if isinstance(current, list) and isinstance(value, (list, tuple)):
            merged[normalized_key] = _merge_metadata_lists(current, value)
            continue
        if current in ("", None, [], {}):
            merged[normalized_key] = _copy_metadata_value(value)
    return merged


def _merge_metadata_lists(existing: Sequence[Any], incoming: Sequence[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[object] = set()
    for value in (*existing, *incoming):
        normalized = _copy_metadata_value(value)
        dedupe_key = _metadata_dedupe_key(normalized)
        if dedupe_key in seen:
            continue
        merged.append(normalized)
        seen.add(dedupe_key)
    return merged


def _metadata_dedupe_key(value: Any) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _metadata_dedupe_key(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key)
        )
    if isinstance(value, (list, tuple)):
        return tuple(_metadata_dedupe_key(item) for item in value)
    return value


def _copy_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _copy_metadata_value(item)
            for key, item in value.items()
            if str(key)
        }
    if isinstance(value, (list, tuple)):
        return [_copy_metadata_value(item) for item in value]
    return value
