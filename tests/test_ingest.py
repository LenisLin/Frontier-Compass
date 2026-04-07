import socket
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from frontier_compass.ingest.arxiv import (
    ArxivClient,
    ArxivRequestError,
    build_biomedical_discovery_queries,
    build_zotero_retrieval_queries,
    filter_paper_batches_by_date,
    latest_available_paper_date,
    merge_category_papers,
    merge_paper_batches,
)
from frontier_compass.ingest.biorxiv import BioRxivClient
from frontier_compass.ingest.common import FeedRequestError
from frontier_compass.ingest.medrxiv import MedRxivClient
from frontier_compass.storage.schema import PaperRecord
from frontier_compass.ui import BIOMEDICAL_LATEST_MODE, FrontierCompassApp
from frontier_compass.zotero.profile_builder import ZoteroProfileBuilder


ARXIV_DAILY_XML = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns="http://www.w3.org/2005/Atom">
  <id>http://rss.arxiv.org/atom/cs</id>
  <title>cs updates on arXiv.org</title>
  <updated>2026-03-23T04:00:05.907047+00:00</updated>
  <entry>
    <id>oai:arXiv.org:2603.19236v1</id>
    <title>Retrieval Agents for Science</title>
    <updated>2026-03-23T04:00:05.965050+00:00</updated>
    <link href="https://arxiv.org/abs/2603.19236" rel="alternate" type="text/html"/>
    <summary>arXiv:2603.19236v1 Announce Type: new Abstract: Agentic ranking for frontier papers.</summary>
    <category term="cs.CL"/>
    <category term="cs.IR"/>
    <published>2026-03-23T00:00:00-04:00</published>
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:creator>A Researcher, B Curator</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2603.19000v1</id>
    <title>Older Item</title>
    <updated>2026-03-22T04:00:05.965050+00:00</updated>
    <link href="https://arxiv.org/abs/2603.19000" rel="alternate" type="text/html"/>
    <summary>arXiv:2603.19000v1 Announce Type: new Abstract: Previous day paper.</summary>
    <category term="cs.AI"/>
    <published>2026-03-22T00:00:00-04:00</published>
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:creator>Older Author</dc:creator>
  </entry>
</feed>
"""

RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <guid>https://www.biorxiv.org/content/10.1101/2026.03.20.000001v1</guid>
      <title>Single-cell retrieval</title>
      <description><![CDATA[Embedding models for atlas search.]]></description>
      <link>https://www.biorxiv.org/content/10.1101/2026.03.20.000001v1</link>
      <pubDate>Mon, 23 Mar 2026 10:00:00 GMT</pubDate>
      <dc:creator>Jane Doe</dc:creator>
      <category>bioinformatics</category>
    </item>
  </channel>
</rss>
"""

RSS10_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="https://biorxiv.org">
    <title>bioRxiv Subject Collection: All</title>
    <items>
      <rdf:Seq>
        <rdf:li rdf:resource="https://www.biorxiv.org/content/10.64898/2026.04.03.716344v1?rss=1"/>
      </rdf:Seq>
    </items>
  </channel>
  <item rdf:about="https://www.biorxiv.org/content/10.64898/2026.04.03.716344v1?rss=1">
    <title>Reachable RSS 1.0 paper</title>
    <link>https://www.biorxiv.org/content/10.64898/2026.04.03.716344v1?rss=1</link>
    <description>bioRxiv RSS 1.0 fixture.</description>
    <dc:date>2026-04-03T10:00:00Z</dc:date>
    <dc:creator>Ada Lovelace</dc:creator>
    <category>bioinformatics</category>
  </item>
