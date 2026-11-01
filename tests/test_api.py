"""API của giao diện web, chạy với provider giả lập."""

import json

import pytest
from conftest import LEGAL_TEXT, FakeEmbedder, FakeLLM
from fastapi.testclient import TestClient

from ragbot.api import create_app
from ragbot.config import Settings
from ragbot.ingest import Ingestor
from ragbot.providers import MissingKeyError
from ragbot.retrieval import Retriever
from ragbot.services import Services
from ragbot.store import Database, VectorStore
from ragbot.websearch import WebResult

NOT_FOUND = {"coverage": "none", "answer": "", "citations": [], "web_query": "mức phạt xe máy"}


class FakeSearch:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return [WebResult("Cổng TTĐT Chính phủ", "https://chinhphu.vn/x", "Mức phạt xe máy ...")]


def events(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


@pytest.fixture
def ctx(tmp_path):
    db = Database(tmp_path / "ragbot.db")
    vectors = VectorStore(tmp_path / "chroma")
    embedder, llm, web = FakeEmbedder(), FakeLLM(NOT_FOUND, text="Theo [1], mức phạt là ..."), FakeSearch()
    ingestor = Ingestor(db, vectors, embedder, llm, tmp_path)
    svc = Services(Settings(_env_file=None, data_dir=tmp_path), db, vectors, llm, embedder, ingestor,
                   Retriever(db, vectors, embedder), web)
    client = TestClient(create_app(lambda: svc, local_setup_only=False))
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "matkhau123"}).status_code == 200
    cid = client.post("/api/collections", json={"name": "Pháp luật"}).json()["id"]
    return client, svc, cid


def _upload_legal(client, cid):
    return events(client.post(f"/api/collections/{cid}/files",
                              files=[("files", ("nd100.txt", LEGAL_TEXT.encode(), "text/plain"))]))


def test_serves_web_ui(ctx):
    client, _, _ = ctx
    assert "<title>RAG Bot</title>" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_info(ctx):
    client, _, _ = ctx
    assert client.get("/api/info").json() == {"llm": "fake:llm", "embedder": "fake:bow:64", "web_search": True}


def test_collection_crud(ctx):
    client, _, cid = ctx
    assert client.post("/api/collections", json={"name": "Pháp luật"}).status_code == 409
    assert client.post("/api/collections", json={"name": "  "}).status_code == 400
    other = client.post("/api/collections", json={"name": "Thuế"}).json()
    names = {c["name"]: c for c in client.get("/api/collections").json()}
    assert set(names) == {"Pháp luật", "Thuế"} and names["Thuế"]["documents"] == 0
    assert client.delete(f"/api/collections/{other['id']}").status_code == 204
    assert [c["id"] for c in client.get("/api/collections").json()] == [cid]
    assert client.delete("/api/collections/999").status_code == 404


def test_upload_streams_progress_then_lists_document(ctx):
    client, _, cid = ctx
    evs = _upload_legal(client, cid)
    types = [e["type"] for e in evs]
    assert types[0] == "start" and "progress" in types and types[-2:] == ["item_done", "done"]
    assert evs[-1] == {"type": "done", "ok": 1, "total": 1}
    assert evs[-2]["document"]["n_chunks"] == 5

    docs = client.get(f"/api/collections/{cid}/documents").json()
    assert [d["title"] for d in docs] == ["nd100"]
    assert client.get("/api/collections").json()[0]["documents"] == 1

    dup = _upload_legal(client, cid)
    assert dup[-2]["type"] == "item_error" and "đã có" in dup[-2]["message"]
    assert dup[-1]["ok"] == 0


