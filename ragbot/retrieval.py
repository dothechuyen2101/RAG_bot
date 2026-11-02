import re
from dataclasses import dataclass

from .ingest.legal import EXPIRED_PENALTY, collection_statuses
from .providers import Embedder
from .store import Database, StoredChunk, VectorStore

CANDIDATES = 20
RRF_K = 60
# Điều dài bị chia theo khoản thành nhiều đoạn: ghép lại thành một, dài tối đa chừng này ký tự.
MAX_ARTICLE_CHARS = 6000
ARTICLE_RE = re.compile(r"\bđiều\s+(\d+[a-zđ]?)\b", re.I)
# "100/2019" trong "Nghị định 100/2019" hay "100/2019/NĐ-CP".
DOC_NUMBER_RE = re.compile(r"\b\d+/\d{4}\b")


@dataclass
class Hit:
    id: str
    text: str
    meta: dict
    score: float  # điểm RRF
    similarity: float | None  # cosine; None nếu chỉ khớp bằng từ khóa hoặc số Điều
    pinned: bool = False  # đúng "Điều N" mà câu hỏi nêu


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: đoạn đứng cao ở nhiều danh sách thì được điểm cao."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def articles_in(query: str) -> list[str]:
    """Các "Điều N" nêu trong câu hỏi, viết như metadata của đoạn: "Điều 12", "Điều 5a"."""
    return list(dict.fromkeys(f"Điều {n.lower()}" for n in ARTICLE_RE.findall(query)))


def _doc_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 1)[0]


def _index(chunk_id: str) -> int:
    return int(chunk_id.rsplit(":", 1)[1])


def _continuation(text: str) -> str:
    """Phần sau của một Điều dài: bỏ dòng ngữ cảnh [..] và dòng "Điều N. ... (tiếp)" lặp lại ở đầu."""
    lines = text.split("\n")[1:]
    if lines and lines[0].rstrip().endswith("(tiếp)"):
        lines = lines[1:]
    return "\n".join(lines)


class Retriever:
    def __init__(self, db: Database, vectors: VectorStore, embedder: Embedder):
        self.db = db
        self.vectors = vectors
        self.embedder = embedder

    def search(self, collection_id: int, query: str, top_k: int) -> list[Hit]:
        qvec = self.embedder.embed([query], "query")[0]
        vector_hits = self.vectors.query(collection_id, qvec, CANDIDATES)
        keyword_ids = self.db.keyword_search(collection_id, query, CANDIDATES)
        scores = rrf([[h.id for h in vector_hits], keyword_ids])

        # Văn bản đã hết hiệu lực vẫn tìm được (có khi người dùng hỏi về quy định cũ) nhưng bị trừ điểm.
        statuses = collection_statuses(self.db.list_documents(collection_id))
        for i in scores:
            if (st := statuses.get(_doc_id(i))) and st.code == "expired":
                scores[i] *= EXPIRED_PENALTY

        pinned = self._pinned(collection_id, query, scores, top_k)
        pinned_ids = {c.id for c in pinned}
        ordered = [c.id for c in pinned] + sorted((i for i in scores if i not in pinned_ids), key=scores.get,
                                                  reverse=True)
        by_id: dict[str, StoredChunk] = {h.id: h for h in vector_hits} | {c.id: c for c in pinned}
        by_id.update({c.id: c for c in self.vectors.get(collection_id, [i for i in ordered if i not in by_id])})

        # Các phần của cùng một Điều tính là một kết quả, để nhường chỗ cho đoạn khác.
        groups: dict[tuple, StoredChunk] = {}
        for i in ordered:
            c = by_id.get(i)
            if c is None:
                continue
            key = (c.meta.get("doc_id"), c.meta["article"]) if c.meta.get("article") else (i,)
            if key not in groups and len(groups) < top_k:
                groups[key] = c

        hits = []
        for key, c in groups.items():
            text, meta = self._whole_article(collection_id, c) if len(key) == 2 else (c.text, dict(c.meta))
            if (st := statuses.get(meta.get("doc_id"))) and st.code:
                meta.update(legal_status=st.code, legal_label=st.label, legal_note=st.note)
            hits.append(Hit(c.id, text, meta, scores.get(c.id, 0.0), c.similarity, pinned=c.id in pinned_ids))
        return hits

    def _pinned(self, collection_id: int, query: str, scores: dict[str, float], top_k: int) -> list[StoredChunk]:
        """Câu hỏi nêu rõ "Điều N" (và số hiệu văn bản): lấy thẳng các đoạn của Điều đó, không trông vào
        việc tìm kiếm có xếp chúng lên đầu hay không."""
        articles = articles_in(query)
        if not articles:
            return []
        found = self.vectors.find(collection_id, {"article": {"$in": articles}})
        found.sort(key=lambda c: scores.get(c.id, 0.0), reverse=True)
        numbers = DOC_NUMBER_RE.findall(query)
        if numbers:
            # Dòng đầu mỗi đoạn là "[Tên văn bản (số hiệu) > Chương ...]".
            return [c for c in found
                    if any(n in c.text.split("\n", 1)[0] or n in c.meta.get("title", "") for n in numbers)]
        # Không nói rõ văn bản nào mà nhiều văn bản cùng có Điều N: chừa chỗ cho các đoạn tìm được khác.
        keep, seen = [], set()
        for c in found:
            key = (c.meta.get("doc_id"), c.meta.get("article"))
            if key in seen or len(seen) < max(1, top_k // 2):
                seen.add(key)
                keep.append(c)
        return keep

    def _whole_article(self, collection_id: int, chunk: StoredChunk) -> tuple[str, dict]:
        """Ghép các phần liền nhau của cùng Điều quanh đoạn tìm được, để LLM không trả lời thiếu khoản."""
        meta = dict(chunk.meta)
        parts = self.vectors.find(collection_id, {"$and": [{"doc_id": meta["doc_id"]}, {"article": meta["article"]}]})
        pos = next((k for k, p in enumerate(parts) if p.id == chunk.id), None)
        if pos is None or len(parts) == 1:
            return chunk.text, meta
        # Chỉ các phần liền nhau: văn bản sửa đổi có thể trích lại "Điều 5" của văn bản khác ở chỗ khác.
        lo = hi = pos
        while lo > 0 and _index(parts[lo - 1].id) == _index(parts[lo].id) - 1:
            lo -= 1
        while hi + 1 < len(parts) and _index(parts[hi + 1].id) == _index(parts[hi].id) + 1:
            hi += 1
        first = last = pos
        size = len(parts[pos].text)
        forward = True
        while True:
            can_fwd = last < hi and size + len(parts[last + 1].text) <= MAX_ARTICLE_CHARS
            can_back = first > lo and size + len(parts[first - 1].text) <= MAX_ARTICLE_CHARS
            if not (can_fwd or can_back):
                break
            if can_fwd and (forward or not can_back):
                last += 1
                size += len(parts[last].text)
            else:
                first -= 1
                size += len(parts[first].text)
            forward = not forward
        run = parts[first : last + 1]
        pages = [p.meta.get(k) for p in run for k in ("page_start", "page_end") if p.meta.get(k)]
        meta.update(page_start=min(pages, default=0), page_end=max(pages, default=0))
        return "\n".join([run[0].text, *(_continuation(p.text) for p in run[1:])]), meta
