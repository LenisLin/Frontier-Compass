from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from frontier_compass.cli.main import _load_command_defaults, _resolve_report_mode, build_parser
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.common.report_mode import (
    DEFAULT_REPORT_MODE,
    ENHANCED_REPORT_MODE,
    ZERO_TOKEN_COST_MODE,
    build_report_runtime_contract,
)
from frontier_compass.storage.schema import DailyDigest, PaperRecord, RankedPaper
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp, build_daily_run_summary


def test_report_runtime_contract_defaults_to_deterministic_zero_token() -> None:
    runtime = build_report_runtime_contract(DEFAULT_REPORT_MODE)

    assert runtime == {
        "requested_report_mode": "deterministic",
        "report_mode": "deterministic",
        "cost_mode": "zero-token",
        "enhanced_track": "",
        "enhanced_item_count": 0,
        "runtime_note": (
            "Zero-token run: fetching, ranking, recommendation summaries, exploration picks, and the current "
            "Frontier Report all use deterministic local logic only."
        ),
        "llm_requested": False,
        "llm_applied": False,
        "llm_provider": None,
        "llm_fallback_reason": None,
        "llm_seconds": None,
    }


def test_enhanced_report_mode_is_explicit_but_stays_zero_token_without_model() -> None:
    runtime = build_report_runtime_contract(ENHANCED_REPORT_MODE)

    assert runtime["requested_report_mode"] == "enhanced"
    assert runtime["report_mode"] == "deterministic"
    assert runtime["cost_mode"] == "zero-token"
    assert runtime["enhanced_track"] == ""
    assert runtime["enhanced_item_count"] == 0
    assert runtime["llm_requested"] is True
    assert runtime["llm_applied"] is False
    assert runtime["llm_provider"] is None
    assert runtime["llm_fallback_reason"] == "No model-assisted provider is configured for this run."
    assert runtime["llm_seconds"] is None
    assert "stayed deterministic and zero-token" in str(runtime["runtime_note"])


def test_enhanced_report_mode_preserves_configured_provider_in_llm_provenance() -> None:
    runtime = build_report_runtime_contract(ENHANCED_REPORT_MODE, llm_provider="openai")

    assert runtime["requested_report_mode"] == "enhanced"
    assert runtime["report_mode"] == "deterministic"
    assert runtime["cost_mode"] == "zero-token"
    assert runtime["llm_requested"] is True
    assert runtime["llm_applied"] is False
    assert runtime["llm_provider"] == "openai"
    assert runtime["llm_fallback_reason"] == "A model-assisted provider is configured, but the Frontier Report run stayed deterministic."
    assert runtime["llm_seconds"] is None


def test_cli_report_mode_resolution_prefers_cli_then_config(tmp_path: Path) -> None:
    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(json.dumps({"default_report_mode": "enhanced"}), encoding="utf-8")

    parser = build_parser()

    config_args = parser.parse_args(["run-daily", "--config", str(config_path)])
    loaded_config = _load_command_defaults(config_args)
    resolved_from_config = _resolve_report_mode(config_args, loaded_config)

    cli_args = parser.parse_args(
        [
            "run-daily",
            "--config",
            str(config_path),
            "--report-mode",
            "deterministic",
        ]
    )
    loaded_cli = _load_command_defaults(cli_args)
    resolved_from_cli = _resolve_report_mode(cli_args, loaded_cli)

    assert resolved_from_config.value == "enhanced"
    assert resolved_from_config.source == "config"
    assert resolved_from_cli.value == "deterministic"
    assert resolved_from_cli.source == "cli"


def test_daily_digest_serialization_preserves_report_runtime_fields() -> None:
    ranked = [_ranked_paper()]
    runtime = build_report_runtime_contract(ENHANCED_REPORT_MODE)
    frontier_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=1,
        **runtime,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=frontier_report,
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        total_fetched=1,
        **runtime,
    )

    restored = DailyDigest.from_mapping(digest.to_mapping())

    assert restored.requested_report_mode == "enhanced"
    assert restored.report_mode == "deterministic"
    assert restored.cost_mode == ZERO_TOKEN_COST_MODE
    assert restored.llm_requested is True
    assert restored.llm_applied is False
    assert restored.llm_provider is None
    assert restored.llm_fallback_reason == "No model-assisted provider is configured for this run."
    assert restored.enhanced_item_count == 0
    assert restored.frontier_report is not None
    assert restored.frontier_report.requested_report_mode == "enhanced"
    assert restored.frontier_report.report_mode == "deterministic"
    assert restored.frontier_report.llm_requested is True
    assert restored.frontier_report.llm_applied is False
    assert restored.frontier_report.deterministic_takeaways == restored.frontier_report.takeaways
    assert restored.frontier_report.deterministic_field_highlights == restored.frontier_report.field_highlights


def test_daily_run_summary_does_not_mark_default_runs_model_assisted() -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_ranked_paper()],
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        total_fetched=1,
    )

    summary = build_daily_run_summary(digest, cache_path=Path("data/cache/example.json"))

    assert summary.requested_report_mode == "deterministic"
    assert summary.report_mode == "deterministic"
    assert summary.cost_mode == "zero-token"
    assert summary.llm_requested is False
    assert summary.llm_applied is False
    assert summary.llm_provider is None
    assert summary.llm_fallback_reason is None
    assert summary.llm_seconds is None
    assert summary.zero_token is True
    assert summary.model_assisted is False


