# FrontierCompass Tutorial

FrontierCompass is easiest to understand as a local reading desk, not as a generic scraping framework. You point it at your local defaults, let it build or reuse a daily run, and then read the result through the Streamlit homepage, the CLI, or a saved HTML report.

FrontierCompass 最适合被理解成一个“本地文献阅读工作台”。你不需要先理解全部内部模块，只需要先跑通默认路径，再逐步解锁个性化、历史回看和高级参数。

> [!TIP]
> Best first run: `frontier-compass ui`

> [!NOTE]
> Your local defaults file `configs/user_defaults.json` and runtime artifacts under `data/` are intentionally kept out of normal Git commits.

## Who This Is For

Use FrontierCompass if you want:

- a local-first way to scan new biomedical papers
- a Zotero-aware shortlist without building a hosted recommendation system
- reproducible HTML and JSON artifacts for each run
- one tool that works from UI, CLI, and Python

If you are looking for a production ingestion platform, background scheduler, or hosted web app, that is out of scope for v0.1.

如果你想要的是线上服务、自动后台轮询或大规模生产级抓取，这一版并不是为那个目标设计的。

## What You Need Before Starting

- Python `>=3.10`
- a local clone of this repository
- optional but recommended: a readable local Zotero SQLite database or a Zotero `CSL JSON` export

You do not need API keys for the default path. The built-in default report mode is deterministic and zero-token.

默认模式不需要模型 API key。只有在你显式使用 `--report-mode enhanced` 时，才需要配置兼容 OpenAI 的模型端点。

## Install

```bash
git clone <your-repo-url>
cd FrontierCompass
pip install -e .
```

Check that the CLI is available:

```bash
frontier-compass --help
```

If your shell does not expose the installed entrypoint yet, use:

```bash
PYTHONPATH=src python -m frontier_compass.cli.main --help
```

> [!IMPORTANT]
> If `frontier-compass --help` works, your editable install is wired correctly.

## Configure Local Defaults

Start from the checked-in example:

```bash
cp configs/user_defaults.example.json configs/user_defaults.json
```

The most useful keys for daily use are:

| Key | What it does |
| --- | --- |
| `default_zotero_db_path` | Preferred live read-only Zotero SQLite path |
| `default_zotero_export_path` | Preferred reusable Zotero export snapshot path |
| `default_report_mode` | Default report mode, usually `deterministic` |
| `default_max_results` | Default display/debug cap for supported workflows |
| `default_allow_stale_cache` | Whether stale cache fallback is allowed |
| `default_llm_base_url` | Optional OpenAI-compatible endpoint for enhanced mode |
| `default_llm_api_key` | Optional API key for enhanced mode |
| `default_llm_model` | Optional model id for enhanced mode |

Minimal example:

```json
{
  "default_report_mode": "deterministic",
  "default_max_results": 100,
  "default_zotero_db_path": "/path/to/zotero.sqlite",
  "default_allow_stale_cache": true
}
```

If you do not have Zotero ready, you can omit the Zotero keys and still use FrontierCompass with the baseline profile.

<details>
<summary><strong>Why this file is local-only</strong></summary>

`configs/user_defaults.json` is where you put machine-specific paths, optional model credentials, and personal workflow preferences. It is meant to stay local, while `configs/user_defaults.example.json` is the shareable template.

</details>

## First Successful UI Launch

This is the best first command for most users:

```bash
frontier-compass ui
```

What happens:

1. FrontierCompass loads `configs/user_defaults.json` if present.
2. It resolves the default profile basis from your Zotero settings.
3. It prewarms or reuses the local artifact set cache-first.
4. It launches the Streamlit homepage.

The homepage is intentionally reading-first. The key lanes are:

1. `Daily Full Report`
2. `Most Relevant to Your Zotero`
3. `Other Frontier Signals`

There is also an advanced compatibility area for source overrides, display limits, report mode selection, and custom bundle management, but new users usually do not need it on day one.

首屏应该先被当成“日报阅读器”使用，而不是“参数控制台”。建议你先直接读这三块内容，再去展开高级区。

### A good first-reading rhythm

| Step | What to read | Why |
| --- | --- | --- |
| 1 | `Daily Full Report` | Get field context first |
| 2 | `Most Relevant to Your Zotero` | Narrow into your current interests |
| 3 | `Other Frontier Signals` | Catch papers worth noticing outside your main lane |

## First CLI Run

If you want a dated, reproducible run and saved artifacts right away:

```bash
frontier-compass run-daily --today 2026-04-07
```

This command:

- uses the default public bundle if you do not override it
- materializes or reuses the current digest
- ensures the HTML report exists
- writes JSON and HTML artifacts into the expected local folders

Useful follow-up:

```bash
frontier-compass history --limit 5
```

That gives you a compact recent-run view with requested date, effective date, and saved artifact paths.

## Understand The Default Bundle

The default public bundle is:

- bundle id: `biomedical`
- sources: `arXiv + bioRxiv`

