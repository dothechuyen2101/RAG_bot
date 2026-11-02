import logging
from dataclasses import dataclass

import requests
from tavily import TavilyClient
from tavily.errors import TimeoutError as TavilyTimeout
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .providers import MissingKeyError

log = logging.getLogger("ragbot.websearch")


@dataclass
class WebResult:
    title: str
    url: str
    content: str


class TavilySearch:
    def __init__(self, api_key: str, max_results: int = 5):
        if not api_key:
            raise MissingKeyError("Chưa có TAVILY_API_KEY trong file .env")
        self.client = TavilyClient(api_key=api_key)
        self.max_results = max_results

    # Lỗi mạng tạm thời (kể cả SSL khi antivirus/proxy soi HTTPS lần đầu) thì thử lại vài lần.
    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, TavilyTimeout)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def search(self, query: str) -> list[WebResult]:
        resp = self.client.search(query, search_depth="advanced", max_results=self.max_results)
        return [
            WebResult(title=r.get("title") or r["url"], url=r["url"], content=r.get("content") or "")
            for r in resp.get("results", [])
        ]
