from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontier_compass.api import DailyRunResult, FrontierCompassRunner, LocalUISession
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.storage.schema import (
    DailyDigest,
    PaperRecord,
    RankedPaper,
    RunHistoryEntry,
    RunTimings,
    SourceRunStats,
)
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, BIOMEDICAL_MULTISOURCE_MODE, FrontierCompassApp
from frontier_compass.ui.streamlit_app import _load_startup_request
from frontier_compass.ui.streamlit_support import render_external_link


class _RecordingStreamlit:
    def __init__(self) -> None:
        self.link_button_calls: list[tuple[str, str, dict[str, object]]] = []
        self.markdown_calls: list[tuple[str, bool]] = []

    def link_button(
        self,
        label: str,
        url: str,
        *,
        help: str | None = None,
        use_container_width: bool | None = None,
    ) -> str:
        self.link_button_calls.append(
            (
                label,
                url,
                {
                    "help": help,
                    "use_container_width": use_container_width,
                },
            )
        )
        return "rendered"

    def markdown(self, body: str, *, unsafe_allow_html: bool = False) -> None:
        self.markdown_calls.append((body, unsafe_allow_html))


class _MarkdownOnlyStreamlit:
    def __init__(self) -> None:
        self.markdown_calls: list[tuple[str, bool]] = []

    def markdown(self, body: str, *, unsafe_allow_html: bool = False) -> None:
        self.markdown_calls.append((body, unsafe_allow_html))


class _TypeErrorStreamlit(_RecordingStreamlit):
    def link_button(
        self,
        label: str,
        url: str,
        *,
        help: str | None = None,
        use_container_width: bool | None = None,
    ) -> str:
        raise TypeError("unexpected optional keyword")


def test_render_external_link_drops_unsupported_kwargs(monkeypatch) -> None:
    recorder = _RecordingStreamlit()
    monkeypatch.setattr("frontier_compass.ui.streamlit_support.st", recorder)

    rendered_as_button = render_external_link(
        "Open abstract",
        "https://arxiv.org/abs/2603.20001",
        help="Open the paper abstract.",
        key="top-link-1",
        type="primary",
        use_container_width=True,
    )

    assert rendered_as_button is True
    assert recorder.link_button_calls == [
        (
            "Open abstract",
            "https://arxiv.org/abs/2603.20001",
            {
                "help": "Open the paper abstract.",
                "use_container_width": True,
            },
        )
    ]
    assert recorder.markdown_calls == []


def test_render_external_link_falls_back_without_link_button(monkeypatch) -> None:
    recorder = _MarkdownOnlyStreamlit()
    monkeypatch.setattr("frontier_compass.ui.streamlit_support.st", recorder)

    rendered_as_button = render_external_link("Open report", "file:///tmp/report.html", use_container_width=True)

    assert rendered_as_button is False
    assert recorder.markdown_calls == [
        (
            '<div class="fc-link-fallback"><a class="fc-link-fallback-link" href="file:///tmp/report.html" target="_blank" rel="noopener noreferrer">Open report</a></div>',
            True,
        )
    ]


def test_render_external_link_falls_back_after_type_error(monkeypatch) -> None:
    recorder = _TypeErrorStreamlit()
    monkeypatch.setattr("frontier_compass.ui.streamlit_support.st", recorder)

    rendered_as_button = render_external_link("Open report", "file:///tmp/report.html", use_container_width=True)

    assert rendered_as_button is False
    assert recorder.markdown_calls == [
        (
            '<div class="fc-link-fallback"><a class="fc-link-fallback-link" href="file:///tmp/report.html" target="_blank" rel="noopener noreferrer">Open report</a></div>',
            True,
        )
    ]


def test_load_startup_request_derives_range_and_zotero_profile_from_paths() -> None:
    request = _load_startup_request(
        [
            "--source",
            "biomedical-multisource",
            "--requested-date",
            "2026-03-24",
            "--start-date",
            "2026-03-20",
            "--end-date",
            "2026-03-24",
            "--zotero-db-path",
            "/tmp/zotero.sqlite",
        ]
    )

    assert request.selected_source == "biomedical-multisource"
    assert request.fetch_scope == "range-full"
    assert request.effective_profile_source == "zotero"
    assert request.request_label == "2026-03-20 -> 2026-03-24"
    assert "Zotero is auto-selected because a local Zotero library is available." == request.auto_profile_source_note


