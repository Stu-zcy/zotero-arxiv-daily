import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from loguru import logger
from omegaconf import ListConfig
from requests import HTTPError

from ..protocol import Paper
from .base import BaseRetriever, register_retriever


@dataclass
class CrossrefPaper:
    ccf_type: str
    ccf_field: str
    ccf_rank: str
    venue_abbr: str
    venue_name: str
    title: str
    authors: list[str]
    publication_date: str
    doi: str
    url: str
    abstract: str
    container_titles: list[str]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, ListConfig)):
        return list(value)
    return [value]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_window(config) -> tuple[date, date]:
    start = _parse_date(config.get("start_date"))
    end = _parse_date(config.get("end_date"))
    if start and end:
        return start, end
    if start and not end:
        return start, date.today()
    if end and not start:
        return end, end
    lookback_days = int(config.get("lookback_days") or 7)
    end = date.today()
    return end - timedelta(days=lookback_days), end


def _normalize_text(value: str) -> str:
    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_markup(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _keyword_in_text(keyword: str, searchable: str) -> bool:
    normalized_keyword = _normalize_text(keyword)
    if not normalized_keyword:
        return False
    return re.search(rf"\b{re.escape(normalized_keyword)}\b", searchable) is not None


def _title_from_item(item: dict[str, Any]) -> str:
    titles = item.get("title") or []
    return str(titles[0]) if titles else ""


def _date_from_parts(parts: dict[str, Any] | None) -> str:
    if not parts:
        return ""
    date_parts = parts.get("date-parts") or []
    if not date_parts:
        return ""
    nums = [str(n) for n in date_parts[0]]
    return "-".join(nums)


def _author_names(item: dict[str, Any]) -> list[str]:
    authors = []
    for author in item.get("author") or []:
        name = " ".join(
            part
            for part in [author.get("given"), author.get("family")]
            if part
        ).strip()
        if name:
            authors.append(name)
    return authors


def _crossref_type_for_venue(venue: dict[str, str]) -> str | None:
    venue_type = venue.get("类型")
    if venue_type == "期刊":
        return "journal-article"
    if venue_type == "会议":
        return "proceedings-article"
    return None


def _venue_matches(item: dict[str, Any], venue: dict[str, str]) -> bool:
    container_titles = [_normalize_text(str(t)) for t in item.get("container-title") or [] if t]
    short_titles = [_normalize_text(str(t)) for t in item.get("short-container-title") or [] if t]
    expected = [_normalize_text(venue.get("全称") or ""), _normalize_text(venue.get("简称") or "")]
    expected = [value for value in expected if value]
    for container in container_titles + short_titles:
        for target in expected:
            if container == target:
                return True
            normalized_container = re.sub(r"^(proceedings of|proceedings)\s+", "", container).strip()
            if normalized_container == target:
                return True
    return False


def _topic_matches(searchable: str, topics: list[dict[str, Any]], min_score: int) -> tuple[bool, list[str]]:
    matched_topics = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        keywords = [str(k).lower() for k in _as_list(topic.get("keywords")) if k]
        hits = {keyword for keyword in keywords if _keyword_in_text(keyword, searchable)}
        if len(hits) >= min_score:
            matched_topics.append(str(topic.get("name") or "topic"))
    return bool(matched_topics), matched_topics


@register_retriever("ccf_crossref")
class CcfCrossrefRetriever(BaseRetriever):
    base_url = "https://api.crossref.org/works"

    def __init__(self, config):
        super().__init__(config)
        self.start_date, self.end_date = _date_window(self.retriever_config)

    def _load_venues(self) -> list[dict[str, str]]:
        path = Path(self.retriever_config.ccf_json_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        with path.open(encoding="utf-8") as file:
            venues = json.load(file)

        types = set(_as_list(self.retriever_config.get("types")))
        fields = set(_as_list(self.retriever_config.get("fields")))
        ranks = set(_as_list(self.retriever_config.get("ranks")))
        selected = [
            venue
            for venue in venues
            if (not types or venue.get("类型") in types)
            and (not fields or venue.get("领域") in fields)
            and (not ranks or venue.get("等级") in ranks)
        ]
        max_venues = self.retriever_config.get("max_venues")
        if self.config.executor.debug and not max_venues:
            max_venues = 5
        if max_venues:
            selected = selected[: int(max_venues)]
        logger.info(f"Selected {len(selected)} CCF Crossref venues for {self.start_date} to {self.end_date}")
        return selected

    def _session(self) -> requests.Session:
        session = requests.Session()
        user_agent = "zotero-arxiv-daily/1.0"
        mailto = self.retriever_config.get("mailto")
        if mailto:
            user_agent += f" (mailto:{mailto})"
        session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        proxy = self.retriever_config.get("proxy")
        if proxy:
            session.proxies.update({"http": str(proxy), "https": str(proxy)})
            logger.info(f"Using Crossref proxy: {proxy}")
        return session

    def _get_json(self, session: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
        timeout_seconds = int(self.retriever_config.get("request_timeout") or 30)
        timeout = (10, timeout_seconds)
        retries = int(self.retriever_config.get("request_retries") or 3)
        delay = float(self.retriever_config.get("request_delay") or 1.0)
        waits = [float(v) for v in _as_list(self.retriever_config.get("rate_limit_waits"))] or [30.0, 60.0, 120.0]
        last_error = None
        for attempt in range(retries):
            try:
                response = session.get(self.base_url, params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and attempt < retries - 1:
                    retry_after = exc.response.headers.get("Retry-After") if exc.response is not None else None
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else waits[min(attempt, len(waits) - 1)]
                    logger.warning(f"Crossref rate limited; retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt == retries - 1:
                    raise
                time.sleep(delay * (attempt + 1))
        raise RuntimeError(last_error)

    def _query_venue(self, session: requests.Session, venue: dict[str, str], search_term: str | None) -> list[dict[str, Any]]:
        filters = [
            f"from-pub-date:{self.start_date.isoformat()}",
            f"until-pub-date:{self.end_date.isoformat()}",
        ]
        crossref_type = _crossref_type_for_venue(venue)
        if crossref_type:
            filters.append(f"type:{crossref_type}")
        params: dict[str, Any] = {
            "query.container-title": venue.get("全称") or venue.get("简称") or "",
            "filter": ",".join(filters),
            "rows": int(self.retriever_config.get("rows") or 20),
            "sort": "published",
            "order": "desc",
        }
        mailto = self.retriever_config.get("mailto")
        if mailto:
            params["mailto"] = mailto
        if search_term:
            params["query.title"] = search_term
        data = self._get_json(session, params)
        return data.get("message", {}).get("items") or []

    def _query_bibliographic(self, session: requests.Session, search_term: str) -> list[dict[str, Any]]:
        filters = [
            f"from-pub-date:{self.start_date.isoformat()}",
            f"until-pub-date:{self.end_date.isoformat()}",
        ]
        params: dict[str, Any] = {
            "query.bibliographic": search_term,
            "filter": ",".join(filters),
            "rows": int(self.retriever_config.get("supplemental_rows") or 50),
            "sort": "relevance",
            "order": "desc",
        }
        mailto = self.retriever_config.get("mailto")
        if mailto:
            params["mailto"] = mailto
        data = self._get_json(session, params)
        return data.get("message", {}).get("items") or []

    def _retrieve_raw_papers(self) -> list[CrossrefPaper]:
        venues = self._load_venues()
        session = self._session()
        delay = float(self.retriever_config.get("request_delay") or 1.0)
        keywords = [str(k).lower() for k in _as_list(self.retriever_config.get("keywords")) if k]
        topics = [
            topic
            for topic in _as_list(self.retriever_config.get("topics"))
            if isinstance(topic, dict) and topic.get("keywords")
        ]
        min_topic_score = int(self.retriever_config.get("min_topic_score") or 2)
        search_queries = [str(k) for k in _as_list(self.retriever_config.get("search_queries")) if k]
        keyword_required = bool(self.retriever_config.get("keyword_required") or False)
        max_papers = self.retriever_config.get("max_papers")
        papers: list[CrossrefPaper] = []
        seen: set[str] = set()
        raw_count = 0
        container_count = 0
        last_progress_bucket = 0

        def append_item(item: dict[str, Any], venue: dict[str, str]) -> bool:
            nonlocal container_count
            if not _venue_matches(item, venue):
                return False
            container_count += 1
            title = _title_from_item(item)
            abstract = _strip_markup(item.get("abstract"))
            searchable = _normalize_text(f"{title} {abstract} {' '.join(item.get('container-title') or [])}")
            if keyword_required and topics:
                matched, matched_topics = _topic_matches(searchable, topics, min_topic_score)
                if not matched:
                    return False
            elif keyword_required and not any(_keyword_in_text(keyword, searchable) for keyword in keywords):
                return False
            doi = (item.get("DOI") or "").lower()
            key = doi or _normalize_text(title)
            if not key or key in seen:
                return False
            seen.add(key)
            papers.append(
                CrossrefPaper(
                    ccf_type=venue.get("类型", ""),
                    ccf_field=venue.get("领域", ""),
                    ccf_rank=venue.get("等级", ""),
                    venue_abbr=venue.get("简称", ""),
                    venue_name=venue.get("全称", ""),
                    title=title,
                    authors=_author_names(item),
                    publication_date=_date_from_parts(item.get("published-print") or item.get("published-online") or item.get("published")),
                    doi=item.get("DOI") or "",
                    url=item.get("URL") or (f"https://doi.org/{item.get('DOI')}" if item.get("DOI") else ""),
                    abstract=abstract,
                    container_titles=[str(t) for t in item.get("container-title") or []],
                )
            )
            return True

        for venue in venues:
            query_mode = str(self.retriever_config.get("query_mode") or "venue")
            search_terms = search_queries if query_mode == "title" and keyword_required and search_queries else [None]
            for search_term in search_terms:
                try:
                    items = self._query_venue(session, venue, search_term)
                except Exception as exc:
                    logger.warning(f"Failed to retrieve Crossref venue {venue.get('简称') or venue.get('全称')}: {exc}")
                    continue
                raw_count += len(items)
                for item in items:
                    append_item(item, venue)
                    if max_papers and len(papers) >= int(max_papers):
                        logger.info(
                            f"Crossref raw={raw_count}, container_matched={container_count}, keyword_matched={len(papers)}"
                        )
                        logger.info(f"Reached ccf_crossref.max_papers={max_papers}")
                        return papers
                if delay > 0:
                    time.sleep(delay)
            progress_bucket = len(papers) // 10
            if progress_bucket > last_progress_bucket:
                last_progress_bucket = progress_bucket
                logger.info(f"Crossref progress: matched {len(papers)} papers so far")

        if self.retriever_config.get("supplemental_bibliographic_search") and search_queries:
            logger.info(f"Running {len(search_queries)} supplemental Crossref bibliographic searches")
            for search_term in search_queries:
                try:
                    items = self._query_bibliographic(session, search_term)
                except Exception as exc:
                    logger.warning(f"Failed supplemental Crossref query {search_term}: {exc}")
                    continue
                raw_count += len(items)
                for item in items:
                    for venue in venues:
                        if append_item(item, venue):
                            break
                    if max_papers and len(papers) >= int(max_papers):
                        logger.info(
                            f"Crossref raw={raw_count}, container_matched={container_count}, keyword_matched={len(papers)}"
                        )
                        logger.info(f"Reached ccf_crossref.max_papers={max_papers}")
                        return papers
                if delay > 0:
                    time.sleep(delay)

        logger.info(f"Crossref raw={raw_count}, container_matched={container_count}, keyword_matched={len(papers)}")
        return papers

    def convert_to_paper(self, raw_paper: CrossrefPaper) -> Paper:
        meta = (
            f"CCF {raw_paper.ccf_rank} {raw_paper.ccf_type}; "
            f"{raw_paper.ccf_field}; {raw_paper.venue_abbr or raw_paper.venue_name}; "
            f"published {raw_paper.publication_date}; source Crossref."
        )
        abstract = raw_paper.abstract or raw_paper.title
        return Paper(
            source=self.name,
            title=f"[CCF {raw_paper.ccf_rank}] {raw_paper.title}",
            authors=raw_paper.authors or ["Unknown authors"],
            abstract=f"{abstract}\n\n{meta}",
            url=raw_paper.url,
            pdf_url=raw_paper.url,
            full_text=None,
            published_date=raw_paper.publication_date or None,
            venue=raw_paper.venue_name or (raw_paper.container_titles[0] if raw_paper.container_titles else None),
            venue_abbr=raw_paper.venue_abbr or None,
            source_label="Crossref",
        )
