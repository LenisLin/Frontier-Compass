# Final Bug Sweep Round 2

## Release contract used

- Default public sources: `arXiv` + `bioRxiv`
- Default public source id: `biomedical`
- `medRxiv` is compatibility-only and not part of the default public release path
- Partial runs are acceptable only when labeled truthfully
- CLI, UI, history, HTML, cache, and docs must tell the same provenance story

## Findings first

### Fixed

1. `history` was still treating legacy `biomedical-latest` artifacts as current-contract entries.
   - Impact: legacy compatibility artifacts could still appear in the top current-contract section and blur the public `biomedical` release story.
   - Fix:
     - `src/frontier_compass/ui/history.py`
     - `tests/test_history.py`
   - Change: mark legacy fixed mode ids (`biomedical-latest`, `biomedical-daily`, `biomedical-discovery`) as compatibility-only during history classification.

2. Fresh day reports were embedding an empty run-summary `fetch_status`, which made `history` show `fetch status unavailable (report missing)` for fresh current-contract day artifacts.
   - Impact: fresh CLI/runtime output, saved HTML, and later `history` scans disagreed about provenance for the same `biomedical` run.
   - Fix:
     - `src/frontier_compass/ui/app.py`
     - `tests/test_history.py`
   - Change: write fresh day reports with explicit `acquisition_status_label="fresh source fetch"` so HTML run summary and history readback stay aligned.

### Not found

- No additional verified default-path `medRxiv` leak in freshly generated default `biomedical` cache or HTML artifacts
- No broken default `biomedical` CLI path after fixes
- No false-success labeling in the final live range validation; the observed partial was labeled partial

## Validation

### Static test evidence

- `pytest -q tests/test_cli.py tests/test_source_bundles.py tests/test_report_mode.py`
  - Passed
- `pytest -q tests/test_history.py tests/test_frontier_report.py tests/test_public_api.py`
  - Passed
- `pytest -q tests/test_ui_support.py tests/test_streamlit_support.py tests/test_surface_freeze.py tests/test_live_path_contract.py`
  - Passed
- `pytest -q tests/test_ui_support.py tests/test_history.py tests/test_cli.py`
  - Passed after the final fixes
- `python -m py_compile $(rg --files -g '*.py' src tests)`
  - Passed before closeout

### Local runtime evidence

- `PYTHONPATH=src python -m frontier_compass.cli.main run-daily --today 2026-04-03`
  - Result after fresh live write: `Fetch status: same-day cache`
  - `Report status: ready`
  - `Source run: default public bundle (arXiv + bioRxiv)`
  - No `medRxiv` row

- `PYTHONPATH=src python -m frontier_compass.cli.main history`
  - Result after fixes:
    - fresh `2026-04-03 | Biomedical` row shows `fresh source fetch`
    - current-contract rows stay above compatibility rows
    - legacy `biomedical-latest` entries are demoted to `Compatibility / archived entries`

- `python -c "from frontier_compass.ui.history import read_report_history_metadata; ..."`
  - `frontier_compass_bundle_biomedical_2026-04-03.html -> fresh source fetch / ready / complete`
  - `frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.html -> range aggregated from day artifacts / partial / partial`

### Live source evidence

- Fresh default biomedical run required unrestricted network because sandboxed fetches returned `Operation not permitted`.

- `PYTHONPATH=src python -m frontier_compass.cli.main run-daily --today 2026-04-03 --refresh --no-stale-cache`
  - Result:
    - `Fetch status: fresh source fetch`
    - `Report status: ready`
    - `Source run: default public bundle (arXiv + bioRxiv)`
    - `arxiv fetched 1053 / retained 98 [live-success; ready; fresh]`
    - `biorxiv fetched 10 / retained 10 [live-success; ready; fresh]`
    - no `medRxiv`

- `PYTHONPATH=src python -m frontier_compass.cli.main run-daily --start-date 2026-04-02 --end-date 2026-04-03 --fetch-scope range-full --refresh --no-stale-cache`
  - Result:
    - `Fetch status: range aggregated from day artifacts`
    - `Report status: partial`
    - request window recorded:
      - `2026-04-02 -> 2026-04-03 (partial; completed 2026-04-02, 2026-04-03; failed 2026-04-02 / biorxiv (... only exposes 2026-04-03))`
    - default source run stayed `arXiv + bioRxiv`
    - no `medRxiv`

### History / HTML / cache consistency check

- `rg -n '"fetch_status"|"report_status"|"request_window"|medrxiv|failures' ...`
  - Default `biomedical` day artifact:
    - HTML run summary contains fresh `fetch_status`
    - cache `report_status` is `ready`
    - request-window failures list is empty
    - no `medrxiv`
  - Default `biomedical` range artifact:
    - HTML run summary contains `range aggregated from day artifacts`
    - cache `report_status` is `partial`
    - request-window failures list includes the `biorxiv` failure
    - no `medrxiv`

## Could not validate

- Initial fresh fetch attempts inside the sandbox were blocked by `Operation not permitted`.
- Fresh network validations were completed only after unrestricted execution was approved.

## Remaining limitations

1. Historical compatibility artifacts still exist on disk and may still contain older `medRxiv` or legacy-mode provenance, but `history` now demotes them instead of presenting them as current-contract.
2. `bioRxiv` historical availability can still make `range-full` runs partial; the important requirement is truthful labeling, which the final live range run satisfied.
3. Compatibility-only artifacts outside the default public path were not rewritten in this pass; they remain readable and explicitly marked as compatibility-only.

## Final recommendation

`ship`

The two verified public-surface bugs from this sweep are fixed, targeted tests pass, compile passes, the default live `biomedical` day path is now fresh and truthful, the same-day rerun is truthful, and the live range validation is partial-but-honest with no phantom `medRxiv` leak.

## Terminal summary

- Files changed:
  - `src/frontier_compass/ui/history.py`
  - `src/frontier_compass/ui/app.py`
  - `tests/test_history.py`
- Blockers fixed:
  - legacy `biomedical-latest` entries leaking into current-contract history
  - fresh day report/history fetch-status mismatch
- Remaining blockers:
  - none verified
- Final recommendation:
  - `ship`
- Report file written:
  - `docs/final_bug_sweep_round2.md`
