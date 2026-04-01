# FrontierCompass Agent Notes

## Project Goal
FrontierCompass is a lightweight research scouting tool. Its v0.1 goal is to establish stable package boundaries, a unified paper/profile schema, a small CLI surface, and predictable report/output locations without committing to production ingestion or recommendation behavior yet.

## Source Of Truth
- Product code lives under `src/frontier_compass/`.
- The unified schema in `src/frontier_compass/storage/schema.py` is the canonical contract for paper, profile, and ranking objects.
- Everything under `reference/` is read-only reference material and must never be modified.

## Non-Goals For v0.1
- No production-grade ingestion pipelines.
- No background jobs, scheduling, or daemon processes.
- No learned recommendation system or heavy ranking dependencies.
- No persistent database migrations beyond stable directory layout and schema placeholders.
- No UI beyond the minimal orchestration and HTML reporting surface already present.

## Package Boundaries
- `storage/`: shared dataclasses and schema primitives. Cross-module contracts should flow through this package.
- `common/`: deterministic, dependency-light helpers shared across multiple modules.
- `ingest/`: source-specific parsing and fetch adapters only. Do not embed ranking or Zotero logic here.
- `zotero/`: Zotero-derived profile construction only.
- `ranking/`: transparent scoring logic only.
- `exploration/`: shortlist diversification and exploration heuristics only.
- `reporting/`: rendering and output formatting only.
- `ui/`: workflow orchestration for CLI and future surfaces.
- `cli/`: thin command dispatch layer only.

## Runtime Output Locations
- `configs/`: local configuration files and checked-in examples when added.
- `data/raw/`: downloaded or imported source payloads.
- `data/cache/`: disposable caches.
- `data/db/`: local database files when persistence is added.
- `reports/daily/`: date-scoped daily HTML or text briefings.
- `reports/weekly/`: weekly rollups and longer summaries.
- Runtime outputs should not be written to the repository root.

## Unified Schema Rule
- New modules must exchange paper and ranking data through `PaperRecord`, `UserInterestProfile`, and `RankedPaper` from `src/frontier_compass/storage/schema.py`.
- Avoid parallel ad hoc dict schemas once a concept already exists in `storage/schema.py`.
- If a schema change is needed, update the storage models first, then adapt downstream callers.

## Validation Rules
- Keep the package importable with `PYTHONPATH=src`.
- `python -m frontier_compass.cli.main` must execute without crashing and should print help when invoked without subcommands.
- The installed `frontier-compass` entrypoint should behave the same way when available.
- `pytest` must remain fast and deterministic, and new tests should avoid network access.
- Prefer standard-library implementations and avoid heavy dependencies unless the user explicitly asks for them.

## Development Rules
- Prefer the Python standard library for core workflow pieces.
- Keep parsing and ranking logic deterministic so tests stay fast and cheap.
- Add tests for new behavior in `tests/`.
- Avoid tight coupling between the CLI and the ranking pipeline so later UI surfaces can reuse the same app layer.
- Treat caches, generated reports, and other runtime outputs as artifacts that belong under `data/` or `reports/`, not at the repository root.
