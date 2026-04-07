"""medRxiv ingestion via the public RSS feed."""

from __future__ import annotations

from datetime import date

from frontier_compass.ingest.common import (
    FeedFetchDetails,
    FeedRequestError,
    fetch_text,
    measure_operation,
    parse_recent_listing,
    parse_rss_feed,
)
from frontier_compass.storage.schema import PaperRecord


class MedRxivClient:
    feed_url_template = "https://connect.medrxiv.org/medrxiv_xml.php?subject={subject}"
    recent_listing_url = "https://www.medrxiv.org/content/early/recent"
    recent_listing_user_agent = "curl/8.5.0"
    request_timeout_seconds = 8
    request_max_attempts = 1
    recent_listing_timeout_seconds = 15
    recent_listing_max_attempts = 1

    def __init__(self) -> None:
        self.last_fetch_details: FeedFetchDetails | None = None

    def build_feed_url(self, subject: str = "all") -> str:
        normalized_subject = (subject or "all").strip() or "all"
        return self.feed_url_template.format(subject=normalized_subject)

    def fetch_latest(self, *, subject: str = "all", feed_url: str | None = None) -> list[PaperRecord]:
        return list(self.fetch_latest_details(subject=subject, feed_url=feed_url).papers)

    def fetch_latest_with_timings(
        self,
        *,
        subject: str = "all",
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        details = self.fetch_latest_details(subject=subject, feed_url=feed_url)
        return list(details.papers), details.network_seconds, details.parse_seconds

    def fetch_latest_details(
        self,
        *,
        subject: str = "all",
        feed_url: str | None = None,
    ) -> FeedFetchDetails:
        resolved_feed_url = feed_url or self.build_feed_url(subject)

        def _fetch_source_text() -> tuple[str, str, str, str]:
            try:
                return (
                    fetch_text(
                        resolved_feed_url,
                        timeout=self.request_timeout_seconds,
                        max_attempts=self.request_max_attempts,
                        source_label="medRxiv",
                    ),
                    resolved_feed_url,
                    "rss",
                    "",
                )
            except FeedRequestError as exc:
                if feed_url is not None or (subject or "all").strip().lower() != "all":
                    raise
                fallback_note = (
                    f"Primary RSS feed {resolved_feed_url} was unavailable ({exc}); "
                    f"used the reachable official recent listing {self.recent_listing_url}."
                )
                return (
                    fetch_text(
                        self.recent_listing_url,
                        timeout=self.recent_listing_timeout_seconds,
                        max_attempts=self.recent_listing_max_attempts,
                        user_agent=self.recent_listing_user_agent,
                        source_label="medRxiv recent listing",
                    ),
                    self.recent_listing_url,
                    "recent-html",
                    fallback_note,
                )

        (raw_text, endpoint, contract_mode, note), network_seconds = measure_operation(_fetch_source_text)
        if contract_mode == "recent-html":
            papers, parse_seconds = measure_operation(
                lambda: parse_recent_listing(
                    raw_text,
                    source="medrxiv",
                    subject=subject,
                    listing_url=endpoint,
                )
            )
        else:
            papers, parse_seconds = measure_operation(
                lambda: self.parse_feed(
                    raw_text,
                    subject=subject,
                    feed_url=endpoint,
                )
            )

        if contract_mode == "recent-html" and not papers:
            raise FeedRequestError(
                f"medRxiv recent listing at {endpoint} returned no parseable papers after RSS fallback."
            )

        details = FeedFetchDetails(
            papers=tuple(papers),
            network_seconds=network_seconds,
            parse_seconds=parse_seconds,
            endpoint=endpoint,
            contract_mode=contract_mode,
            note=note,
            available_dates=tuple(
                sorted(
                    {
                        paper.published
                        for paper in papers
                        if paper.published is not None
                    }
                )
            ),
        )
        self.last_fetch_details = details
        return details

    def fetch_feed(self, *, subject: str = "all", feed_url: str | None = None) -> list[PaperRecord]:
        return self.fetch_latest(subject=subject, feed_url=feed_url)

    def fetch_feed_with_timings(
        self,
        *,
        subject: str = "all",
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        return self.fetch_latest_with_timings(subject=subject, feed_url=feed_url)

    def fetch_today(
        self,
        *,
        today: date | None = None,
        subject: str = "all",
        max_results: int | None = None,
        feed_url: str | None = None,
    ) -> list[PaperRecord]:
        return list(
            self.fetch_today_details(
                today=today,
                subject=subject,
                max_results=max_results,
                feed_url=feed_url,
            ).papers
        )

    def fetch_today_details(
        self,
        *,
        today: date | None = None,
        subject: str = "all",
        max_results: int | None = None,
        feed_url: str | None = None,
    ) -> FeedFetchDetails:
        target_date = today or date.today()
        details = self.fetch_latest_details(subject=subject, feed_url=feed_url)
        papers = [paper for paper in details.papers if _paper_date(paper) == target_date]
        if details.contract_mode == "recent-html" and target_date not in details.available_dates:
            available_dates = ", ".join(item.isoformat() for item in details.available_dates) or "none"
            raise FeedRequestError(
                "medRxiv live fallback cannot satisfy historical day "
                f"{target_date.isoformat()}: reachable recent listing {details.endpoint} only exposes {available_dates}."
            )
        if max_results is not None:
            papers = papers[: max(max_results, 0)]
        details = FeedFetchDetails(
            papers=tuple(papers),
            network_seconds=details.network_seconds,
            parse_seconds=details.parse_seconds,
            endpoint=details.endpoint,
            contract_mode=details.contract_mode,
            note=details.note,
            available_dates=details.available_dates,
        )
        self.last_fetch_details = details
        return details

    def fetch_today_with_timings(
        self,
        *,
        today: date | None = None,
        subject: str = "all",
        max_results: int | None = None,
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        details = self.fetch_today_details(
            today=today,
            subject=subject,
            max_results=max_results,
            feed_url=feed_url,
        )
        return list(details.papers), details.network_seconds, details.parse_seconds

    def parse_feed(
        self,
        xml_text: str,
        *,
        subject: str = "all",
        feed_url: str | None = None,
    ) -> list[PaperRecord]:
        return parse_rss_feed(
            xml_text,
            source="medrxiv",
            subject=subject,
            feed_url=feed_url or self.build_feed_url(subject),
        )


def _paper_date(paper: PaperRecord) -> date | None:
    return paper.published or paper.updated