`medRxiv` is not part of the default public onboarding path. It remains available only through compatibility or advanced workflows such as `--mode biomedical-multisource`.

这个区分很重要。README 和 tutorial 都优先讲“默认支持路径”，把兼容路径收进后面，避免第一次上手就被过多模式打断。

## How Zotero Personalization Works

FrontierCompass supports three profile sources:

| Source | Meaning |
| --- | --- |
| `baseline` | built-in deterministic profile |
| `zotero_export` | profile built from a local reusable `CSL JSON` export |
| `live_zotero_db` | profile built directly from a local read-only Zotero SQLite DB |

Default resolution order:

1. use `default_zotero_db_path` if it is readable
2. otherwise use `default_zotero_export_path` or a reusable snapshot
3. otherwise fall back to `baseline`

Advanced examples:

```bash
frontier-compass run-daily --today 2026-04-07 --profile-source baseline
frontier-compass run-daily --today 2026-04-07 --profile-source zotero_export --zotero-export path/to/library.csl.json
frontier-compass run-daily --today 2026-04-07 --profile-source live_zotero_db --zotero-db-path /path/to/zotero.sqlite
frontier-compass run-daily --today 2026-04-07 --profile-source live_zotero_db --zotero-collection "Tumor microenvironment"
```

Practical advice:

- if you want the smoothest UI experience, configure Zotero once in `configs/user_defaults.json`
- if you want a portable snapshot, use an export-backed flow
- if you want explicit control in automation, pass `--profile-source` on the CLI

## Reading The Three Main Lanes

### Daily Full Report

Use this lane to understand what happened across the field in the selected run. It is the broadest view and the right starting point when you have not looked at today’s papers yet.

### Most Relevant to Your Zotero

Use this lane when you want a faster shortlist aligned with your existing library, collections, and recurring interests.

### Other Frontier Signals

Use this lane to avoid tunnel vision. It surfaces additional papers that may not rank highest for your current profile but still look strategically interesting.

推荐使用顺序是：

1. 先扫 `Daily Full Report`
2. 再看 `Most Relevant to Your Zotero`
3. 最后补看 `Other Frontier Signals`

## Common Commands You Will Actually Use

### Open the reading UI

```bash
frontier-compass ui
```

### Materialize a dated daily run

```bash
frontier-compass run-daily --today 2026-04-07
```

### Force a fresh fetch

```bash
frontier-compass run-daily --today 2026-04-07 --refresh
```

### Inspect recent history

```bash
frontier-compass history --limit 10
```

### Print the exact Streamlit launch command

```bash
frontier-compass ui --print-command --today 2026-04-07
```

### Run a range workflow

```bash
frontier-compass run-daily --mode biomedical --start-date 2026-04-01 --end-date 2026-04-07 --fetch-scope range-full
```

### Use an advanced compatibility bundle

```bash
frontier-compass run-daily --mode ai-for-medicine --today 2026-04-07
frontier-compass run-daily --mode biomedical-multisource --today 2026-04-07
```

## Where Files Go

FrontierCompass keeps outputs in predictable places:

| Path | Meaning |
| --- | --- |
| `configs/` | local defaults and checked-in examples |
| `data/raw/source_snapshots/` | normalized per-day source snapshots |
| `data/raw/zotero/` | local Zotero export snapshots and discovery status |
| `data/cache/` | JSON cache artifacts |
| `reports/daily/` | saved HTML daily reports |
| `reports/weekly/` | weekly rollups when added |

This is deliberate. Runtime artifacts should not be written to the repository root.

> [!TIP]
> If you are preparing a GitHub push, you normally want to share code and docs, not files under `data/` or your local `configs/user_defaults.json`.

## Troubleshooting

### `frontier-compass` is not found

Use:

```bash
PYTHONPATH=src python -m frontier_compass.cli.main --help
```

If that works, your editable install or shell PATH exposure needs attention.

### The UI opens, but personalization is missing

Usually one of these is true:

- `default_zotero_db_path` is unreadable
- `default_zotero_export_path` does not exist
- no reusable snapshot is available yet

The app should still work with the baseline profile.

### I want exact provenance for a run

Read:

- [Provenance and Runtime Notes](provenance.md)
- [Live Validation Guide](live_validation.md)

### I only want the intended beginner path

Stick to:

1. `pip install -e .`
2. copy and edit `configs/user_defaults.json`
3. `frontier-compass ui`
4. `frontier-compass run-daily --today YYYY-MM-DD`
5. `frontier-compass history`

Ignore `daily`, `deliver-daily`, `demo-report`, and `demo-ranking` until you actually need compatibility workflows.

## Where To Go Next

- Start with the [README](../README.md) for the short version.
- Use [provenance.md](provenance.md) when you need to understand requested vs effective date, cache reuse, and artifact-source labeling.
- Use [live_validation.md](live_validation.md) when you need to verify network-touching behavior against real sources.

Once the default path feels natural, the advanced bundle and report-mode controls will make much more sense.
