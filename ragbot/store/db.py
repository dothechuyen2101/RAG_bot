"""SQLite: danh mục bộ tài liệu/tài liệu và chỉ mục từ khóa FTS5 cho các đoạn."""

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    embed_model TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    n_pages       INTEGER,
    n_ocr_pages   INTEGER,
    n_chunks      INTEGER,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (collection_id, content_hash)
);
-- remove_diacritics 0: giữ dấu, để "điều" không khớp nhầm với "điêu"/"dieu".
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, chunk_id UNINDEXED, doc_id UNINDEXED, collection_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0'
);
"""

# Từ quá phổ biến, khớp gần như mọi đoạn nên chỉ làm nhiễu xếp hạng.
STOPWORDS = {
    "và", "của", "là", "có", "các", "những", "được", "cho", "trong", "với", "theo", "này", "đó",
    "thì", "mà", "để", "khi", "nào", "gì", "không", "như", "về", "từ", "tại", "bị", "do", "một",
    "hay", "hoặc", "nếu", "thế", "sao", "bao", "nhiêu", "ai", "đâu", "ra", "vào", "đã", "sẽ",
    "the", "a", "an", "of", "and", "or", "to", "in", "is", "are", "what", "how",
}


@dataclass
class Collection:
    id: int
    name: str
    embed_model: str | None


@dataclass
class Document:
    id: str
    collection_id: int
    title: str
    source: str
    source_type: str
    content_hash: str
    n_pages: int | None
    n_ocr_pages: int | None
    n_chunks: int | None
    created_at: str


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Streamlit chạy mỗi phiên trên một thread khác nhau, nên dùng chung kết nối có khóa.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    # --- collections ---

    def create_collection(self, name: str) -> Collection:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO collections (name) VALUES (?)", (name.strip(),))
        return Collection(cur.lastrowid, name.strip(), None)

    def list_collections(self) -> list[Collection]:
        with self.lock:
            rows = self.conn.execute("SELECT id, name, embed_model FROM collections ORDER BY name").fetchall()
        return [Collection(**dict(r)) for r in rows]

    def get_collection(self, collection_id: int) -> Collection | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, name, embed_model FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
        return Collection(**dict(row)) if row else None

    def set_embed_model(self, collection_id: int, embed_model: str | None) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE collections SET embed_model = ? WHERE id = ?", (embed_model, collection_id))

    def delete_collection(self, collection_id: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM chunks_fts WHERE collection_id = ?", (collection_id,))
            self.conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    # --- documents ---

    def add_document(self, doc: Document, chunks: list[tuple[str, str]]) -> None:
        """chunks: danh sách (chunk_id, text) để đưa vào chỉ mục từ khóa."""
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO documents (id, collection_id, title, source, source_type, content_hash,
                                          n_pages, n_ocr_pages, n_chunks)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc.id, doc.collection_id, doc.title, doc.source, doc.source_type, doc.content_hash,
                 doc.n_pages, doc.n_ocr_pages, doc.n_chunks),
            )
            self._insert_fts(doc.collection_id, doc.id, chunks)

    def replace_chunks(self, collection_id: int, doc_id: str, chunks: list[tuple[str, str]]) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
            self._insert_fts(collection_id, doc_id, chunks)
            self.conn.execute("UPDATE documents SET n_chunks = ? WHERE id = ?", (len(chunks), doc_id))

    def _insert_fts(self, collection_id: int, doc_id: str, chunks: list[tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT INTO chunks_fts (text, chunk_id, doc_id, collection_id) VALUES (?, ?, ?, ?)",
            [(text, cid, doc_id, collection_id) for cid, text in chunks],
        )

    def list_documents(self, collection_id: int) -> list[Document]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM documents WHERE collection_id = ? ORDER BY created_at DESC, title", (collection_id,)
            ).fetchall()
        return [Document(**dict(r)) for r in rows]

    def find_by_hash(self, collection_id: int, content_hash: str) -> Document | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM documents WHERE collection_id = ? AND content_hash = ?", (collection_id, content_hash)
            ).fetchone()
        return Document(**dict(row)) if row else None

    def delete_document(self, doc_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    # --- keyword search ---

    def keyword_search(self, collection_id: int, query: str, limit: int) -> list[str]:
        match = build_fts_query(query)
        if not match:
            return []
        with self.lock:
            rows = self.conn.execute(
                """SELECT chunk_id FROM chunks_fts
                   WHERE chunks_fts MATCH ? AND collection_id = ?
                   ORDER BY bm25(chunks_fts) LIMIT ?""",
                (match, collection_id, limit),
            ).fetchall()
        return [r["chunk_id"] for r in rows]


def build_fts_query(query: str) -> str:
    """Tách câu hỏi thành từ, ghép OR cả từ đơn lẫn cặp từ liền nhau.

    Tiếng Việt viết theo âm tiết ("xử phạt", "giấy phép"), nên cặp âm tiết liền nhau khớp
    nguyên cụm sẽ được BM25 cộng điểm, đẩy đoạn chứa đúng cụm từ lên trên.
    """
    tokens = [t for t in re.findall(r"\w+", query.lower()) if t]
    if not tokens:
        return ""
    terms = [f'"{a} {b}"' for a, b in zip(tokens, tokens[1:])]
    terms += [f'"{t}"' for t in dict.fromkeys(tokens) if t not in STOPWORDS]
    return " OR ".join(terms)
