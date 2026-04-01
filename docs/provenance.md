# FrontierCompass Provenance And Runtime Notes

FrontierCompass writes local artifacts and keeps enough metadata to explain what you are looking at. This page is the user-facing reference for freshness, dates, source labels, and runtime labels.

For requirements that depend on real network behavior, this page is not the final closure gate. Static code review and deterministic tests are necessary, but the documented live checks in [live_validation.md](live_validation.md) are also required before treating multisource truthfulness, cache-fallback behavior, or source-stability claims as closed.

## Requested Date And Effective Displayed Date

The `requested date` is the day you asked FrontierCompass to review. The `effective displayed date` is the day the displayed paper set actually comes from.

In the current bundle-centric workflow, those dates usually match because the default `biomedical` and `ai-for-medicine` bundles read from one saved per-day local snapshot. Older compatibility modes such as `biomedical-latest` can still diverge when they fall back to an earlier effective release date, and that difference stays visible in the Python result, CLI output, JSON cache, HTML report, and local UI.

If a stale-cache fallback is used, the artifact can also record the stale cache's own requested and effective dates so you can see exactly which earlier run is being reused.

For explicit date windows, the artifact also keeps a `request window` contract. A complete range records the requested start and end dates. A partial range can also record completed dates, failed dates, failed sources when known, and the failure reason that stopped or degraded the window.

## Fetch Status And Artifact Source

FrontierCompass exposes two closely related provenance labels:

- `Fetch status`: the full freshness story for the current run
- `Artifact source`: the shorter label used when describing the saved artifact itself

Common statuses are:

- `fresh source fetch`: FrontierCompass fetched source data successfully for the current run.
- `same-day cache`: FrontierCompass reused an existing same-day artifact without needing a fresh fetch.
- `same-date cache reused after fetch failure`: a fetch was attempted, failed, and the run fell back to a same-date cache.
- `older compatible cache reused after fetch failure`: no same-date artifact was available after a fetch failure, so FrontierCompass reused an older compatible cache.

The `Artifact source` label intentionally compresses the story a bit. For example, a fetch failure that lands on a same-date cache still presents the artifact as `same-day cache`, while the fuller `Fetch status` keeps the explicit failure-and-fallback wording. The CLI can print `Artifact source` when that compression is useful; the UI, HTML report, and history views primarily present fetch-status or display-source style provenance from the saved run data.

## Source Provenance

The public source story is bundle-centric. The official `biomedical` and `ai-for-medicine` bundles both read from one local per-day snapshot family covering `arXiv`, `bioRxiv`, and `medRxiv`.

The saved report and UI surfaces can expose source-level provenance such as:

- selected mode or category
- searched categories
- source counts
- source endpoints or feed URLs when applicable
- profile basis, including whether Zotero augmentation was active

This lets you distinguish a default bundle run, a custom bundle run, or an older compatibility mode. The `history` view focuses on the compact persisted run summary and source counts; it does not promise to surface the raw source endpoints or feed URLs.

For multisource and range runs, the source rows are meant to stay honest even when a source returns nothing or fails. A `0` row should stay visible, and partial or failed source states should stay attached to that source rather than disappearing into aggregate totals.

## Profile Basis Provenance

`Profile source` records which local basis was used to build the current profile:

- `baseline`: default biomedical keyword profile, no Zotero augmentation
- `zotero`: augmented from the reusable local Zotero export snapshot
- `zotero_export` and `live_zotero_db`: compatibility aliases that can still appear in older artifacts or explicit legacy requests

The primary user-facing Zotero path is now export-backed: FrontierCompass auto-discovers a local read-only Zotero SQLite library when available, exports it to `data/raw/zotero/library.csl.json`, and reuses that snapshot until you refresh it. Zotero-derived keywords, concepts, retrieval hints, and optional collection selections are then merged into the baseline profile. FrontierCompass reads the SQLite library in read-only mode and does not write back to Zotero.

When a Zotero basis is active, the artifact carries the reusable export filename, selected collections when present, and parsed and used item counts. Older artifacts can still carry the original read-only SQLite filename.

## Report Mode And Cost Mode

`Report mode` describes how the `Frontier Report` was actually produced. `Cost mode` describes whether tokens were used.

In the current build:

- `deterministic` is the active runtime
- `zero-token` is the active cost mode
- `enhanced` is a formalized request mode for the `Frontier Report` track only

If you request `--report-mode enhanced` today, FrontierCompass still records that request, but the actual run remains `deterministic` and `zero-token` because no model-assisted frontier reporter is configured in this build.

## Fetch Scope And Window Contracts

`day-full` is the default fetch contract for a single requested day. It fetches the full paper set for that day without applying a `max-results` truncation to the ranked output.

`range-full` is the explicit window contract for date ranges specified via `--start-date` and `--end-date`. It iterates each day in the range, builds a per-day digest, and merges the papers into a single ranked pool. `shortlist` remains a supported CLI option for compact previews. `day-full` and `range-full` keep the full ranked output, while `shortlist` uses `--max-results` as the shortlist ceiling.

A partial range run records which dates completed, which date failed, which source failed (when known), and the failure reason. The merged ranked output still includes papers from all completed dates.

## Output Files

FrontierCompass keeps runtime artifacts out of the repository root:

- `data/raw/source_snapshots/`: normalized per-day source snapshots for `arXiv`, `bioRxiv`, and `medRxiv`
- `data/raw/zotero/`: reusable Zotero export snapshot plus discovery/status sidecar
- `data/cache/`: JSON cache artifacts for daily runs
- `reports/daily/`: saved HTML reports
- optional `.eml` files beside the report when you use compatibility email delivery or a dry-run email workflow

The `history` command reads the same artifact family and shows the same provenance story in a compact local inspection view.

## Validation Gate

Use the live-validation guide in [live_validation.md](live_validation.md) when you need to confirm the current shipped path against real source behavior. That guide covers `frontier-compass run-daily`, `frontier-compass ui`, and `frontier-compass history`, plus the current contracts for bundle ids, `--profile-source zotero`, `--zotero-db-path`, `--zotero-collection`, `--start-date`, `--end-date`, and `--fetch-scope`.
