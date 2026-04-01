from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from frontier_compass.reporting.html_report import HtmlReportBuilder
from frontier_compass.storage.schema import DailyDigest, PaperRecord, RankedPaper
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.ui.email_delivery import (
    default_eml_output_path,
    prepare_daily_digest_email,
    resolve_smtp_settings,
    write_eml_message,
)


def test_prepare_daily_digest_email_builds_plain_and_html_parts_from_saved_report(tmp_path: Path) -> None:
    digest = _sample_digest()
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_html = HtmlReportBuilder().render_daily_digest(
        digest,
        acquisition_status_label="same-date cache reused after fetch failure",
        fetch_error="arXiv request timed out",
    )
    report_path.write_text(report_html, encoding="utf-8")

    prepared = prepare_daily_digest_email(
        digest,
        report_path=report_path,
        display_source="loaded from cache",
        email_to="reviewer@example.com",
        email_from="frontier@example.com",
    )

    plain_body = prepared.message.get_body(preferencelist=("plain",))
    html_body = prepared.message.get_body(preferencelist=("html",))

    assert prepared.subject.startswith("FrontierCompass Biomedical Latest Available arXiv Brief")
    assert "fetch: same-date cache reused after fetch failure" in prepared.subject
    assert prepared.artifact_source_label == "same-day cache"
    assert prepared.digest_fetch_status_label == "same-date cache reused after fetch failure"
    assert prepared.fresh_fetch_error == "arXiv request timed out"
    assert plain_body is not None
    assert html_body is not None
    assert "Fetch status: same-date cache reused after fetch failure" in plain_body.get_content()
    assert "Artifact source: same-day cache" in plain_body.get_content()
    assert "Fresh fetch error: arXiv request timed out" in plain_body.get_content()
    assert "Top recommendations:" in plain_body.get_content()
    assert "same-date cache reused after fetch failure" in html_body.get_content()
    assert "FrontierCompass Biomedical Latest Available arXiv Brief" in html_body.get_content()


def test_prepare_daily_digest_email_includes_exploration_section_when_present(tmp_path: Path) -> None:
    digest = _sample_digest_with_exploration()
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text(HtmlReportBuilder().render_daily_digest(digest), encoding="utf-8")

    prepared = prepare_daily_digest_email(
        digest,
        report_path=report_path,
        display_source="freshly fetched",
        email_to="reviewer@example.com",
        email_from="frontier@example.com",
    )

    plain_body = prepared.message.get_body(preferencelist=("plain",))
    html_body = prepared.message.get_body(preferencelist=("html",))

    assert plain_body is not None
    assert html_body is not None
    assert "Exploration picks:" in plain_body.get_content()
    assert "Why it's exploratory:" in plain_body.get_content()
    assert "Exploration picks" in html_body.get_content()
    assert "Why it&#x27;s exploratory" in html_body.get_content()


def test_prepare_daily_digest_email_marks_stale_cache_fallback_honestly(tmp_path: Path) -> None:
    digest = _sample_digest()
    digest.strict_same_day_counts_known = False
    digest.requested_date = date(2026, 3, 24)
    digest.effective_date = date(2026, 3, 23)
    digest.stale_cache_source_requested_date = date(2026, 3, 23)
    digest.stale_cache_source_effective_date = date(2026, 3, 23)
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(
            digest,
            acquisition_status_label="older compatible cache reused after fetch failure",
            fetch_error="arXiv request timed out",
        ),
        encoding="utf-8",
    )

    prepared = prepare_daily_digest_email(
        digest,
        report_path=report_path,
        display_source="older compatible cache reused after fetch failure",
        email_to="reviewer@example.com",
        email_from="frontier@example.com",
    )

    plain_body = prepared.message.get_body(preferencelist=("plain",))
    assert "fetch: older compatible cache reused after fetch failure" in prepared.subject
    assert "stale cache fallback" in prepared.subject
    assert prepared.artifact_source_label == "older compatible cache"
    assert plain_body is not None
    assert "Artifact source: older compatible cache" in plain_body.get_content()
    assert "Stale cache fallback: yes" in plain_body.get_content()
    assert "Stale cache source requested date: 2026-03-23" in plain_body.get_content()
    assert "Strict same-day fetched / ranked: unavailable / unavailable" in plain_body.get_content()