def test_load_startup_request_reorders_inverted_range_dates() -> None:
    request = _load_startup_request(
        [
            "--source",
            "biomedical-multisource",
            "--requested-date",
            "2026-03-24",
            "--start-date",
            "2026-03-24",
            "--end-date",
            "2026-03-20",
        ]
    )

    assert request.fetch_scope == "range-full"
    assert request.requested_date == date(2026, 3, 20)
    assert request.start_date == date(2026, 3, 20)
    assert request.end_date == date(2026, 3, 24)
    assert request.request_label == "2026-03-20 -> 2026-03-24"


def test_render_external_link_ignores_empty_urls(monkeypatch) -> None:
    recorder = _RecordingStreamlit()
    monkeypatch.setattr("frontier_compass.ui.streamlit_support.st", recorder)

    rendered_as_button = render_external_link("Open report", "", use_container_width=True)

    assert rendered_as_button is False
    assert recorder.link_button_calls == []
    assert recorder.markdown_calls == []


def test_load_startup_request_parses_cli_style_streamlit_args() -> None:
    request = _load_startup_request(
        [
            "--source",
            "cs.LG",
            "--requested-date",
            "2026-03-24",
            "--max-results",
            "60",
            "--report-mode",
            "enhanced",
            "--zotero-export",
            "configs/sample.csl.json",
            "--zotero-collection",
            "Tumor microenvironment",
            "--zotero-collection",
            "Foundation models",
            "--no-stale-cache",
        ]
    )

    assert request.selected_source == "cs.LG"
    assert request.requested_date == date(2026, 3, 24)
    assert request.max_results == 60
    assert request.report_mode == "enhanced"
    assert request.zotero_export_path == Path("configs/sample.csl.json")
    assert request.zotero_collections == ("Tumor microenvironment", "Foundation models")
    assert request.allow_stale_cache is False


def test_load_startup_request_parses_skip_initial_load_flag() -> None:
    request = _load_startup_request(
        [
            "--source",
            "biomedical-latest",
            "--requested-date",
            "2026-03-24",
            "--skip-initial-load",
        ]
    )

    assert request.skip_initial_load is True


def test_streamlit_request_switching_stays_staged_until_apply(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_current.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_current.json"
    cache_path.write_text("{}", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        calls.append(dict(kwargs))
        requested_source = str(kwargs["source"])
        requested_date = kwargs["requested_date"]
        assert isinstance(requested_date, date)
        digest = DailyDigest(
            source="multisource" if requested_source == "ai-for-medicine" else "arxiv",
            category=requested_source,
            target_date=requested_date,
            generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
            feed_url="https://export.arxiv.org/api/query",
            profile=FrontierCompassApp.daily_profile(requested_source),
            ranked=[],
            frontier_report=_frontier_report_for([], requested_date=requested_date, effective_date=requested_date),
            requested_date=requested_date,
            effective_date=requested_date,
            mode_label=requested_source,
            mode_kind="source-bundle" if requested_source == "ai-for-medicine" else "source-bundle",
        )
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(calls) == 1
    assert calls[0]["refresh"] is False

    assert _selectbox(at, "Source bundle", key="fc-launch-source-bundle").key == "fc-launch-source-bundle"
    assert _radio(at, "Time scope", key="fc-launch-time-scope").key == "fc-launch-time-scope"
    _selectbox(at, "Source bundle", key="fc-launch-source-bundle").select("ai-for-medicine")
    at.run(timeout=15)

    assert len(calls) == 1
    assert any("Launcher choices are staged." in item.value for item in at.info)

    _button(at, "Daily Recommendation").click()
    at.run(timeout=15)

    assert len(calls) == 2
    assert calls[-1]["source"] == "ai-for-medicine"
    assert calls[-1]["refresh"] is False


def test_streamlit_app_renders_link_actions_without_runtime_exception(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    cache_path.write_text("{}", encoding="utf-8")
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher", "B Collaborator"),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.20001",
                ),
                score=0.88,
                reasons=(
                    "biomedical evidence: transcriptomics, proteomics",
                    "topic match: q-bio, q-bio.gn",
                ),
                recommendation_summary="Strong biomedical match for reviewer triage.",
            ),
        ],
        frontier_report=_frontier_report_for(
            [
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.20001v1",
                        title="Single-cell atlas alignment with multimodal omics",
                        summary="Atlas integration for transcriptomics and proteomics.",
                        authors=("A Researcher", "B Collaborator"),
                        categories=("q-bio.GN", "q-bio.QM"),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.20001",
                    ),
                    score=0.88,
                    reasons=(
                        "biomedical evidence: transcriptomics, proteomics",
                        "topic match: q-bio, q-bio.gn",
                    ),
                    recommendation_summary="Strong biomedical match for reviewer triage.",
                )
            ],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 1},
        total_fetched=1,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        mode_notes="Hybrid biomedical reviewer mode using the fixed q-bio bundle plus fixed broader arXiv API searches.",
        search_profile_label="broader-biomedical-discovery-v1",
        search_queries=(
            "((cat:q-bio OR cat:cs.CV) AND (all:biomedical OR all:medical OR all:clinical OR all:pathology))",
        ),
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    assert [tab.label for tab in at.tabs] == ["Digest", "Frontier Report", "History"]
    assert any("## Personalized Digest" in item.value for item in at.markdown)
    assert any("## Frontier Report" in item.value for item in at.markdown)
    assert any("## History" in item.value for item in at.markdown)
    assert any("Top recommendations" in item.value for item in at.markdown)
    assert any("Repeated themes" in item.value for item in at.markdown)
    assert any("genomics / transcriptomics / single-cell" in item.value for item in at.markdown)
    assert any("Why it surfaced" in item.value for item in at.markdown)
    assert any(getattr(item, "label", "") == "Score details" for item in at.expander)
    assert _expander(at, "Request window").proto.expanded is False
    assert _expander(at, "Custom bundles").proto.expanded is False
    assert _expander(at, "Full run details").proto.expanded is False
    assert _expander(at, "Run details and provenance").proto.expanded is False
    assert any("Status: fresh source fetch." in item.value for item in at.success)
    assert any("Profile source" in item.label for item in at.metric)
    assert any(
        item.value.startswith("Source mix: ")
        and "arXiv" in item.value
        and "bioRxiv" in item.value
        for item in at.caption
    )
    assert any("Active profile basis:" in item.value for item in at.caption)
    link_labels = [item.proto.label for item in at if getattr(item, "type", "") == "link_button"]
    assert "Open current HTML report" in link_labels
    assert "Open current cache JSON" in link_labels
    assert "Open top arXiv abstract" in link_labels
    assert "Open arXiv abstract" in link_labels
    assert any("Session briefing" in item.value for item in at.markdown)
    assert _button(at, "Daily Recommendation").label == "Daily Recommendation"
    assert _button(at, "Daily Report").label == "Daily Report"


