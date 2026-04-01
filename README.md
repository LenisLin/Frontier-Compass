<div align="center">
  <h1>FrontierCompass</h1>
  <p><strong>A local research-scouting workflow for new biomedical papers.</strong></p>
  <p>Two product tracks. Three local surfaces. Provenance-honest daily artifacts.</p>
</div>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#at-a-glance">At a Glance</a> ·
  <a href="#choose-your-surface">Choose Your Surface</a> ·
  <a href="#supported-bundles-and-snapshots">Bundles and Snapshots</a> ·
  <a href="#runtime-outputs-and-provenance">Runtime and Provenance</a>
</p>

FrontierCompass is a local research-scouting tool for reviewing new biomedical papers from your own machine. It fetches paper metadata, ranks a reading-first shortlist, writes local artifacts, and lets you inspect the same run from Python, the CLI, or a local Streamlit UI.

What makes the product legible is that each run keeps two product tracks separate instead of blending everything into one undifferentiated report. The `Personalized Digest` answers what you should look at first. The `Frontier Report` answers what surfaced in the field today. The saved JSON cache, HTML report, and interactive UI all describe the same run and the same provenance story.

## At a Glance

| Layer | What it gives you |
| --- | --- |
| `Personalized Digest` | A profile-aware shortlist for what to read first |
| `Frontier Report` | A broader field summary of what surfaced in the run |
| `Python API` | The shortest programmable entrypoint for local automation |
| `local CLI` | The primary day-to-day command-line path |
| `local interactive UI` | A local Streamlit view over the same digest and report |
| Local artifacts | JSON caches in `data/cache/` and HTML reports in `reports/daily/` |
| Provenance | Requested date, effective displayed date, fetch status, artifact source, report mode, cost mode, and profile basis |

## Quickstart

If you only want the shortest supported local workflow, install the editable package, materialize a run, and then open the UI:

```bash
pip install -e .
frontier-compass run-daily --today 2026-03-24
frontier-compass ui --today 2026-03-24
```

`run-daily` is the primary local CLI path. It materializes or reuses the current daily digest, writes the matching HTML report, and keeps provenance visible in the saved artifacts. `ui` opens the local interactive inspection surface for the same workflow. `history` is the normal follow-up command when you want to inspect recent runs, compare requested and displayed dates, or reopen saved artifacts.

```bash
frontier-compass history --limit 5
frontier-compass ui --print-command --today 2026-03-24
```

## Choose Your Surface

The primary supported surfaces are the `Python API`, the `local CLI`, and the `local interactive UI`. They all point at the same underlying local artifact flow, so the saved report, the UI, and the returned Python result stay aligned.

| Surface | Best for | Shortest path |
| --- | --- | --- |
| `Python API` | Scripting, notebooks, local automation | `run_daily(...)` |
| `local CLI` | Fast daily use from the terminal | `frontier-compass run-daily --today 2026-03-24` |
| `local interactive UI` | Browsing the current run visually | `frontier-compass ui --today 2026-03-24` |

The package root and `frontier_compass.api` intentionally expose the same supported public surface: `FrontierCompassRunner`, `DailyRunResult`, `LocalUISession`, `run_daily`, `prepare_ui_session`, and `load_recent_history`.

The shortest supported Python path is the package-root `run_daily()` helper:

```python
from datetime import date

from frontier_compass import run_daily

result = run_daily(requested_date=date(2026, 3, 24), max_results=80)

print(result.digest.category)
print(result.fetch_status_label)
print(result.cache_path)
print(result.report_path)
```

Use `from frontier_compass.api import run_daily` if you prefer the explicit module path. Use `FrontierCompassRunner` when you want a reusable object-oriented entrypoint or when you want to prepare a `LocalUISession` directly.

## Zotero Augmentation

FrontierCompass now treats `zotero` as the primary user-facing profile source. The local UI auto-discovers standard local Zotero library locations, exports one reusable `CSL JSON` snapshot to `data/raw/zotero/library.csl.json`, and reuses that export until you explicitly refresh it.

Primary examples:

```bash
frontier-compass run-daily --today 2026-03-24 --profile-source zotero
frontier-compass run-daily --today 2026-03-24 --profile-source zotero --zotero-db-path /path/to/zotero.sqlite
frontier-compass run-daily --today 2026-03-24 --profile-source zotero --zotero-collection "Tumor microenvironment"
```

Manual export files still work as a compatibility fallback:

