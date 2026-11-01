import pytest
from conftest import LEGAL_TEXT, FakeEmbedder

from ragbot.ingest import DuplicateError, EmbedModelMismatch, Ingestor
from ragbot.retrieval import Retriever, rrf
from ragbot.store.db import build_fts_query


def _ingest_legal(stack):
    return stack["ingestor"].ingest_file(stack["cid"], "nghi_dinh_100.txt", LEGAL_TEXT.encode())


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