def test_write_eml_message_creates_non_empty_file(tmp_path: Path) -> None:
    digest = _sample_digest()
    report_path = tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.html"
    report_path.write_text(
        HtmlReportBuilder().render_daily_digest(
            digest,
            acquisition_status_label="fresh source fetch",
        ),
        encoding="utf-8",
    )
    prepared = prepare_daily_digest_email(
        digest,
        report_path=report_path,
        display_source="freshly fetched",
        email_to="reviewer@example.com",
        email_from="frontier@example.com",
    )

    output_path = write_eml_message(prepared.message, default_eml_output_path(report_path))

    assert output_path == tmp_path / "frontier_compass_arxiv_biomedical-latest_2026-03-24.eml"
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    eml_text = output_path.read_text(encoding="utf-8")
    assert "Subject: FrontierCompass Biomedical Latest Available arXiv Brief" in eml_text
    assert "To: reviewer@example.com" in eml_text
    assert "fresh source fetch" in eml_text


def test_resolve_smtp_settings_reports_missing_values() -> None:
    with pytest.raises(ValueError, match="Missing SMTP settings for --send:"):
        resolve_smtp_settings({})


def test_resolve_smtp_settings_rejects_invalid_security() -> None:
    with pytest.raises(ValueError, match="FRONTIER_COMPASS_SMTP_SECURITY must be one of"):
        resolve_smtp_settings(
            {
                "FRONTIER_COMPASS_SMTP_HOST": "smtp.example.com",
                "FRONTIER_COMPASS_SMTP_PORT": "587",
                "FRONTIER_COMPASS_SMTP_SECURITY": "tls",
            }
        )


def _sample_digest() -> DailyDigest:
    return DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.21990v1",
                    title="Sparse Autoencoders for Medical Imaging",
                    summary="Medical imaging workflow for MRI and CT review.",
                    authors=("A Scientist",),
                    categories=("cs.CV", "cs.LG"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.21990",
                ),
                score=0.824,
                reasons=(
                    "biomedical evidence: medical imaging, biomedical",
                    "topic match: cs.cv, cs.lg",
                ),
                recommendation_summary="Priority review for medical imaging coverage.",
            )
        ],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )


def _sample_digest_with_exploration() -> DailyDigest:
    exploration_pick = RankedPaper(
        paper=PaperRecord(
            source="arxiv",
            identifier="2603.21991v1",
            title="Exploration lane protein fixture",
            summary="Protein biomolecular exploration fixture outside the main shortlist.",
            authors=("A Scientist",),
            categories=("q-bio.BM", "cs.LG"),
            published=date(2026, 3, 24),
            url="https://arxiv.org/abs/2603.21991",
        ),
        score=0.41,
        reasons=("biomedical evidence: protein",),
        recommendation_summary="Exploration lane fixture.",
    )
    return DailyDigest(
        source="arxiv",
        category=BIOMEDICAL_LATEST_MODE,
        target_date=date(2026, 3, 24),
        generated_at=datetime(2026, 3, 24, 7, 0, tzinfo=timezone.utc),
        feed_url="https://export.arxiv.org/api/query",
        profile=FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        ranked=[
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.21990v1",
                    title="Sparse Autoencoders for Medical Imaging",
                    summary="Medical imaging workflow for MRI and CT review.",
                    authors=("A Scientist",),
                    categories=("cs.CV", "cs.LG"),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.21990",
                ),
                score=0.824,
                reasons=(
                    "biomedical evidence: medical imaging, biomedical",
                    "topic match: cs.cv, cs.lg",
                ),
                recommendation_summary="Priority review for medical imaging coverage.",
            ),
            RankedPaper(
                paper=PaperRecord(
                    source="arxiv",
                    identifier="2603.21992v1",
                    title="Whole-slide Histopathology Reasoning",
                    summary="Pathology and whole-slide microscopy pipeline for diagnostics.",
                    authors=("A Scientist",),
                    categories=("cs.CV",),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.21992",
                ),
                score=0.781,
                reasons=(
                    "biomedical evidence: pathology, microscopy",
                    "topic match: cs.cv",
                ),
                recommendation_summary="Priority review for pathology coverage.",
            ),
            exploration_pick,
        ],
        exploration_picks=[exploration_pick],
        searched_categories=("q-bio", "q-bio.GN", "cs.LG"),
        per_category_counts={"q-bio": 1, "q-bio.GN": 1, "cs.LG": 1},
        total_fetched=3,
        feed_urls={"q-bio": "https://rss.arxiv.org/atom/q-bio"},
        mode_label="Biomedical latest available",
        mode_kind="latest-available-hybrid",
        requested_date=date(2026, 3, 24),
        effective_date=date(2026, 3, 24),
    )
