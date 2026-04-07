# Pre-Release Bug Sweep Report

Date: 2026-04-03
Repository: FrontierCompass

## A. Release Contract Used

This release is evaluated under the temporary 2-source public contract:

- default public sources: `arXiv` + `bioRxiv`
- default public source id: `biomedical`
- `medRxiv` is excluded from the default public release path
- `medRxiv` remains available only through compatibility or advanced paths such as `biomedical-multisource`

## B. Bugs Found

| Severity | Current code fact | Why it matters | Fix applied | Files changed |
| --- | --- | --- | --- | --- |
| blocker | The shipped public default and multiple public surfaces still reflected the retired 3-source contract, centering `biomedical-multisource` or 3-source wording instead of the `biomedical` public bundle. | The release would have shipped with the wrong contract on CLI, UI launch text, history/HTML wording, docs, and example defaults. | Moved the public default to the `biomedical` bundle, relabeled `biomedical-multisource` as compatibility-only, and updated public wording across runtime and release-facing surfaces. | `src/frontier_compass/common/source_bundles.py`, `src/frontier_compass/ui/app.py`, `src/frontier_compass/cli/main.py`, `src/frontier_compass/ui/streamlit_app.py`, `src/frontier_compass/ui/history.py`, `src/frontier_compass/reporting/html_report.py`, `README.md`, `docs/live_validation.md`, `docs/provenance.md`, `configs/user_defaults.example.json`, `docs/final_release_readiness_audit.md`, `docs/final_release_readiness_audit_supplement.md`, `docs/final_live_validation_report.md`, `docs/final_live_validation_remediation_report.md` |
| blocker | The CLI parser/help no longer matched the real public contract: explicit `--mode biomedical` selection was missing while public help still described the old path. | The release contract could not be selected explicitly or described consistently from the shipped CLI. | Added `biomedical` to the accepted parser choices and updated CLI help/source-label rendering to make `biomedical` the public default and `biomedical-multisource` compatibility-only. | `src/frontier_compass/cli/main.py`, `tests/test_cli.py` |
| blocker | Bundle provenance backfill could reintroduce `medRxiv` into default public artifacts because `source="multisource"` was treated like the legacy 3-source contract. This affected saved bundle digests and, before the final fix, `range-full` aggregation. | Cache JSON, HTML, and history could falsely imply that `medRxiv` was part of the default `biomedical` release path. | Made expected-source resolution bundle-aware by category and reused that same logic in range aggregation. Added regression coverage so default public bundle artifacts keep only `arXiv` + `bioRxiv`. | `src/frontier_compass/ui/app.py`, `tests/test_source_bundles.py` |
| medium | Release-facing docs and example defaults still published the old 3-source story, including archived final audit/live-validation documents. | Even with corrected runtime behavior, release reviewers and users would still read the wrong contract. | Rewrote release-facing docs and example defaults around the 2-source public contract while preserving older artifacts as compatibility/archive context rather than current release proof. | `README.md`, `docs/live_validation.md`, `docs/provenance.md`, `configs/user_defaults.example.json`, `docs/final_release_readiness_audit.md`, `docs/final_release_readiness_audit_supplement.md`, `docs/final_live_validation_report.md`, `docs/final_live_validation_remediation_report.md`, `tests/test_surface_freeze.py` |

## C. Validation Run

Validation workspace for runtime checks: `/tmp/frontier_bug_sweep_validation_v2`

