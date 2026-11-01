"""Chạy app.py thật bằng Streamlit AppTest, thay provider bằng bản giả lập."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from conftest import LEGAL_TEXT, FakeEmbedder, FakeLLM
from streamlit.testing.v1 import AppTest

import ragbot.services
from ragbot.config import Settings
from ragbot.ingest import Ingestor
from ragbot.retrieval import Retriever
from ragbot.services import Services
from ragbot.store import Database, VectorStore
from ragbot.websearch import WebResult

APP = str(Path(__file__).resolve().parent.parent / "app.py")
NOT_FOUND = {"coverage": "none", "answer": "", "citations": [], "web_query": "mức phạt xe máy 2026"}


class FakeSearch:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return [WebResult("Cổng TTĐT Chính phủ", "https://chinhphu.vn/x", "Mức phạt xe máy ...")]


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = Database(tmp_path / "ragbot.db")
    vectors = VectorStore(tmp_path / "chroma")
    embedder, llm, web = FakeEmbedder(), FakeLLM(NOT_FOUND, text="Theo [1], mức phạt là ..."), FakeSearch()
    ingestor = Ingestor(db, vectors, embedder, llm, tmp_path)
    cid = db.create_collection("Pháp luật").id
    ingestor.ingest_file(cid, "nd100.txt", LEGAL_TEXT.encode())
    svc = Services(Settings(data_dir=tmp_path), db, vectors, llm, embedder, ingestor,
                   Retriever(db, vectors, embedder), web)

    monkeypatch.setattr(ragbot.services, "build_services", lambda settings: svc)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    return at, svc


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_sidebar_lists_collection_and_document(app):
    at, _ = app
    assert at.title[0].value == "💬 Pháp luật"
    assert any("nd100" in m.value for m in at.sidebar.markdown)


def test_not_found_asks_before_web_search_then_searches(app):
    at, svc = app
    at.chat_input[0].set_value("Phạt xe máy vượt đèn đỏ bao nhiêu?").run()
    assert not at.exception
    assert any("Không tìm thấy thông tin này trong tài liệu" in m.value for m in at.markdown)
    assert at.info[0].value == "Bạn có muốn tìm trên internet không?"
    assert at.text_input(key="web_query_1").value == "mức phạt xe máy 2026"
    assert svc.web.queries == []  # chưa được tìm khi người dùng chưa đồng ý

    at.text_input(key="web_query_1").set_value("phạt xe máy vượt đèn đỏ")
    _button(at, "🌐 Tìm trên internet").click().run()
    assert not at.exception
    assert svc.web.queries == ["phạt xe máy vượt đèn đỏ"]
    assert any("Nguồn internet" in m.value for m in at.markdown)
    assert any("Theo [1], mức phạt" in m.value for m in at.markdown)
    assert not at.info  # lời mời đã đóng


def test_declining_web_search(app):
    at, svc = app
    at.chat_input[0].set_value("Câu hỏi ngoài tài liệu").run()
    _button(at, "Không, cảm ơn").click().run()
    assert svc.web.queries == []
    assert any("Đã bỏ qua" in m.value for m in at.markdown)
    assert not at.info


def test_found_answer_has_no_web_offer(app):
    at, svc = app
    svc.llm.json_result = {"coverage": "full", "answer": "Phạt 6-8 triệu đồng [1].", "citations": [1], "web_query": ""}
    at.chat_input[0].set_value("Phạt nồng độ cồn ô tô?").run()
    assert not at.exception
    assert any("Phạt 6-8 triệu đồng [1]." in m.value for m in at.markdown)
    assert not at.info
    assert [e.label for e in at.main.expander] == ["📎 Nguồn (1)"]


def test_delete_document_from_sidebar(app):
    at, svc = app
    doc = svc.db.list_documents(1)[0]
    at.button(key=f"del_{doc.id}").click().run()
    assert not at.exception
    assert svc.db.list_documents(1) == []
    assert at.chat_input[0].disabled


def test_create_collection_switches_to_it(app):
    at, svc = app
    at.sidebar.text_input[0].set_value("Thuế")
    next(b for b in at.sidebar.button if b.label == "Tạo bộ").click().run()
    assert not at.exception
    assert at.title[0].value == "💬 Thuế"
    assert {c.name for c in svc.db.list_collections()} == {"Pháp luật", "Thuế"}
    assert at.chat_input[0].disabled  # bộ mới chưa có tài liệu


def test_partial_answer_then_add_web_source(app, monkeypatch):
    at, svc = app
    svc.llm.json_result = {"coverage": "partial", "answer": "Ô tô: 200-400 nghìn [1].", "citations": [1],
                           "web_query": "phạt xe máy"}
    at.chat_input[0].set_value("Phạt ô tô và xe máy?").run()
    assert "một phần" in at.info[0].value
    _button(at, "🌐 Tìm trên internet").click().run()

    added = []

    def fake_ingest_url(cid, url, progress=None):
        added.append(url)
        return SimpleNamespace(document=SimpleNamespace(title="Trang web"))

    monkeypatch.setattr(svc.ingestor, "ingest_url", fake_ingest_url)
    _button(at, "➕ Thêm vào bộ tài liệu").click().run()
    assert not at.exception
    assert added == ["https://chinhphu.vn/x"]
    assert _button(at, "✅ Đã thêm").disabled


def test_delete_collection(app):
    at, svc = app
    at.sidebar.checkbox[0].check().run()
    next(b for b in at.sidebar.button if b.label == "Xóa bộ này").click().run()
    assert not at.exception
    assert svc.db.list_collections() == []