</rdf:RDF>
"""
RECENT_LISTING_HTML = """<!DOCTYPE html>
<html>
  <body>
    <div class="highwire-list-wrapper highwire-list-pap highwire-article-citation-list">
      <h3 class="highwire-list-title">April 02, 2026</h3>
      <div class="highwire-list">
        <ul id="april-02-2026">
          <li class="first odd">
            <div class="highwire-article-citation" data-pisa="biorxiv;2026.04.01.716005v1" data-pisa-master="biorxiv;2026.04.01.716005">
              <div class="highwire-cite">
                <span class="highwire-cite-title">
                  <a href="/content/10.64898/2026.04.01.716005v1" class="highwire-cite-linked-title">
                    <span class="highwire-cite-title">Reachable fallback paper</span>
                  </a>
                </span>
                <div class="highwire-cite-authors">
                  <span class="highwire-citation-authors">
                    <span class="highwire-citation-author first"><span class="nlm-given-names">Ada</span> <span class="nlm-surname">Lovelace</span></span>,
                    <span class="highwire-citation-author"><span class="nlm-given-names">Grace</span> <span class="nlm-surname">Hopper</span></span>
                  </span>
                </div>
                <div class="highwire-cite-metadata">
                  <span class="highwire-cite-metadata-journal highwire-cite-metadata">bioRxiv </span>
                  <span class="highwire-cite-metadata-pages highwire-cite-metadata">2026.04.01.716005; </span>
                  <span class="highwire-cite-metadata-doi highwire-cite-metadata"><span class="doi_label">doi:</span> https://doi.org/10.64898/2026.04.01.716005 </span>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </body>
