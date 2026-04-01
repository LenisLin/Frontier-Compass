"""Core data structures shared across FrontierCompass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    ZERO_TOKEN_COST_MODE,
)
from frontier_compass.common.text_normalization import slugify

PROFILE_SOURCE_BASELINE = "baseline"
PROFILE_SOURCE_ZOTERO = "zotero"
PROFILE_SOURCE_ZOTERO_EXPORT = "zotero_export"
PROFILE_SOURCE_LIVE_ZOTERO_DB = "live_zotero_db"
PROFILE_SOURCE_CHOICES = (
    PROFILE_SOURCE_BASELINE,
    PROFILE_SOURCE_ZOTERO,
    PROFILE_SOURCE_ZOTERO_EXPORT,
    PROFILE_SOURCE_LIVE_ZOTERO_DB,
)
_PROFILE_SOURCE_LABELS = {
    PROFILE_SOURCE_BASELINE: "Baseline",
    PROFILE_SOURCE_ZOTERO: "Zotero",
    PROFILE_SOURCE_ZOTERO_EXPORT: "Zotero Export",
    PROFILE_SOURCE_LIVE_ZOTERO_DB: "Live Zotero DB",
}

FETCH_SCOPE_SHORTLIST = "shortlist"
FETCH_SCOPE_DAY_FULL = "day-full"
FETCH_SCOPE_RANGE_FULL = "range-full"
FETCH_SCOPE_OPTIONS = (
    FETCH_SCOPE_DAY_FULL,
    FETCH_SCOPE_RANGE_FULL,
    FETCH_SCOPE_SHORTLIST,
)
_FETCH_SCOPE_LABELS = {
    FETCH_SCOPE_SHORTLIST: "Shortlist",
    FETCH_SCOPE_DAY_FULL: "Day full",
    FETCH_SCOPE_RANGE_FULL: "Range full",
}


def normalize_profile_source(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in PROFILE_SOURCE_CHOICES:
        return normalized
    if normalized == "zotero-profile":
        return PROFILE_SOURCE_ZOTERO
    return None


def profile_source_label(value: str | None) -> str:
    normalized = normalize_profile_source(value) or PROFILE_SOURCE_BASELINE
    return _PROFILE_SOURCE_LABELS[normalized]


def normalize_fetch_scope(
    value: str | None,
    *,
    default: str = FETCH_SCOPE_DAY_FULL,
) -> str:
    normalized_default = str(default or FETCH_SCOPE_DAY_FULL).strip().lower()
    if normalized_default not in FETCH_SCOPE_OPTIONS:
        normalized_default = FETCH_SCOPE_DAY_FULL
    normalized_value = str(value or "").strip().lower()
    if normalized_value in FETCH_SCOPE_OPTIONS:
        return normalized_value
    return normalized_default


def fetch_scope_label(value: str | None) -> str:
    normalized = normalize_fetch_scope(value)
    return _FETCH_SCOPE_LABELS.get(normalized, normalized)


@dataclass(slots=True, frozen=True)
class PaperRecord:
    source: str
    identifier: str
    title: str
    summary: str = ""
    authors: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    published: date | None = None
    updated: date | None = None
    url: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display_id(self) -> str:
        return self.identifier or slugify(self.title)

    @property
    def source_identifier(self) -> str:
        value = self.source_metadata.get("native_identifier")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.identifier

    @property
    def source_url(self) -> str:
        value = self.source_metadata.get("native_url")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.url

    @property
    def source_tags(self) -> tuple[str, ...]:
        value = self.source_metadata.get("tags")
        if isinstance(value, (list, tuple)):
            tags = tuple(str(item).strip() for item in value if str(item).strip())
            if tags:
                return tags
        return self.categories

    def normalized_text(self) -> str:
        parts = [self.title, self.summary, " ".join(self.categories), " ".join(self.authors)]
        return " ".join(part.strip().lower() for part in parts if part).strip()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "title": self.title,
            "summary": self.summary,
            "authors": list(self.authors),
            "categories": list(self.categories),
            "published": self.published.isoformat() if self.published else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "url": self.url,
            "source_metadata": _normalize_metadata_value(self.source_metadata),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PaperRecord":
        authors = tuple(str(value) for value in payload.get("authors", ()))
        categories = tuple(str(value) for value in payload.get("categories", ()))
        published = _parse_date(payload.get("published"))
        updated = _parse_date(payload.get("updated"))
        source_metadata = _parse_metadata_mapping(payload.get("source_metadata"))

        return cls(
            source=str(payload.get("source", "unknown")),
            identifier=str(payload.get("identifier", "")),
            title=str(payload.get("title", "Untitled paper")),
            summary=str(payload.get("summary", "")),
            authors=authors,
            categories=categories,
            published=published,
            updated=updated,
            url=str(payload.get("url", "")),
            source_metadata=source_metadata,
        )


@dataclass(slots=True, frozen=True)
class RequestWindow:
    kind: str = "day"
    requested_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = "complete"
    completed_dates: tuple[date, ...] = ()
    failed_date: date | None = None
    failed_source: str = ""
    failure_reason: str = ""

    @property
    def is_range(self) -> bool:
        return self.kind == "range"

    @property
    def is_partial(self) -> bool:
        return self.status == "partial"

    @property
    def resolved_start_date(self) -> date | None:
        if self.start_date is not None:
            return self.start_date
        return self.requested_date

    @property
    def resolved_end_date(self) -> date | None:
        if self.end_date is not None:
            return self.end_date
        return self.resolved_start_date

    @property
    def requested_dates(self) -> tuple[date, ...]:
        start = self.resolved_start_date
        end = self.resolved_end_date
        if start is None or end is None:
            return ()
        if end < start:
            return ()
        values: list[date] = []
        current = start
        while current <= end:
            values.append(current)
            current = current.fromordinal(current.toordinal() + 1)
        return tuple(values)

    @property
    def requested_day_count(self) -> int:
        return len(self.requested_dates)

    @property
    def completed_day_count(self) -> int:
        return len(self.completed_dates)

    @property
    def label(self) -> str:
        if self.is_range:
            start = self.resolved_start_date.isoformat() if self.resolved_start_date is not None else "n/a"
            end = self.resolved_end_date.isoformat() if self.resolved_end_date is not None else start
            base = f"{start} -> {end}"
        else:
            base = self.requested_date.isoformat() if self.requested_date is not None else "n/a"
        if self.status in {"partial", "failed"}:
            return _request_window_label_with_details(base, self)
        return base

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "requested_date": self.requested_date.isoformat() if self.requested_date is not None else None,
            "start_date": self.start_date.isoformat() if self.start_date is not None else None,
            "end_date": self.end_date.isoformat() if self.end_date is not None else None,
            "status": self.status,
            "completed_dates": [value.isoformat() for value in self.completed_dates],
            "failed_date": self.failed_date.isoformat() if self.failed_date is not None else None,
            "failed_source": self.failed_source,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RequestWindow":
        completed_dates_value = payload.get("completed_dates", ())
        if not isinstance(completed_dates_value, (list, tuple)):
            completed_dates_value = ()
        return cls(
            kind=str(payload.get("kind", "day")),
            requested_date=_parse_date(payload.get("requested_date")),
            start_date=_parse_date(payload.get("start_date")),
            end_date=_parse_date(payload.get("end_date")),
            status=str(payload.get("status", "complete")),
            completed_dates=tuple(
                parsed
                for value in completed_dates_value
                for parsed in (_parse_date(value),)
                if parsed is not None
            ),
            failed_date=_parse_date(payload.get("failed_date")),
            failed_source=str(payload.get("failed_source", "")),
            failure_reason=str(payload.get("failure_reason", "")),
        )


def _request_window_label_with_details(base: str, request_window: RequestWindow) -> str:
    details: list[str] = []
    if request_window.completed_dates:
        details.append(
            "completed "
            + ", ".join(value.isoformat() for value in request_window.completed_dates)
        )
    if request_window.failed_date is not None:
        failed_bits = [f"failed {request_window.failed_date.isoformat()}"]
        if request_window.failed_source:
            failed_bits.append(request_window.failed_source)
        details.append(" / ".join(failed_bits))
    if request_window.failure_reason:
        details.append(request_window.failure_reason)
    if details:
        return f"{base} ({request_window.status}; " + "; ".join(details) + ")"
    return f"{base} ({request_window.status})"


@dataclass(slots=True, frozen=True)
class RunTimings:
    cache_seconds: float | None = None
    network_seconds: float | None = None
    parse_seconds: float | None = None
    rank_seconds: float | None = None
    report_seconds: float | None = None
    total_seconds: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "cache_seconds": self.cache_seconds,
            "network_seconds": self.network_seconds,
            "parse_seconds": self.parse_seconds,
            "rank_seconds": self.rank_seconds,
            "report_seconds": self.report_seconds,
            "total_seconds": self.total_seconds,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RunTimings":
        cache_seconds = _parse_float(payload.get("cache_seconds"))
        network_seconds = _parse_float(payload.get("network_seconds"))
        parse_seconds = _parse_float(payload.get("parse_seconds"))
        rank_seconds = _parse_float(payload.get("rank_seconds"))
        report_seconds = _parse_float(payload.get("report_seconds"))
        total_seconds = _parse_float(payload.get("total_seconds"))
        if total_seconds is None:
            total_seconds = _sum_known_seconds(
                cache_seconds,
                network_seconds,
                parse_seconds,
                rank_seconds,
                report_seconds,
            )
        return cls(
            cache_seconds=cache_seconds,
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            rank_seconds=rank_seconds,
            report_seconds=report_seconds,
            total_seconds=total_seconds,
        )


@dataclass(slots=True, frozen=True)
class SourceRunStats:
    source: str
    requested: bool = True
    fetched_count: int = 0
    displayed_count: int = 0
    status: str = "ready"
    outcome: str = ""
    live_outcome: str = ""
    cache_status: str = "fresh"
    error: str = ""
    endpoint: str = ""
    note: str = ""
    timings: RunTimings = field(default_factory=RunTimings)

    @property
    def resolved_live_outcome(self) -> str:
        if self.live_outcome:
            return self.live_outcome
        if self.outcome in {"live-success", "live-zero", "live-failed", "unknown-legacy"}:
            return self.outcome
        if self.cache_status == "fresh":
            if self.fetched_count > 0 or self.displayed_count > 0:
                return "live-success"
            if self.error:
                return "live-failed"
            return "live-zero"
        return "unknown-legacy"

    @property
    def resolved_outcome(self) -> str:
        if self.outcome:
            return self.outcome
        if self.cache_status == "same-day-cache":
            return "unknown-legacy" if self.resolved_live_outcome == "unknown-legacy" else "same-day-cache"
        if self.cache_status == "stale-compatible-cache":
            return "unknown-legacy" if self.resolved_live_outcome == "unknown-legacy" else "stale-cache"
        return self.resolved_live_outcome

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "requested": self.requested,
            "fetched_count": self.fetched_count,
            "displayed_count": self.displayed_count,
            "status": self.status,
            "outcome": self.outcome,
            "live_outcome": self.live_outcome,
            "cache_status": self.cache_status,
            "error": self.error,
            "endpoint": self.endpoint,
            "note": self.note,
            "timings": self.timings.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SourceRunStats":
        timings_value = payload.get("timings")
        return cls(
            source=str(payload.get("source", "unknown")),
            requested=bool(payload.get("requested", True)),
            fetched_count=_parse_int(payload.get("fetched_count")) or 0,
            displayed_count=_parse_int(payload.get("displayed_count")) or 0,
            status=str(payload.get("status", "ready")),
            outcome=str(payload.get("outcome", "")),
            live_outcome=str(payload.get("live_outcome", "")),
            cache_status=str(payload.get("cache_status", "fresh")),
            error=str(payload.get("error", "")),
            endpoint=str(payload.get("endpoint", "")),
            note=str(payload.get("note", "")),
            timings=(
                RunTimings.from_mapping(timings_value)
                if isinstance(timings_value, Mapping)
                else RunTimings()
            ),
        )


@dataclass(slots=True, frozen=True)
class ProfileBasis:
    source: str = PROFILE_SOURCE_BASELINE
    label: str = ""
    description: str = ""
    path: str = ""
    item_count: int = 0
    used_item_count: int = 0

    @property
    def source_label(self) -> str:
        return profile_source_label(self.source)

    @property
    def compact_label(self) -> str:
        return self.label or self.source_label

    @property
    def path_name(self) -> str:
        if not self.path:
            return ""
        return Path(self.path).name

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "description": self.description,
            "path": self.path,
            "item_count": self.item_count,
            "used_item_count": self.used_item_count,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ProfileBasis":
        return cls(
            source=normalize_profile_source(str(payload.get("source", PROFILE_SOURCE_BASELINE)))
            or PROFILE_SOURCE_BASELINE,
            label=str(payload.get("label", "")),
            description=str(payload.get("description", "")),
            path=str(payload.get("path", "")),
            item_count=_parse_int(payload.get("item_count")) or 0,
            used_item_count=_parse_int(payload.get("used_item_count")) or 0,
        )


@dataclass(slots=True)
class UserInterestProfile:
    keywords: tuple[str, ...] = ()
    category_weights: dict[str, float] = field(default_factory=dict)
    seed_titles: tuple[str, ...] = ()
    notes: str = ""
    basis_label: str = ""
    profile_basis: ProfileBasis | None = None
    zotero_item_count: int = 0
    zotero_used_item_count: int = 0
    zotero_export_name: str = ""
    zotero_db_name: str = ""
    zotero_keywords: tuple[str, ...] = ()
    zotero_concepts: tuple[str, ...] = ()
    zotero_selected_collections: tuple[str, ...] = ()
    zotero_retrieval_hints: tuple["ZoteroRetrievalHint", ...] = ()

    @property
    def zotero_active(self) -> bool:
        return bool(
            self.zotero_export_name
            or self.zotero_db_name
            or self.zotero_keywords
            or self.zotero_concepts
            or self.zotero_retrieval_hints
        )

    @property
    def profile_source(self) -> str:
        if self.profile_basis is not None and self.profile_basis.source:
            return normalize_profile_source(self.profile_basis.source) or PROFILE_SOURCE_BASELINE
        if self.zotero_db_name:
            return PROFILE_SOURCE_LIVE_ZOTERO_DB
        if self.zotero_export_name:
            return PROFILE_SOURCE_ZOTERO_EXPORT
        return PROFILE_SOURCE_BASELINE

    @property
    def profile_source_label(self) -> str:
        return profile_source_label(self.profile_source)

    @property
    def profile_label(self) -> str:
        if self.profile_basis is not None:
            return self.profile_basis.compact_label
        return self.basis_label or self.profile_source_label

    @property
    def profile_path(self) -> str:
        if self.profile_basis is not None and self.profile_basis.path:
            return self.profile_basis.path
        if self.zotero_db_name:
            return self.zotero_db_name
        if self.zotero_export_name:
            return self.zotero_export_name
        return ""

    @property
    def profile_path_name(self) -> str:
        if self.profile_basis is not None and self.profile_basis.path_name:
            return self.profile_basis.path_name
        if not self.profile_path:
            return ""
        return Path(self.profile_path).name

    @property
    def profile_item_count(self) -> int:
        if self.profile_basis is not None and self.profile_basis.item_count > 0:
            return self.profile_basis.item_count
        return self.zotero_item_count

    @property
    def profile_used_item_count(self) -> int:
        if self.profile_basis is not None and self.profile_basis.used_item_count > 0:
            return self.profile_basis.used_item_count
        return self.zotero_used_item_count

    @property
    def basis_summary_label(self) -> str:
        if self.profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
            return "baseline + live Zotero DB"
        if self.profile_source == PROFILE_SOURCE_ZOTERO:
            return "baseline + Zotero"
        if self.profile_source == PROFILE_SOURCE_ZOTERO_EXPORT:
            return "baseline + Zotero export"
        return "baseline only"

    def contract_summary_bits(self, term_limit: int = 3) -> tuple[str, ...]:
        bits = [f"{self.profile_source} ({self.profile_source_label})"]
        if self.profile_label:
            bits.append(self.profile_label)
        if self.profile_path_name:
            bits.append(self.profile_path_name)
        if self.profile_item_count or self.profile_used_item_count:
            bits.append(f"{self.profile_item_count}/{self.profile_used_item_count} items")
        top_profile_terms = self.top_profile_terms(limit=term_limit)
        if top_profile_terms:
            bits.append(", ".join(top_profile_terms))
        return tuple(bit for bit in bits if bit)

    def inspector_lines(self, term_limit: int = 6) -> tuple[str, ...]:
        lines = [
            f"Profile mode: {self.basis_summary_label}",
            f"Profile basis: {self.basis_label or 'n/a'}",
            f"Profile label: {self.profile_label or 'n/a'}",
            f"Profile source: {self.profile_source} ({self.profile_source_label})",
        ]
        if self.profile_path:
            lines.append(f"Profile path: {self.profile_path}")
        if self.profile_source == PROFILE_SOURCE_ZOTERO_EXPORT and self.zotero_export_name:
            lines.append(f"Zotero export: {self.zotero_export_name}")
        if self.profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB and self.zotero_db_name:
            lines.append(f"Live Zotero DB: {self.zotero_db_name}")
        if self.zotero_selected_collections:
            lines.append(f"Zotero collections: {', '.join(self.zotero_selected_collections)}")
        if self.profile_item_count or self.profile_used_item_count:
            lines.append(
                "Profile items parsed / used: "
                f"{self.profile_item_count} / {self.profile_used_item_count}"
            )
        profile_terms = self.top_profile_terms(limit=term_limit)
        if profile_terms:
            lines.append(f"Top profile terms: {', '.join(profile_terms)}")
        zotero_signals = self.top_zotero_signals(limit=term_limit)
        if zotero_signals and zotero_signals != profile_terms:
            lines.append(f"Top Zotero signals: {', '.join(zotero_signals)}")
        zotero_retrieval_terms = self.top_zotero_retrieval_terms(limit=term_limit)
        if zotero_retrieval_terms:
            lines.append(f"Zotero retrieval hints: {', '.join(zotero_retrieval_terms)}")
        return tuple(lines)

    def top_categories(self, limit: int = 5) -> tuple[str, ...]:
        ordered = sorted(self.category_weights.items(), key=lambda item: item[1], reverse=True)
        return tuple(name for name, _weight in ordered[:limit])

    def top_zotero_signals(self, limit: int = 5) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for value in (*self.zotero_keywords, *self.zotero_concepts):
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            values.append(normalized)
            seen.add(normalized)
            if len(values) >= limit:
                break
        return tuple(values)

    def top_zotero_retrieval_terms(self, limit: int = 6) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for hint in self.zotero_retrieval_hints:
            for value in hint.terms:
                normalized = value.strip()
                if not normalized or normalized in seen:
                    continue
                values.append(normalized)
                seen.add(normalized)
                if len(values) >= limit:
                    return tuple(values)
        return tuple(values)

    def top_profile_terms(self, limit: int = 6) -> tuple[str, ...]:
        if self.zotero_active:
            return self.top_zotero_signals(limit=limit)
        values: list[str] = []
        seen: set[str] = set()
        for value in (*self.keywords, *self.top_categories(limit=limit)):
            normalized = value.strip()
            canonical = normalized.lower()
            if not normalized or canonical in seen:
                continue
            values.append(normalized)
            seen.add(canonical)
            if len(values) >= limit:
                break
        return tuple(values)

    def query_string(self) -> str:
        return " OR ".join(self.keywords)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "keywords": list(self.keywords),
            "category_weights": dict(self.category_weights),
            "seed_titles": list(self.seed_titles),
            "notes": self.notes,
            "basis_label": self.basis_label,
            "profile_basis": self.profile_basis.to_mapping() if self.profile_basis is not None else None,
            "zotero_item_count": self.zotero_item_count,
            "zotero_used_item_count": self.zotero_used_item_count,
            "zotero_export_name": self.zotero_export_name,
            "zotero_db_name": self.zotero_db_name,
            "zotero_keywords": list(self.zotero_keywords),
            "zotero_concepts": list(self.zotero_concepts),
            "zotero_selected_collections": list(self.zotero_selected_collections),
            "zotero_retrieval_hints": [hint.to_mapping() for hint in self.zotero_retrieval_hints],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "UserInterestProfile":
        category_weights_value = payload.get("category_weights", {})
        category_weights: dict[str, float] = {}
        if isinstance(category_weights_value, Mapping):
            category_weights = {str(key): float(value) for key, value in category_weights_value.items()}

        retrieval_hints_value = payload.get("zotero_retrieval_hints", ())
        if not isinstance(retrieval_hints_value, (list, tuple)):
            retrieval_hints_value = ()
        profile_basis_value = payload.get("profile_basis")
        profile_basis = None
        if isinstance(profile_basis_value, Mapping):
            profile_basis = ProfileBasis.from_mapping(profile_basis_value)
        else:
            profile_basis = _legacy_profile_basis(
                basis_label=str(payload.get("basis_label", "")),
                zotero_export_name=str(payload.get("zotero_export_name", "")),
                zotero_db_name=str(payload.get("zotero_db_name", "")),
                zotero_item_count=_parse_int(payload.get("zotero_item_count")) or 0,
                zotero_used_item_count=_parse_int(payload.get("zotero_used_item_count")) or 0,
            )

        return cls(
            keywords=tuple(str(value) for value in payload.get("keywords", ())),
            category_weights=category_weights,
            seed_titles=tuple(str(value) for value in payload.get("seed_titles", ())),
            notes=str(payload.get("notes", "")),
            basis_label=str(payload.get("basis_label", "")),
            profile_basis=profile_basis,
            zotero_item_count=_parse_int(payload.get("zotero_item_count")) or 0,
            zotero_used_item_count=_parse_int(payload.get("zotero_used_item_count")) or 0,
            zotero_export_name=str(payload.get("zotero_export_name", "")),
            zotero_db_name=str(payload.get("zotero_db_name", "")),
            zotero_keywords=tuple(str(value) for value in payload.get("zotero_keywords", ())),
            zotero_concepts=tuple(str(value) for value in payload.get("zotero_concepts", ())),
            zotero_selected_collections=tuple(
                str(value) for value in payload.get("zotero_selected_collections", ()) if str(value)
            ),
            zotero_retrieval_hints=tuple(
                ZoteroRetrievalHint.from_mapping(value)
                for value in retrieval_hints_value
                if isinstance(value, Mapping)
            ),
        )


@dataclass(slots=True, frozen=True)
class ZoteroRetrievalHint:
    label: str
    terms: tuple[str, ...] = ()
    rationale: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "terms": list(self.terms),
            "rationale": self.rationale,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ZoteroRetrievalHint":
        return cls(
            label=str(payload.get("label", "")),
            terms=tuple(str(value) for value in payload.get("terms", ()) if str(value)),
            rationale=str(payload.get("rationale", "")),
        )


@dataclass(slots=True, frozen=True)
class ExplorationPolicy:
    label: str
    shortlist_size: int = 8
    max_items: int = 3
    max_per_theme: int = 1
    min_score: float = 0.35
    min_biomedical_keyword: float = 0.13
    notes: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "shortlist_size": self.shortlist_size,
            "max_items": self.max_items,
            "max_per_theme": self.max_per_theme,
            "min_score": self.min_score,
            "min_biomedical_keyword": self.min_biomedical_keyword,
            "notes": self.notes,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExplorationPolicy":
        return cls(
            label=str(payload.get("label", "")),
            shortlist_size=_parse_int(payload.get("shortlist_size")) or 8,
            max_items=_parse_int(payload.get("max_items")) or 3,
            max_per_theme=_parse_int(payload.get("max_per_theme")) or 1,
            min_score=float(payload.get("min_score", 0.35)),
            min_biomedical_keyword=float(payload.get("min_biomedical_keyword", 0.13)),
            notes=str(payload.get("notes", "")),
        )


@dataclass(slots=True, frozen=True)
class RecommendationExplanation:
    total_score: float
    baseline_contribution: float = 0.0
    category_contribution: float = 0.0
    recency_contribution: float = 0.0
    zotero_bonus_contribution: float = 0.0
    generic_cs_penalty_contribution: float = 0.0
    baseline_keyword_hits: tuple[str, ...] = ()
    category_hits: tuple[str, ...] = ()
    zotero_keyword_hits: tuple[str, ...] = ()
    zotero_concept_hits: tuple[str, ...] = ()
    zotero_effect: str = "inactive"
    zotero_active: bool = False
    retrieval_support_origin: str = ""
    retrieval_support_labels: tuple[str, ...] = ()
    retrieval_support_terms: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "baseline_contribution": self.baseline_contribution,
            "category_contribution": self.category_contribution,
            "recency_contribution": self.recency_contribution,
            "zotero_bonus_contribution": self.zotero_bonus_contribution,
            "generic_cs_penalty_contribution": self.generic_cs_penalty_contribution,
            "baseline_keyword_hits": list(self.baseline_keyword_hits),
            "category_hits": list(self.category_hits),
            "zotero_keyword_hits": list(self.zotero_keyword_hits),
            "zotero_concept_hits": list(self.zotero_concept_hits),
            "zotero_effect": self.zotero_effect,
            "zotero_active": self.zotero_active,
            "retrieval_support_origin": self.retrieval_support_origin,
            "retrieval_support_labels": list(self.retrieval_support_labels),
            "retrieval_support_terms": list(self.retrieval_support_terms),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RecommendationExplanation":
        return cls(
            total_score=float(payload.get("total_score", 0.0)),
            baseline_contribution=float(payload.get("baseline_contribution", 0.0)),
            category_contribution=float(payload.get("category_contribution", 0.0)),
            recency_contribution=float(payload.get("recency_contribution", 0.0)),
            zotero_bonus_contribution=float(payload.get("zotero_bonus_contribution", 0.0)),
            generic_cs_penalty_contribution=float(payload.get("generic_cs_penalty_contribution", 0.0)),
            baseline_keyword_hits=tuple(str(value) for value in payload.get("baseline_keyword_hits", ())),
            category_hits=tuple(str(value) for value in payload.get("category_hits", ())),
            zotero_keyword_hits=tuple(str(value) for value in payload.get("zotero_keyword_hits", ())),
            zotero_concept_hits=tuple(str(value) for value in payload.get("zotero_concept_hits", ())),
            zotero_effect=str(payload.get("zotero_effect", "inactive")),
            zotero_active=bool(payload.get("zotero_active", False)),
            retrieval_support_origin=str(payload.get("retrieval_support_origin", "")),
            retrieval_support_labels=tuple(str(value) for value in payload.get("retrieval_support_labels", ())),
            retrieval_support_terms=tuple(str(value) for value in payload.get("retrieval_support_terms", ())),
        )


@dataclass(slots=True)
class RankedPaper:
    paper: PaperRecord
    score: float
    reasons: tuple[str, ...] = ()
    facets: dict[str, float] = field(default_factory=dict)
    recommendation_summary: str = ""
    explanation: RecommendationExplanation | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "paper": self.paper.to_mapping(),
            "score": self.score,
            "reasons": list(self.reasons),
            "facets": dict(self.facets),
            "recommendation_summary": self.recommendation_summary,
            "explanation": self.explanation.to_mapping() if self.explanation is not None else None,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RankedPaper":
        paper_payload = payload.get("paper", {})
        if not isinstance(paper_payload, Mapping):
            paper_payload = {}

        facets_value = payload.get("facets", {})
        facets: dict[str, float] = {}
        if isinstance(facets_value, Mapping):
            facets = {str(key): float(value) for key, value in facets_value.items()}

        explanation_value = payload.get("explanation")
        explanation = None
        if isinstance(explanation_value, Mapping):
            explanation = RecommendationExplanation.from_mapping(explanation_value)

        return cls(
            paper=PaperRecord.from_mapping(paper_payload),
            score=float(payload.get("score", 0.0)),
            reasons=tuple(str(value) for value in payload.get("reasons", ())),
            facets=facets,
            recommendation_summary=str(payload.get("recommendation_summary", "")),
            explanation=explanation,
        )


@dataclass(slots=True, frozen=True)
class FrontierReportSignal:
    label: str
    count: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FrontierReportSignal":
        return cls(
            label=str(payload.get("label", "")),
            count=int(payload.get("count", 0)),
        )


@dataclass(slots=True, frozen=True)
class FrontierReportHighlight:
    source: str
    identifier: str
    title: str
    theme_label: str
    why: str
    summary: str = ""
    categories: tuple[str, ...] = ()
    url: str = ""
    published: date | None = None
    score: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "title": self.title,
            "theme_label": self.theme_label,
            "why": self.why,
            "summary": self.summary,
            "categories": list(self.categories),
            "url": self.url,
            "published": self.published.isoformat() if self.published else None,
            "score": self.score,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FrontierReportHighlight":
        return cls(
            source=str(payload.get("source", "unknown")),
            identifier=str(payload.get("identifier", "")),
            title=str(payload.get("title", "")),
            theme_label=str(payload.get("theme_label", "")),
            why=str(payload.get("why", "")),
            summary=str(payload.get("summary", "")),
            categories=tuple(str(value) for value in payload.get("categories", ())),
            url=str(payload.get("url", "")),
            published=_parse_date(payload.get("published")),
            score=float(payload["score"]) if payload.get("score") is not None else None,
        )


@dataclass(slots=True, frozen=True)
class DailyFrontierReport:
    requested_date: date
    effective_date: date
    source: str
    mode: str
    mode_label: str
    mode_kind: str = ""
    request_window: RequestWindow = field(default_factory=RequestWindow)
    source_run_stats: tuple[SourceRunStats, ...] = ()
    run_timings: RunTimings = field(default_factory=RunTimings)
    requested_report_mode: str = DEFAULT_REPORT_MODE
    report_mode: str = DEFAULT_REPORT_MODE
    cost_mode: str = ZERO_TOKEN_COST_MODE
    enhanced_track: str = ""
    enhanced_item_count: int = 0
    runtime_note: str = ""
    report_status: str = "ready"
    report_error: str = ""
    fetch_scope: str = FETCH_SCOPE_DAY_FULL
    searched_categories: tuple[str, ...] = ()
    total_fetched: int = 0
    total_ranked: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    repeated_themes: tuple[FrontierReportSignal, ...] = ()
    salient_topics: tuple[FrontierReportSignal, ...] = ()
    adjacent_themes: tuple[FrontierReportSignal, ...] = ()
    takeaways: tuple[str, ...] = ()
    field_highlights: tuple[FrontierReportHighlight, ...] = ()
    profile_relevant_highlights: tuple[FrontierReportHighlight, ...] = ()

    @property
    def zero_token(self) -> bool:
        return self.cost_mode == ZERO_TOKEN_COST_MODE

    @property
    def model_assisted(self) -> bool:
        return not self.zero_token

    @property
    def displayed_highlight_count(self) -> int:
        return len(self.field_highlights) + len(self.profile_relevant_highlights)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "requested_date": self.requested_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "source": self.source,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "mode_kind": self.mode_kind,
            "request_window": self.request_window.to_mapping(),
            "source_run_stats": [item.to_mapping() for item in self.source_run_stats],
            "run_timings": self.run_timings.to_mapping(),
            "requested_report_mode": self.requested_report_mode,
            "report_mode": self.report_mode,
            "cost_mode": self.cost_mode,
            "enhanced_track": self.enhanced_track,
            "enhanced_item_count": self.enhanced_item_count,
            "runtime_note": self.runtime_note,
            "report_status": self.report_status,
            "report_error": self.report_error,
            "fetch_scope": normalize_fetch_scope(self.fetch_scope),
            "searched_categories": list(self.searched_categories),
            "total_fetched": self.total_fetched,
            "total_ranked": self.total_ranked,
            "source_counts": dict(self.source_counts),
            "repeated_themes": [signal.to_mapping() for signal in self.repeated_themes],
            "salient_topics": [signal.to_mapping() for signal in self.salient_topics],
            "adjacent_themes": [signal.to_mapping() for signal in self.adjacent_themes],
            "takeaways": list(self.takeaways),
            "field_highlights": [item.to_mapping() for item in self.field_highlights],
            "profile_relevant_highlights": [item.to_mapping() for item in self.profile_relevant_highlights],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DailyFrontierReport":
        requested_date = _parse_date(payload.get("requested_date"))
        effective_date = _parse_date(payload.get("effective_date"))
        if requested_date is None or effective_date is None:
            raise ValueError("daily frontier report dates must be ISO dates")

        repeated_themes_payload = payload.get("repeated_themes", ())
        if not isinstance(repeated_themes_payload, list):
            repeated_themes_payload = []
        salient_topics_payload = payload.get("salient_topics", ())
        if not isinstance(salient_topics_payload, list):
            salient_topics_payload = []
        adjacent_themes_payload = payload.get("adjacent_themes", ())
        if not isinstance(adjacent_themes_payload, list):
            adjacent_themes_payload = []
        field_highlights_payload = payload.get("field_highlights", ())
        if not isinstance(field_highlights_payload, list):
            field_highlights_payload = []
        profile_highlights_payload = payload.get("profile_relevant_highlights", ())
        if not isinstance(profile_highlights_payload, list):
            profile_highlights_payload = []
        request_window_value = payload.get("request_window")
        request_window = (
            RequestWindow.from_mapping(request_window_value)
            if isinstance(request_window_value, Mapping)
            else RequestWindow(kind="day", requested_date=requested_date)
        )
        source_run_stats_value = payload.get("source_run_stats", ())
        if not isinstance(source_run_stats_value, (list, tuple)):
            source_run_stats_value = ()
        run_timings_value = payload.get("run_timings")
        run_timings = (
            RunTimings.from_mapping(run_timings_value)
            if isinstance(run_timings_value, Mapping)
            else RunTimings()
        )

        return cls(
            requested_date=requested_date,
            effective_date=effective_date,
            source=str(payload.get("source", "unknown")),
            mode=str(payload.get("mode", "")),
            mode_label=str(payload.get("mode_label", "")),
            mode_kind=str(payload.get("mode_kind", "")),
            request_window=request_window,
            source_run_stats=tuple(
                SourceRunStats.from_mapping(item)
                for item in source_run_stats_value
                if isinstance(item, Mapping)
            ),
            run_timings=run_timings,
            requested_report_mode=str(payload.get("requested_report_mode", DEFAULT_REPORT_MODE)),
            report_mode=str(payload.get("report_mode", DEFAULT_REPORT_MODE)),
            cost_mode=str(payload.get("cost_mode", ZERO_TOKEN_COST_MODE)),
            enhanced_track=str(payload.get("enhanced_track", "")),
            enhanced_item_count=_parse_int(payload.get("enhanced_item_count")) or 0,
            runtime_note=str(payload.get("runtime_note", "")),
            report_status=str(payload.get("report_status", "ready")),
            report_error=str(payload.get("report_error", "")),
            fetch_scope=normalize_fetch_scope(
                payload.get("fetch_scope"),
                default=FETCH_SCOPE_SHORTLIST,
            ),
            searched_categories=tuple(str(value) for value in payload.get("searched_categories", ())),
            total_fetched=int(payload.get("total_fetched", 0)),
            total_ranked=int(payload.get("total_ranked", 0)),
            source_counts={
                str(key): int(value)
                for key, value in payload.get("source_counts", {}).items()
            }
            if isinstance(payload.get("source_counts"), Mapping)
            else {},
            repeated_themes=tuple(
                FrontierReportSignal.from_mapping(item)
                for item in repeated_themes_payload
                if isinstance(item, Mapping)
            ),
            salient_topics=tuple(
                FrontierReportSignal.from_mapping(item)
                for item in salient_topics_payload
                if isinstance(item, Mapping)
            ),
            adjacent_themes=tuple(
                FrontierReportSignal.from_mapping(item)
                for item in adjacent_themes_payload
                if isinstance(item, Mapping)
            ),
            takeaways=tuple(str(value) for value in payload.get("takeaways", ())),
            field_highlights=tuple(
                FrontierReportHighlight.from_mapping(item)
                for item in field_highlights_payload
                if isinstance(item, Mapping)
            ),
            profile_relevant_highlights=tuple(
                FrontierReportHighlight.from_mapping(item)
                for item in profile_highlights_payload
                if isinstance(item, Mapping)
            ),
        )


@dataclass(slots=True)
class DailyDigest:
    source: str
    category: str
    target_date: date
    generated_at: datetime
    feed_url: str
    profile: UserInterestProfile
    ranked: list[RankedPaper]
    request_window: RequestWindow = field(default_factory=RequestWindow)
    source_run_stats: tuple[SourceRunStats, ...] = ()
    run_timings: RunTimings = field(default_factory=RunTimings)
    exploration_picks: list[RankedPaper] = field(default_factory=list)
    exploration_policy: ExplorationPolicy | None = None
    frontier_report: DailyFrontierReport | None = None
    searched_categories: tuple[str, ...] = ()
    per_category_counts: dict[str, int] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    total_fetched: int = 0
    feed_urls: dict[str, str] = field(default_factory=dict)
    source_endpoints: dict[str, str] = field(default_factory=dict)
    source_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    mode_label: str = ""
    mode_kind: str = ""
    requested_report_mode: str = DEFAULT_REPORT_MODE
    report_mode: str = DEFAULT_REPORT_MODE
    cost_mode: str = ZERO_TOKEN_COST_MODE
    enhanced_track: str = ""
    enhanced_item_count: int = 0
    runtime_note: str = ""
    report_status: str = "ready"
    report_error: str = ""
    fetch_scope: str = FETCH_SCOPE_DAY_FULL
    mode_notes: str = ""
    search_profile_label: str = ""
    search_queries: tuple[str, ...] = ()
    requested_date: date | None = None
    effective_date: date | None = None
    strict_same_day_fetched: int | None = None
    strict_same_day_ranked: int | None = None
    used_latest_available_fallback: bool = False
    strict_same_day_counts_known: bool = True
    stale_cache_source_requested_date: date | None = None
    stale_cache_source_effective_date: date | None = None

    @property
    def requested_target_date(self) -> date:
        return self.requested_date or self.target_date

    @property
    def effective_display_date(self) -> date:
        return self.effective_date or self.requested_target_date

    @property
    def strict_same_day_fetched_count(self) -> int:
        if self.strict_same_day_fetched is not None:
            return self.strict_same_day_fetched
        if not self.strict_same_day_counts_known:
            return 0
        return self.total_fetched

    @property
    def strict_same_day_ranked_count(self) -> int:
        if self.strict_same_day_ranked is not None:
            return self.strict_same_day_ranked
        if not self.strict_same_day_counts_known:
            return 0
        return len(self.ranked)

    @property
    def total_ranked_count(self) -> int:
        return len(self.ranked)

    @property
    def personalized_displayed_count(self) -> int:
        return min(self.total_ranked_count, 8) + len(self.exploration_picks)

    @property
    def frontier_displayed_count(self) -> int:
        if self.frontier_report is None:
            return 0
        return self.frontier_report.displayed_highlight_count

    @property
    def total_displayed_count(self) -> int:
        return self.personalized_displayed_count + self.frontier_displayed_count

    @property
    def displayed_fetched_count(self) -> int:
        return self.total_fetched

    @property
    def displayed_ranked_count(self) -> int:
        return self.total_ranked_count

    @property
    def selection_basis_label(self) -> str:
        if self.used_latest_available_fallback:
            return "Latest available fallback results"
        return "Strict same-day results"

    @property
    def stale_cache_fallback_used(self) -> bool:
        return self.stale_cache_source_requested_date is not None or self.stale_cache_source_effective_date is not None

    @property
    def zero_token(self) -> bool:
        return self.cost_mode == ZERO_TOKEN_COST_MODE

    @property
    def model_assisted(self) -> bool:
        return not self.zero_token

    @property
    def strict_same_day_fetched_label(self) -> str:
        if not self.strict_same_day_counts_known:
            return "unavailable"
        return str(self.strict_same_day_fetched_count)

    @property
    def strict_same_day_ranked_label(self) -> str:
        if not self.strict_same_day_counts_known:
            return "unavailable"
        return str(self.strict_same_day_ranked_count)

    @property
    def strict_same_day_counts_label(self) -> str:
        return f"{self.strict_same_day_fetched_label} / {self.strict_same_day_ranked_label}"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "category": self.category,
            "target_date": self.target_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "feed_url": self.feed_url,
            "profile": self.profile.to_mapping(),
            "ranked": [item.to_mapping() for item in self.ranked],
            "request_window": self.request_window.to_mapping(),
            "source_run_stats": [item.to_mapping() for item in self.source_run_stats],
            "run_timings": self.run_timings.to_mapping(),
            "exploration_picks": [item.to_mapping() for item in self.exploration_picks],
            "exploration_policy": (
                self.exploration_policy.to_mapping() if self.exploration_policy is not None else None
            ),
            "frontier_report": self.frontier_report.to_mapping() if self.frontier_report is not None else None,
            "searched_categories": list(self.searched_categories),
            "per_category_counts": dict(self.per_category_counts),
            "source_counts": dict(self.source_counts),
            "total_fetched": self.total_fetched,
            "feed_urls": dict(self.feed_urls),
            "source_endpoints": dict(self.source_endpoints),
            "source_metadata": _normalize_metadata_value(self.source_metadata),
            "mode_label": self.mode_label,
            "mode_kind": self.mode_kind,
            "requested_report_mode": self.requested_report_mode,
            "report_mode": self.report_mode,
            "cost_mode": self.cost_mode,
            "enhanced_track": self.enhanced_track,
            "enhanced_item_count": self.enhanced_item_count,
            "runtime_note": self.runtime_note,
            "report_status": self.report_status,
            "report_error": self.report_error,
            "fetch_scope": normalize_fetch_scope(self.fetch_scope),
            "mode_notes": self.mode_notes,
            "search_profile_label": self.search_profile_label,
            "search_queries": list(self.search_queries),
            "requested_date": self.requested_target_date.isoformat(),
            "effective_date": self.effective_display_date.isoformat(),
            "strict_same_day_fetched": (
                self.strict_same_day_fetched_count if self.strict_same_day_counts_known else None
            ),
            "strict_same_day_ranked": (
                self.strict_same_day_ranked_count if self.strict_same_day_counts_known else None
            ),
            "used_latest_available_fallback": self.used_latest_available_fallback,
            "strict_same_day_counts_known": self.strict_same_day_counts_known,
            "stale_cache_source_requested_date": (
                self.stale_cache_source_requested_date.isoformat()
                if self.stale_cache_source_requested_date is not None
                else None
            ),
            "stale_cache_source_effective_date": (
                self.stale_cache_source_effective_date.isoformat()
                if self.stale_cache_source_effective_date is not None
                else None
            ),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DailyDigest":
        target_date = _parse_date(payload.get("target_date"))
        if target_date is None:
            raise ValueError("daily digest target_date must be an ISO date")

        generated_at = _parse_datetime(payload.get("generated_at"))
        if generated_at is None:
            raise ValueError("daily digest generated_at must be an ISO datetime")

        profile_payload = payload.get("profile", {})
        if not isinstance(profile_payload, Mapping):
            profile_payload = {}

        ranked_payload = payload.get("ranked", ())
        if not isinstance(ranked_payload, list):
            ranked_payload = []
        ranked = [RankedPaper.from_mapping(item) for item in ranked_payload if isinstance(item, Mapping)]
        request_window_value = payload.get("request_window")
        request_window = (
            RequestWindow.from_mapping(request_window_value)
            if isinstance(request_window_value, Mapping)
            else RequestWindow(kind="day", requested_date=target_date)
        )
        source_run_stats_value = payload.get("source_run_stats", ())
        if not isinstance(source_run_stats_value, (list, tuple)):
            source_run_stats_value = ()
        run_timings_value = payload.get("run_timings")
        run_timings = (
            RunTimings.from_mapping(run_timings_value)
            if isinstance(run_timings_value, Mapping)
            else RunTimings()
        )

        exploration_payload = payload.get("exploration_picks", ())
        if not isinstance(exploration_payload, list):
            exploration_payload = []
        exploration_picks = [
            RankedPaper.from_mapping(item) for item in exploration_payload if isinstance(item, Mapping)
        ]

        exploration_policy_value = payload.get("exploration_policy")
        exploration_policy = None
        if isinstance(exploration_policy_value, Mapping):
            exploration_policy = ExplorationPolicy.from_mapping(exploration_policy_value)

        frontier_report_value = payload.get("frontier_report")
        frontier_report = None
        if isinstance(frontier_report_value, Mapping):
            frontier_report = DailyFrontierReport.from_mapping(frontier_report_value)

        category = str(payload.get("category", ""))

        searched_categories_value = payload.get("searched_categories")
        if isinstance(searched_categories_value, (list, tuple)):
            searched_categories = tuple(str(value) for value in searched_categories_value if str(value))
        elif category:
            searched_categories = (category,)
        else:
            searched_categories = ()

        total_fetched = _parse_int(payload.get("total_fetched"))
        if total_fetched is None:
            total_fetched = len(ranked)

        per_category_counts_value = payload.get("per_category_counts", {})
        per_category_counts: dict[str, int] = {}
        if isinstance(per_category_counts_value, Mapping):
            per_category_counts = {
                str(key): int(value)
                for key, value in per_category_counts_value.items()
                if str(key)
            }
        elif category:
            per_category_counts = {category: total_fetched}

        if not per_category_counts and category:
            per_category_counts = {category: total_fetched}

        source_counts_value = payload.get("source_counts", {})
        source_counts: dict[str, int] = {}
        if isinstance(source_counts_value, Mapping):
            source_counts = {
                str(key): int(value)
                for key, value in source_counts_value.items()
                if str(key)
            }

        feed_url = str(payload.get("feed_url", ""))
        feed_urls_value = payload.get("feed_urls", {})
        feed_urls: dict[str, str] = {}
        if isinstance(feed_urls_value, Mapping):
            feed_urls = {
                str(key): str(value)
                for key, value in feed_urls_value.items()
                if str(key) and str(value)
            }
        elif feed_url and category:
            feed_urls = {category: feed_url}

        if not feed_urls and feed_url and category:
            feed_urls = {category: feed_url}

        source_endpoints_value = payload.get("source_endpoints", {})
        source_endpoints: dict[str, str] = {}
        if isinstance(source_endpoints_value, Mapping):
            source_endpoints = {
                str(key): str(value)
                for key, value in source_endpoints_value.items()
                if str(key) and str(value)
            }

        source_metadata_value = payload.get("source_metadata", {})
        source_metadata: dict[str, dict[str, Any]] = {}
        if isinstance(source_metadata_value, Mapping):
            source_metadata = {
                str(key): _parse_metadata_mapping(value)
                for key, value in source_metadata_value.items()
                if str(key) and isinstance(value, Mapping)
            }

        search_queries_value = payload.get("search_queries", ())
        if isinstance(search_queries_value, (list, tuple)):
            search_queries = tuple(str(value) for value in search_queries_value if str(value))
        else:
            search_queries = ()

        requested_date = _parse_date(payload.get("requested_date")) or target_date
        effective_date = _parse_date(payload.get("effective_date")) or requested_date
        strict_same_day_fetched = _parse_int(payload.get("strict_same_day_fetched"))
        strict_same_day_ranked = _parse_int(payload.get("strict_same_day_ranked"))
        strict_same_day_counts_known = bool(payload.get("strict_same_day_counts_known", True))
        stale_cache_source_requested_date = _parse_date(payload.get("stale_cache_source_requested_date"))
        stale_cache_source_effective_date = _parse_date(payload.get("stale_cache_source_effective_date"))

        return cls(
            source=str(payload.get("source", "unknown")),
            category=category,
            target_date=target_date,
            generated_at=generated_at,
            feed_url=feed_url,
            profile=UserInterestProfile.from_mapping(profile_payload),
            ranked=ranked,
            request_window=request_window,
            source_run_stats=tuple(
                SourceRunStats.from_mapping(item)
                for item in source_run_stats_value
                if isinstance(item, Mapping)
            ),
            run_timings=run_timings,
            exploration_picks=exploration_picks,
            exploration_policy=exploration_policy,
            frontier_report=frontier_report,
            searched_categories=searched_categories,
            per_category_counts=per_category_counts,
            source_counts=source_counts,
            total_fetched=total_fetched,
            feed_urls=feed_urls,
            source_endpoints=source_endpoints,
            source_metadata=source_metadata,
            mode_label=str(payload.get("mode_label", category)),
            mode_kind=str(payload.get("mode_kind", "")),
            requested_report_mode=str(payload.get("requested_report_mode", DEFAULT_REPORT_MODE)),
            report_mode=str(payload.get("report_mode", DEFAULT_REPORT_MODE)),
            cost_mode=str(payload.get("cost_mode", ZERO_TOKEN_COST_MODE)),
            enhanced_track=str(payload.get("enhanced_track", "")),
            enhanced_item_count=_parse_int(payload.get("enhanced_item_count")) or 0,
            runtime_note=str(payload.get("runtime_note", "")),
            report_status=str(payload.get("report_status", "ready")),
            report_error=str(payload.get("report_error", "")),
            fetch_scope=normalize_fetch_scope(
                payload.get("fetch_scope"),
                default=FETCH_SCOPE_SHORTLIST,
            ),
            mode_notes=str(payload.get("mode_notes", "")),
            search_profile_label=str(payload.get("search_profile_label", "")),
            search_queries=search_queries,
            requested_date=requested_date,
            effective_date=effective_date,
            strict_same_day_fetched=strict_same_day_fetched,
            strict_same_day_ranked=strict_same_day_ranked,
            used_latest_available_fallback=bool(payload.get("used_latest_available_fallback", False)),
            strict_same_day_counts_known=strict_same_day_counts_known,
            stale_cache_source_requested_date=stale_cache_source_requested_date,
            stale_cache_source_effective_date=stale_cache_source_effective_date,
        )


@dataclass(slots=True, frozen=True)
class RunHistoryEntry:
    requested_date: date
    effective_date: date
    category: str
    mode_label: str
    mode_kind: str
    profile_basis: str
    fetch_status: str
    ranked_count: int
    generated_at: datetime
    request_window: RequestWindow = field(default_factory=RequestWindow)
    source_run_stats: tuple[SourceRunStats, ...] = ()
    source_counts: dict[str, int] = field(default_factory=dict)
    profile_source: str = ""
    requested_report_mode: str = DEFAULT_REPORT_MODE
    report_mode: str = DEFAULT_REPORT_MODE
    cost_mode: str = ZERO_TOKEN_COST_MODE
    enhanced_track: str = ""
    fetch_scope: str = FETCH_SCOPE_DAY_FULL
    report_status: str = "ready"
    run_timings: RunTimings = field(default_factory=RunTimings)
    total_fetched: int = 0
    total_displayed: int = 0
    frontier_report_present: bool | None = None
    report_artifact_aligned: bool | None = None
    same_date_cache_reused: bool = False
    stale_cache_fallback_used: bool = False
    zotero_export_name: str = ""
    zotero_db_name: str = ""
    profile_path: str = ""
    profile_item_count: int = 0
    profile_used_item_count: int = 0
    profile_terms: tuple[str, ...] = ()
    exploration_pick_count: int | None = None
    cache_path: str | None = None
    report_path: str | None = None
    eml_path: str | None = None

    @property
    def zotero_augmented(self) -> bool:
        profile_basis = self.profile_basis.strip().lower()
        return bool(self.zotero_export_name or self.zotero_db_name or "zotero" in profile_basis)

    @property
    def zero_token(self) -> bool:
        return self.cost_mode == ZERO_TOKEN_COST_MODE

    @property
    def profile_label(self) -> str:
        return self.profile_basis or profile_source_label(self.profile_source)

    @property
    def profile_path_name(self) -> str:
        if not self.profile_path:
            return ""
        return Path(self.profile_path).name

    def profile_summary_bits(self, term_limit: int = 3) -> tuple[str, ...]:
        bits: list[str] = []
        if self.profile_source:
            bits.append(self.profile_source)
        if self.profile_label:
            bits.append(self.profile_label)
        if self.profile_path_name:
            bits.append(f"profile {self.profile_path_name}")
        if self.profile_item_count or self.profile_used_item_count:
            bits.append(f"profile items {self.profile_item_count}/{self.profile_used_item_count}")
        if self.profile_terms:
            bits.append(f"profile terms {', '.join(self.profile_terms[:term_limit])}")
        return tuple(bits)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_known_seconds(*values: float | None) -> float | None:
    known_values = [value for value in values if value is not None]
    if not known_values:
        return None
    return float(sum(known_values))


def _parse_metadata_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _parse_metadata_value(item)
        for key, item in value.items()
        if str(key)
    }


def _parse_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _parse_metadata_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_parse_metadata_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _normalize_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_metadata_value(item)
            for key, item in value.items()
            if str(key)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_metadata_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _legacy_profile_basis(
    *,
    basis_label: str,
    zotero_export_name: str,
    zotero_db_name: str,
    zotero_item_count: int,
    zotero_used_item_count: int,
) -> ProfileBasis:
    if zotero_db_name:
        return ProfileBasis(
            source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
            label=basis_label or "live zotero db",
            path=zotero_db_name,
            item_count=zotero_item_count,
            used_item_count=zotero_used_item_count,
        )
    if zotero_export_name:
        return ProfileBasis(
            source=PROFILE_SOURCE_ZOTERO_EXPORT,
            label=basis_label or "zotero export",
            path=zotero_export_name,
            item_count=zotero_item_count,
            used_item_count=zotero_used_item_count,
        )
    return ProfileBasis(
        source=PROFILE_SOURCE_BASELINE,
        label=basis_label or "biomedical baseline",
        item_count=zotero_item_count,
        used_item_count=zotero_used_item_count,
    )
