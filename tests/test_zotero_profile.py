from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from frontier_compass.storage.schema import RunHistoryEntry
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.ui.app import build_profile_inspector_lines
from frontier_compass.ui.history import build_history_summary_bits
from frontier_compass.zotero.local_library import ensure_local_zotero_export
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder
from frontier_compass.zotero.sqlite_loader import load_sqlite_library


def test_load_sqlite_library_and_build_live_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    items = load_sqlite_library(db_path)

    assert len(items) == 1
    assert items[0].title == "Spatial Transcriptomics Atlas"
    assert items[0].abstract == "Digital pathology and tumor microenvironment analysis."
    assert items[0].keywords == ("digital pathology", "spatial transcriptomics")
    assert items[0].collections == ("Tumor microenvironment",)
    assert items[0].date_added == date(2026, 3, 25)

    profile = ZoteroProfileBuilder().build_augmented_profile_from_db(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        db_path=db_path,
    )

    assert profile.profile_source == "live_zotero_db"
    assert profile.basis_label == "biomedical baseline + live Zotero DB"
    assert profile.zotero_db_name == db_path.name
    assert profile.profile_basis is not None
    assert profile.profile_basis.path == str(db_path)
    assert profile.profile_basis.item_count == 1
    assert "live local Zotero DB" in profile.notes
    assert "transcriptomics" in profile.zotero_keywords
    assert any("spatial transcriptomics" in hint.terms for hint in profile.zotero_retrieval_hints)


