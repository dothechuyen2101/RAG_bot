from dataclasses import dataclass

from .providers import Embedder
from .store import Database, VectorStore

CANDIDATES = 20
RRF_K = 60


@dataclass
class Hit:
    id: str
    text: str
    meta: dict
    score: float  # điểm RRF
    similarity: float | None  # cosine; None nếu chỉ khớp bằng từ khóa


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: đoạn đứng cao ở nhiều danh sách thì được điểm cao."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


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
        top = sorted(scores, key=scores.get, reverse=True)[:top_k]

        by_id = {h.id: h for h in vector_hits}
        missing = [i for i in top if i not in by_id]
        by_id.update({c.id: c for c in self.vectors.get(collection_id, missing)})
        return [
            Hit(i, by_id[i].text, by_id[i].meta, scores[i], by_id[i].similarity)
            for i in top
            if i in by_id
        ]