</html>
"""
ZOTERO_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zotero" / "sample_library.csl.json"


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        del exc_type, exc, tb
        return False


def test_arxiv_atom_parser() -> None:
    papers = ArxivClient().parse_feed(ARXIV_DAILY_XML)
    assert len(papers) == 2
    assert papers[0].source == "arxiv"
    assert papers[0].identifier == "2603.19236v1"
    assert papers[0].source_identifier == "2603.19236v1"
    assert papers[0].source_url == "https://arxiv.org/abs/2603.19236"
    assert papers[0].source_tags == ("cs.CL", "cs.IR")
    assert papers[0].authors == ("A Researcher", "B Curator")
    assert papers[0].summary == "Agentic ranking for frontier papers."
    assert papers[0].updated == date(2026, 3, 23)
    assert papers[0].source_metadata["feed_kind"] == "atom"
    assert papers[0].source_metadata["announce_type"] == "new"


def test_arxiv_fetch_today_filters_to_target_date(tmp_path) -> None:
    feed_path = tmp_path / "arxiv.xml"
    feed_path.write_text(ARXIV_DAILY_XML, encoding="utf-8")
    papers = ArxivClient().fetch_today("cs", today=date(2026, 3, 23), feed_url=feed_path.as_uri())
    assert [paper.identifier for paper in papers] == ["2603.19236v1"]
    assert papers[0].url == "https://arxiv.org/abs/2603.19236"


def test_arxiv_fetch_today_with_timings_reports_network_and_parse(tmp_path) -> None:
    feed_path = tmp_path / "arxiv.xml"
    feed_path.write_text(ARXIV_DAILY_XML, encoding="utf-8")

    papers, network_seconds, parse_seconds = ArxivClient().fetch_today_with_timings(
        "cs",
        today=date(2026, 3, 23),
        feed_url=feed_path.as_uri(),
    )

    assert [paper.identifier for paper in papers] == ["2603.19236v1"]
    assert network_seconds >= 0.0
    assert parse_seconds >= 0.0


def test_arxiv_fetch_today_by_category_merges_and_deduplicates(tmp_path) -> None:
    qbio_feed = tmp_path / "qbio.xml"
    qbio_gn_feed = tmp_path / "qbio_gn.xml"
    qbio_feed.write_text(
        ARXIV_DAILY_XML.replace("http://rss.arxiv.org/atom/cs", "http://rss.arxiv.org/atom/q-bio").replace(
            "cs updates on arXiv.org",
            "q-bio updates on arXiv.org",
        ).replace(
            '<category term="cs.CL"/>\n    <category term="cs.IR"/>',
            '<category term="q-bio"/>\n    <category term="q-bio.GN"/>',
        ),
        encoding="utf-8",
    )
    qbio_gn_feed.write_text(
        ARXIV_DAILY_XML.replace("http://rss.arxiv.org/atom/cs", "http://rss.arxiv.org/atom/q-bio.GN").replace(
            "cs updates on arXiv.org",
            "q-bio.GN updates on arXiv.org",
        ).replace(
            "Retrieval Agents for Science",
            "Retrieval Agents for Science",
        ).replace(
            '<category term="cs.CL"/>\n    <category term="cs.IR"/>',
            '<category term="q-bio.GN"/>\n    <category term="q-bio.QM"/>',
        ),
        encoding="utf-8",
    )

    category_papers = ArxivClient().fetch_today_by_category(
        ("q-bio", "q-bio.GN"),
        today=date(2026, 3, 23),
        feed_urls={
            "q-bio": qbio_feed.as_uri(),
            "q-bio.GN": qbio_gn_feed.as_uri(),
        },
    )
    merged = merge_category_papers(category_papers)

    assert {category: len(papers) for category, papers in category_papers.items()} == {
        "q-bio": 1,
        "q-bio.GN": 1,
    }
    assert len(merged) == 1
    assert merged[0].identifier == "2603.19236v1"
    assert merged[0].categories == ("q-bio", "q-bio.GN", "q-bio.QM")


def test_biorxiv_fetch_today_with_timings_reports_network_and_parse(tmp_path) -> None:
    feed_path = tmp_path / "biorxiv.xml"
    feed_path.write_text(RSS_XML, encoding="utf-8")

    papers, network_seconds, parse_seconds = BioRxivClient().fetch_today_with_timings(
        today=date(2026, 3, 23),
        feed_url=feed_path.as_uri(),
    )

    assert [paper.identifier for paper in papers] == ["10.1101/2026.03.20.000001v1"]
    assert network_seconds >= 0.0
    assert parse_seconds >= 0.0


def test_medrxiv_fetch_today_with_timings_reports_network_and_parse(tmp_path) -> None:
    feed_path = tmp_path / "medrxiv.xml"
    feed_path.write_text(RSS_XML, encoding="utf-8")

    papers, network_seconds, parse_seconds = MedRxivClient().fetch_today_with_timings(
        today=date(2026, 3, 23),
        feed_url=feed_path.as_uri(),
    )

    assert [paper.identifier for paper in papers] == ["10.1101/2026.03.20.000001v1"]
    assert network_seconds >= 0.0
    assert parse_seconds >= 0.0


def test_arxiv_fetch_recent_retries_after_http_429(monkeypatch) -> None:
    sleep_calls: list[float] = []
    attempts: list[tuple[str, int | float | None]] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        attempts.append((request.get_header("User-agent"), timeout))
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)
        return _FakeResponse(ARXIV_DAILY_XML)

    monkeypatch.setattr("frontier_compass.ingest.arxiv.sleep", fake_sleep)
    monkeypatch.setattr("frontier_compass.ingest.arxiv.urlopen", fake_urlopen)

    papers = ArxivClient().fetch_recent("cat:q-bio", max_results=25)

    assert [paper.identifier for paper in papers] == ["2603.19236v1", "2603.19000v1"]
    assert attempts == [
        ("FrontierCompass/0.1 (arXiv scouting CLI)", 15),
        ("FrontierCompass/0.1 (arXiv scouting CLI)", 15),
    ]
    assert sleep_calls == [2.0]


def test_arxiv_fetch_feed_retries_after_timeout(monkeypatch) -> None:
    sleep_calls: list[float] = []
    attempts: list[int] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        del request, timeout
        attempts.append(1)
        if len(attempts) == 1:
            raise URLError(socket.timeout("timed out"))
        return _FakeResponse(ARXIV_DAILY_XML)

    monkeypatch.setattr("frontier_compass.ingest.arxiv.sleep", fake_sleep)
    monkeypatch.setattr("frontier_compass.ingest.arxiv.urlopen", fake_urlopen)

    papers = ArxivClient().fetch_feed("q-bio")

    assert [paper.identifier for paper in papers] == ["2603.19236v1", "2603.19000v1"]
    assert sleep_calls == [1.0]
    assert len(attempts) == 2


def test_arxiv_fetch_feed_raises_after_retry_exhaustion(monkeypatch) -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
        del request, timeout
        raise HTTPError("https://rss.arxiv.org/atom/q-bio", 429, "Too Many Requests", hdrs=None, fp=None)

    monkeypatch.setattr("frontier_compass.ingest.arxiv.sleep", fake_sleep)
    monkeypatch.setattr("frontier_compass.ingest.arxiv.urlopen", fake_urlopen)

    with pytest.raises(ArxivRequestError, match="HTTP 429 Too Many Requests"):
        ArxivClient().fetch_feed("q-bio")

    assert sleep_calls == [2.0]


def test_arxiv_fetch_recent_by_category_applies_request_pacing(monkeypatch) -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_fetch_feed_with_timings(self, category: str, *, feed_url=None):  # type: ignore[no-untyped-def]
        del self, feed_url
        return (
            [
                PaperRecord(
                    source="arxiv",
                    identifier=f"{category}-paper",
                    title=f"{category} paper",
                    summary="A category paper.",
                    categories=(category,),
                    published=date(2026, 3, 24),
                    url=f"https://arxiv.org/abs/{category}",
                )
            ],
            0.1,
            0.0,
        )

    monkeypatch.setattr("frontier_compass.ingest.arxiv.sleep", fake_sleep)
    monkeypatch.setattr(ArxivClient, "fetch_feed_with_timings", fake_fetch_feed_with_timings)

    category_papers = ArxivClient().fetch_recent_by_category(("q-bio", "q-bio.GN", "q-bio.QM"))

    assert sorted(category_papers) == ["q-bio", "q-bio.GN", "q-bio.QM"]
    assert sleep_calls == [0.5, 0.5]


def test_arxiv_fetch_recent_by_queries_applies_request_pacing(monkeypatch) -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_fetch_recent_with_timings(self, query: str, *, max_results: int = 25):  # type: ignore[no-untyped-def]
        del self, max_results
        return (
            [
                PaperRecord(
                    source="arxiv",
                    identifier=query,
                    title=query,
                    summary="A query paper.",
                    categories=("q-bio",),
                    published=date(2026, 3, 24),
                    url="https://arxiv.org/abs/2603.20001",
                )
            ],
            0.1,
            0.0,
        )

    monkeypatch.setattr("frontier_compass.ingest.arxiv.sleep", fake_sleep)
    monkeypatch.setattr(ArxivClient, "fetch_recent_with_timings", fake_fetch_recent_with_timings)

    query_papers = ArxivClient().fetch_recent_by_queries(
        (
            build_biomedical_discovery_queries()[0],
            build_biomedical_discovery_queries()[1],
        ),
        max_results=80,
    )

    assert sorted(query_papers) == ["biomedical-imaging-and-clinical-ai", "omics-and-single-cell"]
    assert sleep_calls == [0.5]


def test_build_biomedical_discovery_queries_uses_fixed_profile() -> None:
    queries = build_biomedical_discovery_queries()

    assert [query.label for query in queries] == [
        "omics-and-single-cell",
        "biomedical-imaging-and-clinical-ai",
    ]
    assert "cat:q-bio" in queries[0].query
    assert "cat:cs.LG" in queries[0].query
    assert "cat:stat.ML" in queries[1].query
    assert 'all:"single-cell"' in queries[0].query
    assert "all:bioinformatics" in queries[0].query
    assert "all:clinical" in queries[1].query
    assert 'all:"foundation model"' not in queries[1].query
    assert "all:healthcare" not in queries[1].query
    assert "all:multimodal" not in queries[1].query


def test_build_zotero_retrieval_queries_uses_compact_biomedical_hints() -> None:
    profile = ZoteroProfileBuilder().build_augmented_profile(
        FrontierCompassApp.daily_profile(BIOMEDICAL_LATEST_MODE),
        export_path=ZOTERO_FIXTURE_PATH,
    )

    queries = build_zotero_retrieval_queries(profile)

    assert [query.label for query in queries] == [
        "zotero-omics-pathology",
        "zotero-protein-discovery",
    ]
    assert all(query.origin == "zotero" for query in queries)
    assert queries[0].terms == ("spatial transcriptomics", "digital pathology")
    assert 'all:"spatial transcriptomics"' in queries[0].query
    assert 'all:"digital pathology"' in queries[0].query
    assert queries[1].terms == ("drug discovery", "protein structure")
    assert 'all:"drug discovery"' in queries[1].query


def test_merge_paper_batches_preserves_retrieval_support_metadata_across_duplicates() -> None:
    baseline = PaperRecord(
        source="arxiv",
        identifier="2603.21001v1",
        title="Spatial transcriptomics from digital pathology images",
        summary="Biomedical fixture.",
        categories=("q-bio.GN",),
        published=date(2026, 3, 24),
        url="https://arxiv.org/abs/2603.21001",
    )
    zotero_augmented = PaperRecord(
        source="arxiv",
        identifier="2603.21001v1",
        title="Spatial transcriptomics from digital pathology images",
        summary="Biomedical fixture.",
        categories=("cs.CV",),
        published=date(2026, 3, 24),
        url="https://arxiv.org/abs/2603.21001",
        source_metadata={
            "retrieval_support": [
                {
                    "label": "zotero-omics-pathology",
                    "origin": "zotero",
                    "terms": ["spatial transcriptomics", "digital pathology"],
                }
            ]
        },
    )

    merged = merge_paper_batches(
        {
            "baseline": [baseline],
            "zotero": [zotero_augmented],
        }
    )

    assert len(merged) == 1
    assert merged[0].categories == ("q-bio.GN", "cs.CV")
    assert merged[0].source_metadata["retrieval_support"][0]["origin"] == "zotero"
    assert merged[0].source_metadata["retrieval_support"][0]["terms"] == [
        "spatial transcriptomics",
        "digital pathology",
    ]


def test_arxiv_fetch_today_by_queries_filters_to_target_date(monkeypatch) -> None:
    target_date = date(2026, 3, 24)

    def fake_fetch_recent_with_timings(self, query: str, *, max_results: int = 25) -> tuple[list[PaperRecord], float, float]:
        del query, max_results
        return (
            [
                PaperRecord(
                    source="arxiv",
                    identifier="2603.21001v1",
                    title="Same-day biomedical discovery paper",
                    summary="A same-day paper.",
                    categories=("q-bio.GN", "cs.LG"),
                    published=target_date,
                    url="https://arxiv.org/abs/2603.21001",
                ),
                PaperRecord(
                    source="arxiv",
                    identifier="2603.20900v1",
                    title="Previous-day biomedical discovery paper",
                    summary="An older paper.",
                    categories=("q-bio.GN",),
                    published=date(2026, 3, 23),
                    url="https://arxiv.org/abs/2603.20900",
                ),
            ],
            0.1,
            0.0,
        )

    monkeypatch.setattr(ArxivClient, "fetch_recent_with_timings", fake_fetch_recent_with_timings)

    query_definitions = build_biomedical_discovery_queries()[:1]
    query_papers = ArxivClient().fetch_today_by_queries(
        query_definitions,
        today=target_date,
        max_results=80,
    )

    assert list(query_papers) == ["omics-and-single-cell"]
    assert [paper.identifier for paper in query_papers["omics-and-single-cell"]] == ["2603.21001v1"]


def test_filter_paper_batches_by_date_preserves_labels() -> None:
    target_date = date(2026, 3, 24)
    filtered = filter_paper_batches_by_date(
        {
            "q-bio": [
                PaperRecord(
                    source="arxiv",
                    identifier="2603.21001v1",
                    title="Same-day paper",
                    summary="Same-day summary.",
                    categories=("q-bio.GN",),
                    published=target_date,
                    url="https://arxiv.org/abs/2603.21001",
                ),
                PaperRecord(
                    source="arxiv",
                    identifier="2603.20999v1",
                    title="Previous-day paper",
                    summary="Previous-day summary.",
                    categories=("q-bio.GN",),
                    published=date(2026, 3, 23),
                    url="https://arxiv.org/abs/2603.20999",
                ),
            ]
        },
        target_date=target_date,
    )

    assert list(filtered) == ["q-bio"]
    assert [paper.identifier for paper in filtered["q-bio"]] == ["2603.21001v1"]


def test_latest_available_paper_date_selects_most_recent_on_or_before_requested_date() -> None:
    papers = [
        PaperRecord(
            source="arxiv",
            identifier="2603.21001v1",
            title="Requested-day paper",
            summary="Same-day paper.",
            categories=("q-bio.GN",),
            published=date(2026, 3, 24),
            url="https://arxiv.org/abs/2603.21001",
        ),
        PaperRecord(
            source="arxiv",
            identifier="2603.20999v1",
            title="Previous-day paper",
            summary="Previous-day paper.",
            categories=("q-bio.GN",),
            published=date(2026, 3, 23),
            url="https://arxiv.org/abs/2603.20999",
        ),
        PaperRecord(
            source="arxiv",
            identifier="2603.22000v1",
            title="Future paper",
            summary="Future paper.",
            categories=("q-bio.GN",),
            published=date(2026, 3, 25),
            url="https://arxiv.org/abs/2603.22000",
        ),
    ]

    assert latest_available_paper_date(papers, requested_date=date(2026, 3, 24)) == date(2026, 3, 24)
    assert latest_available_paper_date(papers, requested_date=date(2026, 3, 23)) == date(2026, 3, 23)
    assert latest_available_paper_date(papers, requested_date=date(2026, 3, 22)) is None


def test_merge_paper_batches_deduplicates_hybrid_bundle_and_query_results() -> None:
    target_date = date(2026, 3, 24)
    merged = merge_paper_batches(
        {
            "q-bio": [
                PaperRecord(
                    source="arxiv",
                    identifier="2603.21001v1",
                    title="Hybrid discovery match",
                    summary="Bundle entry.",
                    categories=("q-bio.GN",),
                    published=target_date,
                    url="https://arxiv.org/abs/2603.21001",
                )
            ],
            "omics-and-single-cell": [
                PaperRecord(
                    source="arxiv",
                    identifier="2603.21001v2",
                    title="Hybrid discovery match",
                    summary="Query entry.",
                    categories=("cs.LG", "q-bio.QM"),
                    published=target_date,
                    url="https://arxiv.org/abs/2603.21001",
                )
            ],
        }
    )

    assert len(merged) == 1
    assert merged[0].identifier == "2603.21001v1"
    assert merged[0].categories == ("q-bio.GN", "cs.LG", "q-bio.QM")


def test_biorxiv_and_medrxiv_rss_parser() -> None:
    biorxiv_papers = BioRxivClient().parse_feed(
        RSS_XML,
        subject="all",
        feed_url="https://connect.biorxiv.org/biorxiv_xml.php?subject=all",
    )
    medrxiv_papers = MedRxivClient().parse_feed(
        RSS_XML.replace("biorxiv", "medrxiv"),
        subject="all",
        feed_url="https://connect.medrxiv.org/medrxiv_xml.php?subject=all",
    )

    assert biorxiv_papers[0].source == "biorxiv"
    assert medrxiv_papers[0].source == "medrxiv"
    assert biorxiv_papers[0].identifier == "10.1101/2026.03.20.000001v1"
    assert medrxiv_papers[0].identifier == "10.1101/2026.03.20.000001v1"
    assert biorxiv_papers[0].source_identifier == "10.1101/2026.03.20.000001v1"
    assert medrxiv_papers[0].source_identifier == "10.1101/2026.03.20.000001v1"
    assert biorxiv_papers[0].source_url == "https://www.biorxiv.org/content/10.1101/2026.03.20.000001v1"
    assert medrxiv_papers[0].source_url == "https://www.medrxiv.org/content/10.1101/2026.03.20.000001v1"
    assert biorxiv_papers[0].source_tags == ("bioinformatics",)
    assert biorxiv_papers[0].authors == ("Jane Doe",)
    assert biorxiv_papers[0].source_metadata["subject"] == "all"
    assert medrxiv_papers[0].source_metadata["feed_url"] == "https://connect.medrxiv.org/medrxiv_xml.php?subject=all"


def test_biorxiv_and_medrxiv_rss_parser_accepts_dc_date_without_pubdate() -> None:
    rss_with_dc_date = RSS_XML.replace(
        "<pubDate>Mon, 23 Mar 2026 10:00:00 GMT</pubDate>",
        "<dc:date>2026-03-23T10:00:00Z</dc:date>",
    )

    biorxiv_papers = BioRxivClient().parse_feed(
        rss_with_dc_date,
        subject="all",
        feed_url="https://connect.biorxiv.org/biorxiv_xml.php?subject=all",
    )
    medrxiv_papers = MedRxivClient().parse_feed(
        rss_with_dc_date.replace("biorxiv", "medrxiv"),
        subject="all",
        feed_url="https://connect.medrxiv.org/medrxiv_xml.php?subject=all",
    )

    assert biorxiv_papers[0].published == date(2026, 3, 23)
    assert medrxiv_papers[0].published == date(2026, 3, 23)
    assert biorxiv_papers[0].authors == ("Jane Doe",)
    assert medrxiv_papers[0].authors == ("Jane Doe",)


def test_biorxiv_and_medrxiv_rss_parser_accepts_rss10_namespace_items() -> None:
    biorxiv_papers = BioRxivClient().parse_feed(
        RSS10_XML,
        subject="all",
        feed_url="https://connect.biorxiv.org/biorxiv_xml.php?subject=all",
    )
    medrxiv_papers = MedRxivClient().parse_feed(
        RSS10_XML.replace("biorxiv", "medrxiv"),
        subject="all",
        feed_url="https://connect.medrxiv.org/medrxiv_xml.php?subject=all",
    )

    assert biorxiv_papers[0].title == "Reachable RSS 1.0 paper"
    assert medrxiv_papers[0].title == "Reachable RSS 1.0 paper"
    assert biorxiv_papers[0].published == date(2026, 4, 3)
    assert medrxiv_papers[0].published == date(2026, 4, 3)
    assert biorxiv_papers[0].source_tags == ("bioinformatics",)
    assert medrxiv_papers[0].authors == ("Ada Lovelace",)


def test_biorxiv_fetch_today_falls_back_to_recent_listing(monkeypatch) -> None:
    calls: list[str] = []
    fallback_user_agents: list[str] = []

    def fake_fetch_text(url: str, **kwargs) -> str:  # type: ignore[no-untyped-def]
        calls.append(url)
        if "connect.biorxiv.org" in url:
            raise FeedRequestError("bioRxiv request timed out")
        fallback_user_agents.append(kwargs.get("user_agent", ""))
        return RECENT_LISTING_HTML

    monkeypatch.setattr("frontier_compass.ingest.biorxiv.fetch_text", fake_fetch_text)

    client = BioRxivClient()
    papers, network_seconds, parse_seconds = client.fetch_today_with_timings(today=date(2026, 4, 2))

    assert [paper.identifier for paper in papers] == ["10.64898/2026.04.01.716005v1"]
    assert papers[0].authors == ("Ada Lovelace", "Grace Hopper")
    assert papers[0].source_metadata["feed_kind"] == "recent-html"
    assert client.last_fetch_details is not None
    assert client.last_fetch_details.contract_mode == "recent-html"
    assert client.last_fetch_details.endpoint == client.recent_listing_url
    assert "Primary RSS feed" in client.last_fetch_details.note
    assert calls == [client.build_feed_url("all"), client.recent_listing_url]
    assert fallback_user_agents == [client.recent_listing_user_agent]
    assert network_seconds >= 0.0
    assert parse_seconds >= 0.0


def test_medrxiv_recent_listing_fails_truthfully_for_unavailable_historical_date(monkeypatch) -> None:
    def fake_fetch_text(url: str, **kwargs) -> str:  # type: ignore[no-untyped-def]
        del kwargs
        if "connect.medrxiv.org" in url:
            raise FeedRequestError("medRxiv request timed out")
        return RECENT_LISTING_HTML.replace("April 02, 2026", "April 01, 2026").replace("biorxiv", "medrxiv")

    monkeypatch.setattr("frontier_compass.ingest.medrxiv.fetch_text", fake_fetch_text)

    client = MedRxivClient()

    with pytest.raises(FeedRequestError, match="only exposes 2026-04-01"):
        client.fetch_today_with_timings(today=date(2026, 4, 2))
