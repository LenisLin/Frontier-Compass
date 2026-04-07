# Final Live Validation Report

Validation date reference: 2026-04-03

This document is rewritten so it no longer implies that the default public live gate is a 3-source run.

## Current Live Gate

- Default public live path: `arXiv` + `bioRxiv`
- Default public source id: `biomedical`
- `medRxiv` is excluded from the default release gate
- Any `medRxiv` validation belongs to the compatibility-only lane and must be labeled that way

## Historical 3-Source Notes

Earlier versions of this document recorded live-validation work against a temporary 3-source default. That evidence is now historical only.

- It may still help explain older compatibility artifacts.
- It must not be used as the current default-path signoff basis.
- It does not override the current `biomedical` public contract.

## Current Validation References

- Active checklist: [docs/live_validation.md](docs/live_validation.md)
- Current release verdict: [docs/pre_release_bug_sweep_report.md](docs/pre_release_bug_sweep_report.md)

## No New Evidence Claimed Here

This rewrite does not add new live-validation evidence. It only updates the interpretation of this document so public release readers are not told that `medRxiv` is part of the default shipping path.