```bash
frontier-compass run-daily --today 2026-03-24 --profile-source zotero --zotero-export path/to/zotero-export.csl.json
```

`zotero_export` and `live_zotero_db` remain compatibility aliases for older scripts, caches, and explicit CLI requests, but new runs normalize to the single `zotero` profile basis. Zotero augmentation stays local-only, personalizes the `Personalized Digest`, surfaces score and retrieval hints in the saved HTML report and UI, and can influence the profile-relevant highlights shown inside the `Frontier Report`. FrontierCompass reads the SQLite library in read-only mode and does not write back to Zotero.

## Supported Bundles And Snapshots

FrontierCompass is bundle-centric in the current build. The two official public bundles are:

| Bundle | What it means now |
| --- | --- |
| `biomedical` | Default cross-source biomedical scouting bundle over the saved daily `arXiv` + `bioRxiv` + `medRxiv` snapshot |
| `ai-for-medicine` | Curated AI-for-medicine local filter over the same saved daily snapshot |
| custom bundles in `configs/source_bundles.json` | Persistent local presets with enabled sources, include terms, and optional exclude terms |

Both official bundles read from the same local per-day source snapshots under `data/raw/source_snapshots/YYYY-MM-DD/{source}.json`. Switching bundle, profile mode, or Zotero collections in the UI reuses those local day snapshots unless you explicitly refresh data.

Bundle examples:

```bash
frontier-compass run-daily --mode biomedical --today 2026-03-24
frontier-compass run-daily --mode ai-for-medicine --today 2026-03-24
```

Range example:

```bash
frontier-compass run-daily --mode biomedical --start-date 2026-03-20 --end-date 2026-03-24 --fetch-scope range-full
```

Legacy ids such as `biomedical-latest`, `biomedical-discovery`, `biomedical-daily`, and `biomedical-multisource` still resolve in the CLI/API for compatibility, but they are internal resolver targets rather than the primary user-facing bundle list. The local UI now exposes only the two official bundles plus any saved custom presets.

## Runtime, Outputs, And Provenance

The default report runtime is `deterministic`, and the current build is `zero-token` by default. Fetching, ranking, summaries, exploration picks, and the current `Frontier Report` all run with deterministic local logic. `enhanced` is a formalized request mode for the `Frontier Report` track only, but the current build does not ship a model-assisted frontier reporter, so requesting `--report-mode enhanced` still produces a deterministic zero-token run and says so in the saved artifacts.

FrontierCompass writes runtime artifacts to stable local locations rather than the repository root. JSON caches land under `data/cache/`. HTML reports land under `reports/daily/`. Optional `.eml` files appear only when you use the compatibility email path or dry-run email output. The persisted cache keeps the underlying digest and provenance fields. The CLI can also print a compressed `Artifact source` label when it differs from the fetch status, while the UI, HTML report, and history surfaces primarily show fetch-status or display-source style provenance from that run data. Each run keeps enough provenance to explain what you are looking at, including requested date, effective displayed date, request-window status, completed dates, failed dates or sources when a range is partial, report mode, cost mode, and profile basis.

The user-facing provenance model is documented in [docs/provenance.md](docs/provenance.md).
The live-validation gate for network-touching requirements is documented in [docs/live_validation.md](docs/live_validation.md). Static tests are necessary, but they do not by themselves close multisource truthfulness, cache-fallback, or other real-network acceptance claims.

## Optional Local Defaults

If `configs/user_defaults.json` exists, FrontierCompass loads it automatically. Start from [configs/user_defaults.example.json](configs/user_defaults.example.json). CLI flags override config values, and config values override built-ins. The most useful day-to-day defaults are `default_mode`, `default_report_mode`, `default_max_results`, `default_zotero_export_path`, `default_email_to`, `default_email_from`, `default_generate_dry_run_email`, and `default_allow_stale_cache`.

## Current Scope

FrontierCompass is intentionally narrow in this phase. It scouts papers from titles, abstracts, metadata, and optional Zotero-derived signals; there is no full-text reading in the current build. The primary supported path is local: Python API, local CLI, and local interactive UI. `history` is a supported local inspection surface, while `daily`, `deliver-daily`, `demo-report`, and `demo-ranking` remain compatibility or demo commands rather than the main onboarding path.

Email delivery is still present as a compatibility surface, but it is secondary to the local artifact workflow. There is no default model-assisted `Frontier Report` in this build. The current surface does not imply a hosted service, background job, scheduler, or daemon process.
