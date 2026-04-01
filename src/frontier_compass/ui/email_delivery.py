"""Minimal email delivery helpers for daily FrontierCompass digests."""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Mapping, Sequence

from frontier_compass.common.user_defaults import normalize_email_recipients
from frontier_compass.exploration.selector import (
    daily_exploration_intro,
    daily_exploration_note,
    resolve_daily_exploration_picks,
)
from frontier_compass.ranking.relevance import priority_label_for_score
from frontier_compass.reporting.daily_brief import (
    build_daily_brief,
    build_reviewer_shortlist,
    theme_label_for_ranked_paper,
)
from frontier_compass.reporting.html_report import daily_digest_title
from frontier_compass.reporting.html_report import extract_report_summary_value
from frontier_compass.storage.schema import DailyDigest, RankedPaper
from frontier_compass.ui.app import display_artifact_source_label, display_source_label


SMTP_SECURITY_OPTIONS = frozenset({"starttls", "ssl", "none"})


@dataclass(slots=True, frozen=True)
class ResolvedEmailAddresses:
    to_addresses: tuple[str, ...]
    from_address: str


@dataclass(slots=True, frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str = ""
    password: str = ""


@dataclass(slots=True, frozen=True)
class DeliveryProvenance:
    artifact_source_label: str
    digest_fetch_status_label: str
    fresh_fetch_error: str = ""


@dataclass(slots=True, frozen=True)
class PreparedDailyEmail:
    message: EmailMessage
    subject: str
    report_path: Path
    artifact_source_label: str
    digest_fetch_status_label: str
    fresh_fetch_error: str


def resolve_email_addresses(
    *,
    email_to: str | Sequence[str] | None = None,
    email_from: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ResolvedEmailAddresses:
    resolved_env = os.environ if env is None else env
    to_value = email_to if email_to is not None else resolved_env.get("FRONTIER_COMPASS_EMAIL_TO", "")
    from_value = (email_from or resolved_env.get("FRONTIER_COMPASS_EMAIL_FROM", "")).strip()

    to_addresses = normalize_email_recipients(to_value)
    missing: list[str] = []
    if not to_addresses:
        missing.append("--email-to or FRONTIER_COMPASS_EMAIL_TO")
    if not from_value:
        missing.append("--email-from or FRONTIER_COMPASS_EMAIL_FROM")
    if missing:
        raise ValueError(f"Missing email delivery settings: {', '.join(missing)}.")

    return ResolvedEmailAddresses(to_addresses=to_addresses, from_address=from_value)


def resolve_smtp_settings(env: Mapping[str, str] | None = None) -> SmtpSettings:
    resolved_env = os.environ if env is None else env
    host = resolved_env.get("FRONTIER_COMPASS_SMTP_HOST", "").strip()
    port_value = resolved_env.get("FRONTIER_COMPASS_SMTP_PORT", "").strip()
    security = resolved_env.get("FRONTIER_COMPASS_SMTP_SECURITY", "").strip().lower()
    username = resolved_env.get("FRONTIER_COMPASS_SMTP_USERNAME", "").strip()
    password = resolved_env.get("FRONTIER_COMPASS_SMTP_PASSWORD", "")

    missing: list[str] = []
    if not host:
        missing.append("FRONTIER_COMPASS_SMTP_HOST")
    if not port_value:
        missing.append("FRONTIER_COMPASS_SMTP_PORT")
    if not security:
        missing.append("FRONTIER_COMPASS_SMTP_SECURITY")
    if missing:
        raise ValueError(
            "Missing SMTP settings for --send: "
            + ", ".join(missing)
            + ". Use dry-run without --send to review a .eml file instead."
        )

    if security not in SMTP_SECURITY_OPTIONS:
        raise ValueError(
            "FRONTIER_COMPASS_SMTP_SECURITY must be one of: starttls, ssl, none. "
            "Use dry-run without --send to review a .eml file instead."
        )

    try:
        port = int(port_value)
    except ValueError as exc:
        raise ValueError(
            "FRONTIER_COMPASS_SMTP_PORT must be an integer. "
            "Use dry-run without --send to review a .eml file instead."
        ) from exc

    if (username and not password) or (password and not username):
        raise ValueError(
            "FRONTIER_COMPASS_SMTP_USERNAME and FRONTIER_COMPASS_SMTP_PASSWORD must be set together. "
            "Use dry-run without --send to review a .eml file instead."
        )

    return SmtpSettings(
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
    )


def default_eml_output_path(report_path: str | Path) -> Path:
    return Path(report_path).with_suffix(".eml")


def prepare_daily_digest_email(
    digest: DailyDigest,
    *,
    report_path: str | Path,
    display_source: str,
    fetch_error: str = "",
    email_to: str | Sequence[str] | None = None,
    email_from: str | None = None,
    attach_report: bool = False,
    env: Mapping[str, str] | None = None,
) -> PreparedDailyEmail:
    resolved_report_path = Path(report_path)
    report_html = resolved_report_path.read_text(encoding="utf-8")
    addresses = resolve_email_addresses(email_to=email_to, email_from=email_from, env=env)
    provenance = resolve_delivery_provenance(
        report_html,
        display_source=display_source,
        fetch_error=fetch_error,
    )
    subject = build_daily_email_subject(digest, digest_fetch_status_label=provenance.digest_fetch_status_label)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = addresses.from_address
    message["To"] = ", ".join(addresses.to_addresses)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="frontier-compass.local")
    message.set_content(
        build_daily_email_plain_text(
            digest,
            report_path=resolved_report_path,
            artifact_source_label=provenance.artifact_source_label,
            digest_fetch_status_label=provenance.digest_fetch_status_label,
            fresh_fetch_error=provenance.fresh_fetch_error,
        )
    )
    message.add_alternative(report_html, subtype="html")
    if attach_report:
        message.add_attachment(
            report_html.encode("utf-8"),
            maintype="text",
            subtype="html",
            filename=resolved_report_path.name,
        )

    return PreparedDailyEmail(
        message=message,
        subject=subject,
        report_path=resolved_report_path,
        artifact_source_label=provenance.artifact_source_label,
        digest_fetch_status_label=provenance.digest_fetch_status_label,
        fresh_fetch_error=provenance.fresh_fetch_error,
    )


def build_daily_email_subject(digest: DailyDigest, *, digest_fetch_status_label: str) -> str:
    parts = [
        daily_digest_title(digest),
        f"requested {digest.requested_target_date.isoformat()}",
    ]
    if digest.effective_display_date != digest.requested_target_date:
        parts.append(f"showing {digest.effective_display_date.isoformat()}")
    parts.append(f"fetch: {digest_fetch_status_label}")
    if digest.used_latest_available_fallback:
        parts.append("latest-available fallback")
    if digest.stale_cache_fallback_used:
        parts.append("stale cache fallback")
    return " | ".join(parts)


def build_daily_email_plain_text(
    digest: DailyDigest,
    *,
    report_path: str | Path,
    artifact_source_label: str,
    digest_fetch_status_label: str,
    fresh_fetch_error: str = "",
) -> str:
    shortlist, shortlist_title = build_reviewer_shortlist(digest.ranked, max_items=8)
    exploration_picks = resolve_daily_exploration_picks(digest)
    brief = build_daily_brief(digest.profile, shortlist, total_ranked=len(digest.ranked))
    mode_label = digest.mode_label or digest.category or "n/a"
    lines = [
        daily_digest_title(digest),
        "",
        f"Fetch status: {digest_fetch_status_label}",
        f"Artifact source: {artifact_source_label}",
    ]
    if fresh_fetch_error:
        lines.append(f"Fresh fetch error: {fresh_fetch_error}")
    if digest.stale_cache_fallback_used:
        lines.extend(
            [
                f"Stale cache source requested date: {digest.stale_cache_source_requested_date.isoformat() if digest.stale_cache_source_requested_date else 'unknown'}",
                f"Stale cache source effective date: {digest.stale_cache_source_effective_date.isoformat() if digest.stale_cache_source_effective_date else 'unknown'}",
            ]
        )
    lines.extend(
        [
            f"Requested date: {digest.requested_target_date.isoformat()}",
            f"Effective displayed date: {digest.effective_display_date.isoformat()}",
            f"Latest-available display fallback: {'yes' if digest.used_latest_available_fallback else 'no'}",
            f"Stale cache fallback: {'yes' if digest.stale_cache_fallback_used else 'no'}",
            f"Display basis: {digest.selection_basis_label}",
            f"Mode: {mode_label} ({digest.category})" if digest.category else f"Mode: {mode_label}",
            f"Mode kind: {digest.mode_kind or 'n/a'}",
            f"Strict same-day fetched / ranked: {digest.strict_same_day_counts_label}",
            f"Total fetched: {max(digest.total_fetched, digest.total_ranked_count)}",
            f"Total ranked pool: {digest.total_ranked_count}",
            f"Total displayed: {digest.total_displayed_count}",
            f"HTML report: {Path(report_path)}",
            "",
            "Reviewer summary:",
        ]
    )
    lines.extend(f"- {takeaway}" for takeaway in brief.takeaways)
    lines.extend(("", f"{shortlist_title}:"))
    if not shortlist:
        lines.append("No ranked papers are available in the current digest.")
    else:
        lines.extend(_format_ranked_paper_lines(index, item) for index, item in enumerate(shortlist, start=1))
    if exploration_picks:
        lines.extend(
            (
                "",
                "Exploration picks:",
                daily_exploration_intro(digest.profile, policy=digest.exploration_policy),
            )
        )
        lines.extend(
            _format_exploration_paper_lines(
                index,
                item,
                note=daily_exploration_note(
                    item,
                    ranked_papers=digest.ranked,
                    profile=digest.profile,
                    policy=digest.exploration_policy,
                ),
            )
            for index, item in enumerate(exploration_picks, start=1)
        )
    return "\n".join(lines).strip() + "\n"


def resolve_delivery_provenance(
    report_html: str,
    *,
    display_source: str,
    fetch_error: str = "",
) -> DeliveryProvenance:
    artifact_source_label = display_artifact_source_label(display_source)
    report_fetch_status = extract_report_summary_value(report_html, "Fetch status")
    report_fetch_error = extract_report_summary_value(report_html, "Fresh fetch error")
    digest_fetch_status_label = report_fetch_status or artifact_source_label
    resolved_fetch_error = report_fetch_error or fetch_error
    return DeliveryProvenance(
        artifact_source_label=artifact_source_label,
        digest_fetch_status_label=digest_fetch_status_label,
        fresh_fetch_error=resolved_fetch_error,
    )


def write_eml_message(message: EmailMessage, output_path: str | Path) -> Path:
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_bytes(message.as_bytes(policy=policy.SMTP))
    return resolved_output_path


def send_email_message(message: EmailMessage, settings: SmtpSettings, *, timeout: float = 30.0) -> None:
    if settings.security == "ssl":
        smtp_client: smtplib.SMTP = smtplib.SMTP_SSL(settings.host, settings.port, timeout=timeout)
    else:
        smtp_client = smtplib.SMTP(settings.host, settings.port, timeout=timeout)

    with smtp_client as client:
        client.ehlo()
        if settings.security == "starttls":
            client.starttls()
            client.ehlo()
        if settings.username and settings.password:
            client.login(settings.username, settings.password)
        client.send_message(message)


def _format_ranked_paper_lines(index: int, item: RankedPaper) -> str:
    theme = theme_label_for_ranked_paper(item)
    status = priority_label_for_score(item.score)
    url = item.paper.url or "n/a"
    return (
        f"{index}. {item.paper.title}\n"
        f"   Score: {item.score:.3f} | Status: {status} | Theme: {theme}\n"
        f"   URL: {url}"
    )


def _format_exploration_paper_lines(index: int, item: RankedPaper, *, note: str) -> str:
    theme = theme_label_for_ranked_paper(item)
    status = priority_label_for_score(item.score)
    url = item.paper.url or "n/a"
    return (
        f"{index}. {item.paper.title}\n"
        f"   Score: {item.score:.3f} | Status: {status} | Theme: {theme}\n"
        f"   Why it's exploratory: {note}\n"
        f"   URL: {url}"
    )
