"""Đăng nhập, phân quyền và tách dữ liệu giữa các tài khoản."""

import sqlite3

import pytest
from conftest import LEGAL_TEXT, FakeEmbedder, FakeLLM
from fastapi.testclient import TestClient

from ragbot.api import create_app
from ragbot.auth import SESSION_COOKIE, Accounts, LoginThrottle, hash_password, verify_password
from ragbot.config import Settings
from ragbot.ingest import Ingestor
from ragbot.retrieval import Retriever
from ragbot.services import Services
from ragbot.store import Database, VectorStore

PW = "matkhau123"


@pytest.fixture
def svc(tmp_path):
    db = Database(tmp_path / "ragbot.db")
    vectors = VectorStore(tmp_path / "chroma")
    emb, llm = FakeEmbedder(), FakeLLM()
    return Services(Settings(_env_file=None, data_dir=tmp_path), db, vectors, llm, emb,
                    Ingestor(db, vectors, emb, llm, tmp_path), Retriever(db, vectors, emb), None)


def client_for(svc, local_setup_only=False) -> TestClient:
    return TestClient(create_app(lambda: svc, local_setup_only=local_setup_only))


def login(svc, username, password=PW) -> TestClient:
    c = client_for(svc)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(svc):
    c = client_for(svc)
    assert c.post("/api/auth/setup", json={"username": "Admin", "password": PW}).status_code == 200
    return c


def test_password_hashing():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h) and not verify_password("sai-mat-khau", h)
    assert hash_password(PW) != h  # salt ngẫu nhiên
    assert not verify_password(PW, "rác") and not verify_password(PW, "bcrypt$1$2$3$4$5")


