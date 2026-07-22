from omegaconf import open_dict
import requests
from types import SimpleNamespace

from zotero_arxiv_daily.retriever.iacr_eprint_retriever import IacrEprintRetriever, _keyword_in_text, _normalize_text


RSS = """<?xml version="1.0"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <item>
      <title>Lattice Foundations for Fully Homomorphic Encryption</title>
      <link>https://eprint.iacr.org/2026/123</link>
      <description>A fully homomorphic encryption construction from LWE.</description>
      <guid>https://eprint.iacr.org/2026/123</guid>
      <category>Public-key cryptography</category>
      <enclosure url="https://eprint.iacr.org/2026/123.pdf" type="application/pdf" />
      <pubDate>Tue, 23 Jun 2026 09:00:00 +0000</pubDate>
      <dc:creator>Alice</dc:creator>
      <dc:creator>Bob</dc:creator>
    </item>
    <item>
      <title>Block Cipher Analysis</title>
      <link>https://eprint.iacr.org/2026/124</link>
      <description>Symmetric-key analysis.</description>
      <category>Secret-key cryptography</category>
      <pubDate>Tue, 23 Jun 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_iacr_eprint_retriever_filters_feed(config, monkeypatch):
    with open_dict(config):
        config.source.iacr_eprint.start_date = "2026-06-23"
        config.source.iacr_eprint.end_date = "2026-06-23"
        config.source.iacr_eprint.categories = ["Public-key cryptography"]
        config.source.iacr_eprint.keyword_required = True
        config.source.iacr_eprint.keywords = ["fully homomorphic", "lwe"]
        config.source.iacr_eprint.request_delay = 0

    class FakeResponse:
        text = RSS

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.proxies = {}

        def get(self, url, timeout):
            return FakeResponse()

    monkeypatch.setattr("zotero_arxiv_daily.retriever.iacr_eprint_retriever.requests.Session", FakeSession)

    papers = IacrEprintRetriever(config).retrieve_papers()
    assert len(papers) == 1
    assert papers[0].source == "iacr_eprint"
    assert papers[0].title == "[ePrint] Lattice Foundations for Fully Homomorphic Encryption"
    assert papers[0].authors == ["Alice", "Bob"]
    assert papers[0].pdf_url == "https://eprint.iacr.org/2026/123.pdf"


def test_iacr_keyword_matching_avoids_substring_false_positives():
    searchable = _normalize_text("Physics-Aware Temporal Feature Engineering for Eavesdropping Detection")
    assert not _keyword_in_text("sis", searchable)
    assert not _keyword_in_text("fhe", searchable)
    assert _keyword_in_text("physics", searchable)


def test_iacr_fetch_falls_back_to_powershell_when_requests_fails(config, monkeypatch):
    with open_dict(config):
        config.source.iacr_eprint.request_timeout = 30
        config.source.iacr_eprint.proxy = None

    class FailingSession:
        def __init__(self):
            self.headers = {}
            self.proxies = {}

        def get(self, url, timeout):
            raise requests.exceptions.SSLError("tls eof")

    calls = []

    def fake_run(command, check, capture_output, text, timeout):
        calls.append(command)
        return SimpleNamespace(stdout=RSS)

    monkeypatch.setattr("zotero_arxiv_daily.retriever.iacr_eprint_retriever.requests.Session", FailingSession)
    monkeypatch.setattr("zotero_arxiv_daily.retriever.iacr_eprint_retriever.subprocess.run", fake_run)

    assert IacrEprintRetriever(config)._fetch_feed() == RSS
    assert calls
