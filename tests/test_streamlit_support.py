from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from frontier_compass.api import DailyRunResult, FrontierCompassRunner, LocalUISession
from frontier_compass.common.frontier_report import build_daily_frontier_report
from frontier_compass.storage.schema import DailyDigest, PaperRecord, RankedPaper, RunHistoryEntry, RunTimings, SourceRunStats
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.ui.streamlit_app import (
    _build_zero_result_guidance,
    _build_personalization_state,
    _load_startup_request,
    _persist_uploaded_zotero_export,
    _render_frontier_highlights,
)
from frontier_compass.ui.app import DEFAULT_REVIEWER_SOURCE
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


class _UploadedExport:
    def __init__(self, name: str, payload: bytes) -> None:
        self.name = name
        self._payload = payload

    def getvalue(self) -> bytes:
        return self._payload


class _ContainerContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


class _HighlightStreamlit:
    def __init__(self) -> None:
        self.markdown_calls: list[tuple[str, bool]] = []
        self.write_calls: list[str] = []
        self.caption_calls: list[str] = []
        self.info_calls: list[str] = []

    def container(self, *, border: bool = False) -> _ContainerContext:
        del border
        return _ContainerContext()

    def markdown(self, body: str, *, unsafe_allow_html: bool = False) -> None:
        self.markdown_calls.append((body, unsafe_allow_html))

    def write(self, body: str) -> None:
        self.write_calls.append(body)

    def caption(self, body: str) -> None:
        self.caption_calls.append(body)

    def info(self, body: str) -> None:
        self.info_calls.append(body)


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


def test_load_startup_request_derives_range_and_live_zotero_from_explicit_db_path() -> None:
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
    assert request.effective_profile_source == "live_zotero_db"
    assert request.request_label == "2026-03-20 -> 2026-03-24"
    assert request.auto_profile_source_note == "Personalization defaults to the configured live Zotero DB."


def test_load_startup_request_collapses_equal_start_and_end_dates_to_single_day() -> None:
    request = _load_startup_request(
        [
            "--requested-date",
            "2026-03-24",
            "--start-date",
            "2026-03-24",
            "--end-date",
            "2026-03-24",
        ]
    )

    assert request.requested_date == date(2026, 3, 24)
    assert request.start_date is None
    assert request.end_date is None
    assert request.fetch_scope == "day-full"
    assert request.request_label == "2026-03-24"


def test_build_zero_result_guidance_explains_empty_default_bundle() -> None:
    ranked: list[RankedPaper] = []
    digest = DailyDigest(
        source="multisource",
        category="biomedical",
        target_date=date(2026, 4, 3),
        generated_at=datetime(2026, 4, 3, 7, 15, tzinfo=timezone.utc),
        feed_url="",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=ranked,
        frontier_report=build_daily_frontier_report(
            paper_pool=[],
            ranked_papers=[],
            requested_date=date(2026, 4, 3),
            effective_date=date(2026, 4, 3),
            source="multisource",
            mode="biomedical",
            mode_label="Biomedical",
            mode_kind="source-bundle",
            total_fetched=0,
        ),
        source_run_stats=(
            SourceRunStats(
                source="arxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                outcome="live-zero",
                note="The live category Atom feeds returned no same-day entries, so the snapshot reused the biomedical discovery API query fallback.",
                timings=RunTimings(network_seconds=1.0, parse_seconds=0.1, total_seconds=1.1),
            ),
            SourceRunStats(
                source="biorxiv",
                fetched_count=0,
                displayed_count=0,
                status="empty",
                outcome="live-zero",
                note="Daily bioRxiv all-subject local snapshot.",
                timings=RunTimings(network_seconds=0.5, parse_seconds=0.05, total_seconds=0.55),
            ),
        ),
        total_fetched=0,
        mode_label="Biomedical",
        mode_kind="source-bundle",
        requested_date=date(2026, 4, 3),
        effective_date=date(2026, 4, 3),
    )
    session = LocalUISession(
        current_run=DailyRunResult(
            digest=digest,
            cache_path=Path("data/cache/frontier_compass_bundle_biomedical_2026-04-03.json"),
            report_path=Path("reports/daily/frontier_compass_bundle_biomedical_2026-04-03.html"),
            display_source="freshly fetched",
        )
    )

    guidance = _build_zero_result_guidance(session, selected_source=DEFAULT_REVIEWER_SOURCE)

    assert guidance[0] == "No papers matched 2026-04-03 in the current source contract."
    assert "widen the Reading date range above" in guidance[1]
    assert guidance[2].startswith("arXiv:")
    assert "API query fallback" in guidance[2]
    assert guidance[3].startswith("bioRxiv:")


