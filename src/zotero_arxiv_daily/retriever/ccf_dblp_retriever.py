import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from loguru import logger
from omegaconf import ListConfig

from ..protocol import Paper
from .base import BaseRetriever, register_retriever


ENTRY_TAGS = {"article", "inproceedings", "proceedings", "book", "incollection"}


@dataclass
class DblpPaper:
    ccf_type: str
    ccf_field: str
    ccf_rank: str
    venue_abbr: str
    venue_name: str
    title: str
    authors: list[str]
    year: str
    mdate: str
    url: str
    doi: str
    source_url: str


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


def _normalize_dblp_venue_xml_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    path = parsed.path
    if "/db/" not in path:
        return None
    path = re.sub(r"/index\.html?$", "/", path)
    if not path.endswith("/"):
        path += "/"
    return f"https://dblp.org{path}index.xml"


def _text(entry: ET.Element, name: str) -> str:
    element = entry.find(name)
    if element is None or element.text is None:
        return ""
    return " ".join(element.itertext()).strip()


def _entry_date_matches(entry: ET.Element, start: date, end: date) -> bool:
    mdate = entry.attrib.get("mdate")
    if mdate:
        updated = _parse_date(mdate)
        return updated is not None and start <= updated <= end

    year_text = _text(entry, "year")
    if year_text.isdigit():
        year = int(year_text)
        return start.year <= year <= end.year
    return False


def _parse_dblp_xml(
    xml_text: str,
    *,
    venue: dict[str, str],
    source_url: str,
    start: date,
    end: date,
    keywords: list[str],
    keyword_required: bool,
) -> list[DblpPaper]:
    root = ET.fromstring(xml_text)
    papers: list[DblpPaper] = []
    normalized_keywords = [k.lower() for k in keywords if k]

    for entry in root.iter():
        tag = entry.tag.rsplit("}", 1)[-1]
        if tag not in ENTRY_TAGS or not _entry_date_matches(entry, start, end):
            continue

        title = _text(entry, "title").rstrip(".")
        if not title:
            continue

        searchable = f"{title} {venue.get('简称', '')} {venue.get('全称', '')} {venue.get('领域', '')}".lower()
        keyword_hits = [k for k in normalized_keywords if k in searchable]
        if keyword_required and not keyword_hits:
            continue

        url = _text(entry, "ee") or _text(entry, "url")
        doi = _text(entry, "doi")
        papers.append(
            DblpPaper(
                ccf_type=venue.get("类型", ""),
                ccf_field=venue.get("领域", ""),
                ccf_rank=venue.get("等级", ""),
                venue_abbr=venue.get("简称", ""),
                venue_name=venue.get("全称", ""),
                title=title,
                authors=[a.text.strip() for a in entry.findall("author") if a.text],
                year=_text(entry, "year"),
                mdate=entry.attrib.get("mdate", ""),
                url=url,
                doi=doi,
                source_url=source_url,
            )
        )

    return papers


@register_retriever("ccf_dblp")
class CcfDblpRetriever(BaseRetriever):
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
            and _normalize_dblp_venue_xml_url(venue.get("网址", ""))
        ]

        max_venues = self.retriever_config.get("max_venues")
        if self.config.executor.debug and not max_venues:
            max_venues = 5
        if max_venues:
            selected = selected[: int(max_venues)]

        logger.info(
            f"Selected {len(selected)} CCF DBLP venues for {self.start_date} to {self.end_date}"
        )
        return selected

    def _retrieve_raw_papers(self) -> list[DblpPaper]:
        venues = self._load_venues()
        timeout = int(self.retriever_config.get("request_timeout") or 30)
        retries = int(self.retriever_config.get("request_retries") or 3)
        delay = float(self.retriever_config.get("request_delay") or 1.0)
        keywords = [str(k) for k in _as_list(self.retriever_config.get("keywords"))]
        keyword_required = bool(self.retriever_config.get("keyword_required") or False)
        max_papers = self.retriever_config.get("max_papers")
        proxy = self.retriever_config.get("proxy")
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "zotero-arxiv-daily/1.0 (CCF DBLP monitor)",
                "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            }
        )
        if proxy:
            session.proxies.update({"http": str(proxy), "https": str(proxy)})
            logger.info(f"Using DBLP proxy: {proxy}")
        papers: list[DblpPaper] = []
        seen: set[tuple[str, str]] = set()

        for venue in venues:
            xml_url = _normalize_dblp_venue_xml_url(venue.get("网址", ""))
            if not xml_url:
                continue
            last_error = None
            try:
                for attempt in range(retries):
                    try:
                        response = session.get(xml_url, timeout=timeout)
                        response.raise_for_status()
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt == retries - 1:
                            raise
                        time.sleep(delay * (attempt + 1))
                venue_papers = _parse_dblp_xml(
                    response.text,
                    venue=venue,
                    source_url=xml_url,
                    start=self.start_date,
                    end=self.end_date,
                    keywords=keywords,
                    keyword_required=keyword_required,
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to retrieve DBLP venue {venue.get('简称') or venue.get('全称')}: {last_error or exc}"
                )
                venue_papers = []

            for paper in venue_papers:
                key = (paper.doi.lower(), paper.title.lower())
                if key in seen:
                    continue
                seen.add(key)
                papers.append(paper)
                if max_papers and len(papers) >= int(max_papers):
                    logger.info(f"Reached ccf_dblp.max_papers={max_papers}")
                    return papers

            if delay > 0:
                time.sleep(delay)

        return papers

    def convert_to_paper(self, raw_paper: DblpPaper) -> Paper:
        authors = raw_paper.authors or ["Unknown authors"]
        meta = (
            f"CCF {raw_paper.ccf_rank} {raw_paper.ccf_type}; "
            f"{raw_paper.ccf_field}; {raw_paper.venue_abbr or raw_paper.venue_name}; "
            f"year {raw_paper.year}; DBLP updated {raw_paper.mdate}."
        )
        abstract = f"{raw_paper.title}. {meta}"
        url = raw_paper.url or raw_paper.source_url
        return Paper(
            source=self.name,
            title=f"[CCF {raw_paper.ccf_rank}] {raw_paper.title}",
            authors=authors,
            abstract=abstract,
            url=url,
            pdf_url=url,
            full_text=None,
        )