def test_add_urls_reports_per_item_errors(ctx, monkeypatch):
    client, svc, cid = ctx

    def fake_ingest_url(cid_, url, progress):
        progress("OCR 1/2 trang")
        raise RuntimeError("trang chặn bot")

    monkeypatch.setattr(svc.ingestor, "ingest_url", fake_ingest_url)
    evs = events(client.post(f"/api/collections/{cid}/urls", json={"urls": ["https://a.vn/x", "  "]}))
    assert [e["type"] for e in evs] == ["start", "progress", "item_error", "done"]
    assert evs[1]["ratio"] == 0.5
    assert "trang chặn bot" in evs[2]["message"]
    assert client.post(f"/api/collections/{cid}/urls", json={"urls": [" "]}).status_code == 400


def test_ask_not_found_offers_web_without_searching(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    evs = events(client.post(f"/api/collections/{cid}/ask", json={"question": "Phạt xe máy?", "history": []}))
    assert any(e["type"] == "status" for e in evs)
    answer = evs[-1]["answer"]
    assert evs[-1]["type"] == "answer" and answer["coverage"] == "none" and answer["offer_web"] is True
    assert answer["web_query"] == "mức phạt xe máy"
    assert svc.web.queries == []


def test_ask_full_answer_with_sources(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    svc.llm.json_result = {"coverage": "full", "answer": "Phạt 6-8 triệu [1].", "citations": [1], "web_query": ""}
    history = [{"role": "user", "content": "Câu trước"}, {"role": "assistant", "content": "Trả lời trước"}]
    evs = events(client.post(f"/api/collections/{cid}/ask", json={"question": "còn ô tô?", "history": history}))
    answer = evs[-1]["answer"]
    assert answer["offer_web"] is False and answer["sources"][0]["n"] == 1
    assert "Đang hiểu câu hỏi" in evs[0]["message"]  # có lịch sử nên có bước viết lại câu hỏi


def test_web_search(ctx):
    client, svc, _ = ctx
    evs = events(client.post("/api/web", json={"question": "Phạt xe máy?", "query": "phạt xe máy 2026"}))
    assert svc.web.queries == ["phạt xe máy 2026"]
    answer = evs[-1]["answer"]
    assert answer["kind"] == "web" and answer["sources"][0]["url"] == "https://chinhphu.vn/x"


def test_web_search_without_tavily_key(ctx):
    client, svc, _ = ctx
    svc.web = None
    resp = client.post("/api/web", json={"question": "q", "query": "q"})
    assert resp.status_code == 503 and "TAVILY_API_KEY" in resp.json()["detail"]


def test_llm_error_is_streamed_as_error_event(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)

    def boom(*args, **kwargs):
        raise MissingKeyError("Chưa có WELLFLOW_API_KEY trong file .env")

    svc.llm.complete_json = boom
    evs = events(client.post(f"/api/collections/{cid}/ask", json={"question": "Phạt?"}))
    assert evs[-1] == {"type": "error", "message": "Chưa có WELLFLOW_API_KEY trong file .env"}


def test_delete_document(ctx):
    client, _, cid = ctx
    doc_id = _upload_legal(client, cid)[-2]["document"]["id"]
    assert client.delete(f"/api/collections/{cid}/documents/{doc_id}").status_code == 204
    assert client.get(f"/api/collections/{cid}/documents").json() == []


def test_ask_blocked_when_reindex_needed(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    svc.db.set_embed_model(cid, "other:model")
    assert client.get("/api/collections").json()[0]["needs_reindex"] is True
    assert client.post(f"/api/collections/{cid}/ask", json={"question": "q"}).status_code == 409
    evs = events(client.post(f"/api/collections/{cid}/reindex"))
    assert evs[-1] == {"type": "done", "errors": []}
    assert client.get("/api/collections").json()[0]["needs_reindex"] is False


def test_missing_key_reported_by_api():
    def factory():
        raise MissingKeyError("Chưa có GEMINI_API_KEY trong file .env")

    resp = TestClient(create_app(factory)).get("/api/info")
    assert resp.status_code == 503 and "GEMINI_API_KEY" in resp.json()["detail"]
