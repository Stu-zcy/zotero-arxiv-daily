import json

from omegaconf import open_dict

from zotero_arxiv_daily.retriever.ccf_crossref_retriever import CcfCrossrefRetriever, _keyword_in_text, _topic_matches, _venue_matches


def test_crossref_venue_matches_container_title():
    venue = {"简称": "JoC", "全称": "Journal of Cryptology"}
    assert _venue_matches({"container-title": ["Journal of Cryptology"]}, venue)
    assert _venue_matches({"short-container-title": ["JoC"]}, venue)
    assert not _venue_matches({"container-title": ["Lecture Notes in Computer Science"]}, venue)


def test_topic_matches_distinguish_user_profiles():
    grasp = "GRASP: Accelerating Hash-Based PQC Performance on GPU Parallel Architecture".lower()
    chenyang_topics = [
        {
            "name": "pqc",
            "keywords": ["pqc", "hash-based", "gpu", "parallel architecture"],
        }
    ]
    liruoyi_topics = [
        {
            "name": "fhe",
            "keywords": ["fully homomorphic", "homomorphic encryption", "fhe", "bootstrapping"],
        },
        {
            "name": "lattice-foundations",
            "keywords": ["lattice", "lwe", "rlwe", "sis"],
        },
    ]
    assert _topic_matches(grasp, chenyang_topics, min_score=2) == (True, ["pqc"])
    assert _topic_matches(grasp, liruoyi_topics, min_score=2) == (False, [])


def test_short_keywords_require_word_boundaries():
    searchable = "performance prediction of concurrent dnn training tasks in gpu spatial sharing environments"
    assert not _keyword_in_text("sis", searchable)
    assert _keyword_in_text("gpu", searchable)
    assert _keyword_in_text("spatial sharing", searchable)


def test_ccf_crossref_retriever_filters_dates_container_keywords_and_doi(config, tmp_path, monkeypatch):
    ccf_path = tmp_path / "ccf.json"
    ccf_path.write_text(
        json.dumps(
            [
                {
                    "类型": "期刊",
                    "领域": "网络与信息安全",
                    "等级": "A",
                    "简称": "JoC",
                    "全称": "Journal of Cryptology",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with open_dict(config):
        config.source.ccf_crossref.ccf_json_path = str(ccf_path)
        config.source.ccf_crossref.types = ["期刊"]
        config.source.ccf_crossref.fields = ["网络与信息安全"]
        config.source.ccf_crossref.ranks = ["A"]
        config.source.ccf_crossref.start_date = "2026-05-01"
        config.source.ccf_crossref.end_date = "2026-07-31"
        config.source.ccf_crossref.keyword_required = True
        config.source.ccf_crossref.query_mode = "title"
        config.source.ccf_crossref.search_queries = ["homomorphic encryption"]
        config.source.ccf_crossref.keywords = ["homomorphic encryption", "lattice"]
        config.source.ccf_crossref.request_delay = 0
        config.source.ccf_crossref.proxy = "http://127.0.0.1:7890"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {
                            "title": ["Fast Homomorphic Linear Algebra"],
                            "container-title": ["Journal of Cryptology"],
                            "short-container-title": ["JoC"],
                            "abstract": "<jats:p>A homomorphic encryption paper from lattices.</jats:p>",
                            "DOI": "10.1000/joc.1",
                            "URL": "https://doi.org/10.1000/joc.1",
                            "published-online": {"date-parts": [[2026, 6, 1]]},
                            "author": [{"given": "Alice", "family": "A"}],
                        },
                        {
                            "title": ["Fast Homomorphic Linear Algebra"],
                            "container-title": ["Journal of Cryptology"],
                            "abstract": "duplicate",
                            "DOI": "10.1000/joc.1",
                        },
                        {
                            "title": ["Unrelated Systems Paper"],
                            "container-title": ["Journal of Cryptology"],
                            "abstract": "databases",
                            "DOI": "10.1000/joc.2",
                        },
                        {
                            "title": ["Homomorphic Encryption in Another Venue"],
                            "container-title": ["Not Journal of Cryptology"],
                            "abstract": "homomorphic encryption",
                            "DOI": "10.1000/joc.3",
                        },
                    ]
                }
            }

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
            assert params["query.container-title"] == "Journal of Cryptology"
            assert params["query.title"] == "homomorphic encryption"
            assert "from-pub-date:2026-05-01" in params["filter"]
            assert "until-pub-date:2026-07-31" in params["filter"]
            assert "type:journal-article" in params["filter"]
            return FakeResponse()

    monkeypatch.setattr("zotero_arxiv_daily.retriever.ccf_crossref_retriever.requests.Session", FakeSession)

    papers = CcfCrossrefRetriever(config).retrieve_papers()
    assert len(papers) == 1
    assert papers[0].source == "ccf_crossref"
    assert papers[0].title == "[CCF A] Fast Homomorphic Linear Algebra"
    assert papers[0].authors == ["Alice A"]
    assert "source Crossref" in papers[0].abstract
