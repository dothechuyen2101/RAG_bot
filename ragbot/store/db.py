"""SQLite: tài khoản, phiên đăng nhập, danh mục bộ tài liệu/tài liệu, lịch sử hội thoại và chỉ mục từ khóa FTS5."""

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Chỉ lưu SHA-256 của token phiên: lộ file DB cũng không dùng lại được phiên.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
-- Mỗi bộ tài liệu thuộc về một tài khoản; owner_id NULL là bộ tạo từ trước khi có đăng nhập.
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY,
    owner_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    embed_model TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_id, name)
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
-- Lịch sử hỏi đáp. Cuộc trò chuyện thuộc một bộ tài liệu, nên cũng thuộc chủ của bộ đó.
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS conversations_by_collection ON conversations (collection_id, updated_at);
-- Mỗi tin nhắn lưu nguyên dạng JSON mà giao diện hiển thị (câu trả lời, nguồn, lời mời tìm web).
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    data            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages (conversation_id, id);
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
    owner_id: int | None = None


@dataclass
class User:
    id: int
    username: str
    is_admin: bool
    created_at: str


@dataclass
class Conversation:
    id: int
    collection_id: int
    title: str
    created_at: str
    updated_at: str
    n_messages: int = 0


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
        # Web server xử lý request trên nhiều thread, nên dùng chung một kết nối có khóa.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate_collections_owner()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def _migrate_collections_owner(self) -> None:
        """DB tạo trước khi có đăng nhập: bảng collections chưa có owner_id và tên đang UNIQUE toàn cục."""
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(collections)")]
        if not cols or "owner_id" in cols:
            return
        # Chạy trước PRAGMA foreign_keys = ON nên chưa cần bảng users tồn tại.
        self.conn.executescript("""
            BEGIN;
            CREATE TABLE collections_new (
                id          INTEGER PRIMARY KEY,
                owner_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                embed_model TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (owner_id, name)
            );
            INSERT INTO collections_new (id, name, embed_model, created_at)
                SELECT id, name, embed_model, created_at FROM collections;
            DROP TABLE collections;
            ALTER TABLE collections_new RENAME TO collections;
            COMMIT;
        """)

    # --- collections ---

    def create_collection(self, name: str, owner_id: int | None = None) -> Collection:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO collections (name, owner_id) VALUES (?, ?)", (name.strip(), owner_id))
        return Collection(cur.lastrowid, name.strip(), None, owner_id)

    def list_collections(self, owner_id: int | None = None) -> list[Collection]:
        """owner_id: chỉ lấy bộ của tài khoản đó; None: lấy tất cả (dùng nội bộ)."""
        query = "SELECT id, name, embed_model, owner_id FROM collections"
        with self.lock:
            if owner_id is None:
                rows = self.conn.execute(f"{query} ORDER BY name").fetchall()
            else:
                rows = self.conn.execute(f"{query} WHERE owner_id = ? ORDER BY name", (owner_id,)).fetchall()
        return [Collection(**dict(r)) for r in rows]

    def get_collection(self, collection_id: int) -> Collection | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, name, embed_model, owner_id FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
        return Collection(**dict(row)) if row else None

    def claim_orphan_collections(self, owner_id: int) -> int:
        """Giao các bộ tài liệu tạo trước khi có đăng nhập cho tài khoản này."""
        with self.lock, self.conn:
            return self.conn.execute("UPDATE collections SET owner_id = ? WHERE owner_id IS NULL", (owner_id,)).rowcount

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

    # --- conversations ---

    def create_conversation(self, collection_id: int, title: str) -> Conversation:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO conversations (collection_id, title) VALUES (?, ?)",
                                    (collection_id, title))
        return self.get_conversation(cur.lastrowid)

    def list_conversations(self, collection_id: int) -> list[Conversation]:
        """Mới cập nhật trước."""
        with self.lock:
            rows = self.conn.execute(
                f"{_CONVERSATION_QUERY} WHERE c.collection_id = ? GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC",
                (collection_id,),
            ).fetchall()
        return [Conversation(**dict(r)) for r in rows]

    def get_conversation(self, conversation_id: int) -> Conversation | None:
        with self.lock:
            row = self.conn.execute(f"{_CONVERSATION_QUERY} WHERE c.id = ? GROUP BY c.id",
                                    (conversation_id,)).fetchone()
        return Conversation(**dict(row)) if row else None

    def delete_conversation(self, conversation_id: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def add_message(self, conversation_id: int, message: dict) -> dict:
        """Lưu tin nhắn, trả về tin nhắn kèm id."""
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO messages (conversation_id, data) VALUES (?, ?)",
                                    (conversation_id, json.dumps(message, ensure_ascii=False)))
            self.conn.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                              (conversation_id,))
        return {"id": cur.lastrowid, **message}

    def list_messages(self, conversation_id: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT id, data FROM messages WHERE conversation_id = ? ORDER BY id",
                                     (conversation_id,)).fetchall()
        return [{"id": r["id"], **json.loads(r["data"])} for r in rows]

    def get_message(self, conversation_id: int, message_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT id, data FROM messages WHERE id = ? AND conversation_id = ?",
                                    (message_id, conversation_id)).fetchone()
        return {"id": row["id"], **json.loads(row["data"])} if row else None

    def set_offer_state(self, conversation_id: int, state: str, message_id: int | None = None,
                        query: str | None = None) -> None:
        """Đổi trạng thái lời mời tìm web của một tin nhắn, hoặc mọi lời mời còn mở (message_id None)."""
        if message_id is None:
            target, params = "json_extract(data, '$.offer.state') = 'open'", ()
        else:
            target, params = "id = ?", (message_id,)
        with self.lock, self.conn:
            self.conn.execute(
                f"""UPDATE messages
                    SET data = json_set(data, '$.offer.state', ?,
                                        '$.offer.query', coalesce(?, json_extract(data, '$.offer.query')))
                    WHERE conversation_id = ? AND json_type(data, '$.offer') = 'object' AND {target}""",
                (state, query, conversation_id, *params),
            )

    # --- users & sessions ---

    def count_users(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password_hash: str, is_admin: bool = False) -> User:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                (username, password_hash, int(is_admin)),
            )
        return self.get_user(cur.lastrowid)

    def get_user(self, user_id: int) -> User | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, username, is_admin, created_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return _user(row)

    def get_user_with_hash(self, username: str) -> tuple[User, str] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT id, username, is_admin, created_at, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        return (_user(row), row["password_hash"]) if row else None

    def list_users(self) -> list[User]:
        with self.lock:
            rows = self.conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    def delete_user(self, user_id: int) -> None:
        """Xóa tài khoản; bộ tài liệu và phiên đăng nhập bị xóa theo (ON DELETE CASCADE)."""
        with self.lock, self.conn:
            self.conn.execute(
                "DELETE FROM chunks_fts WHERE collection_id IN (SELECT id FROM collections WHERE owner_id = ?)",
                (user_id,),
            )
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def create_session(self, token_hash: str, user_id: int, expires_at: float) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                              (token_hash, user_id, expires_at))

    def get_session_user(self, token_hash: str, now: float) -> User | None:
        with self.lock:
            row = self.conn.execute(
                """SELECT u.id, u.username, u.is_admin, u.created_at FROM sessions s
                   JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
        return _user(row)

    def delete_session(self, token_hash: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep: str | None = None) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash IS NOT ?", (user_id, keep))

    def purge_expired_sessions(self, now: float) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

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


_CONVERSATION_QUERY = """
    SELECT c.id, c.collection_id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS n_messages
    FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id"""


def _user(row) -> User | None:
    return User(row["id"], row["username"], bool(row["is_admin"]), row["created_at"]) if row else None


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
