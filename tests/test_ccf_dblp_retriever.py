import json
from types import SimpleNamespace

from omegaconf import open_dict

from zotero_arxiv_daily.retriever.ccf_dblp_retriever import (
    CcfDblpRetriever,
    _normalize_dblp_venue_xml_url,
    _parse_dblp_xml,
)


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<dblp>
  <article mdate="2026-05-15" key="journals/joc/Sample26">
    <author>Alice</author>
    <author>Bob</author>
    <title>Fully Homomorphic Encryption from Lattice Assumptions.</title>
    <year>2026</year>
    <ee>https://doi.org/10.1000/example</ee>
    <doi>10.1000/example</doi>
  </article>
  <article mdate="2026-04-30" key="journals/joc/Old26">
    <author>Carol</author>
    <title>Old Cryptography Paper.</title>
    <year>2026</year>
  </article>
  <article mdate="2026-06-01" key="journals/joc/Other26">
    <author>Dan</author>
    <title>Database Systems Without Crypto.</title>
    <year>2026</year>
  </article>
</dblp>
"""


def test_normalize_dblp_venue_xml_url():
    assert (
        _normalize_dblp_venue_xml_url("http://dblp.uni-trier.de/db/journals/joc/")
        == "https://dblp.org/db/journals/joc/index.xml"
    )
    assert (
        _normalize_dblp_venue_xml_url("https://dblp.org/db/conf/crypto/index.html")
        == "https://dblp.org/db/conf/crypto/index.xml"
    )
    assert _normalize_dblp_venue_xml_url("https://example.com/not-dblp") is None


def test_parse_dblp_xml_filters_update_date_and_keyword():
    venue = {
        "类型": "期刊",
        "领域": "网络与信息安全",
        "等级": "A",
        "简称": "JoC",
        "全称": "Journal of Cryptology",
    }
    papers = _parse_dblp_xml(
        SAMPLE_XML,
        venue=venue,
        source_url="https://dblp.org/db/journals/joc/index.xml",
        start=__import__("datetime").date(2026, 5, 1),
        end=__import__("datetime").date(2026, 5, 31),
        keywords=["homomorphic"],
        keyword_required=True,
    )
    assert len(papers) == 1
    assert papers[0].title == "Fully Homomorphic Encryption from Lattice Assumptions"
    assert papers[0].authors == ["Alice", "Bob"]
    assert papers[0].ccf_rank == "A"


def test_ccf_dblp_retriever_fetches_and_converts(config, tmp_path, monkeypatch):
    ccf_path = tmp_path / "ccf.json"
    ccf_path.write_text(
        json.dumps(
            [
                {
                    "类型": "期刊",
                    "领域": "网络与信息安全",
                    "等级": "A",
                    "序号": "1",
                    "简称": "JoC",
                    "全称": "Journal of Cryptology",
                    "出版社": "Springer",
                    "网址": "https://dblp.org/db/journals/joc/",
                    "页码": 17,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with open_dict(config):
        config.source.ccf_dblp.ccf_json_path = str(ccf_path)
        config.source.ccf_dblp.fields = ["网络与信息安全"]
        config.source.ccf_dblp.ranks = ["A"]
        config.source.ccf_dblp.types = ["期刊"]
        config.source.ccf_dblp.start_date = "2026-05-01"
        config.source.ccf_dblp.end_date = "2026-07-31"
        config.source.ccf_dblp.keyword_required = False
        config.source.ccf_dblp.request_delay = 0
        config.source.ccf_dblp.proxy = "http://127.0.0.1:7890"

    class FakeResponse:
        text = SAMPLE_XML

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.proxies = {}

        def get(self, url, timeout):
            assert url == "https://dblp.org/db/journals/joc/index.xml"
            assert self.proxies == {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            }
            return FakeResponse()

    monkeypatch.setattr("zotero_arxiv_daily.retriever.ccf_dblp_retriever.requests.Session", FakeSession)

    retriever = CcfDblpRetriever(config)
    papers = retriever.retrieve_papers()
    assert len(papers) == 2
    assert papers[0].source == "ccf_dblp"
    assert papers[0].title.startswith("[CCF A]")
    assert "CCF A 期刊" in papers[0].abstract
