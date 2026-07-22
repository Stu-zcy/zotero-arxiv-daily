from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from omegaconf import OmegaConf

from .base import BaseReranker, register_reranker


def _plain(value: Any) -> Any:
    if OmegaConf.is_config(value):
        return OmegaConf.to_container(value, resolve=True)
    return value


def _json_object(content: str) -> dict:
    content = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        content = fenced.group(1)
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek reranker response must be a JSON object")
    return payload


@register_reranker("deepseek")
class DeepSeekReranker(BaseReranker):
    def __init__(self, config):
        super().__init__(config)
        self.client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)

    def _research_profile(self, corpus) -> str:
        reranker_config = self.config.reranker.get("deepseek") or {}
        max_papers = int(reranker_config.get("max_corpus_papers") or 100)
        abstract_chars = int(reranker_config.get("abstract_chars") or 500)
        recent = sorted(corpus, key=lambda item: item.added_date, reverse=True)[:max_papers]
        lines = []
        for index, paper in enumerate(recent):
            paths = ", ".join(paper.paths or [])
            abstract = (paper.abstract or "").replace("\n", " ")[:abstract_chars]
            lines.append(f"R{index}. {paper.title}\nCollections: {paths}\nAbstract: {abstract}")
        return "\n\n".join(lines) or "No Zotero reference papers are available."

    def _score_batch(self, candidates, offset: int, profile: str) -> dict[int, float]:
        candidate_text = "\n\n".join(
            f"ID {offset + index}: {paper.title}\nAbstract: {(paper.abstract or '')[:2000]}"
            for index, paper in enumerate(candidates)
        )
        prompt = (
            "Score each candidate paper's similarity to the user's research profile from 0 to 10. "
            "Prioritize topic and technical-method similarity; do not reward generic security terminology. "
            "Return every candidate exactly once as JSON with this schema: "
            '{"scores":[{"id":0,"score":8.5}]}. Return JSON only.\n\n'
            f"USER RESEARCH PROFILE (recent Zotero papers first):\n{profile}\n\n"
            f"CANDIDATES:\n{candidate_text}"
        )
        generation_kwargs = dict(_plain(self.config.llm.get("generation_kwargs") or {}))
        response = self.client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise academic paper relevance judge. Output valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            **generation_kwargs,
        )
        payload = _json_object(response.choices[0].message.content or "")
        scores: dict[int, float] = {}
        for item in payload.get("scores") or []:
            candidate_id = int(item["id"])
            score = float(item["score"])
            if candidate_id in scores or not 0 <= score <= 10:
                raise ValueError(f"Invalid DeepSeek score for candidate {candidate_id}: {score}")
            scores[candidate_id] = score
        return scores

    def rerank(self, candidates, corpus):
        if not candidates:
            return []
        reranker_config = self.config.reranker.get("deepseek") or {}
        batch_size = int(reranker_config.get("batch_size") or 20)
        profile = self._research_profile(corpus)
        scores: dict[int, float] = {}
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            scores.update(self._score_batch(batch, offset, profile))
        expected = set(range(len(candidates)))
        missing = sorted(expected - set(scores))
        extra = sorted(set(scores) - expected)
        if missing or extra:
            raise ValueError(f"DeepSeek reranker missing candidate scores={missing}, unexpected scores={extra}")
        for index, paper in enumerate(candidates):
            paper.score = scores[index]
        return sorted(candidates, key=lambda paper: paper.score, reverse=True)

    def get_similarity_score(self, s1, s2):
        raise NotImplementedError("DeepSeek reranker scores candidates directly")