def test_load_startup_request_uses_configured_live_zotero_db(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "zotero" / "zotero.sqlite"
    db_path.parent.mkdir(parents=True)
    _write_minimal_zotero_db(db_path)

    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(
        json.dumps({"default_zotero_db_path": "zotero/zotero.sqlite"}),
        encoding="utf-8",
    )

    request = _load_startup_request(["--config", str(config_path)])

    assert request.effective_profile_source == "live_zotero_db"
    assert request.zotero_db_path == db_path
    assert request.zotero_export_path is None


def test_load_startup_request_falls_back_to_reusable_export_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    export_path = tmp_path / "data" / "raw" / "zotero" / "library.csl.json"
    export_path.parent.mkdir(parents=True)
    export_path.write_text(_sample_export_payload(), encoding="utf-8")

    request = _load_startup_request(["--no-config"])

    assert request.effective_profile_source == "zotero_export"
    assert request.zotero_export_path == Path("data/raw/zotero/library.csl.json")
    assert request.zotero_db_path is None


def test_load_startup_request_uses_configured_export_when_no_live_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    export_path = tmp_path / "exports" / "configured.csl.json"
    export_path.parent.mkdir(parents=True)
    export_path.write_text(_sample_export_payload(), encoding="utf-8")

    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(
        json.dumps({"default_zotero_export_path": "exports/configured.csl.json"}),
        encoding="utf-8",
    )

    request = _load_startup_request(["--config", str(config_path)])

    assert request.effective_profile_source == "zotero_export"
    assert request.zotero_export_path == export_path
    assert request.zotero_db_path is None


def test_load_startup_request_defaults_to_baseline_without_zotero_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")

    request = _load_startup_request(["--config", str(config_path)])

    assert request.effective_profile_source == "baseline"
    assert request.zotero_export_path is None
    assert request.zotero_db_path is None


def test_load_startup_request_explicit_export_override_beats_configured_live_db(tmp_path: Path) -> None:
    db_path = tmp_path / "zotero" / "zotero.sqlite"
    db_path.parent.mkdir(parents=True)
    _write_minimal_zotero_db(db_path)

    config_path = tmp_path / "user_defaults.json"
    config_path.write_text(
        json.dumps({"default_zotero_db_path": "zotero/zotero.sqlite"}),
        encoding="utf-8",
    )
    override_export = tmp_path / "override.csl.json"
    override_export.write_text(_sample_export_payload(), encoding="utf-8")

    request = _load_startup_request(
        [
            "--config",
            str(config_path),
            "--zotero-export",
            str(override_export),
        ]
    )

    assert request.effective_profile_source == "zotero_export"
    assert request.zotero_export_path == override_export
    assert request.zotero_db_path is None


def test_build_personalization_state_reads_configured_live_db(tmp_path: Path) -> None:
    db_path = tmp_path / "zotero.sqlite"
    _write_minimal_zotero_db(db_path)

    request = _load_startup_request(["--zotero-db-path", str(db_path)])
    state = _build_personalization_state(request)

    assert state.active is True
    assert state.profile_source == "live_zotero_db"
    assert state.available_collections == ("Tumor microenvironment",)
    assert state.item_count == 1


def test_persist_uploaded_zotero_export_creates_reusable_snapshot_and_collections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    app = FrontierCompassApp(
        zotero_export_path=tmp_path / "data" / "raw" / "zotero" / "library.csl.json",
        zotero_status_path=tmp_path / "data" / "raw" / "zotero" / "library_status.json",
    )
    runner = FrontierCompassRunner(app=app)

    state = _persist_uploaded_zotero_export(
        runner,
        _UploadedExport("library.csl.json", _sample_export_payload().encode("utf-8")),
    )

    assert state.ready is True
    assert state.export_path.exists()
    assert state.collections == ("Tumor microenvironment", "Foundation models")

    request = _load_startup_request(["--no-config"])
    personalization_state = _build_personalization_state(request)

    assert request.effective_profile_source == "zotero_export"
    assert personalization_state.active is True
    assert personalization_state.available_collections == ("Tumor microenvironment", "Foundation models")


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


def test_streamlit_homepage_is_reading_first_single_page(monkeypatch, tmp_path: Path) -> None:
    session = _sample_ui_session(tmp_path, requested_date=date(2026, 3, 24))

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        del kwargs
        return session

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    at = AppTest.from_file(str(_app_path()))
    at.run(timeout=15)

    assert len(at.exception) == 0
    assert len(at.tabs) == 3
    assert any("## Daily Full Report" in item.value for item in at.markdown)
    assert any("## Most Relevant to Your Zotero" in item.value for item in at.markdown)
    assert any("## Other Frontier Signals" in item.value for item in at.markdown)
    assert any(expander.label == "Advanced compatibility" for expander in at.expander)
    assert any(expander.label == "Personalization" for expander in at.expander)
    assert any(expander.label == "Full ranked pool" for expander in at.expander)
    assert any(expander.label == "History and provenance" for expander in at.expander)
    assert any(expander.label == "Runtime and compatibility" for expander in at.expander)
    assert _button(at, "Refresh").label == "Refresh"


def test_render_frontier_highlights_renders_source_link_when_available(monkeypatch) -> None:
    recorder = _HighlightStreamlit()
    link_calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_render_external_link(label: str, url: str, **kwargs) -> bool:
        link_calls.append((label, url, kwargs))
        return True

    monkeypatch.setattr("frontier_compass.ui.streamlit_app.st", recorder)
    monkeypatch.setattr("frontier_compass.ui.streamlit_app.render_external_link", fake_render_external_link)

    _render_frontier_highlights(
        (
            SimpleNamespace(
                title="Frontier fixture",
                source="biorxiv",
                theme_label="single-cell systems",
                published=date(2026, 3, 24),
                why="Shows a sharp methods crossover.",
                summary="A readable highlight summary.",
                url="https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
            ),
        )
    )

    assert any("### 1. Frontier fixture" in body for body, _ in recorder.markdown_calls)
    assert any("bioRxiv" in body and "single-cell systems" in body for body, _ in recorder.markdown_calls)
    assert recorder.write_calls == ["A readable highlight summary."]
    assert link_calls == [
        (
            "Open source paper",
            "https://www.biorxiv.org/content/10.1101/2026.03.24.000001v1",
            {
                "key": "frontier-highlight-link-frontier-fixture",
                "use_container_width": True,
            },
        )
    ]


def test_render_frontier_highlights_shows_missing_link_caption(monkeypatch) -> None:
    recorder = _HighlightStreamlit()

    def fail_render_external_link(*args, **kwargs) -> bool:
        del args, kwargs
        raise AssertionError("render_external_link should not be called")

    monkeypatch.setattr("frontier_compass.ui.streamlit_app.st", recorder)
    monkeypatch.setattr("frontier_compass.ui.streamlit_app.render_external_link", fail_render_external_link)

    _render_frontier_highlights(
        (
            SimpleNamespace(
                title="Unlinked frontier fixture",
                source="arxiv",
                theme_label="diagnostics",
                published=date(2026, 3, 24),
                why="Missing source url fixture.",
                summary="No URL on purpose.",
                url="",
            ),
        )
    )

    assert recorder.caption_calls == ["No source link is attached to this highlight."]


def test_streamlit_date_change_auto_loads_cache_first_and_refresh_forces_fetch(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        calls.append(dict(kwargs))
        requested_date = kwargs["requested_date"]
        assert isinstance(requested_date, date)
        return _sample_ui_session(tmp_path, requested_date=requested_date)

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    at = AppTest.from_file(str(_app_path()))
    at.run(timeout=15)

    assert len(calls) == 1
    assert calls[0]["refresh"] is False

    _date_input(at, "Reading date", key="fc-home-requested-date").set_value((date(2026, 3, 25), date(2026, 3, 25)))
    at.run(timeout=15)

    assert len(calls) == 2
    assert calls[-1]["requested_date"] == date(2026, 3, 25)
    assert calls[-1]["refresh"] is False

    _button(at, "Refresh").click()
    at.run(timeout=15)

    assert len(calls) == 3
    assert calls[-1]["requested_date"] == date(2026, 3, 25)
    assert calls[-1]["refresh"] is True


def test_streamlit_advanced_source_override_auto_loads_without_apply(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare_ui_session(self, **kwargs):  # type: ignore[no-untyped-def]
        assert isinstance(self, FrontierCompassRunner)
        calls.append(dict(kwargs))
        return _sample_ui_session(tmp_path, requested_date=kwargs["requested_date"], source=str(kwargs["source"]))

    monkeypatch.setattr(FrontierCompassRunner, "prepare_ui_session", fake_prepare_ui_session)

    at = AppTest.from_file(str(_app_path()))
    at.run(timeout=15)

    _selectbox(at, "Source override", key="fc-advanced-source-bundle").select("ai-for-medicine")
    at.run(timeout=15)

    assert len(calls) == 2
    assert calls[-1]["source"] == "ai-for-medicine"
    assert calls[-1]["refresh"] is False


def _app_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "frontier_compass" / "ui" / "streamlit_app.py"


def _sample_ui_session(tmp_path: Path, *, requested_date: date, source: str = BIOMEDICAL_LATEST_MODE) -> LocalUISession:
    report_path = tmp_path / f"{source}_{requested_date.isoformat()}.html"
    report_path.write_text("<html><body>report</body></html>", encoding="utf-8")
    cache_path = tmp_path / f"{source}_{requested_date.isoformat()}.json"
    cache_path.write_text("{}", encoding="utf-8")
    digest = DailyDigest(
        source="arxiv",
        category=source,
        target_date=requested_date,
        generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(source),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher", "B Collaborator"),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=requested_date,
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
        frontier_report=build_daily_frontier_report(
            paper_pool=[
                PaperRecord(
                    source="arxiv",
                    identifier="2603.20001v1",
                    title="Single-cell atlas alignment with multimodal omics",
                    summary="Atlas integration for transcriptomics and proteomics.",
                    authors=("A Researcher", "B Collaborator"),
                    categories=("q-bio.GN", "q-bio.QM"),
                    published=requested_date,
                    url="https://arxiv.org/abs/2603.20001",
                )
            ],
            ranked_papers=[
                RankedPaper(
                    paper=PaperRecord(
                        source="arxiv",
                        identifier="2603.20001v1",
                        title="Single-cell atlas alignment with multimodal omics",
                        summary="Atlas integration for transcriptomics and proteomics.",
                        authors=("A Researcher", "B Collaborator"),
                        categories=("q-bio.GN", "q-bio.QM"),
                        published=requested_date,
                        url="https://arxiv.org/abs/2603.20001",
                    ),
                    score=0.88,
                    reasons=("signal",),
                    recommendation_summary="Strong biomedical match for reviewer triage.",
                )
            ],
            requested_date=requested_date,
            effective_date=requested_date,
            source="arxiv",
            mode=source,
            mode_label=source,
            total_fetched=1,
        ),
        searched_categories=("q-bio", "q-bio.GN"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1},
        total_fetched=1,
        mode_label=source,
        mode_kind="source-bundle",
        requested_date=requested_date,
        effective_date=requested_date,
    )
    return LocalUISession(
        current_run=DailyRunResult(
            digest=digest,
            cache_path=cache_path,
            report_path=report_path,
            display_source="freshly fetched",
            fetch_status_label="fresh source fetch",
            artifact_source_label="fresh source fetch",
        ),
        recent_history=(
            RunHistoryEntry(
                requested_date=requested_date,
                effective_date=requested_date,
                category=source,
                mode_label=source,
                mode_kind="source-bundle",
                profile_basis="biomedical baseline",
                fetch_status="fresh source fetch",
                ranked_count=1,
                cache_path=str(cache_path),
                report_path=str(report_path),
                generated_at=datetime(2026, 3, 24, 7, 15, tzinfo=timezone.utc),
            ),
        ),
    )


def _sample_export_payload() -> str:
    return json.dumps(
        [
            {
                "title": "Spatial Transcriptomics Atlas",
                "abstractNote": "Tumor microenvironment analysis.",
                "keywords": ["spatial transcriptomics", "digital pathology"],
                "collections": ["Tumor microenvironment"],
                "dateAdded": "2026-03-25",
            },
            {
                "title": "Foundation models for biology",
                "abstractNote": "Foundation model reading list.",
                "keywords": ["foundation models"],
                "collections": ["Foundation models"],
                "dateAdded": "2026-03-26",
            },
        ]
    )


def _write_minimal_zotero_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY,
                dateAdded TEXT,
                itemTypeID INTEGER
            );
            CREATE TABLE deletedItems (itemID INTEGER);
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
            CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT);
            CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO itemTypes (itemTypeID, typeName) VALUES (?, ?)",
            [(1, "journalArticle")],
        )
        connection.executemany(
            "INSERT INTO fields (fieldID, fieldName) VALUES (?, ?)",
            [(1, "title"), (2, "abstractNote")],
        )
        connection.executemany(
            "INSERT INTO itemDataValues (valueID, value) VALUES (?, ?)",
            [(1, "Spatial Transcriptomics Atlas"), (2, "Tumor microenvironment analysis.")],
        )
        connection.execute(
            "INSERT INTO items (itemID, dateAdded, itemTypeID) VALUES (?, ?, ?)",
            (1, "2026-03-25 10:00:00", 1),
        )
        connection.executemany(
            "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
            [(1, 1, 1), (1, 2, 2)],
        )
        connection.executemany(
            "INSERT INTO tags (tagID, name) VALUES (?, ?)",
            [(1, "spatial transcriptomics"), (2, "digital pathology")],
        )
        connection.executemany(
            "INSERT INTO itemTags (itemID, tagID) VALUES (?, ?)",
            [(1, 1), (1, 2)],
        )
        connection.execute(
            "INSERT INTO collections (collectionID, collectionName) VALUES (?, ?)",
            (1, "Tumor microenvironment"),
        )
        connection.execute(
            "INSERT INTO collectionItems (collectionID, itemID) VALUES (?, ?)",
            (1, 1),
        )
        connection.commit()
    finally:
        connection.close()


def _selectbox(at: AppTest, label: str, *, key: str | None = None):
    for widget in at.selectbox:
        if widget.label == label and (key is None or widget.key == key):
            return widget
    raise AssertionError(f"selectbox with label {label!r} and key {key!r} not found")


def _date_input(at: AppTest, label: str, *, key: str | None = None):
    for widget in at.date_input:
        if widget.label == label and (key is None or widget.key == key):
            return widget
    raise AssertionError(f"date_input with label {label!r} and key {key!r} not found")


def _button(at: AppTest, label: str):
    for widget in at.button:
        if widget.label == label:
            return widget
    raise AssertionError(f"button with label {label!r} not found")
