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


def new_conversation(client, cid, title="Phạt xe máy?") -> int:
    resp = client.post(f"/api/collections/{cid}/conversations", json={"title": title})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def ask(client, conv, question):
    return events(client.post(f"/api/conversations/{conv}/ask", json={"question": question}))


def test_ask_not_found_offers_web_without_searching(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    evs = ask(client, new_conversation(client, cid), "Phạt xe máy?")
    assert any(e["type"] == "status" for e in evs)
    msg = evs[-1]["message"]
    assert evs[-1]["type"] == "answer" and msg["coverage"] == "none"
    assert msg["offer"] == {"query": "mức phạt xe máy", "state": "open"}
    assert svc.web.queries == []


def test_conversation_history_saved_on_server(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    svc.llm.json_result = {"coverage": "full", "answer": "Phạt 6-8 triệu [1].", "citations": [1], "web_query": ""}
    conv = new_conversation(client, cid, "  Mức phạt   nồng độ cồn " + "x" * 100)
    first = ask(client, conv, "Phạt nồng độ cồn?")
    assert not any("Đang hiểu câu hỏi" in e.get("message", "") for e in first)
    assert first[-1]["message"]["sources"][0]["n"] == 1

    # Câu hỏi nối tiếp: server tự lấy lịch sử đã lưu để viết lại câu hỏi.
    second = ask(client, conv, "còn ô tô?")
    assert "Đang hiểu câu hỏi" in second[0]["message"]
    assert "Phạt nồng độ cồn?" in svc.llm.complete_calls[-1] and "Phạt 6-8 triệu" in svc.llm.complete_calls[-1]

    saved = client.get(f"/api/conversations/{conv}").json()
    assert [(m["role"], m["text"]) for m in saved["messages"]] == [
        ("user", "Phạt nồng độ cồn?"), ("assistant", "Phạt 6-8 triệu [1]."),
        ("user", "còn ô tô?"), ("assistant", "Phạt 6-8 triệu [1]."),
    ]
    assert saved["messages"][1]["sources"][0]["title"] == "nd100"
    listed = client.get(f"/api/collections/{cid}/conversations").json()
    assert [(c["id"], c["n_messages"]) for c in listed] == [(conv, 4)]
    assert listed[0]["title"].startswith("Mức phạt nồng độ cồn x") and listed[0]["title"].endswith("…")


def test_conversations_listed_newest_first_and_deleted(ctx):
    client, _, cid = ctx
    _upload_legal(client, cid)
    old, new = new_conversation(client, cid, "Cũ"), new_conversation(client, cid, "Mới")
    assert [c["title"] for c in client.get(f"/api/collections/{cid}/conversations").json()] == ["Mới", "Cũ"]
    assert client.post(f"/api/collections/{cid}/conversations", json={}).json()["title"] == "Cuộc trò chuyện mới"

    assert client.delete(f"/api/conversations/{old}").status_code == 204
    assert client.get(f"/api/conversations/{old}").status_code == 404
    assert new in [c["id"] for c in client.get(f"/api/collections/{cid}/conversations").json()]


def test_new_question_expires_open_web_offer(ctx):
    client, _, cid = ctx
    _upload_legal(client, cid)
    conv = new_conversation(client, cid)
    offer_id = ask(client, conv, "Phạt xe máy?")[-1]["message"]["id"]
    ask(client, conv, "Còn xe đạp?")
    states = [m["offer"]["state"] for m in client.get(f"/api/conversations/{conv}").json()["messages"] if m.get("offer")]
    assert states == ["expired", "open"]
    assert client.post(f"/api/conversations/{conv}/messages/{offer_id}/web", json={"query": "x"}).status_code == 409


def test_web_search_after_user_agrees(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    conv = new_conversation(client, cid)
    offer_id = ask(client, conv, "Phạt xe máy?")[-1]["message"]["id"]
    evs = events(client.post(f"/api/conversations/{conv}/messages/{offer_id}/web", json={"query": "phạt xe máy 2026"}))
    assert svc.web.queries == ["phạt xe máy 2026"]
    msg = evs[-1]["message"]
    assert msg["kind"] == "web" and msg["sources"][0]["url"] == "https://chinhphu.vn/x" and msg["offer"] is None

    saved = client.get(f"/api/conversations/{conv}").json()["messages"]
    assert saved[1]["offer"] == {"query": "phạt xe máy 2026", "state": "done"} and saved[2]["kind"] == "web"
    # Lời mời đã dùng thì không tìm lại được.
    assert client.post(f"/api/conversations/{conv}/messages/{offer_id}/web", json={}).status_code == 409


def test_decline_web_offer(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    conv = new_conversation(client, cid)
    offer_id = ask(client, conv, "Phạt?")[-1]["message"]["id"]
    assert client.post(f"/api/conversations/{conv}/messages/{offer_id}/decline").status_code == 204
    question, offer = client.get(f"/api/conversations/{conv}").json()["messages"]
    assert offer["offer"]["state"] == "declined"
    assert client.post(f"/api/conversations/{conv}/messages/{offer_id}/web", json={}).status_code == 409
    # Tin nhắn không có lời mời tìm web.
    assert client.post(f"/api/conversations/{conv}/messages/{question['id']}/decline").status_code == 404
    assert svc.web.queries == []


def test_web_search_without_tavily_key(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)
    conv = new_conversation(client, cid)
    offer_id = ask(client, conv, "Phạt?")[-1]["message"]["id"]
    svc.web = None
    resp = client.post(f"/api/conversations/{conv}/messages/{offer_id}/web", json={"query": "q"})
    assert resp.status_code == 503 and "TAVILY_API_KEY" in resp.json()["detail"]


def test_llm_error_is_streamed_and_saved(ctx):
    client, svc, cid = ctx
    _upload_legal(client, cid)

    def boom(*args, **kwargs):
        raise MissingKeyError("Chưa có WELLFLOW_API_KEY trong file .env")

    svc.llm.complete_json = boom
    conv = new_conversation(client, cid)
    evs = ask(client, conv, "Phạt?")
    assert evs[-1] == {"type": "error", "message": "Chưa có WELLFLOW_API_KEY trong file .env"}
    saved = client.get(f"/api/conversations/{conv}").json()["messages"]
    assert saved[-1] == {"id": saved[-1]["id"], "role": "assistant", "kind": "error",
                         "text": "Chưa có WELLFLOW_API_KEY trong file .env", "question": "Phạt?"}


def test_import_history_from_browser(ctx):
    client, _, cid = ctx
    old = [
        {"id": "abc", "role": "user", "text": "Phạt?"},
        {"id": "def", "role": "assistant", "kind": "web", "coverage": "full", "text": "Theo [1]...", "question": "Phạt?",
         "sources": [{"n": 1, "title": "Báo", "location": "a.vn", "excerpt": "...", "url": "https://a.vn", "added": True}],
         "offer": None, "lạ": "bỏ qua"},
    ]
    conv = client.post(f"/api/collections/{cid}/conversations", json={"title": "Phạt?", "messages": old}).json()
    assert conv["n_messages"] == 2
    msgs = client.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert msgs[0] == {"id": msgs[0]["id"], "role": "user", "text": "Phạt?"}
    assert "lạ" not in msgs[1] and "added" not in msgs[1]["sources"][0] and isinstance(msgs[1]["id"], int)
    bad = client.post(f"/api/collections/{cid}/conversations", json={"messages": [{"role": "system", "text": "x"}]})
    assert bad.status_code == 422


def test_deleting_collection_deletes_its_conversations(ctx):
    client, svc, cid = ctx
    conv = new_conversation(client, cid)
    client.delete(f"/api/collections/{cid}")
    assert client.get(f"/api/conversations/{conv}").status_code == 404
    assert svc.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


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
    conv = new_conversation(client, cid)
    assert client.post(f"/api/conversations/{conv}/ask", json={"question": "q"}).status_code == 409
    evs = events(client.post(f"/api/collections/{cid}/reindex"))
    assert evs[-1] == {"type": "done", "errors": []}
    assert client.get("/api/collections").json()[0]["needs_reindex"] is False


def test_missing_key_reported_by_api():
    def factory():
        raise MissingKeyError("Chưa có GEMINI_API_KEY trong file .env")

    resp = TestClient(create_app(factory)).get("/api/info")
    assert resp.status_code == 503 and "GEMINI_API_KEY" in resp.json()["detail"]