def test_load_sqlite_library_reports_missing_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "broken.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, dateAdded TEXT, itemTypeID INTEGER)")
        connection.execute("CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT)")
        connection.execute("CREATE TABLE deletedItems (itemID INTEGER)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="missing required tables"):
        load_sqlite_library(db_path)


def test_load_sqlite_library_reports_missing_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"

    with pytest.raises(ValueError, match="Zotero database not found"):
        load_sqlite_library(db_path)


def test_load_sqlite_library_reports_locked_database(tmp_path: Path) -> None:
    db_path = tmp_path / "locked.sqlite"
    _write_minimal_zotero_db(db_path)

    locking_connection = sqlite3.connect(db_path, timeout=0.0)
    try:
        locking_connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        locking_connection.execute("BEGIN EXCLUSIVE")

        with pytest.raises(ValueError, match="Unable to read Zotero database|Unable to open Zotero database"):
            load_sqlite_library(db_path)
    finally:
        locking_connection.rollback()
        locking_connection.close()


def test_load_sqlite_library_reports_malformed_database(tmp_path: Path) -> None:
    db_path = tmp_path / "malformed.sqlite"
    db_path.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(ValueError, match="Unable to read Zotero database|Unsupported Zotero SQLite database"):
        load_sqlite_library(db_path)


def test_ensure_local_zotero_export_discovers_standard_library_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_root = tmp_path / "Zotero"
    db_root.mkdir(parents=True)
    db_path = db_root / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    export_path = tmp_path / "data" / "raw" / "zotero" / "library.csl.json"
    status_path = tmp_path / "data" / "raw" / "zotero" / "library_status.json"
    state = ensure_local_zotero_export(export_path=export_path, status_path=status_path)

    assert state.ready is True
    assert state.discovered_db_path == db_path
    assert state.collections == ("Tumor microenvironment",)
    assert state.item_count == 1
    assert export_path.exists()
    assert status_path.exists()


def test_resolve_daily_profile_explicit_baseline_does_not_use_available_zotero_paths(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    baseline = FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE)
    export_path = tmp_path / "library.csl.json"
    db_path = tmp_path / "zotero.sqlite"
    export_path.write_text("[]", encoding="utf-8")
    _write_minimal_zotero_db(db_path)

    class _SpyBuilder:
        def build_augmented_profile_from_items(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(f"item builder should not run for explicit baseline: {args!r} {kwargs!r}")

    app.profile_builder = _SpyBuilder()  # type: ignore[assignment]

    resolved = app._resolve_daily_profile(
        BIOMEDICAL_LATEST_MODE,
        profile_source="baseline",
        zotero_export_path=export_path,
        zotero_db_path=db_path,
    )

    assert resolved.profile_source == baseline.profile_source
    assert resolved.keywords == baseline.keywords


def test_resolve_daily_profile_explicit_zotero_export_builds_export_backed_profile(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    resolved = app._resolve_daily_profile(
        BIOMEDICAL_LATEST_MODE,
        profile_source="zotero_export",
        zotero_db_path=db_path,
    )

    export_path = tmp_path / "data" / "raw" / "zotero" / "library.csl.json"
    assert export_path.exists()
    assert resolved.profile_source == "zotero_export"
    assert resolved.basis_label == "biomedical baseline + Zotero export"
    assert resolved.zotero_export_name == export_path.name
    assert resolved.zotero_db_name == ""
    assert resolved.profile_basis is not None
    assert resolved.profile_basis.path == str(export_path.resolve())
    assert resolved.profile_basis.item_count == 1
    assert resolved.profile_basis.used_item_count == 1
    assert resolved.zotero_used_item_count == 1
    assert "Read-only CSL JSON Zotero profile source." == resolved.profile_basis.description


def test_resolve_daily_profile_derived_live_zotero_db_from_db_path(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    resolved = app._resolve_daily_profile(
        BIOMEDICAL_LATEST_MODE,
        zotero_db_path=db_path,
    )

    assert resolved.profile_source == "live_zotero_db"
    assert resolved.basis_label == "biomedical baseline + live Zotero DB"
    assert resolved.profile_basis is not None
    assert resolved.profile_basis.path == str(db_path.resolve())
    assert resolved.zotero_db_name == db_path.name
    assert resolved.zotero_export_name == ""
    assert app.zotero_export_path.exists() is False


def test_resolve_daily_profile_filters_export_by_selected_collections(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    resolved = app._resolve_daily_profile(
        BIOMEDICAL_LATEST_MODE,
        profile_source="zotero_export",
        zotero_db_path=db_path,
        zotero_collections=("Tumor microenvironment",),
    )

    assert resolved.profile_source == "zotero_export"
    assert resolved.zotero_selected_collections == ("Tumor microenvironment",)
    assert resolved.profile_basis is not None
    assert resolved.profile_basis.used_item_count == 1
    assert "Selected collections: Tumor microenvironment." in resolved.notes


def test_resolve_daily_profile_legacy_zotero_alias_maps_to_export_contract(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    resolved = app._resolve_daily_profile(
        BIOMEDICAL_LATEST_MODE,
        profile_source="zotero",
        zotero_db_path=db_path,
    )

    assert resolved.profile_source == "zotero_export"
    assert resolved.basis_label == "biomedical baseline + Zotero export"
    assert resolved.zotero_export_name == app.zotero_export_path.name
    assert resolved.profile_basis is not None
    assert resolved.profile_basis.path == str(app.zotero_export_path.resolve())


def test_resolve_daily_profile_explicit_live_zotero_db_does_not_fallback_to_existing_export(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    healthy_db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(healthy_db_path)
    app.zotero_library_state(refresh=True, db_path=healthy_db_path)

    missing_db_path = tmp_path / "missing.sqlite"

    with pytest.raises(ValueError, match="Zotero database not found|Unable to read Zotero database"):
        app._resolve_daily_profile(
            BIOMEDICAL_LATEST_MODE,
            profile_source="live_zotero_db",
            zotero_export_path=app.zotero_export_path,
            zotero_db_path=missing_db_path,
        )


def test_resolve_daily_profile_rejects_ambiguous_derived_zotero_inputs(tmp_path: Path) -> None:
    app = _app_with_local_paths(tmp_path)
    db_path = tmp_path / "zotero.sqlite"
    export_path = tmp_path / "custom-library.csl.json"
    _write_minimal_zotero_db(db_path)
    export_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="Both a Zotero export path and a Zotero DB path were supplied"):
        app._resolve_daily_profile(
            BIOMEDICAL_LATEST_MODE,
            zotero_export_path=export_path,
            zotero_db_path=db_path,
        )


def test_resolve_daily_profile_rejects_unknown_profile_source() -> None:
    with pytest.raises(ValueError, match="Unsupported profile_source"):
        FrontierCompassApp()._resolve_daily_profile(
            BIOMEDICAL_LATEST_MODE,
            profile_source="mystery_source",
        )


def test_live_zotero_profile_inspector_lines_include_contract_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)
    profile = ZoteroProfileBuilder().build_augmented_profile_from_db(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        db_path=db_path,
    )

    lines = build_profile_inspector_lines(profile)

    assert any("Profile label: Live Zotero DB" in line for line in lines)
    assert any("Profile source: live_zotero_db (Live Zotero DB)" in line for line in lines)
    assert any(f"Profile path: {db_path}" in line for line in lines)
    assert any("Profile items parsed / used: 1 / 1" in line for line in lines)
    assert any("Top profile terms:" in line for line in lines)


def test_history_summary_bits_include_live_zotero_profile_contract() -> None:
    entry = RunHistoryEntry(
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        category=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        profile_basis="biomedical baseline + live Zotero DB",
        profile_source="live_zotero_db",
        profile_path="/tmp/zotero.sqlite",
        profile_item_count=9,
        profile_used_item_count=5,
        profile_terms=("spatial transcriptomics", "digital pathology"),
        zotero_db_name="zotero.sqlite",
        fetch_status="fresh source fetch",
        ranked_count=12,
        generated_at=datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc),
    )

    summary_bits = build_history_summary_bits(entry)

    assert "live_zotero_db" in summary_bits
    assert "profile zotero.sqlite" in summary_bits
    assert "profile items 9/5" in summary_bits
    assert any(bit.startswith("profile terms spatial transcriptomics") for bit in summary_bits)


def _app_with_local_paths(tmp_path: Path) -> FrontierCompassApp:
    return FrontierCompassApp(
        zotero_export_path=tmp_path / "data" / "raw" / "zotero" / "library.csl.json",
        zotero_status_path=tmp_path / "data" / "raw" / "zotero" / "library_status.json",
    )


def _write_minimal_zotero_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY,
                dateAdded TEXT,
                itemTypeID INTEGER
            );
            CREATE TABLE deletedItems (
                itemID INTEGER
            );
            CREATE TABLE itemTypes (
                itemTypeID INTEGER PRIMARY KEY,
                typeName TEXT
            );
            CREATE TABLE fields (
                fieldID INTEGER PRIMARY KEY,
                fieldName TEXT
            );
            CREATE TABLE itemData (
                itemID INTEGER,
                fieldID INTEGER,
                valueID INTEGER
            );
            CREATE TABLE itemDataValues (
                valueID INTEGER PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE tags (
                tagID INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE itemTags (
                itemID INTEGER,
                tagID INTEGER
            );
            CREATE TABLE collections (
                collectionID INTEGER PRIMARY KEY,
                collectionName TEXT
            );
            CREATE TABLE collectionItems (
                collectionID INTEGER,
                itemID INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO itemTypes(itemTypeID, typeName) VALUES (?, ?)",
            [
                (1, "journalArticle"),
                (2, "attachment"),
            ],
        )
        connection.executemany(
            "INSERT INTO fields(fieldID, fieldName) VALUES (?, ?)",
            [
                (1, "title"),
                (2, "abstractNote"),
            ],
        )
        connection.executemany(
            "INSERT INTO itemDataValues(valueID, value) VALUES (?, ?)",
            [
                (1, "Spatial Transcriptomics Atlas"),
                (2, "Digital pathology and tumor microenvironment analysis."),
            ],
        )
        connection.execute(
            "INSERT INTO items(itemID, dateAdded, itemTypeID) VALUES (?, ?, ?)",
            (1, "2026-03-25 10:30:00", 1),
        )
        connection.executemany(
            "INSERT INTO itemData(itemID, fieldID, valueID) VALUES (?, ?, ?)",
            [
                (1, 1, 1),
                (1, 2, 2),
            ],
        )
        connection.executemany(
            "INSERT INTO tags(tagID, name) VALUES (?, ?)",
            [
                (1, "spatial transcriptomics"),
                (2, "digital pathology"),
            ],
        )
        connection.executemany(
            "INSERT INTO itemTags(itemID, tagID) VALUES (?, ?)",
            [
                (1, 1),
                (1, 2),
            ],
        )
        connection.execute(
            "INSERT INTO collections(collectionID, collectionName) VALUES (?, ?)",
            (1, "Tumor microenvironment"),
        )
        connection.execute(
            "INSERT INTO collectionItems(collectionID, itemID) VALUES (?, ?)",
            (1, 1),
        )
        connection.commit()
    finally:
        connection.close()
