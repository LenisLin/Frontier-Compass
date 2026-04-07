# Final Release Readiness Audit Supplement

Audit date: 2026-04-03

This supplement is rewritten to align with the current 2-source public release contract.

## Current Interpretation

- The default public path is the `biomedical` bundle over `arXiv` + `bioRxiv`.
- `biomedical-multisource` and any `medRxiv`-containing artifact now belong to the compatibility-only lane unless explicitly requested for advanced use.
- Archived validation or history entries may still contain older 3-source outputs. They remain readable, but they are not the current release baseline.

## Compatibility Boundary

Compatibility support is still useful for:

- reopening older artifacts without breaking provenance
- running explicit `biomedical-multisource` checks when needed
- keeping historical evidence accessible during the temporary release-contract change

Compatibility support must not redefine the public default path.

## Current Signoff Source

For the current go/no-go decision, use [docs/pre_release_bug_sweep_report.md](docs/pre_release_bug_sweep_report.md) rather than the earlier 3-source supplement logic. This file now exists only to prevent the archived supplement from being misread as the active release contract.