def test_streamlit_app_hides_missing_current_artifacts(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "missing_frontier_report.html"
    cache_path = tmp_path / "missing_frontier_cache.json"
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher",),
                    categories=("q-bio.GN",),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.20001",
                ),
                score=0.88,
                recommendation_summary="Strong biomedical match for reviewer triage.",
            ),
        ],
        frontier_report=_frontier_report_for(
            [
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.20001v1",
                        title="Single-cell atlas alignment with multimodal omics",
                        summary="Atlas integration for transcriptomics and proteomics.",
                        authors=("A Researcher",),
                        categories=("q-bio.GN",),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.20001",
                    ),
                    score=0.88,
                    recommendation_summary="Strong biomedical match for reviewer triage.",
                )
            ],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
    )

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    link_labels = [item.proto.label for item in at if getattr(item, "type", "") == "link_button"]
    assert "Open current HTML report" not in link_labels
    assert "Open current cache JSON" not in link_labels
    assert any("Current HTML report is missing for this run." in item.value for item in at.caption)
    assert any("Report missing" in item.value for item in at.caption)
    assert any("Cache missing" in item.value for item in at.caption)


def test_streamlit_app_renders_empty_state_when_session_load_fails(monkeypatch) -> None:
    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        raise RuntimeError("arXiv request failed with HTTP 429 Too Many Requests")

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    assert [tab.label for tab in at.tabs] == ["Digest", "Frontier Report", "History"]
    assert any("Unable to load a reviewer digest" in item.value for item in at.error)
    assert any("No digest is loaded yet." in item.value for item in at.markdown)
    assert any("No frontier report is available yet." in item.value for item in at.markdown)
    assert any("Startup note." in item.value for item in at.markdown)