def test_load_daily_digest_backfills_runtime_contract_into_legacy_cache(tmp_path: Path) -> None:
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[_ranked_paper()],
        frontier_report=build_daily_frontier_report(
            paper_pool=[_ranked_paper().paper],
            ranked_papers=[_ranked_paper()],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="arxiv",
            mode=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            total_fetched=1,
        ),
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        total_fetched=1,
    )
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    payload = digest.to_mapping()
    payload.pop("requested_report_mode", None)
    payload.pop("report_mode", None)
    payload.pop("cost_mode", None)
    payload.pop("enhanced_track", None)
    payload.pop("enhanced_item_count", None)
    payload.pop("runtime_note", None)
    assert isinstance(payload["frontier_report"], dict)
    payload["frontier_report"].pop("requested_report_mode", None)
    payload["frontier_report"].pop("report_mode", None)
    payload["frontier_report"].pop("cost_mode", None)
    payload["frontier_report"].pop("enhanced_track", None)
    payload["frontier_report"].pop("enhanced_item_count", None)
    payload["frontier_report"].pop("runtime_note", None)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = FrontierCompassApp().load_daily_digest(cache_path)
    rewritten = json.loads(cache_path.read_text(encoding="utf-8"))

    assert loaded.report_mode == "deterministic"
    assert loaded.cost_mode == "zero-token"
    assert loaded.runtime_note
    assert loaded.llm_requested is False
    assert loaded.llm_applied is False
    assert loaded.llm_provider is None
    assert loaded.llm_fallback_reason is None
    assert rewritten["report_mode"] == "deterministic"
    assert rewritten["cost_mode"] == "zero-token"
    assert rewritten["runtime_note"] == loaded.runtime_note


def test_digest_for_report_mode_applies_model_assisted_frontier_report(monkeypatch) -> None:
    app = FrontierCompassApp()
    monkeypatch.setenv("FRONTIER_COMPASS_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FRONTIER_COMPASS_LLM_API_KEY", "secret-token")
    monkeypatch.setenv("FRONTIER_COMPASS_LLM_MODEL", "gpt-5-mini")
    ranked = [_ranked_paper()]
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=build_daily_frontier_report(
            paper_pool=[item.paper for item in ranked],
            ranked_papers=ranked,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="arxiv",
            mode=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            total_fetched=1,
        ),
        searched_categories=("q-bio",),
        per_category_counts={"q-bio": 1},
        total_fetched=1,
    )

    def fake_build_model_assisted_frontier_report(frontier_report, *, settings):  # type: ignore[no-untyped-def]
        assert settings.provider_label == "openai-compatible"
        return type(
            "LLMResult",
            (),
            {
                "report": replace(
                    frontier_report,
                    takeaways=("Model-assisted takeaway.",),
                    field_highlights=tuple(
                        replace(item, why="Model-assisted rationale.")
                        for item in frontier_report.field_highlights
                    ),
                ),
                "enhanced_item_count": 3,
            },
        )()

    monkeypatch.setattr(
        "frontier_compass.ui.app.build_model_assisted_frontier_report",
        fake_build_model_assisted_frontier_report,
    )

    updated = app._digest_for_report_mode(
        digest,
        report_mode=ENHANCED_REPORT_MODE,
    )

    assert updated.report_mode == "enhanced"
    assert updated.cost_mode == "model-assisted"
    assert updated.llm_applied is True
    assert updated.llm_provider == "openai-compatible"
    assert updated.enhanced_item_count == 3
    assert updated.frontier_report is not None
    assert updated.frontier_report.takeaways == ("Model-assisted takeaway.",)
    assert updated.frontier_report.field_highlights[0].why == "Model-assisted rationale."


def test_digest_for_report_mode_restores_deterministic_frontier_report_when_enhanced_is_unavailable() -> None:
    app = FrontierCompassApp()
    ranked = [_ranked_paper()]
    frontier_report = build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=1,
    )
    mutated_report = replace(
        frontier_report,
        requested_report_mode="enhanced",
        report_mode="enhanced",
        cost_mode="model-assisted",
        enhanced_track="frontier-report",
        enhanced_item_count=2,
        runtime_note="Enhanced.",
        llm_requested=True,
        llm_applied=True,
        llm_provider="openai-compatible",
        llm_fallback_reason=None,
        llm_seconds=1.0,
        takeaways=("Model-assisted takeaway.",),
        field_highlights=tuple(
            replace(item, why="Model-assisted rationale.")
            for item in frontier_report.field_highlights
        ),
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=mutated_report,
        searched_categories=("q-bio",),
        per_category_counts={"q-bio": 1},
        total_fetched=1,
        requested_report_mode="enhanced",
        report_mode="enhanced",
        cost_mode="model-assisted",
        enhanced_track="frontier-report",
        enhanced_item_count=2,
        llm_requested=True,
        llm_applied=True,
        llm_provider="openai-compatible",
        llm_seconds=1.0,
    )

    restored = app._digest_for_report_mode(digest, report_mode=DEFAULT_REPORT_MODE)

    assert restored.report_mode == "deterministic"
    assert restored.cost_mode == ZERO_TOKEN_COST_MODE
    assert restored.llm_applied is False
    assert restored.frontier_report is not None
    assert restored.frontier_report.takeaways == frontier_report.takeaways
    assert restored.frontier_report.field_highlights == frontier_report.field_highlights


def _ranked_paper() -> RankedPaper:
    published = date(2026, 3, 24)
    return RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.30001v1",
            title="Deterministic frontier runtime fixture",
            summary="Deterministic local reporting fixture.",
            authors=("A Researcher",),
            categories=("q-bio.GN", "cs.LG"),
            published=published,
            updated=published,
            url="https://arxiv.org/abs/2603.30001",
        ),
        score=0.84,
        reasons=("deterministic fixture",),
        recommendation_summary="Deterministic local summary.",
    )
