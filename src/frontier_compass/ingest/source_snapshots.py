"""Daily local source snapshots for bundle-driven scouting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from frontier_compass.storage.schema import PaperRecord


DEFAULT_SOURCE_SNAPSHOT_DIR = Path("data/raw/source_snapshots")


@dataclass(slots=True, frozen=True)
class DailySourceSnapshot:
    source: str
    requested_date: date
    generated_at: datetime
    endpoint: str = ""
    papers: tuple[PaperRecord, ...] = ()
    fetched_count: int = 0
    status: str = "ready"
    error: str = ""
    note: str = ""
    network_seconds: float | None = None
    parse_seconds: float | None = None
    metadata: dict[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "requested_date": self.requested_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "endpoint": self.endpoint,
            "papers": [paper.to_mapping() for paper in self.papers],
            "fetched_count": self.fetched_count,
            "status": self.status,
            "error": self.error,
            "note": self.note,
            "network_seconds": self.network_seconds,
            "parse_seconds": self.parse_seconds,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DailySourceSnapshot":
        requested_date = date.fromisoformat(str(payload.get("requested_date", "")))
        generated_at = datetime.fromisoformat(str(payload.get("generated_at", "")))
        papers_payload = payload.get("papers", ())
        if not isinstance(papers_payload, list):
            papers_payload = []
        metadata_payload = payload.get("metadata")
        metadata = dict(metadata_payload) if isinstance(metadata_payload, Mapping) else {}
        return cls(
            source=str(payload.get("source", "unknown")),
            requested_date=requested_date,
            generated_at=generated_at,
            endpoint=str(payload.get("endpoint", "")),
            papers=tuple(
                PaperRecord.from_mapping(item)
                for item in papers_payload
                if isinstance(item, Mapping)
            ),
            fetched_count=int(payload.get("fetched_count", 0)),
            status=str(payload.get("status", "ready")),
            error=str(payload.get("error", "")),
            note=str(payload.get("note", "")),
            network_seconds=_parse_optional_float(payload.get("network_seconds")),
            parse_seconds=_parse_optional_float(payload.get("parse_seconds")),
            metadata=metadata,
        )


def source_snapshot_path(
    requested_date: date,
    source: str,
    *,
    snapshot_root: str | Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> Path:
    normalized_source = str(source or "unknown").strip().lower() or "unknown"
    return Path(snapshot_root) / requested_date.isoformat() / f"{normalized_source}.json"


def load_daily_source_snapshot(
    requested_date: date,
    source: str,
    *,
    snapshot_root: str | Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> DailySourceSnapshot | None:
    path = source_snapshot_path(requested_date, source, snapshot_root=snapshot_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    try:
        snapshot = DailySourceSnapshot.from_mapping(payload)
    except (TypeError, ValueError):
        return None
    if snapshot.requested_date != requested_date:
        return None
    return snapshot


def write_daily_source_snapshot(
    snapshot: DailySourceSnapshot,
    *,
    snapshot_root: str | Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> Path:
    path = source_snapshot_path(snapshot.requested_date, snapshot.source, snapshot_root=snapshot_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_mapping(), indent=2), encoding="utf-8")
    return path


def load_day_snapshots(
    requested_date: date,
    *,
    snapshot_root: str | Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
    expected_sources: Sequence[str],
) -> tuple[DailySourceSnapshot, ...]:
    loaded: list[DailySourceSnapshot] = []
    for source in expected_sources:
        snapshot = load_daily_source_snapshot(requested_date, source, snapshot_root=snapshot_root)
        if snapshot is not None:
            loaded.append(snapshot)
    return tuple(loaded)


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