| Step | Command / check | Result | Artifact path(s) | Status |
| --- | --- | --- | --- | --- |
| targeted tests | `PYTHONPATH=src pytest -q tests/test_source_bundles.py tests/test_cli.py tests/test_public_api.py tests/test_ui_support.py tests/test_history.py tests/test_streamlit_support.py tests/test_surface_freeze.py tests/test_live_path_contract.py` | All targeted tests for touched contract/runtime/UI/history/report areas passed. | none | pass |
| compile / syntax | `python -m py_compile $(rg --files -g '*.py' src tests)` | Python sources compiled successfully. | none | pass |
| CLI default launch contract | `PYTHONPATH=/home/lenislin/Experiment/projects/FrontierCompass/src python -m frontier_compass.cli.main ui --print-command --today 2026-04-03` | Printed `Source path: default public bundle (arXiv + bioRxiv)` and emitted `--source biomedical`. | none | pass |
| default public day-full run | `PYTHONPATH=/home/lenislin/Experiment/projects/FrontierCompass/src python -m frontier_compass.cli.main run-daily --today 2026-04-03 --mode biomedical` | Fresh live run wrote new default-path artifacts. `arXiv` succeeded live; `bioRxiv` stayed an explicit live failure because the reachable official recent listing only exposed `2026-04-02`. Report status stayed `partial`, which matches reality. | `/tmp/frontier_bug_sweep_validation_v2/data/cache/frontier_compass_bundle_biomedical_2026-04-03.json`; `/tmp/frontier_bug_sweep_validation_v2/reports/daily/frontier_compass_bundle_biomedical_2026-04-03.html` | partial |
| same-day rerun | same command as above, rerun in the same workspace | Reused the same-day cache truthfully. Artifact paths stayed aligned and source rows stayed on `arXiv` + `bioRxiv` only. | `/tmp/frontier_bug_sweep_validation_v2/data/cache/frontier_compass_bundle_biomedical_2026-04-03.json`; `/tmp/frontier_bug_sweep_validation_v2/reports/daily/frontier_compass_bundle_biomedical_2026-04-03.html` | pass |
| narrow range-full check | `PYTHONPATH=/home/lenislin/Experiment/projects/FrontierCompass/src python -m frontier_compass.cli.main run-daily --mode biomedical --start-date 2026-04-02 --end-date 2026-04-03 --fetch-scope range-full` | Range artifact was written and marked `partial` because `bioRxiv` could not satisfy `2026-04-03`. After the aggregation fix, the produced range artifact stayed on `arXiv` + `bioRxiv` and no longer backfilled a phantom `medRxiv` row. | `/tmp/frontier_bug_sweep_validation_v2/data/cache/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.json`; `/tmp/frontier_bug_sweep_validation_v2/reports/daily/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.html` | partial |
| history / HTML / cache consistency | `PYTHONPATH=/home/lenislin/Experiment/projects/FrontierCompass/src python -m frontier_compass.cli.main history --limit 5`; targeted `rg` inspection on the produced cache JSON and HTML reports | History showed the same artifact paths and only `arXiv` + `bioRxiv` on the default public artifacts. The fixed range cache/report no longer surfaced a phantom `medRxiv` row. | `/tmp/frontier_bug_sweep_validation_v2/data/cache/frontier_compass_bundle_biomedical_2026-04-03.json`; `/tmp/frontier_bug_sweep_validation_v2/reports/daily/frontier_compass_bundle_biomedical_2026-04-03.html`; `/tmp/frontier_bug_sweep_validation_v2/data/cache/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.json`; `/tmp/frontier_bug_sweep_validation_v2/reports/daily/frontier_compass_bundle_biomedical_2026-04-02_to_2026-04-03.html` | pass |

## D. Remaining Known Limitations

- `medRxiv` remains available only outside the default contract. The compatibility path is still present, but it is not part of the public release gate.
- A healthy same-day public run can still be `partial` when upstream `bioRxiv` availability lags the requested calendar day. In the live validation above, the reachable official recent listing exposed `2026-04-02` while the requested day was `2026-04-03`. FrontierCompass now reports that limitation honestly instead of silently folding it into a fake success.
- The actual browser-bound Streamlit surface was not live-validated here. The shipped startup contract was validated through `ui --print-command` and the supporting test suite, but no socket-bound browser session is claimed in this report.

## E. Final Release Recommendation

Recommendation: `release-ready under the 2-source contract`

Rationale:

- The public default path, explicit public mode, docs, config examples, CLI help, history classification, and HTML/report provenance now align on `arXiv` + `bioRxiv`.
- The one additional runtime bug found during final validation, phantom `medRxiv` backfill on default-bundle `range-full` artifacts, was fixed and revalidated.
- Remaining issues are upstream availability limitations rather than repo-side contract bugs, and the product now reports those limitations truthfully.

## F. Explicit Answer

“Under the temporary 2-source release contract, is FrontierCompass ready to ship?”

Yes. FrontierCompass is ready to ship under the temporary 2-source contract, with `medRxiv` kept compatibility-only and with the known upstream limitation that same-day `bioRxiv` availability may cause an honest `partial` run.
