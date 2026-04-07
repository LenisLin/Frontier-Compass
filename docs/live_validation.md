# FrontierCompass Live Validation Guide

This guide is the manual acceptance gate for the current shipped path. Static tests and code review are necessary, but they are not sufficient for requirements that depend on real source behavior. Use these checks when you need to close source-truthfulness, cache-fallback, or network-stability claims.

Do not treat this guide as a promise of future behavior. It only covers the supported local surfaces that ship today: `frontier-compass run-daily`, `frontier-compass ui`, and `frontier-compass history`.

## Before You Start

Use a real local workspace with `PYTHONPATH=src`, and keep runtime artifacts under `data/` or `reports/`. The commands below assume the packaged CLI is available as `frontier-compass`.

If you are validating against live network sources, do not rely on mocked responses for the final pass. A mocked or fixture-based run is useful for development, but the live gate needs the real source endpoints or the real local Zotero database.

## 1. Default Single-Day Public Run

Run a single-day digest and confirm that the output reflects the requested day and the default public `arXiv` + `bioRxiv` bundle.

```bash
frontier-compass run-daily \
  --today 2026-03-24 \
  --fetch-scope day-full
```

Check that the saved cache and report were written under `data/cache/` and `reports/daily/`, and that the provenance shows the same requested day plus the default public bundle in the CLI output, HTML report, and `history`. The default public path writes the day cache/report pair and materializes `arXiv` and `bioRxiv` snapshots under `data/raw/source_snapshots/` when needed. In the saved HTML, confirm that both the Digest and Frontier Report sections show explicit source provenance rows with source name, outcome, live outcome when relevant, cache status, fetched/displayed counts, and any error or failure note.

## 2. Same-Day Cache Reuse

Run the same command twice on the same day. The second run should reuse the same-day artifact instead of forcing a fresh fetch.

```bash
frontier-compass run-daily \
  --today 2026-03-24 \
  --fetch-scope day-full
```

On the second pass, verify that the fetch status or artifact source indicates same-day cache reuse and that the report and cache paths stay aligned with the first run. If you also want to validate the advanced `ai-for-medicine` bundle override, run that mode twice for the same requested day and confirm that its second pass reuses any same-day source snapshots created by the first `ai-for-medicine` run instead of refetching sources whose snapshots were already materialized.

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

Confirm that the UI startup summary prints the same `Cache:` and `Report:` paths as the run output, and that the UI history section and `frontier-compass history --limit 5` show the same persisted artifact pair for the same requested day. History should keep current-contract runs in the primary lane and separate any compatibility or archived entries instead of blending them into the same recent-run group.

If you run with `--report-mode enhanced`, also confirm that CLI, UI, history, and HTML all show the explicit LLM provenance fields for the same run:

- `LLM requested`
- `LLM applied`
- `LLM provider`
- `LLM fallback reason`
- `LLM time`

If the current build stays deterministic and zero-token, those fields should say so explicitly rather than relying only on a free-text runtime note.

## 6. Compatibility-Only medRxiv Check

`medRxiv` is not part of the default public release path. If you need to validate that the compatibility path still behaves honestly, run it explicitly and keep it separate from the public release gate.

```bash
frontier-compass run-daily \
  --mode biomedical-multisource \
  --today 2026-03-24 \
  --fetch-scope day-full
```

Confirm that CLI, HTML, and history label this run as a compatibility 3-source path rather than as the default release contract. If `medRxiv` fails or returns zero rows, that source-level truth should stay visible and should not be reported as a clean success.

## 7. Reusable Zotero Export Workflow

Validate the public Zotero profile-source contract with a read-only Zotero database and the reusable export snapshot.

```bash
frontier-compass run-daily \
  --today 2026-03-24 \
  --profile-source live_zotero_db \
  --zotero-db-path /path/to/zotero.sqlite \
  --zotero-collection "Tumor microenvironment"
```

Confirm that the run records `live_zotero_db` as the profile source in new artifacts, that selected collections are surfaced in the saved report/UI/history metadata when provided, and that the same live-DB profile basis appears in the saved report, UI, and history output. The SQLite database should be read-only, and explicit live requests should fail clearly instead of silently degrading to export-backed Zotero.

For the reusable snapshot path, also validate:

```bash
frontier-compass run-daily \
  --today 2026-03-24 \
  --profile-source zotero_export \
  --zotero-export path/to/zotero-export.csl.json
```

Confirm that the run records `zotero_export` as the profile source in new artifacts and that export provenance remains aligned across the saved report, UI, and history output.

## What A Passing Live Gate Should Show

At the end of a live pass, you should be able to point at:

- the requested date or date window
- the effective displayed date when fallback occurs
- the fetch status and artifact source
- the report path and cache path
- the profile basis, including the export-backed Zotero workflow when used
- the source-level provenance rows in the saved HTML, including zero and failed sources
- the explicit LLM provenance fields when report-mode behavior is relevant
- any partial-window completion or failure details

For the public release gate, those proofs should come from the default `arXiv` + `bioRxiv` path. Any `medRxiv` evidence belongs to the compatibility-only lane and should be labeled that way.

If any of those facts only hold in tests but not in a live run, keep the requirement open.
