import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from . import prompts
from .providers import LLM
from .retrieval import Hit, Retriever
from .websearch import TavilySearch

Coverage = Literal["full", "partial", "none"]
HISTORY_TURNS = 6
# Gửi cả đoạn (tối đa ~2000 ký tự); giao diện tự thu gọn còn 3 dòng, bấm vào mới mở hết.
EXCERPT_CHARS = 2400
WEB_CONTENT_CHARS = 3000

ProgressFn = Callable[[str], None]
TextFn = Callable[[str], None]

_ANSWER_KEY = re.compile(r'"answer"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _noop(_: str) -> None:
    pass


@dataclass
class Source:
    n: int
    title: str
    location: str  # "Trang 3 · Điều 5" hoặc tên miền
    excerpt: str
    url: str = ""  # trang web (câu trả lời từ internet)
    doc_id: str = ""  # tài liệu trong bộ: mở file gốc qua /api/documents/{doc_id}/file
    page: int = 0
    status: str = ""  # hiệu lực văn bản cần lưu ý: pending | expiring | amended | expired
    status_label: str = ""
    status_note: str = ""


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


def _legal_warning(meta: dict) -> str:
    """Ghi chú hiệu lực cần lưu ý khi trích đoạn này, ví dụ "HẾT HIỆU LỰC: Bị thay thế bởi ..."."""
    if meta.get("legal_status") not in ("pending", "expiring", "amended", "expired"):
        return ""
    note = meta.get("legal_note", "")
    return f"{meta['legal_label'].upper()}{': ' + note if note else ''}"


def format_context(hits: list[Hit]) -> str:
    blocks = []
    for n, h in enumerate(hits, 1):
        parts = [h.meta.get("title", ""), location_of(h.meta), _legal_warning(h.meta)]
        blocks.append(f"[{n}] (Nguồn: {' | '.join(p for p in parts if p)})\n{h.text}")
    return "\n\n".join(blocks)


def _cited_numbers(result_citations: list, text: str, n_max: int) -> list[int]:
    found = {int(n) for n in result_citations if isinstance(n, int)}
    found |= {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return sorted(n for n in found if 1 <= n <= n_max)


def partial_answer(raw: str) -> str:
    """Phần trường "answer" đã nhận được trong chuỗi JSON đang stream (có thể chưa đóng ngoặc)."""
    m = _ANSWER_KEY.search(raw)
    if not m:
        return ""
    out: list[str] = []
    i = m.end()
    while i < len(raw):
        c = raw[i]
        if c == '"':
            break
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(raw):
            break  # ký tự escape bị cắt ở cuối, chờ phần sau
        if raw[i + 1] != "u":
            out.append(_ESCAPES.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        try:
            code = int(raw[i + 2 : i + 6], 16) if len(raw) >= i + 6 else None
            if code is not None and 0xD800 <= code < 0xDC00:  # cặp surrogate (emoji...)
                low = int(raw[i + 8 : i + 12], 16) if raw[i + 6 : i + 8] == "\\u" and len(raw) >= i + 12 else None
                code = None if low is None else 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                i += 6
        except ValueError:
            code = None
        if code is None:
            break
        out.append(chr(code))
        i += 6
    return "".join(out)


def _source(n: int, hit: Hit) -> Source:
    meta = hit.meta
    warn = bool(_legal_warning(meta))
    return Source(
        n, meta.get("title", ""), location_of(meta), _excerpt(hit.text),
        doc_id=meta.get("doc_id", ""), page=meta.get("page_start") or 0,
        status=meta.get("legal_status", "") if warn else "", status_label=meta.get("legal_label", "") if warn else "",
        status_note=meta.get("legal_note", "") if warn else "",
    )


def answer_from_docs(
    llm: LLM,
    retriever: Retriever,
    collection_id: int,
    question: str,
    history: list[dict],
    top_k: int,
    min_similarity: float = 0.0,
    progress: ProgressFn = _noop,
    condense_llm: LLM | None = None,
    on_text: TextFn | None = None,
) -> Answer:
    """condense_llm: model viết lại câu hỏi nối tiếp (mặc định là llm).
    on_text: nhận phần câu trả lời đã sinh được, để hiện dần trên giao diện."""
    if history:
        progress("Đang hiểu câu hỏi trong ngữ cảnh hội thoại...")
    standalone = condense_question(condense_llm or llm, history, question)
    progress("Đang tìm trong tài liệu...")
    hits = retriever.search(collection_id, standalone, top_k)

    best = max((h.similarity for h in hits if h.similarity is not None), default=0.0)
    pinned = any(h.pinned for h in hits)
    if not hits or (min_similarity > 0 and best < min_similarity and not pinned):
        return Answer(kind="docs", coverage="none", text="", question=standalone, web_query=standalone)

    progress(f"Đang đọc {len(hits)} đoạn liên quan và soạn câu trả lời...")
    result = llm.complete_json(
        prompts.DOCS_SYSTEM,
        prompts.DOCS_USER.format(context=format_context(hits), question=standalone),
        prompts.DOCS_SCHEMA,
        on_text=(lambda raw: on_text(partial_answer(raw))) if on_text else None,
    )
    coverage: Coverage = result.get("coverage") if result.get("coverage") in ("full", "partial", "none") else "none"
    text = (result.get("answer") or "").strip()
    if coverage != "none" and not text:
        coverage = "none"
    if coverage == "none":
        text = ""

    sources = [_source(n, hits[n - 1]) for n in _cited_numbers(result.get("citations") or [], text, len(hits))
               ] if text else []
    return Answer(
        kind="docs",
        coverage=coverage,
        text=text,
        question=standalone,
        sources=sources,
        web_query=(result.get("web_query") or "").strip() or standalone,
    )


def answer_from_web(llm: LLM, search: TavilySearch, query: str, question: str, progress: ProgressFn = _noop,
                    on_text: TextFn | None = None) -> Answer:
    progress("Đang tìm trên internet...")
    results = search.search(query)
    if not results:
        return Answer(kind="web", coverage="none", text="Không tìm thấy kết quả phù hợp trên internet.", question=question)

    progress(f"Đang đọc {len(results)} trang web và tổng hợp câu trả lời...")
    context = "\n\n".join(
        f"[{n}] {r.title}\nURL: {r.url}\n{r.content[:WEB_CONTENT_CHARS]}" for n, r in enumerate(results, 1)
    )
    text = llm.complete(prompts.WEB_SYSTEM, prompts.WEB_USER.format(context=context, question=question),
                        on_text=on_text).strip()
    sources = [
        Source(n, r.title, urlparse(r.url).netloc, _excerpt(r.content), url=r.url) for n, r in enumerate(results, 1)
    ]
    return Answer(kind="web", coverage="full", text=text, question=question, sources=sources)


def _excerpt(text: str) -> str:
    # Bỏ dòng ngữ cảnh [Tên văn bản > Chương ...] ở đầu đoạn, vì đã hiển thị riêng.
    if text.startswith("[") and "\n" in text:
        text = text.split("\n", 1)[1]
    return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")
