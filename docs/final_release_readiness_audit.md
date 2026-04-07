# Final Release Readiness Audit

Audit date: 2026-04-03

This document is rewritten to match the current release contract and supersedes the earlier 3-source interpretation.

## Current Release Contract

- Default public sources: `arXiv` + `bioRxiv`
- Default public source id: `biomedical`
- `medRxiv` is excluded from the default public release path
- `medRxiv` remains available only through explicit compatibility or advanced workflows such as `biomedical-multisource`

## How To Read Earlier Evidence

Older notes in prior versions of this file described a fixed 3-source default path. That is no longer the release contract.

- Historical cache, HTML, and validation artifacts that include `medRxiv` remain readable.
- Those artifacts now belong to the compatibility or archival lane unless they were produced by an explicitly requested compatibility run.
- They must not be cited as proof of the current public default path.

## Current Audit Interpretation

Under the temporary 2-source release contract:

- Public CLI, UI, history, docs, and validation instructions must describe the default path as `arXiv` + `bioRxiv`.
- Compatibility-only paths may still mention `medRxiv`, but they must say so explicitly.
- Current release signoff should be taken from the active pre-release bug sweep rather than from the earlier 3-source audit narrative.

## Current Reference Point

Use [docs/pre_release_bug_sweep_report.md](docs/pre_release_bug_sweep_report.md) as the current release-facing audit and recommendation once it is present. This rewritten audit file exists so older references do not continue to imply that `medRxiv` is part of the default public contract.
