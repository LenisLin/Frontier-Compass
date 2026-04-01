# FrontierCompass Live Validation Guide

This guide is the manual acceptance gate for the current shipped path. Static tests and code review are necessary, but they are not sufficient for requirements that depend on real source behavior. Use these checks when you need to close multisource truthfulness, cache-fallback, or network-stability claims.

Do not treat this guide as a promise of future behavior. It only covers the supported local surfaces that ship today: `frontier-compass run-daily`, `frontier-compass ui`, and `frontier-compass history`.

## Before You Start

Use a real local workspace with `PYTHONPATH=src`, and keep runtime artifacts under `data/` or `reports/`. The commands below assume the packaged CLI is available as `frontier-compass`.

If you are validating against live network sources, do not rely on mocked responses for the final pass. A mocked or fixture-based run is useful for development, but the live gate needs the real source endpoints or the real local Zotero database.

## 1. `biomedical` Single-Day Bundle Run

Run a single-day bundle digest and confirm that the output reflects the requested day, the bundle id, and the cross-source snapshot mix.

```bash
frontier-compass run-daily \
  --mode biomedical \
  --today 2026-03-24 \
  --fetch-scope day-full
```

Check that the saved cache and report were written under `data/cache/` and `reports/daily/`, that source snapshots were written under `data/raw/source_snapshots/2026-03-24/`, and that the provenance shows `biomedical` plus the same requested day in the CLI output, HTML report, and `history`.

## 2. Same-Day Cache Reuse

Run the same command twice on the same day. The second run should reuse the same-day artifact instead of forcing a fresh fetch.

```bash
frontier-compass run-daily \
  --mode biomedical \
  --today 2026-03-24 \
  --fetch-scope day-full
```

On the second pass, verify that the fetch status or artifact source indicates same-day cache reuse and that the report, cache, and source snapshot paths stay aligned with the first run. Then switch to `--mode ai-for-medicine` for the same requested day and confirm that the run reuses the existing day snapshots instead of refetching the three sources.

## 3. Stale-Compatible Fallback After Fetch Failure

Confirm the last-resort fallback path with a disposable workspace and a real source interruption.

Preconditions: there must already be one older compatible cache in the normal `data/cache/` discovery path for the same mode/profile, and there must not be a same-day cache for the target day. Leave `--cache` and `--output` at their defaults so you exercise the standard discovery path.

1. Seed an older compatible run for the same mode/profile on an earlier day, using the normal local cache location.
1. Keep the same workspace and run the target day again with `--refresh --allow-stale-cache`.
1. During that second run, make the real source temporarily unavailable by interrupting network access or the source endpoint itself.
1. Verify that the run records the fetch failure and reuses the older cached data while still writing the target-day report/cache outputs and provenance for the requested day.

If you cannot create a live interruption in your environment, keep this step conditional rather than claiming the fallback was validated.

## 4. `range-full` Window Behavior

Run a multi-day window and confirm that the full requested range is honored.

```bash
frontier-compass run-daily \
  --mode biomedical \
  --start-date 2026-03-20 \
  --end-date 2026-03-24 \
  --fetch-scope range-full
```

Check both a complete window and a partial window. For a partial window, start the range in a disposable workspace, let earlier days complete, then interrupt source or network access before the later day finishes. Verify that the resulting partial-window metadata records the completed dates, the failed date, the failed source when known, and the failure reason, while keeping zero-count source rows visible.

If you cannot arrange a live interruption, treat the partial-window case as conditional manual validation instead of a scripted pass.

## 5. Report Artifact Alignment Across CLI, UI, And History

Use one run to confirm that the three local surfaces point to the same artifact family.

```bash
frontier-compass run-daily --today 2026-03-24
frontier-compass history --limit 5
frontier-compass ui --today 2026-03-24
```

Confirm that the UI startup summary prints the same `Cache:` and `Report:` paths as the run output, and that the UI history section and `frontier-compass history --limit 5` show the same persisted artifact pair for the same requested day.

## 6. Reusable Zotero Export Workflow

Validate the normalized Zotero profile basis with a read-only Zotero database and the reusable export snapshot.

```bash
frontier-compass run-daily \
  --today 2026-03-24 \
  --profile-source zotero \
  --zotero-db-path /path/to/zotero.sqlite \
  --zotero-collection "Tumor microenvironment"
```

Confirm that the run records `zotero` as the profile source in new artifacts, that `data/raw/zotero/library.csl.json` is created or refreshed, that selected collections are surfaced in the saved report/UI/history metadata when provided, and that the same export-backed profile basis appears in the saved report, UI, and history output. The SQLite database should be read-only. Compatibility aliases such as `live_zotero_db` can still be validated for older scripts, but the normalized user-facing profile basis is now `zotero`.

## What A Passing Live Gate Should Show

At the end of a live pass, you should be able to point at:

- the requested date or date window
- the effective displayed date when fallback occurs
- the fetch status and artifact source
- the report path and cache path
- the profile basis, including the export-backed Zotero workflow when used
- any partial-window completion or failure details

If any of those facts only hold in tests but not in a live run, keep the requirement open.
