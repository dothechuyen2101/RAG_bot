from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings


@dataclass
class StoredChunk:
    id: str
    text: str
    meta: dict
    similarity: float | None = None


class VectorStore:
    """Mỗi bộ tài liệu là một collection Chroma tên col_<id>."""

    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))

    def _col(self, collection_id: int):
        # embedding_function=None: vector luôn do chúng ta tự tính và truyền vào.
        return self.client.get_or_create_collection(
            f"col_{collection_id}", embedding_function=None, configuration={"hnsw": {"space": "cosine"}}
        )

    def add(self, collection_id: int, chunks: list[StoredChunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        self._col(collection_id).add(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[c.meta for c in chunks],
        )

    def query(self, collection_id: int, embedding: list[float], k: int) -> list[StoredChunk]:
        col = self._col(collection_id)
        if col.count() == 0:
            return []
        res = col.query(
            query_embeddings=[embedding], n_results=min(k, col.count()), include=["documents", "metadatas", "distances"]
        )
        return [
            StoredChunk(id=i, text=d, meta=m, similarity=1.0 - dist)
            for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    def get(self, collection_id: int, ids: list[str]) -> list[StoredChunk]:
        if not ids:
            return []
        res = self._col(collection_id).get(ids=ids, include=["documents", "metadatas"])
        return [StoredChunk(id=i, text=d, meta=m) for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]

    def delete_document(self, collection_id: int, doc_id: str) -> None:
        self._col(collection_id).delete(where={"doc_id": doc_id})

    def drop(self, collection_id: int) -> None:
        try:
            self.client.delete_collection(f"col_{collection_id}")
        except Exception:
            pass  # collection chưa từng được tạo
