"""medRxiv ingestion via the public RSS feed."""

from __future__ import annotations

from datetime import date

from frontier_compass.ingest.common import fetch_text, measure_operation, parse_rss_feed
from frontier_compass.storage.schema import PaperRecord


class MedRxivClient:
    feed_url_template = "https://connect.medrxiv.org/medrxiv_xml.php?subject={subject}"
    request_timeout_seconds = 20
    request_max_attempts = 2

    def build_feed_url(self, subject: str = "all") -> str:
        normalized_subject = (subject or "all").strip() or "all"
        return self.feed_url_template.format(subject=normalized_subject)

    def fetch_latest(self, *, subject: str = "all", feed_url: str | None = None) -> list[PaperRecord]:
        papers, _, _ = self.fetch_latest_with_timings(subject=subject, feed_url=feed_url)
        return papers

    def fetch_latest_with_timings(
        self,
        *,
        subject: str = "all",
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        resolved_feed_url = feed_url or self.build_feed_url(subject)
        xml_text, network_seconds = measure_operation(
            lambda: fetch_text(
                resolved_feed_url,
                timeout=self.request_timeout_seconds,
                max_attempts=self.request_max_attempts,
                source_label="medRxiv",
            )
        )
        papers, parse_seconds = measure_operation(
            lambda: self.parse_feed(
                xml_text,
                subject=subject,
                feed_url=resolved_feed_url,
            )
        )
        return papers, network_seconds, parse_seconds

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
        papers, _, _ = self.fetch_today_with_timings(
            today=today,
            subject=subject,
            max_results=max_results,
            feed_url=feed_url,
        )
        return papers

    def fetch_today_with_timings(
        self,
        *,
        today: date | None = None,
        subject: str = "all",
        max_results: int | None = None,
        feed_url: str | None = None,
    ) -> tuple[list[PaperRecord], float, float]:
        target_date = today or date.today()
        papers, network_seconds, parse_seconds = self.fetch_feed_with_timings(
            subject=subject,
            feed_url=feed_url,
        )
        papers = [paper for paper in papers if _paper_date(paper) == target_date]
        if max_results is None:
            return papers, network_seconds, parse_seconds
        return papers[: max(max_results, 0)], network_seconds, parse_seconds

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
