"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser
from omegaconf import open_dict

from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout
import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    paper_ids = [e.id.removeprefix("oai:arXiv.org:") for e in new_entries]

    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in new_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return iter(fake_results)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    # Skip file downloads in convert_to_paper
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert set(p.title for p in papers) == set(e.title for e in new_entries)


def test_arxiv_retriever_accepts_feed_without_attribute_title(config, mock_feedparser, monkeypatch):
    mock_feedparser.feed = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        def results(self, search):
            return iter([])

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    retriever = ArxivRetriever(config)
    assert retriever._retrieve_raw_papers() == []


def test_arxiv_profile_filter_keeps_crypto_and_drops_generic_ai(config):
    with open_dict(config):
        config.source.arxiv.keyword_required = True
        config.source.arxiv.min_topic_score = 2
        config.source.arxiv.topics = [
            {
                "name": "fhe",
                "keywords": ["fully homomorphic", "homomorphic encryption", "fhe", "bootstrapping"],
            },
            {
                "name": "pqc",
                "keywords": ["pqc", "post-quantum", "hash-based", "gpu"],
            },
        ]

    retriever = ArxivRetriever(config)
    raw = [
        SimpleNamespace(
            title="Securing LLM-Agent Long-Term Memory Against Poisoning",
            summary="This paper studies AI agents and prompt injection.",
            primary_category="cs.CR",
            categories=["cs.CR", "cs.AI"],
        ),
        SimpleNamespace(
            title="ComputeFHE: A Privacy-Preserving General-Purpose Computation Library",
            summary="We use fully homomorphic encryption and bootstrapping for private computation.",
            primary_category="cs.CR",
            categories=["cs.CR"],
        ),
        SimpleNamespace(
            title="GRASP: Accelerating Hash-Based PQC Performance on GPU Parallel Architecture",
            summary="This paper accelerates post-quantum signatures.",
            primary_category="cs.CR",
            categories=["cs.CR"],
        ),
    ]

    filtered = retriever._filter_by_profile(raw)

    assert [paper.title for paper in filtered] == [
        "ComputeFHE: A Privacy-Preserving General-Purpose Computation Library",
        "GRASP: Accelerating Hash-Based PQC Performance on GPU Parallel Architecture",
    ]


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]
