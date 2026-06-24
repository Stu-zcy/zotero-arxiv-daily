import json

from omegaconf import open_dict

from zotero_arxiv_daily.retriever.ccf_openalex_retriever import CcfOpenAlexRetriever, _restore_abstract


def test_restore_abstract_from_inverted_index():
    assert _restore_abstract({"fully": [0], "homomorphic": [1], "encryption": [2]}) == "fully homomorphic encryption"
    assert _restore_abstract(None) == ""


def test_ccf_openalex_retriever_fetches_and_converts(config, tmp_path, monkeypatch):
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
        config.source.ccf_openalex.ccf_json_path = str(ccf_path)
        config.source.ccf_openalex.fields = ["网络与信息安全"]
        config.source.ccf_openalex.ranks = ["A"]
        config.source.ccf_openalex.types = ["期刊"]
        config.source.ccf_openalex.start_date = "2026-05-01"
        config.source.ccf_openalex.end_date = "2026-07-31"
        config.source.ccf_openalex.keyword_required = True
        config.source.ccf_openalex.search_queries = ["homomorphic encryption"]
        config.source.ccf_openalex.keywords = ["homomorphic encryption"]
        config.source.ccf_openalex.request_delay = 0
        config.source.ccf_openalex.proxy = "http://127.0.0.1:7890"

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.proxies = {}
            self.calls = []

        def get(self, url, params, timeout):
            self.calls.append((url, params, timeout))
            assert self.proxies == {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            }
            if url.endswith("/sources"):
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": "https://openalex.org/S190936789",
                                "display_name": "Journal of Cryptology",
                            }
                        ]
                    }
                )
            if url.endswith("/works"):
                assert "from_publication_date:2026-05-01" in params["filter"]
                assert "to_publication_date:2026-07-31" in params["filter"]
                assert params["search"] == "homomorphic encryption"
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": "https://openalex.org/W1",
                                "display_name": "Fully Homomorphic Encryption from Lattices",
                                "publication_date": "2026-06-01",
                                "doi": "https://doi.org/10.1000/example",
                                "primary_location": {
                                    "landing_page_url": "https://doi.org/10.1000/example",
                                    "pdf_url": None,
                                },
                                "authorships": [
                                    {"author": {"display_name": "Alice"}},
                                    {"author": {"display_name": "Bob"}},
                                ],
                                "abstract_inverted_index": {
                                    "A": [0],
                                    "lattice": [1],
                                    "FHE": [2],
                                    "paper": [3],
                                },
                            }
                        ]
                    }
                )
            raise AssertionError(url)

    monkeypatch.setattr("zotero_arxiv_daily.retriever.ccf_openalex_retriever.requests.Session", FakeSession)

    papers = CcfOpenAlexRetriever(config).retrieve_papers()
    assert len(papers) == 1
    assert papers[0].source == "ccf_openalex"
    assert papers[0].title == "[CCF A] Fully Homomorphic Encryption from Lattices"
    assert papers[0].authors == ["Alice", "Bob"]
    assert "A lattice FHE paper" in papers[0].abstract
