from dataclasses import dataclass

from tavily import TavilyClient

from .providers import MissingKeyError


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

    def search(self, query: str) -> list[WebResult]:
        resp = self.client.search(query, search_depth="advanced", max_results=self.max_results)
        return [
            WebResult(title=r.get("title") or r["url"], url=r["url"], content=r.get("content") or "")
            for r in resp.get("results", [])
        ]
