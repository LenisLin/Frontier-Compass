from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from frontier_compass.common.source_bundles import (
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
    SOURCE_BUNDLE_BIOMEDICAL,
)
from frontier_compass.ingest.source_snapshots import DailySourceSnapshot
from frontier_compass.storage.schema import PaperRecord
from frontier_compass.ui import FrontierCompassApp


def test_app_lists_official_and_custom_source_bundles(tmp_path: Path) -> None:
    config_path = tmp_path / "configs" / "source_bundles.json"
    app = FrontierCompassApp(source_bundle_config_path=config_path)

    saved_bundle = app.save_custom_source_bundle(
        name="Protein discovery",
        enabled_sources=("arxiv", "biorxiv"),
        include_terms=("protein structure", "drug discovery"),
        exclude_terms=("economics",),
        description="Protein-and-therapeutics local preset.",
    )

    assert tuple(bundle.bundle_id for bundle in app.available_source_bundles()) == (
        SOURCE_BUNDLE_BIOMEDICAL,
        SOURCE_BUNDLE_AI_FOR_MEDICINE,
        saved_bundle.bundle_id,
    )
    assert tuple(bundle.bundle_id for bundle in app.custom_source_bundles()) == (saved_bundle.bundle_id,)

    resolved_bundle = app.resolve_source_bundle(saved_bundle.bundle_id)
    assert resolved_bundle is not None
    assert resolved_bundle.label == "Protein discovery"
    assert resolved_bundle.enabled_sources == ("arxiv", "biorxiv")
    assert resolved_bundle.include_terms == ("protein structure", "drug discovery")
    assert resolved_bundle.exclude_terms == ("economics",)

    app.remove_custom_source_bundle(saved_bundle.bundle_id)

    assert tuple(bundle.bundle_id for bundle in app.available_source_bundles()) == (
        SOURCE_BUNDLE_BIOMEDICAL,
        SOURCE_BUNDLE_AI_FOR_MEDICINE,
    )
    assert app.resolve_source_bundle(saved_bundle.bundle_id) is None


def test_source_bundle_digest_reuses_same_day_snapshots_across_bundle_switches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "data" / "raw" / "source_snapshots"
    app = FrontierCompassApp(source_snapshot_root=snapshot_root)
    fetch_calls: list[tuple[str, date]] = []

    def fake_fetch_source_snapshot(self, *, source: str, target_date: date):  # type: ignore[no-untyped-def]
        fetch_calls.append((source, target_date))
        paper = PaperRecord(
            source=source,
            identifier=f"{source}-2603.25001v1",
            title="Clinical foundation model for pathology",
            summary="Medical imaging, patient cohorts, and genomics for translational medicine.",
            authors=("A Researcher",),
            categories=("q-bio.GN", "cs.LG"),
            published=target_date,
            url=f"https://example.com/{source}",
        )
        return DailySourceSnapshot(
            source=source,
            requested_date=target_date,
            generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
            endpoint=f"https://example.com/{source}",
            papers=(paper,),
            fetched_count=1,
            status="ready",
            note=f"{source} snapshot fixture.",
            network_seconds=0.2,
            parse_seconds=0.05,
        )

    monkeypatch.setattr(FrontierCompassApp, "_fetch_source_snapshot", fake_fetch_source_snapshot)

    biomedical = app.resolve_source_bundle(SOURCE_BUNDLE_BIOMEDICAL)
    ai_for_medicine = app.resolve_source_bundle(SOURCE_BUNDLE_AI_FOR_MEDICINE)
    assert biomedical is not None
    assert ai_for_medicine is not None

    first_digest = app._build_source_bundle_digest(
        bundle=biomedical,
        target_date=date(2026, 3, 24),
        max_results=20,
        report_mode="deterministic",
        fetch_scope="day-full",
        profile_source="baseline",
        zotero_export_path=None,
        zotero_db_path=None,
        zotero_collections=(),
        refresh_sources=False,
    )
    second_digest = app._build_source_bundle_digest(
        bundle=ai_for_medicine,
        target_date=date(2026, 3, 24),
        max_results=20,
        report_mode="deterministic",
        fetch_scope="day-full",
        profile_source="baseline",
        zotero_export_path=None,
        zotero_db_path=None,
        zotero_collections=(),
        refresh_sources=False,
    )

    assert fetch_calls == [
        ("arxiv", date(2026, 3, 24)),
        ("biorxiv", date(2026, 3, 24)),
        ("medrxiv", date(2026, 3, 24)),
    ]
    assert tuple(sorted(path.name for path in (snapshot_root / "2026-03-24").iterdir())) == (
        "arxiv.json",
        "biorxiv.json",
        "medrxiv.json",
    )
    assert first_digest.category == SOURCE_BUNDLE_BIOMEDICAL
    assert second_digest.category == SOURCE_BUNDLE_AI_FOR_MEDICINE
    assert second_digest.mode_kind == "source-bundle"
    assert {row.source: row.cache_status for row in second_digest.source_run_stats} == {
        "arxiv": "same-day-cache",
        "biorxiv": "same-day-cache",
        "medrxiv": "same-day-cache",
    }