def test_streamlit_app_renders_recent_runs_history_section(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    eml_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.eml"
    eml_path.write_text("dry-run email", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    cache_path.write_text("{}", encoding="utf-8")
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher", "B Collaborator"),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.20001",
                ),
                score=0.88,
                recommendation_summary="Strong biomedical match for reviewer triage.",
            ),
        ],
        frontier_report=_frontier_report_for(
            [
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.20001v1",
                        title="Single-cell atlas alignment with multimodal omics",
                        summary="Atlas integration for transcriptomics and proteomics.",
                        authors=("A Researcher", "B Collaborator"),
                        categories=("q-bio.GN", "q-bio.QM"),
                        published=date(2026, 3, 24),
                        url="https://arxiv.org/abs/2603.20001",
                    ),
                    score=0.88,
                    reasons=(
                        "biomedical evidence: transcriptomics, proteomics",
                        "topic match: q-bio, q-bio.gn",
                    ),
                    recommendation_summary="Strong biomedical match for reviewer triage.",
                )
            ],
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
        ),
        searched_categories=("q-bio", "q-bio.GN", "cs.CV"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 1},
        total_fetched=1,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
    recent_runs = [
        RunHistoryEntry(
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            category=BIOMEDICAL_LATEST_MODE,
            mode_label="Biomedical latest available",
            mode_kind="latest-available-hybrid",
            profile_basis="biomedical baseline",
            zotero_export_name="sample_library.csl.json",
            fetch_status="fresh source fetch",
            same_date_cache_reused=False,
            stale_cache_fallback_used=False,
            ranked_count=1,
            exploration_pick_count=1,
            cache_path=str(cache_path),
            report_path=str(report_path),
            eml_path=str(eml_path),
            generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        )
    ]

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
            recent_history=recent_runs,
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    link_labels = [item.proto.label for item in at if getattr(item, "type", "") == "link_button"]
    assert "Open recent report" in link_labels
    assert "Open recent cache" in link_labels
    assert "Open recent .eml" in link_labels


def test_streamlit_frontier_tab_shows_source_mix_and_timings(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_multisource_biomedical-multisource_2026-03-24.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_multisource_biomedical-multisource_2026-03-24.json"
    cache_path.write_text("{}", encoding="utf-8")
    ranked = [
        RankedPaper(
            paper=PaperRecord(
                source="arxiv",
                identifier="2603.24001v1",
                title="Frontier source mix fixture",
                summary="Frontier source mix fixture.",
                authors=("A Researcher",),
                categories=("q-bio.GN",),
                published=date(2026, 3, 24),
                url="https://arxiv.org/abs/2603.24001",
            ),
            score=0.88,
            recommendation_summary="Source mix fixture.",
        )
    ]
    source_run_stats = (
        SourceRunStats(
            source="arxiv",
            fetched_count=1,
            displayed_count=1,
            status="ready",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.8, parse_seconds=0.2, total_seconds=1.0),
        ),
        SourceRunStats(
            source="biorxiv",
            fetched_count=0,
            displayed_count=0,
            status="empty",
            cache_status="fresh",
            timings=RunTimings(network_seconds=0.1, total_seconds=0.1),
        ),
        SourceRunStats(
            source="medrxiv",
            fetched_count=0,
            displayed_count=0,
            status="failed",
            cache_status="same-day-cache",
            error="medRxiv unavailable",
            timings=RunTimings(network_seconds=0.2, total_seconds=0.2),
        ),
    )
    frontier_report = replace(
        build_daily_frontier_report(
            paper_pool=[item.paper for item in ranked],
            ranked_papers=ranked,
            requested_date=date(2026, 3, 24),
            effective_date=date(2026, 3, 24),
            source="multisource",
            mode=BIOMEDICAL_MULTISOURCE_MODE,
            mode_label="Biomedical multisource",
            mode_kind="multisource",
            total_fetched=1,
        ),
        source_run_stats=source_run_stats,
        run_timings=RunTimings(
            network_seconds=1.1,
            parse_seconds=0.2,
            rank_seconds=0.3,
            report_seconds=0.4,
            total_seconds=2.0,
        ),
        report_status="partial",
        report_error="medRxiv unavailable",
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
    )
    digest = DailyDigest(
        source="multisource",
        category=BIOMEDICAL_MULTISOURCE_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_MULTISOURCE_MODE),
        ranked=ranked,
        frontier_report=frontier_report,
        source_run_stats=source_run_stats,
        run_timings=frontier_report.run_timings,
        source_counts={"arxiv": 1, "biorxiv": 0, "medrxiv": 0},
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
        mode_label="Biomedical multisource",
        mode_kind="multisource",
        report_status="partial",
        report_error="medRxiv unavailable",
    )

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    assert any("Source mix: arXiv 1 shown / 1 fetched, bioRxiv 0 shown / 0 fetched, medRxiv 0 shown / 0 fetched" in item.value for item in at.caption)
    assert any("Source stats: arXiv fetched 1 / retained 1 [live-success; ready; fresh]" in item.value for item in at.caption)
    assert any("bioRxiv fetched 0 / retained 0 [live-zero; empty; fresh]" in item.value for item in at.caption)
    assert any("medRxiv fetched 0 / retained 0 [unknown-legacy; failed; same-day-cache]" in item.value for item in at.caption)
    assert any("Current fetch status: fresh source fetch." in item.value for item in at.caption)
    assert any("Run timings: network 1.10s | parse 0.20s | rank 0.30s | report 0.40s | total 2.00s" in item.value for item in at.caption)
    assert any("Report availability in this session: yes." in item.value for item in at.caption)
    assert any("Report note: medRxiv unavailable" in item.value for item in at.caption)


