"""Helpers for reading local exported Zotero library files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


KEYWORD_SPLIT_RE = re.compile(r"[;\n|,]+")


@dataclass(slots=True, frozen=True)
class ZoteroExportItem:
    title: str
    abstract: str = ""
    keywords: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    date_added: date | None = None

    def normalized_text(self) -> str:
        return " ".join(
            part.strip().lower()
            for part in (self.title, self.abstract, " ".join(self.keywords), " ".join(self.collections))
            if part
        ).strip()


def load_csl_json_export(path: str | Path) -> tuple[ZoteroExportItem, ...]:
    export_path = Path(path)
    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Zotero export not found: {export_path}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to read Zotero export {export_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Zotero CSL JSON export {export_path}: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError(
            f"Unsupported Zotero export format in {export_path}: expected a top-level CSL JSON item array."
        )

    items: list[ZoteroExportItem] = []
    for raw_item in payload:
        if not isinstance(raw_item, Mapping):
            continue
        title = str(raw_item.get("title", "")).strip()
        abstract = str(raw_item.get("abstract") or raw_item.get("abstractNote") or "").strip()
        keywords = tuple(_iter_keywords(raw_item))
        collections = tuple(_coerce_keyword_values(raw_item.get("collections")))
        date_added = _parse_item_date(raw_item.get("dateAdded") or raw_item.get("date-added"))
        if not title and not abstract and not keywords and not collections:
            continue
        if not title:
            continue
        items.append(
            ZoteroExportItem(
                title=title,
                abstract=abstract,
                keywords=keywords,
                collections=collections,
                date_added=date_added,
            )
        )
    return tuple(items)


def _iter_keywords(item: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("keyword", "keywords", "tags"):
        values.extend(_coerce_keyword_values(item.get(field)))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        canonical = normalized.lower()
        if not normalized or canonical in seen:
            continue
        deduped.append(normalized)
        seen.add(canonical)
    return deduped


def _coerce_keyword_values(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return _split_keyword_text(raw_value)
    if isinstance(raw_value, Mapping):
        return _coerce_keyword_values(
            raw_value.get("tag") or raw_value.get("name") or raw_value.get("literal")
        )
    if isinstance(raw_value, Iterable) and not isinstance(raw_value, (str, bytes)):
        values: list[str] = []
        for value in raw_value:
            values.extend(_coerce_keyword_values(value))
        return values

    text = str(raw_value).strip()
    return [text] if text else []


def _split_keyword_text(text: str) -> list[str]:
    parts = [part.strip() for part in KEYWORD_SPLIT_RE.split(text) if part.strip()]
    if parts:
        return parts
    cleaned = text.strip()
    return [cleaned] if cleaned else []


def _parse_item_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:10]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
