import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from requests import HTTPError
from loguru import logger
from omegaconf import ListConfig

from ..protocol import Paper
from .base import BaseRetriever, register_retriever


@dataclass
class OpenAlexPaper:
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
    pdf_url: str | None
    abstract: str
    openalex_id: str
    source_id: str


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


def _restore_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted_index.items():
        for index in indexes:
            positions[int(index)] = word
    return " ".join(positions[index] for index in sorted(positions))


def _openalex_short_id(openalex_id: str) -> str:
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


@register_retriever("ccf_openalex")
class CcfOpenAlexRetriever(BaseRetriever):
    base_url = "https://api.openalex.org"

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

        logger.info(
            f"Selected {len(selected)} CCF OpenAlex venues for {self.start_date} to {self.end_date}"
        )
        return selected

    def _source_cache_path(self) -> Path | None:
        cache_path = self.retriever_config.get("source_cache_path")
        if not cache_path:
            return None
        path = Path(cache_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _load_source_cache(self) -> dict[str, str]:
        path = self._source_cache_path()
        if path is None or not path.exists():
            return {}
        try:
            with path.open(encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:
            logger.warning(f"Failed to load OpenAlex source cache {path}: {exc}")
            return {}

    def _save_source_cache(self, cache: dict[str, str]) -> None:
        path = self._source_cache_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(cache, file, ensure_ascii=False, indent=2, sort_keys=True)

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "zotero-arxiv-daily/1.0 (mailto:example@example.com)",
                "Accept": "application/json",
            }
        )
        email = self.retriever_config.get("mailto")
        if email:
            session.headers["User-Agent"] = f"zotero-arxiv-daily/1.0 (mailto:{email})"
        proxy = self.retriever_config.get("proxy")
        if proxy:
            session.proxies.update({"http": str(proxy), "https": str(proxy)})
            logger.info(f"Using OpenAlex proxy: {proxy}")
        return session

    def _get_json(self, session: requests.Session, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        timeout = int(self.retriever_config.get("request_timeout") or 30)
        retries = int(self.retriever_config.get("request_retries") or 3)
        delay = float(self.retriever_config.get("request_delay") or 1.0)
        rate_limit_wait = float(self.retriever_config.get("rate_limit_wait") or 60.0)
        max_rate_limit_wait = float(self.retriever_config.get("max_rate_limit_wait") or 900.0)
        last_error = None
        for attempt in range(retries):
            try:
                response = session.get(f"{self.base_url}{endpoint}", params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and attempt < retries - 1:
                    retry_after = exc.response.headers.get("Retry-After") if exc.response is not None else None
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else rate_limit_wait * (attempt + 1)
                    wait = min(wait, max_rate_limit_wait)
                    logger.warning(f"OpenAlex rate limited; retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt == retries - 1:
                    raise
                time.sleep(delay * (attempt + 1))
        raise RuntimeError(last_error)

    def _resolve_source_id(
        self,
        session: requests.Session,
        venue: dict[str, str],
        source_cache: dict[str, str],
    ) -> str | None:
        query = venue.get("全称") or venue.get("简称")
        if not query:
            return None
        cache_keys = [venue.get("全称", ""), venue.get("简称", "")]
        for key in cache_keys:
            if key and source_cache.get(key):
                return source_cache[key]
        data = self._get_json(session, "/sources", {"search": query, "per-page": 5})
        results = data.get("results") or []
        if not results:
            return None

        venue_name = (venue.get("全称") or "").lower()
        venue_abbr = (venue.get("简称") or "").lower()
        best = results[0]
        for result in results:
            display_name = (result.get("display_name") or "").lower()
            if display_name == venue_name or (venue_abbr and display_name == venue_abbr):
                best = result
                break
        source_id = best.get("id")
        if source_id:
            for key in cache_keys:
                if key:
                    source_cache[key] = source_id
            self._save_source_cache(source_cache)
            logger.debug(f"Mapped CCF venue {query} to OpenAlex source {best.get('display_name')} ({source_id})")
        return source_id

    def _retrieve_raw_papers(self) -> list[OpenAlexPaper]:
        venues = self._load_venues()
        session = self._session()
        source_cache = self._load_source_cache()
        per_page = int(self.retriever_config.get("per_page") or 25)
        delay = float(self.retriever_config.get("request_delay") or 1.0)
        keywords = [str(k).lower() for k in _as_list(self.retriever_config.get("keywords")) if k]
        search_queries = [
            str(k)
            for k in _as_list(self.retriever_config.get("search_queries"))
            if k
        ]
        keyword_required = bool(self.retriever_config.get("keyword_required") or False)
        max_papers = self.retriever_config.get("max_papers")
        papers: list[OpenAlexPaper] = []
        seen: set[str] = set()

        for venue in venues:
            try:
                source_id = self._resolve_source_id(session, venue, source_cache)
                if not source_id:
                    logger.warning(f"OpenAlex source not found for {venue.get('简称') or venue.get('全称')}")
                    continue
                source_short_id = _openalex_short_id(source_id)
                filters = ",".join(
                    [
                        f"primary_location.source.id:{source_short_id}",
                        f"from_publication_date:{self.start_date.isoformat()}",
                        f"to_publication_date:{self.end_date.isoformat()}",
                    ]
                )
                search_terms = search_queries if keyword_required and search_queries else [None]
                results = []
                for search_term in search_terms:
                    params = {
                        "filter": filters,
                        "sort": "publication_date:desc",
                        "per-page": per_page,
                    }
                    if search_term:
                        params["search"] = search_term
                    data = self._get_json(session, "/works", params)
                    results.extend(data.get("results") or [])
                    if delay > 0 and search_term:
                        time.sleep(delay)
            except Exception as exc:
                logger.warning(f"Failed to retrieve OpenAlex venue {venue.get('简称') or venue.get('全称')}: {exc}")
                continue

            for work in results:
                openalex_id = work.get("id") or ""
                if not openalex_id or openalex_id in seen:
                    continue
                title = work.get("display_name") or ""
                abstract = _restore_abstract(work.get("abstract_inverted_index"))
                searchable = f"{title} {abstract} {venue.get('简称', '')} {venue.get('全称', '')} {venue.get('领域', '')}".lower()
                if keyword_required and not any(keyword in searchable for keyword in keywords):
                    continue
                location = work.get("primary_location") or {}
                authors = [
                    authorship.get("author", {}).get("display_name")
                    for authorship in (work.get("authorships") or [])
                    if authorship.get("author", {}).get("display_name")
                ]
                seen.add(openalex_id)
                papers.append(
                    OpenAlexPaper(
                        ccf_type=venue.get("类型", ""),
                        ccf_field=venue.get("领域", ""),
                        ccf_rank=venue.get("等级", ""),
                        venue_abbr=venue.get("简称", ""),
                        venue_name=venue.get("全称", ""),
                        title=title,
                        authors=authors,
                        publication_date=work.get("publication_date") or "",
                        doi=work.get("doi") or "",
                        url=location.get("landing_page_url") or work.get("doi") or openalex_id,
                        pdf_url=location.get("pdf_url"),
                        abstract=abstract,
                        openalex_id=openalex_id,
                        source_id=source_id,
                    )
                )
                if max_papers and len(papers) >= int(max_papers):
                    logger.info(f"Reached ccf_openalex.max_papers={max_papers}")
                    return papers
            if delay > 0:
                time.sleep(delay)

        return papers

    def convert_to_paper(self, raw_paper: OpenAlexPaper) -> Paper:
        meta = (
            f"CCF {raw_paper.ccf_rank} {raw_paper.ccf_type}; "
            f"{raw_paper.ccf_field}; {raw_paper.venue_abbr or raw_paper.venue_name}; "
            f"published {raw_paper.publication_date}; source OpenAlex."
        )
        abstract = raw_paper.abstract or raw_paper.title
        return Paper(
            source=self.name,
            title=f"[CCF {raw_paper.ccf_rank}] {raw_paper.title}",
            authors=raw_paper.authors or ["Unknown authors"],
            abstract=f"{abstract}\n\n{meta}",
            url=raw_paper.url,
            pdf_url=raw_paper.pdf_url or raw_paper.url,
            full_text=None,
            published_date=raw_paper.publication_date or None,
            venue=raw_paper.venue_name or None,
            venue_abbr=raw_paper.venue_abbr or None,
            source_label="OpenAlex",
        )
