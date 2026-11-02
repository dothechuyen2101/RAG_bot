import sqlite3

import pytest
from conftest import LEGAL_TEXT, FakeEmbedder
from test_legal import REPLACING

from ragbot.answer import format_context
from ragbot.ingest import DuplicateError, EmbedModelMismatch, Ingestor
from ragbot.retrieval import Retriever, articles_in, rrf
from ragbot.store import Database
from ragbot.store.db import build_fts_query

# Điều 3 dài (6 khoản, hơn 3000 ký tự) bị chia thành nhiều đoạn; văn bản này cũng có Điều 12 như LEGAL_TEXT.
TAX_TEXT = "\n".join([
    "Số: 45/2020/NĐ-CP", "NGHỊ ĐỊNH", "Về thuế đối với hàng hóa",
    "Điều 1. Phạm vi điều chỉnh", "Nghị định này quy định về thuế đối với hàng hóa.",
    "Điều 3. Mức thuế",
    *(f"{k}. Khoản {k} quy định mức thuế với nhóm hàng {'điện tử nhập khẩu' if k == 6 else f'số {k}'}: "
      + "nội dung chi tiết " * 25 for k in range(1, 7)),
    "Điều 12. Thuế suất ưu đãi", "Thuế suất ưu đãi là 10% đối với doanh nghiệp nhỏ và vừa.",
])


def _ingest_legal(stack):
    return stack["ingestor"].ingest_file(stack["cid"], "nghi_dinh_100.txt", LEGAL_TEXT.encode())


def _ingest_tax(stack):
    return stack["ingestor"].ingest_file(stack["cid"], "nghi_dinh_45.txt", TAX_TEXT.encode())


def test_rrf_rewards_items_in_both_lists():
    scores = rrf([["a", "b", "c"], ["c", "d"]])
    assert max(scores, key=scores.get) == "c"
    assert scores["a"] > scores["b"]


def test_fts_query_keeps_phrases_and_drops_stopwords():
    q = build_fts_query("Mức phạt của Điều 12 là gì?")
    assert '"điều 12"' in q and '"mức phạt"' in q
    assert '"của"' not in q
    assert build_fts_query("???") == ""


def test_ingest_then_keyword_search_finds_article_and_so_hieu(stack):
    result = _ingest_legal(stack)
    assert result.document.n_chunks == 5
    db, cid = stack["db"], stack["cid"]
    assert db.get_collection(cid).embed_model == "fake:bow:64"

    hits = db.keyword_search(cid, "Điều 12", 5)
    assert hits and hits[0].endswith(":0004")
    assert db.keyword_search(cid, "100/2019/NĐ-CP", 5)
    # Giữ dấu: "dieu" không khớp với "điều".
    assert db.keyword_search(cid, "dieu", 5) == []


def test_hybrid_search_returns_relevant_article(stack):
    _ingest_legal(stack)
    hits = stack["retriever"].search(stack["cid"], "phạt nồng độ cồn người điều khiển xe ô tô", 3)
    assert hits[0].meta["article"] == "Điều 5"
    assert hits[0].similarity is not None


def test_articles_named_in_question():
    assert articles_in("điều 12 và ĐIỀU 5a của Nghị định 100/2019?") == ["Điều 12", "Điều 5a"]
    assert articles_in("mức phạt nồng độ cồn") == []


def test_question_naming_article_and_document_gets_it_first(stack):
    _ingest_legal(stack)
    _ingest_tax(stack)
    search = stack["retriever"].search
    for number in ("45/2020", "100/2019"):
        top = search(stack["cid"], f"Điều 12 Nghị định {number} quy định gì?", 4)[0]
        assert top.pinned and top.meta["article"] == "Điều 12" and number in top.text.split("\n", 1)[0]
    # Không nói văn bản nào: Điều 12 của cả hai văn bản đều được đưa lên đầu.
    hits = search(stack["cid"], "Điều 12 quy định gì?", 4)
    assert [h.meta["article"] for h in hits[:2]] == ["Điều 12", "Điều 12"] and all(h.pinned for h in hits[:2])


