import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..providers import LLM, Embedder
from ..store import Database, Document, StoredChunk, VectorStore
from .chunker import chunk_document
from .loaders import OCR_INSTRUCTION, LoadedDoc, LoadError, Page, ProgressFn, _noop, load_file, load_url

# Đánh dấu bộ tài liệu đang re-index dở: khác mọi model_id nên UI vẫn hiện nút Re-index.
REINDEX_PENDING = "pending-reindex"


class DuplicateError(LoadError):
    pass


class EmbedModelMismatch(RuntimeError):
    pass


@dataclass
class IngestResult:
    document: Document
    n_ocr_pages: int


class Ingestor:
    def __init__(self, db: Database, vectors: VectorStore, embedder: Embedder, llm: LLM | None,
                 data_dir: Path, ocr_workers: int = 4):
        self.db = db
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.files_dir = data_dir / "files"
        self.texts_dir = data_dir / "texts"
        self.ocr_workers = ocr_workers
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.texts_dir.mkdir(parents=True, exist_ok=True)

    def _ocr(self, png: bytes) -> str:
        return self.llm.ocr(png, OCR_INSTRUCTION)

    def needs_reindex(self, collection_id: int) -> bool:
        col = self.db.get_collection(collection_id)
        return bool(col and col.embed_model and col.embed_model != self.embedder.model_id)

    def _check_embed_model(self, collection_id: int) -> None:
        col = self.db.get_collection(collection_id)
        if col is None:
            raise LoadError("Bộ tài liệu không tồn tại")
        if self.needs_reindex(collection_id):
            raise EmbedModelMismatch(
                f"Bộ '{col.name}' được index bằng {col.embed_model}, còn cấu hình hiện tại là "
                f"{self.embedder.model_id}. Hãy bấm Re-index trước khi nạp thêm."
            )

    def ingest_file(self, collection_id: int, filename: str, data: bytes, progress: ProgressFn = _noop) -> IngestResult:
        self._check_embed_model(collection_id)
        content_hash = hashlib.sha256(data).hexdigest()
        if dup := self.db.find_by_hash(collection_id, content_hash):
            raise DuplicateError(f"'{filename}' đã có trong bộ tài liệu (trùng với '{dup.title}')")
        ocr = self._ocr if self.llm else None
        doc = load_file(filename, data, ocr, progress, self.ocr_workers)
        return self._store(collection_id, doc, content_hash, progress)

    def ingest_url(self, collection_id: int, url: str, progress: ProgressFn = _noop) -> IngestResult:
        self._check_embed_model(collection_id)
        url = url.strip()
        if any(d.source == url for d in self.db.list_documents(collection_id)):
            raise DuplicateError(f"Link đã có trong bộ tài liệu: {url}")
        ocr = self._ocr if self.llm else None
        doc = load_url(url, ocr, progress, self.ocr_workers)
        content_hash = hashlib.sha256(doc.raw or doc.text.encode()).hexdigest()
        if dup := self.db.find_by_hash(collection_id, content_hash):
            raise DuplicateError(f"Nội dung link trùng với tài liệu '{dup.title}' đã có")
        return self._store(collection_id, doc, content_hash, progress)

    def _store(self, collection_id: int, doc: LoadedDoc, content_hash: str, progress: ProgressFn) -> IngestResult:
        if not doc.text.strip():
            raise LoadError(f"Không trích được chữ nào từ '{doc.source}'")

        doc_id = uuid.uuid4().hex
        stored, embeddings = self._embed_chunks(doc_id, doc.title, doc.source, doc.source_type, doc.pages, progress)
        n_ocr = sum(p.ocr for p in doc.pages)
        record = Document(
            id=doc_id, collection_id=collection_id, title=doc.title, source=doc.source,
            source_type=doc.source_type, content_hash=content_hash,
            n_pages=sum(p.number is not None for p in doc.pages) or None, n_ocr_pages=n_ocr,
            n_chunks=len(stored), created_at="",
        )

        # Lưu văn bản đã trích (kể cả OCR) để re-index sau này không phải OCR lại.
        text_path = self.texts_dir / f"{doc_id}.json"
        text_path.write_text(json.dumps(
            {"title": doc.title, "source": doc.source, "source_type": doc.source_type,
             "pages": [asdict(p) for p in doc.pages]},
            ensure_ascii=False,
        ), encoding="utf-8")
        raw_path = self.files_dir / f"{doc_id}{doc.extension}" if doc.raw is not None else None
        if raw_path:
            raw_path.write_bytes(doc.raw)

        try:
            self.vectors.add(collection_id, stored, embeddings)
            self.db.add_document(record, [(c.id, c.text) for c in stored])
        except Exception:
            self.vectors.delete_document(collection_id, doc_id)
            text_path.unlink(missing_ok=True)
            if raw_path:
                raw_path.unlink(missing_ok=True)
            raise

        if self.db.get_collection(collection_id).embed_model is None:
            self.db.set_embed_model(collection_id, self.embedder.model_id)
        progress(f"Xong: {doc.title} ({len(stored)} đoạn)")
        return IngestResult(record, n_ocr)

    def _embed_chunks(self, doc_id: str, title: str, source: str, source_type: str, pages: list[Page],
                      progress: ProgressFn) -> tuple[list[StoredChunk], list[list[float]]]:
        chunks = chunk_document(title, pages)
        if not chunks:
            raise LoadError(f"Không chia được đoạn nào từ '{source}'")
        progress(f"Chia được {len(chunks)} đoạn, đang tạo embedding...")
        stored = [
            StoredChunk(
                id=f"{doc_id}:{i:04d}",
                text=c.text,
                # Chroma không nhận None trong metadata, dùng 0 cho "không có số trang".
                meta={"doc_id": doc_id, "title": title, "source": source, "source_type": source_type,
                      "page_start": c.page_start or 0, "page_end": c.page_end or 0,
                      "section": c.section, "article": c.article},
            )
            for i, c in enumerate(chunks)
        ]
        embeddings = self.embedder.embed([c.text for c in stored], "document")
        return stored, embeddings

    def delete_document(self, collection_id: int, doc_id: str) -> None:
        self.vectors.delete_document(collection_id, doc_id)
        self.db.delete_document(doc_id)
        self._remove_files(doc_id)

    def delete_collection(self, collection_id: int) -> None:
        for d in self.db.list_documents(collection_id):
            self._remove_files(d.id)
        self.vectors.drop(collection_id)
        self.db.delete_collection(collection_id)

    def _remove_files(self, doc_id: str) -> None:
        (self.texts_dir / f"{doc_id}.json").unlink(missing_ok=True)
        for f in self.files_dir.glob(f"{doc_id}.*"):
            f.unlink(missing_ok=True)

    def reindex_collection(self, collection_id: int, progress: ProgressFn = _noop) -> list[str]:
        """Tạo lại toàn bộ embedding bằng model hiện tại, từ văn bản đã lưu. Trả về danh sách lỗi."""
        self.db.set_embed_model(collection_id, REINDEX_PENDING)
        self.vectors.drop(collection_id)
        errors = []
        docs = self.db.list_documents(collection_id)
        for n, d in enumerate(docs, 1):
            progress(f"[{n}/{len(docs)}] {d.title}")
            path = self.texts_dir / f"{d.id}.json"
            if not path.exists():
                errors.append(f"Thiếu văn bản đã lưu của '{d.title}', hãy xóa và nạp lại tài liệu này")
                continue
            saved = json.loads(path.read_text(encoding="utf-8"))
            pages = [Page(**p) for p in saved["pages"]]
            stored, embeddings = self._embed_chunks(d.id, d.title, d.source, d.source_type, pages, progress)
            self.vectors.add(collection_id, stored, embeddings)
            self.db.replace_chunks(collection_id, d.id, [(c.id, c.text) for c in stored])
        self.db.set_embed_model(collection_id, self.embedder.model_id)
        return errors
