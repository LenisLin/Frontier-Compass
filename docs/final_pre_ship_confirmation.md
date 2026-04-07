# Final Pre-Ship Confirmation

Date: 2026-04-03

## Release contract used

- Public default sources: `arXiv` + `bioRxiv`
- Default public mode / source id: `biomedical`
- `medRxiv` is not part of the default release path
- Honest partial runs are acceptable when upstream `bioRxiv` availability lags, but they must be labeled truthfully

## Public contract re-check

I re-checked the current public contract across:

- CLI help and default `run-daily` / `ui` wording
- Streamlit support and launch-path wording via tests and CLI output
- `run-daily` default behavior
- `history`
- saved HTML report output
- `README.md`
- `docs/provenance.md`
- `docs/live_validation.md`

Result: the public-facing wording is aligned with the 2-source `biomedical` contract, and `medRxiv` is described as compatibility-only rather than default.

## Bugs found and fixes applied

### Fixed

1. `history` could still surface older official-bundle artifacts with phantom `medRxiv` source rows as if they were current-contract entries.
   - Impact: stale 3-source provenance could still leak into the public `history` surface and confuse release reviewers.
   - Fix: updated history classification to mark official bundle artifacts as legacy when their saved source rows contain sources outside the bundle contract.
   - Files:
     - `src/frontier_compass/ui/history.py`
     - `tests/test_history.py`

### Not found in this pass

- No remaining CLI/help mismatch for the default public bundle
- No default-path `medRxiv` leak in freshly generated default biomedical cache or HTML artifacts
- No docs/runtime mismatch across the checked public docs
- No broken `biomedical` default path in the validated runs
- No false success labeling for the live partial runs observed on 2026-04-03

## Validation commands and results

### Focused contract tests before the fix

```bash
pytest -q tests/test_cli.py tests/test_surface_freeze.py tests/test_history.py tests/test_source_bundles.py tests/test_streamlit_support.py tests/test_ui_support.py tests/test_report_mode.py tests/test_live_path_contract.py
```

Result: passed.

### CLI help spot checks

```bash
python -m frontier_compass.cli.main --help
python -m frontier_compass.cli.main run-daily --help
python -m frontier_compass.cli.main ui --help
python -m frontier_compass.cli.main history --help
```

Result: default public path is described as the 2-source `arXiv` + `bioRxiv` bundle; `biomedical-multisource` remains compatibility-only.

### Compile validation

```bash
python -m py_compile $(rg --files -g '*.py' src tests)
```

Result: passed.

### Default biomedical day-full run

```bash
PYTHONPATH=src python -m frontier_compass.cli.main run-daily --today 2026-04-03 --fetch-scope day-full
```

Observed result:

- Exit code `0`
- `Fetch status: fresh source fetch`
- `Source run: default public bundle (arXiv + bioRxiv)`
- `Report status: partial`
- `arxiv fetched 1053 / retained 98 [live-success; ready; fresh]`
- `biorxiv fetched 0 / retained 0 [live-failed; failed; fresh]`
- honest failure note:
  `bioRxiv live fallback cannot satisfy historical day 2026-04-03: reachable recent listing https://www.biorxiv.org/content/early/recent only exposes 2026-04-02.`

Assessment: acceptable under the release contract because the partial run was labeled truthfully.

### Same-day rerun for cache truthfulness

```bash
PYTHONPATH=src python -m frontier_compass.cli.main run-daily --today 2026-04-03 --fetch-scope day-full
```

Observed result:

- Exit code `0`
- `Fetch status: same-day cache`
- `Report status: partial`
- source rows kept the same honest state rather than relabeling the run as clean success
- no `medRxiv` row in the default biomedical artifact

### Narrow range-full run

```bash
PYTHONPATH=src python -m frontier_compass.cli.main run-daily --start-date 2026-04-02 --end-date 2026-04-03 --fetch-scope range-full
```

Observed result:

- Exit code `0`
- `Fetch status: range aggregated from day artifacts`
- `Source run: default public bundle (arXiv + bioRxiv)`
- `Report status: partial`
- request window recorded as partial with explicit failure detail for `2026-04-03 / biorxiv`
- aggregated source rows were only `arxiv` and `biorxiv`
- no phantom `medRxiv` row in the generated default biomedical range artifact

### History / HTML / cache consistency checks

Commands used:

```bash
PYTHONPATH=src python -m frontier_compass.cli.main history --limit 8
PYTHONPATH=src python -m frontier_compass.cli.main history --limit 20 | rg -n "AI for medicine|Compatibility:"
rg -n 'medrxiv|report_status|request_window|failures|source_run_stats' \
  data/cache/frontier_compass_bundle_biomedical_2026-04-03.json \
  reports/daily/frontier_compass_bundle_biomedical_2026-04-03.html \
  data/cache/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.json \
  reports/daily/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.html
python - <<'PY'
import json
from pathlib import Path
for path in [
    Path("data/cache/frontier_compass_bundle_biomedical_2026-04-03.json"),
    Path("data/cache/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.json"),
]:
    payload = json.loads(path.read_text())
    print(path.name, payload["category"], payload["report_status"], [row["source"] for row in payload["source_run_stats"]])
PY
```

Observed result:

- default biomedical cache + HTML artifacts contained only `arxiv` and `biorxiv`
- saved HTML embedded `report_status: partial` and the matching `request_window` / `failures` metadata for the range run
- `history` showed current-contract default biomedical artifacts first
- older `AI for medicine` artifact with phantom `medRxiv` provenance is now demoted with:
  `Compatibility: compatibility-only: official bundle artifact contains unexpected source rows: medrxiv`

### Focused validation after the fix

```bash
pytest -q tests/test_history.py tests/test_surface_freeze.py tests/test_cli.py tests/test_live_path_contract.py
python -m py_compile $(rg --files -g '*.py' src tests)
```

Result: passed.

## Remaining known limitations

1. Live validation on 2026-04-03 did not produce a clean full 2-source same-day run because reachable `bioRxiv` availability lagged and only exposed 2026-04-02 in the fallback listing.
2. Historical compatibility artifacts still exist on disk and may still contain older 3-source provenance, but `history` now marks those entries as compatibility-only / legacy instead of presenting them as current-contract.
3. This pass did not claim a default-path `medRxiv` validation because `medRxiv` is not part of the release gate.

## Final recommendation

Ship.

Reason: the current public contract is aligned across runtime and docs, the last real public-surface leak found in this pass was fixed, and the live default-path behavior on 2026-04-03 remained truthful even when upstream `bioRxiv` lag forced partial results.