def test_streamlit_top_recommendations_stay_score_first_when_full_list_sorts_newest(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    cache_path.write_text("{}", encoding="utf-8")

    imaging_lead = "Sparse Autoencoders for Medical Imaging"
    imaging_second = "Radiology Distillation for CT Cohorts"
    genomics_title = "Single-cell Transcriptomics Atlas Integration"
    pathology_title = "Whole-slide Histopathology Reasoning"

    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            _ranked_paper(
                identifier="2603.21001v1",
                title=imaging_lead,
                summary="Medical imaging workflow for MRI and CT review.",
                categories=("cs.CV",),
                published=date(2026, 3, 20),
                score=0.91,
            ),
            _ranked_paper(
                identifier="2603.21002v1",
                title=imaging_second,
                summary="Radiology and CT pipeline for medical imaging.",
                categories=("cs.CV",),
                published=date(2026, 3, 20),
                score=0.9,
            ),
            _ranked_paper(
                identifier="2603.21003v1",
                title="Zero-shot Chest Scan Segmentation",
                summary="Medical imaging benchmark for chest scan segmentation.",
                categories=("cs.CV",),
                published=date(2026, 3, 20),
                score=0.89,
            ),
            _ranked_paper(
                identifier="2603.21004v1",
                title=genomics_title,
                summary="Genomics and transcriptomics workflow for a single-cell atlas.",
                categories=("q-bio.GN", "cs.LG"),
                published=date(2026, 3, 24),
                score=0.88,
            ),
            _ranked_paper(
                identifier="2603.21005v1",
                title=pathology_title,
                summary="Pathology and whole-slide microscopy pipeline for diagnostics.",
                categories=("cs.CV",),
                published=date(2026, 3, 24),
                score=0.87,
            ),
        ],
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 4, "cs.LG": 1},
        total_fetched=6,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)
    assert _selectbox(at, "Sort order", key="fc-digest-sort-order").key == "fc-digest-sort-order"
    _selectbox(at, "Sort order", key="fc-digest-sort-order").select("Newest first")
    at.run(timeout=15)

    assert len(at.exception) == 0

    title_positions = {
        title: next(i for i, item in enumerate(at.markdown) if title in item.value)
        for title in (imaging_lead, imaging_second, genomics_title, pathology_title)
    }
    full_titles = [
        item.value
        for item in at.markdown
        if item.value.startswith("### ")
        and any(title in item.value for title in (imaging_lead, imaging_second, genomics_title, pathology_title))
    ]

    assert title_positions[imaging_lead] < title_positions[imaging_second] < title_positions[genomics_title] < title_positions[pathology_title]
    assert genomics_title in full_titles[0]
    assert pathology_title in full_titles[1]
    assert imaging_lead in full_titles[2]


