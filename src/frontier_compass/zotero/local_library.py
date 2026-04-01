"""Local Zotero discovery and reusable export helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from frontier_compass.zotero.export_loader import ZoteroExportItem, load_csl_json_export
from frontier_compass.zotero.sqlite_loader import load_sqlite_library, validate_sqlite_library


DEFAULT_ZOTERO_EXPORT_PATH = Path("data/raw/zotero/library.csl.json")
DEFAULT_ZOTERO_STATUS_PATH = Path("data/raw/zotero/library_status.json")


@dataclass(slots=True, frozen=True)
class ZoteroLibraryState:
    export_path: Path
    status_path: Path
    discovered_db_path: Path | None
    collections: tuple[str, ...]
    item_count: int
    generated_at: datetime | None = None
    status: str = "missing"
    error: str = ""
    note: str = ""
    candidate_db_paths: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.export_path.exists()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "export_path": str(self.export_path),
            "status_path": str(self.status_path),
            "discovered_db_path": str(self.discovered_db_path) if self.discovered_db_path is not None else "",
            "collections": list(self.collections),
            "item_count": self.item_count,
            "generated_at": self.generated_at.isoformat() if self.generated_at is not None else "",
            "status": self.status,
            "error": self.error,
            "note": self.note,
            "candidate_db_paths": list(self.candidate_db_paths),
        }


def ensure_local_zotero_export(
    *,
    export_path: str | Path = DEFAULT_ZOTERO_EXPORT_PATH,
    status_path: str | Path = DEFAULT_ZOTERO_STATUS_PATH,
    db_path: str | Path | None = None,
    refresh: bool = False,
) -> ZoteroLibraryState:
    resolved_export_path = Path(export_path)
    resolved_status_path = Path(status_path)
    existing_state = read_local_zotero_state(
        export_path=resolved_export_path,
        status_path=resolved_status_path,
    )
    discovered_db_path, discovery_error, candidate_paths = discover_local_zotero_db_details(db_path=db_path)
    candidate_text = tuple(str(path) for path in candidate_paths)

    if existing_state.ready and not refresh:
        state = ZoteroLibraryState(
            export_path=resolved_export_path,
            status_path=resolved_status_path,
            discovered_db_path=discovered_db_path or existing_state.discovered_db_path,
            collections=existing_state.collections,
            item_count=existing_state.item_count,
            generated_at=existing_state.generated_at,
            status="ready",
            error=existing_state.error or discovery_error,
            note=existing_state.note or "Reusing saved Zotero export snapshot.",
            candidate_db_paths=candidate_text,
        )
        write_local_zotero_state(state)
        return state

    if discovered_db_path is None:
        state = ZoteroLibraryState(
            export_path=resolved_export_path,
            status_path=resolved_status_path,
            discovered_db_path=None,
            collections=existing_state.collections,
            item_count=existing_state.item_count,
            generated_at=existing_state.generated_at,
            status="ready" if existing_state.ready else "missing",
            error=discovery_error,
            note=(
                "Reusing saved Zotero export snapshot because no readable local library was discovered."
                if existing_state.ready
                else "No readable local Zotero library was discovered yet."
            ),
            candidate_db_paths=candidate_text,
        )
        write_local_zotero_state(state)
        return state

    try:
        items = load_sqlite_library(discovered_db_path)
    except ValueError as exc:
        state = ZoteroLibraryState(
            export_path=resolved_export_path,
            status_path=resolved_status_path,
            discovered_db_path=discovered_db_path,
            collections=existing_state.collections,
            item_count=existing_state.item_count,
            generated_at=existing_state.generated_at,
            status="ready" if existing_state.ready else "error",
            error=str(exc),
            note=(
                "Reusing saved Zotero export snapshot because the local library could not be refreshed."
                if existing_state.ready
                else "Unable to refresh the local Zotero export snapshot."
            ),
            candidate_db_paths=candidate_text,
        )
        write_local_zotero_state(state)
        return state

    write_zotero_export_snapshot(items, output_path=resolved_export_path)
    state = ZoteroLibraryState(
        export_path=resolved_export_path,
        status_path=resolved_status_path,
        discovered_db_path=discovered_db_path,
        collections=available_collections(items),
        item_count=len(items),
        generated_at=datetime.now(timezone.utc),
        status="ready",
        error="",
        note="Local Zotero library exported to a reusable CSL JSON snapshot.",
        candidate_db_paths=candidate_text,
    )
    write_local_zotero_state(state)
    return state


def read_local_zotero_state(
    *,
    export_path: str | Path = DEFAULT_ZOTERO_EXPORT_PATH,
    status_path: str | Path = DEFAULT_ZOTERO_STATUS_PATH,
) -> ZoteroLibraryState:
    resolved_export_path = Path(export_path)
    resolved_status_path = Path(status_path)
    candidate_db_paths = tuple(str(path) for path in candidate_zotero_db_paths())
    if resolved_status_path.exists():
        try:
            payload = json.loads(resolved_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, Mapping):
            generated_at_text = str(payload.get("generated_at", "")).strip()
            generated_at = datetime.fromisoformat(generated_at_text) if generated_at_text else None
            collections = tuple(
                str(item).strip()
                for item in payload.get("collections", ())
                if str(item).strip()
            )
            discovered_db_text = str(payload.get("discovered_db_path", "")).strip()
            candidate_payload = payload.get("candidate_db_paths", ())
            return ZoteroLibraryState(
                export_path=resolved_export_path,
                status_path=resolved_status_path,
                discovered_db_path=Path(discovered_db_text) if discovered_db_text else None,
                collections=collections,
                item_count=int(payload.get("item_count", 0)),
                generated_at=generated_at,
                status=str(payload.get("status", "ready" if resolved_export_path.exists() else "missing")),
                error=str(payload.get("error", "")),
                note=str(payload.get("note", "")),
                candidate_db_paths=tuple(
                    str(item).strip()
                    for item in candidate_payload
                    if str(item).strip()
                ) or candidate_db_paths,
            )
    items = load_csl_json_export(resolved_export_path) if resolved_export_path.exists() else ()
    return ZoteroLibraryState(
        export_path=resolved_export_path,
        status_path=resolved_status_path,
        discovered_db_path=None,
        collections=available_collections(items),
        item_count=len(items),
        generated_at=None,
        status="ready" if resolved_export_path.exists() else "missing",
        error="",
        note="Reusing saved Zotero export snapshot." if resolved_export_path.exists() else "",
        candidate_db_paths=candidate_db_paths,
    )


def write_local_zotero_state(state: ZoteroLibraryState) -> Path:
    state.status_path.parent.mkdir(parents=True, exist_ok=True)
    state.status_path.write_text(json.dumps(state.to_mapping(), indent=2), encoding="utf-8")
    return state.status_path


def discover_local_zotero_db(*, db_path: str | Path | None = None) -> Path | None:
    discovered_db_path, _error, _candidate_paths = discover_local_zotero_db_details(db_path=db_path)
    return discovered_db_path


def discover_local_zotero_db_details(
    *,
    db_path: str | Path | None = None,
) -> tuple[Path | None, str, tuple[Path, ...]]:
    candidates = _candidate_paths_for(db_path)
    latest_error = ""
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            validate_sqlite_library(candidate)
        except ValueError as exc:
            latest_error = str(exc)
            continue
        return candidate, "", candidates
    if db_path is not None:
        explicit_path = Path(db_path)
        if explicit_path.exists():
            return None, latest_error or f"Unable to read Zotero database {explicit_path}", candidates
        return None, f"Zotero database not found: {explicit_path}", candidates
    return None, latest_error or "No local Zotero library was discovered.", candidates


def candidate_zotero_db_paths() -> tuple[Path, ...]:
    home = Path.home()
    candidates = [
        home / "Zotero" / "zotero.sqlite",
        home / ".zotero" / "zotero" / "zotero.sqlite",
        home / "Library" / "Application Support" / "Zotero" / "zotero.sqlite",
    ]
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / "Zotero" / "zotero.sqlite")
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "Zotero" / "zotero.sqlite")
    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved in seen:
            continue
        ordered.append(resolved)
        seen.add(resolved)
    return tuple(ordered)


def write_zotero_export_snapshot(
    items: Sequence[ZoteroExportItem],
    *,
    output_path: str | Path = DEFAULT_ZOTERO_EXPORT_PATH,
) -> Path:
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "title": item.title,
            "abstractNote": item.abstract,
            "keywords": list(item.keywords),
            "collections": list(item.collections),
            "dateAdded": item.date_added.isoformat() if item.date_added is not None else None,
        }
        for item in items
    ]
    resolved_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return resolved_output_path


def available_collections(items: Sequence[ZoteroExportItem]) -> tuple[str, ...]:
    collections: list[str] = []
    seen: set[str] = set()
    for item in items:
        for collection in item.collections:
            normalized = str(collection).strip()
            canonical = normalized.lower()
            if not normalized or canonical in seen:
                continue
            collections.append(normalized)
            seen.add(canonical)
    return tuple(collections)


def filter_items_by_collections(
    items: Sequence[ZoteroExportItem],
    collections: Sequence[str] | None,
) -> tuple[ZoteroExportItem, ...]:
    if not collections:
        return tuple(items)
    requested = {str(item).strip().lower() for item in collections if str(item).strip()}
    if not requested:
        return tuple(items)
    return tuple(
        item
        for item in items
        if any(collection.strip().lower() in requested for collection in item.collections)
    )


def _candidate_paths_for(db_path: str | Path | None) -> tuple[Path, ...]:
    if db_path is None:
        return candidate_zotero_db_paths()
    explicit_path = Path(db_path).expanduser()
    combined: list[Path] = [explicit_path]
    for candidate in candidate_zotero_db_paths():
        if candidate == explicit_path:
            continue
        combined.append(candidate)
    return tuple(combined)
