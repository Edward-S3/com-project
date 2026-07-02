"""YouTube URL・WebサイトURLの取り込み。"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib.parse import urlparse

import requests
import trafilatura

from config.design_system import WEB_FETCH_MAX_WORKERS
from core.file_ingest import IngestedSource

logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+",
    re.I,
)


@dataclass
class WebIngestResult:
    sources: List[IngestedSource]
    youtube_failed: int = 0
    web_failed: int = 0
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def parse_urls(text: str) -> List[str]:
    if not text:
        return []
    return [line.strip() for line in text.strip().splitlines() if line.strip()]


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_RE.search(url))


def _requests_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "AI-Slide-Generator/1.0"})
    proxies = {}
    if os.getenv("HTTP_PROXY"):
        proxies["http"] = os.getenv("HTTP_PROXY")
    if os.getenv("HTTPS_PROXY"):
        proxies["https"] = os.getenv("HTTPS_PROXY")
    if proxies:
        session.proxies.update(proxies)
    return session


def fetch_web_page(url: str, timeout: int = 30) -> Optional[str]:
    try:
        session = _requests_session()
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        extracted = trafilatura.extract(resp.text, include_comments=False, include_tables=True)
        return extracted or ""
    except Exception as exc:
        logger.warning("Web取得失敗 %s: %s", url, exc)
        return None


def ingest_web_urls(
    urls: List[str],
    *,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> WebIngestResult:
    sources: List[IngestedSource] = []
    warnings: List[str] = []
    failed = 0
    valid = [u for u in urls if u and not is_youtube_url(u)]
    total = len(valid)

    def _one(url: str) -> Optional[IngestedSource]:
        text = fetch_web_page(url)
        if not text:
            return None
        return IngestedSource(
            name=urlparse(url).netloc or url,
            source_type="web",
            text=text,
            metadata={"url": url, "char_count": len(text)},
        )

    done = 0
    with ThreadPoolExecutor(max_workers=WEB_FETCH_MAX_WORKERS) as pool:
        futures = {pool.submit(_one, u): u for u in valid}
        for fut in as_completed(futures):
            done += 1
            if on_progress:
                on_progress(done, total)
            url = futures[fut]
            try:
                src = fut.result()
                if src:
                    sources.append(src)
                else:
                    failed += 1
                    warnings.append(f"Webサイトを読み込めませんでした: {url}")
            except Exception as exc:
                failed += 1
                warnings.append(f"Webサイトエラー ({url}): {exc}")

    return WebIngestResult(sources=sources, web_failed=failed, warnings=warnings)


def youtube_urls_only(urls: List[str]) -> List[str]:
    return [u for u in urls if is_youtube_url(u)]