def test_status_and_first_admin_setup(svc):
    c = client_for(svc)
    assert c.get("/api/auth/status").json() == {"setup_required": True, "setup_allowed": True, "user": None}
    assert c.post("/api/auth/setup", json={"username": "a", "password": PW}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "bình", "password": PW}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "admin", "password": "ngan"}).status_code == 400

    r = c.post("/api/auth/setup", json={"username": " Admin ", "password": PW})
    assert r.status_code == 200 and r.json()["user"]["username"] == "admin" and r.json()["user"]["is_admin"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/auth/status").json()["user"]["username"] == "admin"
    # Chỉ tạo admin đầu tiên được một lần.
    assert client_for(svc).post("/api/auth/setup", json={"username": "hacker", "password": PW}).status_code == 409


def test_remote_first_setup_blocked(svc):
    c = client_for(svc, local_setup_only=True)  # TestClient không phải localhost
    assert c.get("/api/auth/status").json()["setup_allowed"] is False
    r = c.post("/api/auth/setup", json={"username": "admin", "password": PW})
    assert r.status_code == 403 and "create-user" in r.json()["detail"]
    assert svc.db.count_users() == 0


def test_protected_endpoints_require_login(svc, admin):
    anon = client_for(svc)
    for method, path in [("get", "/api/info"), ("get", "/api/collections"), ("post", "/api/collections"),
                         ("get", "/api/collections/1/documents"), ("post", "/api/web"), ("get", "/api/users")]:
        r = getattr(anon, method)(path, json={}) if method == "post" else anon.get(path)
        assert r.status_code == 401, (path, r.status_code)
    assert anon.get("/").status_code == 200  # trang giao diện (màn hình đăng nhập) vẫn mở được
    anon.cookies.set(SESSION_COOKIE, "forged-token")
    assert anon.get("/api/collections").status_code == 401


def test_login_logout(svc, admin):
    c = client_for(svc)
    bad = c.post("/api/auth/login", json={"username": "admin", "password": "sai"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu"
    assert c.post("/api/auth/login", json={"username": "khongco", "password": PW}).json()["detail"] == \
        "Sai tên đăng nhập hoặc mật khẩu"  # không lộ tên đăng nhập nào có thật

    c = login(svc, "ADMIN")  # không phân biệt hoa thường
    assert c.get("/api/collections").status_code == 200
    token = c.cookies.get(SESSION_COOKIE)
    assert c.post("/api/auth/logout").status_code == 200
    stolen = client_for(svc)
    stolen.cookies.set(SESSION_COOKIE, token)
    assert stolen.get("/api/collections").status_code == 401  # phiên đã hủy phía server


def test_login_throttled_after_repeated_failures(svc, admin):
    c = client_for(svc)
    for _ in range(LoginThrottle.MAX_FAILURES):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "sai"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": PW})
    assert r.status_code == 429 and "Thử lại sau" in r.json()["detail"]


def test_cross_site_write_blocked(admin):
    r = admin.post("/api/collections", json={"name": "X"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert admin.post("/api/collections", json={"name": "X"}, headers={"Origin": "http://testserver"}).status_code == 201


def test_admin_manages_users_and_regular_user_cannot(svc, admin):
    assert admin.post("/api/users", json={"username": "binh", "password": PW}).status_code == 201
    assert admin.post("/api/users", json={"username": "BINH", "password": PW}).status_code == 400  # trùng tên
    users = {u["username"]: u for u in admin.get("/api/users").json()}
    assert set(users) == {"admin", "binh"} and not users["binh"]["is_admin"]

    binh = login(svc, "binh")
    assert binh.get("/api/users").status_code == 403
    assert binh.post("/api/users", json={"username": "x" * 5, "password": PW}).status_code == 403
    assert admin.delete(f"/api/users/{users['admin']['id']}").status_code == 400  # không tự xóa mình


def test_collections_are_private_per_user(svc, admin):
    admin.post("/api/users", json={"username": "binh", "password": PW})
    binh = login(svc, "binh")
    mine = admin.post("/api/collections", json={"name": "Pháp luật"}).json()["id"]
    events = admin.post(f"/api/collections/{mine}/files", files=[("files", ("nd.txt", LEGAL_TEXT.encode(), "text/plain"))])
    doc_id = [line for line in events.text.splitlines() if '"item_done"' in line][0].split('"id": "')[1].split('"')[0]

    # Cùng tên bộ tài liệu vẫn được, vì mỗi người một không gian riêng.
    theirs = binh.post("/api/collections", json={"name": "Pháp luật"})
    assert theirs.status_code == 201
    assert [c["name"] for c in binh.get("/api/collections").json()] == ["Pháp luật"]
    assert binh.get("/api/collections").json()[0]["id"] != mine

    for method, path, kwargs in [
        ("get", f"/api/collections/{mine}/documents", {}),
        ("post", f"/api/collections/{mine}/ask", {"json": {"question": "Phạt?"}}),
        ("post", f"/api/collections/{mine}/urls", {"json": {"urls": ["https://a.vn"]}}),
        ("post", f"/api/collections/{mine}/reindex", {}),
        ("delete", f"/api/collections/{mine}/documents/{doc_id}", {}),
        ("delete", f"/api/collections/{mine}", {}),
    ]:
        assert getattr(binh, method)(path, **kwargs).status_code == 404, path
    # Không xóa chéo được tài liệu của bộ khác qua bộ của mình.
    theirs_id = theirs.json()["id"]
    assert binh.delete(f"/api/collections/{theirs_id}/documents/{doc_id}").status_code == 404
    assert len(admin.get(f"/api/collections/{mine}/documents").json()) == 1


def test_delete_user_removes_their_data(svc, admin, tmp_path):
    admin.post("/api/users", json={"username": "binh", "password": PW})
    binh = login(svc, "binh")
    cid = binh.post("/api/collections", json={"name": "Riêng"}).json()["id"]
    binh.post(f"/api/collections/{cid}/files", files=[("files", ("nd.txt", LEGAL_TEXT.encode(), "text/plain"))])
    uid = next(u["id"] for u in admin.get("/api/users").json() if u["username"] == "binh")

    assert admin.delete(f"/api/users/{uid}").status_code == 204
    assert binh.get("/api/collections").status_code == 401  # phiên của người bị xóa hết hiệu lực
    assert svc.db.get_collection(cid) is None
    assert not list((tmp_path / "texts").iterdir())


def test_change_and_reset_password(svc, admin):
    admin.post("/api/users", json={"username": "binh", "password": PW})
    binh = login(svc, "binh")
    other_device = login(svc, "binh")
    assert binh.post("/api/auth/password", json={"current_password": "sai", "new_password": "moi12345"}).status_code == 400
    assert binh.post("/api/auth/password", json={"current_password": PW, "new_password": "moi12345"}).status_code == 200
    assert binh.get("/api/collections").status_code == 200  # phiên hiện tại giữ nguyên
    assert other_device.get("/api/collections").status_code == 401  # phiên khác bị đăng xuất
    login(svc, "binh", "moi12345")

    uid = next(u["id"] for u in admin.get("/api/users").json() if u["username"] == "binh")
    assert admin.post(f"/api/users/{uid}/password", json={"new_password": "ngan"}).status_code == 400
    assert admin.post(f"/api/users/{uid}/password", json={"new_password": "admindat1"}).status_code == 200
    assert binh.get("/api/collections").status_code == 401
    login(svc, "binh", "admindat1")


def test_legacy_db_migrated_and_claimed_by_first_admin(tmp_path):
    path = tmp_path / "ragbot.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, embed_model TEXT,
                                  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO collections (name, embed_model) VALUES ('Pháp luật', 'gemini:x'), ('Thuế', NULL);
    """)
    old.close()

    db = Database(path)
    assert [(c.name, c.owner_id) for c in db.list_collections()] == [("Pháp luật", None), ("Thuế", None)]
    acc = Accounts(db)
    admin = acc.create("admin", PW, is_admin=True)
    assert db.claim_orphan_collections(admin.id) == 2
    assert [c.name for c in db.list_collections(admin.id)] == ["Pháp luật", "Thuế"]
    assert db.list_collections(admin.id)[0].embed_model == "gemini:x"
    Database(path)  # mở lại lần nữa không chuyển đổi lại
