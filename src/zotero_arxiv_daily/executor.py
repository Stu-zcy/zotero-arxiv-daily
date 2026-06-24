from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig, OmegaConf
from pathlib import Path
import json
import re
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
import random
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from tqdm import tqdm


def _as_plain_list(value):
    if value is None:
        return []
    if OmegaConf.is_config(value):
        return list(OmegaConf.to_container(value, resolve=True))
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _normalize_topic_text(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _keyword_hit(keyword: str, searchable: str) -> bool:
    keyword = _normalize_topic_text(keyword)
    if not keyword:
        return False
    if len(keyword) <= 3:
        return re.search(rf"\b{re.escape(keyword)}\b", searchable) is not None
    return keyword in searchable


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)

    def _paper_key(self, paper) -> str:
        url = (paper.url or paper.pdf_url or "").strip().lower()
        if url:
            return url
        return paper.title.strip().lower()

    def _seen_state_path(self) -> Path | None:
        state_cfg = self.config.get("state")
        if not state_cfg or not state_cfg.get("enabled") or state_cfg.get("ignore_seen"):
            return None
        path = Path(state_cfg.get("path") or "state/default/seen.json")
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _load_seen(self) -> set[str]:
        path = self._seen_state_path()
        if path is None or not path.exists():
            return set()
        try:
            with path.open(encoding="utf-8") as file:
                payload = json.load(file)
            if isinstance(payload, dict):
                return set(str(key) for key in payload.get("papers", []))
            if isinstance(payload, list):
                return set(str(key) for key in payload)
        except Exception as exc:
            logger.warning(f"Failed to load seen state {path}: {exc}")
        return set()

    def _save_seen(self, seen: set[str]) -> None:
        path = self._seen_state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump({"papers": sorted(seen)}, file, ensure_ascii=False, indent=2)

    def _filter_seen(self, papers):
        path = self._seen_state_path()
        if path is None:
            return papers, set()
        seen = self._load_seen()
        fresh = []
        skipped = 0
        for paper in papers:
            key = self._paper_key(paper)
            if key in seen:
                skipped += 1
                continue
            fresh.append(paper)
        if skipped:
            logger.info(f"Skipped {skipped} papers already recorded in {path}")
        return fresh, seen

    def _mark_seen(self, papers, seen: set[str]) -> None:
        if self._seen_state_path() is None:
            return
        for paper in papers:
            key = self._paper_key(paper)
            if key:
                seen.add(key)
        self._save_seen(seen)

    def _profile_topics(self) -> list[dict]:
        for source in self.config.executor.source:
            source_config = getattr(self.config.source, source, None)
            if source_config is not None and source_config.get("topics"):
                return [
                    topic
                    for topic in _as_plain_list(source_config.get("topics"))
                    if isinstance(topic, dict) and topic.get("keywords")
                ]
        return []

    def _annotate_and_sort(self, papers):
        topics = self._profile_topics()
        for paper in papers:
            searchable = _normalize_topic_text(f"{paper.title} {paper.abstract} {paper.venue or ''} {paper.venue_abbr or ''}")
            labels = []
            for topic in topics:
                name = str(topic.get("name") or "matched-topic")
                keywords = [str(keyword) for keyword in _as_plain_list(topic.get("keywords")) if keyword]
                if any(_keyword_hit(keyword, searchable) for keyword in keywords):
                    labels.append(name)
            paper.topic_labels = labels or ["other"]

        def sort_key(paper):
            published = paper.published_date or ""
            return (paper.topic_labels[0] if paper.topic_labels else "other", -int(published.replace("-", "")[:8] or "0"), -(paper.score or 0))

        return sorted(papers, key=sort_key)

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            try:
                papers = retriever.retrieve_papers()
            except Exception as exc:
                logger.warning(f"Retriever {source} failed; continuing with remaining sources: {exc}")
                continue
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        all_papers, seen = self._filter_seen(all_papers)
        logger.info(f"Total {len(all_papers)} papers after seen-state filtering")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
            reranked_papers = self._annotate_and_sort(reranked_papers)
            logger.info("Generating TLDR and affiliations...")
            for p in tqdm(reranked_papers):
                p.generate_tldr(self.openai_client, self.config.llm)
                p.generate_affiliations(self.openai_client, self.config.llm)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        logger.info("Sending email...")
        email_content = render_email(reranked_papers)
        send_email(self.config, email_content)
        self._mark_seen(reranked_papers, seen)
        logger.info("Email sent successfully")
