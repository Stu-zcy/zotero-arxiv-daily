from __future__ import annotations

import time
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import re

import feedparser
import requests
from loguru import logger
from omegaconf import ListConfig

from ..protocol import Paper
from .base import BaseRetriever, register_retriever


@dataclass
class EprintPaper:
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str | None
    published: str
    category: str


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, ListConfig)):
        return list(value)
    return [value]


def _parse_config_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_window(config) -> tuple[date, date]:
    start = _parse_config_date(config.get("start_date"))
    end = _parse_config_date(config.get("end_date"))
    if start and end:
        return start, end
    if start and not end:
        return start, date.today()
    if end and not start:
        return end, end
    lookback_days = int(config.get("lookback_days") or 1)
    end = date.today()
    return end - timedelta(days=lookback_days), end


def _entry_date(entry) -> date | None:
    published = entry.get("published") or entry.get("updated")
    if published:
        try:
            return parsedate_to_datetime(published).astimezone(timezone.utc).date()
        except Exception:
            pass
    if entry.get("published_parsed"):
        return date(*entry.published_parsed[:3])
    return None


def _entry_authors(entry) -> list[str]:
    authors = []
    for author in entry.get("authors") or []:
        name = author.get("name") if isinstance(author, dict) else getattr(author, "name", "")
        if name:
            authors.append(str(name))
    if not authors and entry.get("author"):
        authors.append(str(entry.author))
    return authors


def _entry_pdf(entry) -> str | None:
    for link in entry.get("links") or []:
        href = link.get("href") if isinstance(link, dict) else getattr(link, "href", None)
        link_type = link.get("type") if isinstance(link, dict) else getattr(link, "type", None)
        if href and link_type == "application/pdf":
            return str(href)
    link = entry.get("link")
    if link:
        return f"{str(link).rstrip('/')}.pdf"
    return None


def _normalize_text(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _keyword_in_text(keyword: str, searchable: str) -> bool:
    normalized_keyword = _normalize_text(keyword)
    if not normalized_keyword:
        return False
    return re.search(rf"\b{re.escape(normalized_keyword)}\b", searchable) is not None


def _fetch_with_powershell(url: str, timeout: int) -> str:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$ProgressPreference='SilentlyContinue'; "
            f"(Invoke-WebRequest -Uri {url!r} -UseBasicParsing -TimeoutSec {timeout}).Content"
        ),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout + 10)
    return completed.stdout


@register_retriever("iacr_eprint")
class IacrEprintRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        self.start_date, self.end_date = _date_window(self.retriever_config)

    def _fetch_feed(self) -> str:
        session = requests.Session()
        session.headers.update({"User-Agent": "zotero-arxiv-daily/1.0", "Accept": "application/rss+xml,application/xml,text/xml"})
        proxy = self.retriever_config.get("proxy")
        if proxy:
            session.proxies.update({"http": str(proxy), "https": str(proxy)})
            logger.info(f"Using IACR ePrint proxy: {proxy}")
        timeout = int(self.retriever_config.get("request_timeout") or 30)
        try:
            response = session.get(str(self.retriever_config.feed_url), timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            if proxy:
                raise
            logger.warning(f"IACR ePrint requests fetch failed; trying PowerShell fallback: {exc}")
            return _fetch_with_powershell(str(self.retriever_config.feed_url), timeout)

    def _retrieve_raw_papers(self) -> list[EprintPaper]:
        feed_text = self._fetch_feed()
        parsed = feedparser.parse(feed_text)
        categories = {str(c).lower() for c in _as_list(self.retriever_config.get("categories")) if c}
        keywords = [str(k).lower() for k in _as_list(self.retriever_config.get("keywords")) if k]
        keyword_required = bool(self.retriever_config.get("keyword_required") or False)
        max_papers = self.retriever_config.get("max_papers")
        papers: list[EprintPaper] = []
        seen: set[str] = set()
        stats = {
            "feed": len(parsed.entries),
            "date": 0,
            "category": 0,
            "keyword": 0,
        }

        for entry in parsed.entries:
            published_date = _entry_date(entry)
            if published_date and not (self.start_date <= published_date <= self.end_date):
                continue
            stats["date"] += 1
            category_values = [tag.get("term") for tag in entry.get("tags") or [] if tag.get("term")]
            category = category_values[0] if category_values else ""
            if categories and category.lower() not in categories:
                continue
            stats["category"] += 1
            title = str(entry.get("title") or "").strip()
            abstract = str(entry.get("summary") or entry.get("description") or "").strip()
            searchable = _normalize_text(f"{title} {abstract} {category}")
            if keyword_required and not any(_keyword_in_text(keyword, searchable) for keyword in keywords):
                continue
            stats["keyword"] += 1
            url = str(entry.get("link") or entry.get("id") or "")
            key = url or title.lower()
            if not key or key in seen:
                continue
            seen.add(key)
            papers.append(
                EprintPaper(
                    title=title,
                    authors=_entry_authors(entry),
                    abstract=abstract,
                    url=url,
                    pdf_url=_entry_pdf(entry),
                    published=published_date.isoformat() if published_date else "",
                    category=category,
                )
            )
            if max_papers and len(papers) >= int(max_papers):
                break

        delay = float(self.retriever_config.get("request_delay") or 0)
        if delay > 0:
            time.sleep(delay)
        logger.info(
            "IACR ePrint feed={} date_matched={} category_matched={} keyword_matched={} kept={} for {} to {}",
            stats["feed"],
            stats["date"],
            stats["category"],
            stats["keyword"],
            len(papers),
            self.start_date,
            self.end_date,
        )
        return papers

    def convert_to_paper(self, raw_paper: EprintPaper) -> Paper:
        meta = f"IACR ePrint; {raw_paper.category}; published {raw_paper.published}."
        return Paper(
            source=self.name,
            title=f"[ePrint] {raw_paper.title}",
            authors=raw_paper.authors or ["Unknown authors"],
            abstract=f"{raw_paper.abstract or raw_paper.title}\n\n{meta}",
            url=raw_paper.url,
            pdf_url=raw_paper.pdf_url,
            full_text=None,
            published_date=raw_paper.published or None,
            venue="IACR ePrint",
            venue_abbr="ePrint",
            source_label="IACR ePrint",
        )