def test_long_article_parts_are_merged(stack):
    doc = _ingest_tax(stack).document
    parts = stack["vectors"].find(stack["cid"], {"article": "Điều 3"})
    assert len(parts) >= 2 and "(tiếp)" in parts[1].text
    hits = stack["retriever"].search(stack["cid"], "mức thuế nhóm hàng điện tử nhập khẩu", 3)
    article = next(h for h in hits if h.meta["article"] == "Điều 3")
    # Tìm trúng phần chứa khoản 6 nhưng nhận cả Điều, tiêu đề Điều chỉ xuất hiện một lần.
    assert "Khoản 1 quy định" in article.text and "điện tử nhập khẩu" in article.text
    assert article.text.count("Điều 3. Mức thuế") == 1 and "(tiếp)" not in article.text
    assert sum(h.meta["article"] == "Điều 3" for h in hits) == 1
    assert doc.so_hieu == "45/2020/NĐ-CP"


def test_replaced_document_is_flagged_in_results(stack):
    _ingest_legal(stack)
    stack["ingestor"].ingest_file(stack["cid"], "nghi_dinh_168.txt", REPLACING.encode())
    hits = stack["retriever"].search(stack["cid"], "phạt nồng độ cồn người điều khiển xe ô tô", 4)
    old = next(h for h in hits if h.meta["article"] == "Điều 5")
    assert old.meta["legal_status"] == "expired"
    assert "HẾT HIỆU LỰC: Bị thay thế bởi 168/2024/NĐ-CP" in format_context([old])


def test_backfill_reads_legal_info_of_old_documents(stack):
    doc = _ingest_legal(stack).document
    stack["db"].update_document(doc.id, so_hieu=None)  # như tài liệu nạp từ phiên bản cũ
    assert stack["ingestor"].backfill_legal_info() == 1
    assert stack["db"].get_document(doc.id).so_hieu == "100/2019/NĐ-CP"
    assert stack["ingestor"].backfill_legal_info() == 0


def test_old_database_gets_legal_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE documents (id TEXT PRIMARY KEY, collection_id INTEGER, title TEXT, source TEXT,
                    source_type TEXT, content_hash TEXT, n_pages INTEGER, n_ocr_pages INTEGER, n_chunks INTEGER,
                    created_at TEXT)""")
    conn.execute("INSERT INTO documents VALUES ('d1', 1, 'Cũ', 'cu.pdf', 'pdf', 'h', 1, 0, 3, '2024-01-01')")
    conn.commit()
    conn.close()
    doc = Database(path).get_document("d1")
    assert doc.title == "Cũ" and doc.so_hieu is None and doc.replaces == [] and doc.status == ""


def test_duplicate_file_rejected(stack):
    _ingest_legal(stack)
    with pytest.raises(DuplicateError):
        _ingest_legal(stack)


def test_delete_document_removes_from_both_indexes(stack):
    doc = _ingest_legal(stack).document
    stack["ingestor"].delete_document(stack["cid"], doc.id)
    assert stack["db"].list_documents(stack["cid"]) == []
    assert stack["db"].keyword_search(stack["cid"], "Điều 12", 5) == []
    assert stack["retriever"].search(stack["cid"], "nồng độ cồn", 3) == []
    assert not list((stack["dir"] / "texts").iterdir())


def test_changed_embed_model_requires_reindex(stack):
    _ingest_legal(stack)

    class OtherEmbedder(FakeEmbedder):
        model_id = "fake:other:64"

    other = OtherEmbedder()
    ingestor = Ingestor(stack["db"], stack["vectors"], other, stack["llm"], stack["dir"])
    assert ingestor.needs_reindex(stack["cid"])
    with pytest.raises(EmbedModelMismatch):
        ingestor.ingest_file(stack["cid"], "khac.txt", b"Noi dung khac du dai de nap vao bo tai lieu nay.")

    errors = ingestor.reindex_collection(stack["cid"])
    assert errors == []
    assert not ingestor.needs_reindex(stack["cid"])
    assert stack["db"].get_collection(stack["cid"]).embed_model == "fake:other:64"
    hits = Retriever(stack["db"], stack["vectors"], other).search(stack["cid"], "nồng độ cồn", 3)
    assert hits[0].meta["article"] == "Điều 5"
    # Re-index dùng văn bản đã lưu, không gọi OCR lại.
    assert stack["llm"].ocr_calls == 0


def test_collections_are_isolated(stack):
    _ingest_legal(stack)
    other = stack["db"].create_collection("Thuế")
    assert stack["retriever"].search(other.id, "nồng độ cồn", 3) == []
    assert stack["db"].keyword_search(other.id, "Điều 12", 5) == []


def test_delete_collection_cleans_everything(stack):
    _ingest_legal(stack)
    stack["ingestor"].delete_collection(stack["cid"])
    assert stack["db"].get_collection(stack["cid"]) is None
    assert stack["db"].list_documents(stack["cid"]) == []
    assert not list((stack["dir"] / "files").iterdir())
