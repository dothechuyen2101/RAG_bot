import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from . import prompts
from .providers import LLM
from .retrieval import Hit, Retriever
from .websearch import TavilySearch

Coverage = Literal["full", "partial", "none"]
HISTORY_TURNS = 6
EXCERPT_CHARS = 600
WEB_CONTENT_CHARS = 3000


@dataclass
class Source:
    n: int
    title: str
    location: str  # "Trang 3 · Điều 5" hoặc tên miền
    excerpt: str
    url: str = ""


@dataclass
class Answer:
    kind: Literal["docs", "web"]
    coverage: Coverage
    text: str
    question: str  # câu hỏi độc lập đã dùng để tìm kiếm
    sources: list[Source] = field(default_factory=list)
    web_query: str = ""

    @property
    def offer_web(self) -> bool:
        return self.kind == "docs" and self.coverage != "full"


def condense_question(llm: LLM, history: list[dict], question: str) -> str:
    if not history:
        return question
    lines = [
        f"{'Người dùng' if m['role'] == 'user' else 'Trợ lý'}: {m['content'][:1500]}"
        for m in history[-HISTORY_TURNS:]
        if m.get("content")
    ]
    if not lines:
        return question
    rewritten = llm.complete(prompts.CONDENSE_SYSTEM, prompts.CONDENSE_USER.format(history="\n".join(lines), question=question))
    return rewritten.strip() or question


def location_of(meta: dict) -> str:
    parts = []
    start, end = meta.get("page_start") or 0, meta.get("page_end") or 0
    if start:
        parts.append(f"Trang {start}" if start == end or not end else f"Trang {start}–{end}")
    if meta.get("article"):
        parts.append(meta["article"])
    elif meta.get("section"):
        parts.append(meta["section"])
    return " · ".join(parts)


def format_context(hits: list[Hit]) -> str:
    blocks = []
    for n, h in enumerate(hits, 1):
        loc = location_of(h.meta)
        blocks.append(f"[{n}] (Nguồn: {h.meta.get('title', '')}{' | ' + loc if loc else ''})\n{h.text}")
    return "\n\n".join(blocks)


def _cited_numbers(result_citations: list, text: str, n_max: int) -> list[int]:
    found = {int(n) for n in result_citations if isinstance(n, int)}
    found |= {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return sorted(n for n in found if 1 <= n <= n_max)


def answer_from_docs(
    llm: LLM,
    retriever: Retriever,
    collection_id: int,
    question: str,
    history: list[dict],
    top_k: int,
    min_similarity: float = 0.0,
) -> Answer:
    standalone = condense_question(llm, history, question)
    hits = retriever.search(collection_id, standalone, top_k)

    best = max((h.similarity for h in hits if h.similarity is not None), default=0.0)
    if not hits or (min_similarity > 0 and best < min_similarity):
        return Answer(kind="docs", coverage="none", text="", question=standalone, web_query=standalone)

    result = llm.complete_json(
        prompts.DOCS_SYSTEM,
        prompts.DOCS_USER.format(context=format_context(hits), question=standalone),
        prompts.DOCS_SCHEMA,
    )
    coverage: Coverage = result.get("coverage") if result.get("coverage") in ("full", "partial", "none") else "none"
    text = (result.get("answer") or "").strip()
    if coverage != "none" and not text:
        coverage = "none"
    if coverage == "none":
        text = ""

    sources = [
        Source(n, hits[n - 1].meta.get("title", ""), location_of(hits[n - 1].meta), _excerpt(hits[n - 1].text))
        for n in _cited_numbers(result.get("citations") or [], text, len(hits))
    ] if text else []
    return Answer(
        kind="docs",
        coverage=coverage,
        text=text,
        question=standalone,
        sources=sources,
        web_query=(result.get("web_query") or "").strip() or standalone,
    )


def answer_from_web(llm: LLM, search: TavilySearch, query: str, question: str) -> Answer:
    results = search.search(query)
    if not results:
        return Answer(kind="web", coverage="none", text="Không tìm thấy kết quả phù hợp trên internet.", question=question)

    context = "\n\n".join(
        f"[{n}] {r.title}\nURL: {r.url}\n{r.content[:WEB_CONTENT_CHARS]}" for n, r in enumerate(results, 1)
    )
    text = llm.complete(prompts.WEB_SYSTEM, prompts.WEB_USER.format(context=context, question=question)).strip()
    sources = [
        Source(n, r.title, urlparse(r.url).netloc, _excerpt(r.content), url=r.url) for n, r in enumerate(results, 1)
    ]
    return Answer(kind="web", coverage="full", text=text, question=question, sources=sources)


def _excerpt(text: str) -> str:
    # Bỏ dòng ngữ cảnh [Tên văn bản > Chương ...] ở đầu đoạn, vì đã hiển thị riêng.
    if text.startswith("[") and "\n" in text:
        text = text.split("\n", 1)[1]
    return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")