def test_streamlit_app_renders_exploration_section(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.json"
    cache_path.write_text("{}", encoding="utf-8")
    exploration_pick = _ranked_paper(
        identifier="2603.21109v1",
        title="Exploration lane microscopy fixture",
        summary="Microscopy-led exploration fixture outside the main shortlist.",
        categories=("cs.CV", "cs.AI"),
        published=date(2026, 3, 24),
        score=0.41,
    )
    digest = DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            _ranked_paper(identifier="2603.21001v1", title="Sparse Autoencoders for Medical Imaging", summary="Medical imaging workflow for MRI and CT review.", categories=("cs.CV",), published=date(2026, 3, 20), score=0.91),
            _ranked_paper(identifier="2603.21002v1", title="Radiology Distillation for CT Cohorts", summary="Radiology and CT pipeline for medical imaging.", categories=("cs.CV",), published=date(2026, 3, 20), score=0.90),
            _ranked_paper(identifier="2603.21003v1", title="Whole-slide Histopathology Reasoning", summary="Pathology and whole-slide microscopy pipeline for diagnostics.", categories=("cs.CV",), published=date(2026, 3, 24), score=0.89),
            _ranked_paper(identifier="2603.21004v1", title="Single-cell Transcriptomics Atlas Integration", summary="Genomics and transcriptomics workflow for a single-cell atlas.", categories=("q-bio.GN", "cs.LG"), published=date(2026, 3, 24), score=0.88),
            _ranked_paper(identifier="2603.21005v1", title="Clinical Tabular Learning for EHR Cohorts", summary="Clinical tabular modeling over patient cohorts.", categories=("cs.LG",), published=date(2026, 3, 24), score=0.87),
            _ranked_paper(identifier="2603.21006v1", title="Protein Structure Priors for Biomolecular Discovery", summary="Protein biomolecular priors for therapeutic discovery.", categories=("q-bio.BM", "cs.LG"), published=date(2026, 3, 24), score=0.86),
            _ranked_paper(identifier="2603.21007v1", title="General Biomedical Modeling Notes", summary="Biomedical methods for translational studies.", categories=("q-bio.QM",), published=date(2026, 3, 24), score=0.85),
            _ranked_paper(identifier="2603.21008v1", title="Microscopy-guided Pathology Segmentation", summary="Microscopy and pathology segmentation for whole-slide review.", categories=("cs.CV",), published=date(2026, 3, 24), score=0.84),
            exploration_pick,
        ],
        exploration_picks=[exploration_pick],
        searched_categories=("q-bio", "q-bio.GN", "cs.CV", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.CV": 5, "cs.LG": 3},
        total_fetched=9,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return _local_ui_session(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
        )

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    app_path = Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)

    assert len(at.exception) == 0
    assert any("## Exploration" in item.value for item in at.markdown)
    assert any("Why it's exploratory" in item.value for item in at.markdown)
    assert any("Exploration lane microscopy fixture" in item.value for item in at.markdown)


def _ranked_paper(
    *,
    identifier: str,
    title: str,
    summary: str,
    categories: tuple[str, ...],
    published: date,
    score: float,
) -> RankedPaper:
    return RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier=identifier,
            title=title,
            summary=summary,
            authors=("A Researcher", "B Collaborator"),
            categories=categories,
            published=published,
            updated=published,
            url=f"https://arxiv.org/abs/{identifier.split('v', 1)[0]}",
        ),
        score=score,
        reasons=(
            "biomedical evidence: matched deterministic test fixture",
            "topic match: reviewer shortlist contract",
        ),
        recommendation_summary="Deterministic streamlit reviewer contract fixture.",
    )


def _selectbox(at: AppTest, label: str, *, key: str | None = None):
    for widget in at.selectbox:
        if widget.label == label and (key is None or widget.key == key):
            return widget
    raise AssertionError(f"selectbox with label {label!r} and key {key!r} not found")


def _radio(at: AppTest, label: str, *, key: str | None = None):
    for widget in at.radio:
        if widget.label == label and (key is None or widget.key == key):
            return widget
    raise AssertionError(f"radio with label {label!r} and key {key!r} not found")


def _expander(at: AppTest, label: str):
    for widget in at.expander:
        if widget.label == label:
            return widget
    raise AssertionError(f"expander with label {label!r} not found")


def _button(at: AppTest, label: str):
    for widget in at.button:
        if widget.label == label:
            return widget
    raise AssertionError(f"button with label {label!r} not found")


def _frontier_report_for(
    ranked: list[RankedPaper],
    *,
    requested_date: date,
    effective_date: date,
) -> object:
    return build_daily_frontier_report(
        paper_pool=[item.paper for item in ranked],
        ranked_papers=ranked,
        requested_date=requested_date,
        effective_date=effective_date,
        source="arxiv",
        mode=BIOMEDICAL_LATEST_MODE,
        mode_label="Biomedical latest available",
        total_fetched=len(ranked),
    )


def _local_ui_session(
    *,
    digest: DailyDigest,
    cache_path: Path,
    report_path: Path,
    display_source: str,
    recent_history: list[RunHistoryEntry] | None = None,
    recent_history_error: str = "",
) -> LocalUISession:
    return LocalUISession(
        current_run=DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source=display_source,
            fetch_status_label="fresh source fetch" if display_source == "freshly fetched" else display_source,
            artifact_source_label="fresh source fetch" if display_source == "freshly fetched" else display_source,
        ),
        recent_history=tuple(recent_history or ()),
        recent_history_error=recent_history_error,
    )
