"""Read-only helpers for loading data from a local Zotero SQLite library."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from frontier_compass.zotero.export_loader import ZoteroExportItem


IGNORED_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})


def validate_sqlite_library(path: str | Path) -> Path:
    db_path = Path(path)
    if not db_path.exists():
        raise ValueError(f"Zotero database not found: {db_path}")

    connection = _open_read_only_connection(db_path)
    try:
        _validate_required_tables(connection)
    except sqlite3.Error as exc:
        raise ValueError(f"Unable to read Zotero database {db_path}: {exc}") from exc
    finally:
        connection.close()
    return db_path


def load_sqlite_library(path: str | Path) -> tuple[ZoteroExportItem, ...]:
    db_path = validate_sqlite_library(path)
    connection = _open_read_only_connection(db_path)
    try:
        field_table = "fieldsCombined" if _table_exists(connection, "fieldsCombined") else "fields"
        items: list[ZoteroExportItem] = []
        for item_id, date_added, item_type in connection.execute(
            """
            SELECT items.itemID, COALESCE(items.dateAdded, ''), COALESCE(itemTypes.typeName, '')
            FROM items
            LEFT JOIN deletedItems ON deletedItems.itemID = items.itemID
            LEFT JOIN itemTypes ON itemTypes.itemTypeID = items.itemTypeID
            WHERE deletedItems.itemID IS NULL
            ORDER BY COALESCE(items.dateAdded, '') DESC, items.itemID DESC
            """
        ):
            normalized_item_type = str(item_type or "").strip().lower()
            if normalized_item_type in IGNORED_ITEM_TYPES:
                continue
            fields = _load_item_fields(connection, item_id=item_id, field_table=field_table)
            tags = _load_item_tags(connection, item_id=item_id)
            collections = _load_item_collections(connection, item_id=item_id)
            title = str(fields.get("title", "")).strip()
            abstract = str(fields.get("abstractNote", "")).strip()
            if not title and not abstract and not tags and not collections:
                continue
            if not title:
                title = str(fields.get("shortTitle", "")).strip() or f"Zotero item {item_id}"
            items.append(
                ZoteroExportItem(
                    title=title,
                    abstract=abstract,
                    keywords=tags,
                    collections=collections,
                    date_added=_parse_sqlite_date(date_added),
                )
            )
        return tuple(items)
    except sqlite3.Error as exc:
        raise ValueError(f"Unable to read Zotero database {db_path}: {exc}") from exc
    finally:
        connection.close()


def _open_read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.0)
    except sqlite3.Error as exc:
        raise ValueError(f"Unable to open Zotero database {path}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 0")
    except sqlite3.Error as exc:
        connection.close()
        raise ValueError(f"Unable to prepare read-only Zotero database {path}: {exc}") from exc
    return connection


def _validate_required_tables(connection: sqlite3.Connection) -> None:
    required_tables = {
        "deletedItems",
        "itemData",
        "itemDataValues",
        "itemTags",
        "itemTypes",
        "items",
        "tags",
    }
    missing = sorted(table for table in required_tables if not _table_exists(connection, table))
    if not _table_exists(connection, "fieldsCombined") and not _table_exists(connection, "fields"):
        missing.append("fields or fieldsCombined")
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(
            "Unsupported Zotero SQLite database: missing required tables "
            f"{missing_text}."
        )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _load_item_fields(
    connection: sqlite3.Connection,
    *,
    item_id: int,
    field_table: str,
) -> dict[str, str]:
    rows = connection.execute(
        f"""
        SELECT {field_table}.fieldName, itemDataValues.value
        FROM itemData
        JOIN {field_table} ON {field_table}.fieldID = itemData.fieldID
        JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
        WHERE itemData.itemID = ?
        """,
        (item_id,),
    ).fetchall()
    values: dict[str, str] = {}
    for row in rows:
        field_name = str(row[0] or "").strip()
        value = str(row[1] or "").strip()
        if field_name and value and field_name not in values:
            values[field_name] = value
    return values


def _load_item_tags(connection: sqlite3.Connection, *, item_id: int) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT tags.name
        FROM itemTags
        JOIN tags ON tags.tagID = itemTags.tagID
        WHERE itemTags.itemID = ?
        ORDER BY tags.name COLLATE NOCASE
        """,
        (item_id,),
    ).fetchall()
    return tuple(_dedupe(str(row[0] or "").strip() for row in rows))


def _load_item_collections(connection: sqlite3.Connection, *, item_id: int) -> tuple[str, ...]:
    if not _table_exists(connection, "collectionItems") or not _table_exists(connection, "collections"):
        return ()
    rows = connection.execute(
        """
        SELECT collections.collectionName
        FROM collectionItems
        JOIN collections ON collections.collectionID = collectionItems.collectionID
        WHERE collectionItems.itemID = ?
        ORDER BY collections.collectionName COLLATE NOCASE
        """,
        (item_id,),
    ).fetchall()
    return tuple(_dedupe(str(row[0] or "").strip() for row in rows))


def _dedupe(values: object) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        canonical = normalized.lower()
        if not normalized or canonical in seen:
            continue
        ordered.append(normalized)
        seen.add(canonical)
    return ordered


def _parse_sqlite_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:10]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
