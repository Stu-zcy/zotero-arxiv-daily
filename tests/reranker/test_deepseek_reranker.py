import json
from datetime import datetime
from types import SimpleNamespace

from omegaconf import OmegaConf

from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.reranker.deepseek import DeepSeekReranker


def _paper(title: str, abstract: str) -> Paper:
    return Paper(source="arxiv", title=title, authors=[], abstract=abstract, url=f"https://example.com/{title}")


def test_deepseek_reranker_scores_and_sorts_candidates(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = json.dumps(
            {
                "scores": [
                    {"id": 0, "score": 2.0},
                    {"id": 1, "score": 9.5},
                ]
            }
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("zotero_arxiv_daily.reranker.deepseek.OpenAI", lambda **kwargs: client)
    config = OmegaConf.create(
        {
            "llm": {
                "api": {"key": "sk-test", "base_url": "https://api.deepseek.com"},
                "generation_kwargs": {"model": "deepseek-v4-pro", "reasoning_effort": "high"},
            },
            "reranker": {"deepseek": {"batch_size": 20, "max_corpus_papers": 100, "abstract_chars": 500}},
        }
    )
    candidates = [
        _paper("Generic Security", "A general security paper."),
        _paper("Fast CKKS", "An efficient homomorphic encryption construction."),
    ]
    corpus = [
        CorpusPaper(
            title="FHE Reference",
            abstract="Fully homomorphic encryption and bootstrapping.",
            added_date=datetime(2026, 7, 1),
            paths=["Library/FHE"],
        )
    ]

    ranked = DeepSeekReranker(config).rerank(candidates, corpus)

    assert [paper.title for paper in ranked] == ["Fast CKKS", "Generic Security"]
    assert [paper.score for paper in ranked] == [9.5, 2.0]
    assert calls[0]["model"] == "deepseek-v4-pro"
    assert "FHE Reference" in calls[0]["messages"][1]["content"]


def test_deepseek_reranker_rejects_incomplete_scores(monkeypatch):
    content = json.dumps({"scores": [{"id": 0, "score": 5}]})
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                )
            )
        )
    )
    monkeypatch.setattr("zotero_arxiv_daily.reranker.deepseek.OpenAI", lambda **kwargs: client)
    config = OmegaConf.create(
        {
            "llm": {
                "api": {"key": "sk-test", "base_url": "https://api.deepseek.com"},
                "generation_kwargs": {"model": "deepseek-v4-pro"},
            },
            "reranker": {"deepseek": {"batch_size": 20}},
        }
    )

    try:
        DeepSeekReranker(config).rerank([_paper("A", "a"), _paper("B", "b")], [])
    except ValueError as exc:
        assert "missing candidate scores" in str(exc)
    else:
        raise AssertionError("Incomplete DeepSeek scores must fail the run")
