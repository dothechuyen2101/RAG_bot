"""API cho giao diện web: đăng nhập, quản lý tài khoản, bộ tài liệu, nạp tài liệu, hỏi đáp.

Mỗi tài khoản chỉ thấy bộ tài liệu của mình. Admin thêm quản lý tài khoản, nhưng cũng không xem được
tài liệu của người khác. Hỏi đáp diễn ra trong các cuộc trò chuyện của từng bộ tài liệu; server tự lưu
câu hỏi và câu trả lời, nên lịch sử còn nguyên khi đổi trình duyệt hay máy.

Thao tác chạy lâu (nạp tài liệu, re-index, trả lời) trả về NDJSON: mỗi dòng là một sự kiện JSON
để giao diện hiện tiến trình ngay, dòng cuối là kết quả ({"type": "done"} / {"type": "answer"})
hoặc lỗi ({"type": "error"}).
"""

import json
import queue
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .answer import Answer, answer_from_docs, answer_from_web
from .auth import SESSION_COOKIE, Accounts, AuthError, TooManyAttempts
from .config import get_settings
from .ingest import DuplicateError, EmbedModelMismatch, IngestResult, LoadError
from .providers import MissingKeyError
from .providers.base import QuotaExhaustedError
from .services import Services, build_services
from .store import Collection, Conversation, User

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TITLE_CHARS = 80
OCR_PROGRESS = re.compile(r"OCR (\d+)/(\d+)")
# Lỗi người dùng tự xử lý được: hiện nguyên thông báo, không kèm tên lớp lỗi.
USER_ERRORS = (LoadError, DuplicateError, EmbedModelMismatch, MissingKeyError, QuotaExhaustedError)
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

Emit = Callable[[dict], None]


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(Credentials):
    is_admin: bool = False


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordReset(BaseModel):
    new_password: str


class NewCollection(BaseModel):
    name: str


class UrlList(BaseModel):
    urls: list[str]


class SavedSource(BaseModel):
    n: int
    title: str = ""
    location: str = ""
    excerpt: str = ""
    url: str = ""


class SavedOffer(BaseModel):
    query: str = ""
    state: Literal["open", "done", "declined", "expired"]


class SavedMessage(BaseModel):
    """Tin nhắn nhập từ lịch sử cũ lưu trong trình duyệt; trường lạ bị bỏ qua."""
    role: Literal["user", "assistant"]
    text: str = ""
    kind: Literal["docs", "web", "error"] | None = None
    coverage: Literal["full", "partial", "none"] | None = None
    question: str | None = None
    sources: list[SavedSource] | None = None
    offer: SavedOffer | None = None


class NewConversation(BaseModel):
    title: str = ""
    messages: list[SavedMessage] = Field(default=[], max_length=500)


class Question(BaseModel):
    question: str


class WebQuery(BaseModel):
    query: str = ""


def friendly_error(e: Exception) -> str:
    return str(e) if isinstance(e, USER_ERRORS) else f"Lỗi {type(e).__name__}: {e}"


def conversation_title(text: str) -> str:
    text = " ".join(text.split())
    return (text[:TITLE_CHARS] + ("…" if len(text) > TITLE_CHARS else "")) or "Cuộc trò chuyện mới"


def answer_message(ans: Answer) -> dict:
    return {
        "role": "assistant", "kind": ans.kind, "coverage": ans.coverage, "text": ans.text, "question": ans.question,
        "sources": [asdict(s) for s in ans.sources],
        "offer": {"query": ans.web_query, "state": "open"} if ans.offer_web else None,
    }


def history_of(messages: list[dict]) -> list[dict]:
    """Các lượt hỏi đáp đã có (bỏ thông báo lỗi), để hiểu câu hỏi nối tiếp như "còn ô tô thì sao?"."""
    return [{"role": m["role"], "content": m["text"]} for m in messages
            if m.get("text") and (m["role"] == "user" or m.get("kind") in ("docs", "web"))]


def user_json(user: User | None) -> dict | None:
    return asdict(user) if user else None


def stream(work: Callable[[Emit], dict]) -> StreamingResponse:
    """Chạy work(emit) ở thread riêng, gửi từng sự kiện emit() rồi kết quả cuối về dạng NDJSON."""
    events: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            events.put(work(events.put))
        except Exception as e:
            events.put({"type": "error", "message": friendly_error(e)})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()

    def lines():
        while (event := events.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


def create_app(services_factory: Callable[[], Services] | None = None, local_setup_only: bool = True) -> FastAPI:
    """local_setup_only: chỉ cho tạo tài khoản admin đầu tiên từ chính máy chạy server."""
    app = FastAPI(title="RAG Bot", docs_url="/api/docs", openapi_url="/api/openapi.json")
    factory = services_factory or (lambda: build_services(get_settings()))
    lock = threading.Lock()
    setup_lock = threading.Lock()
    cache: dict = {}

    def svc() -> Services:
        with lock:
            if "svc" not in cache:
                try:
                    cache["svc"] = factory()
                except MissingKeyError as e:
                    raise HTTPException(503, f"{e}. Điền vào file .env rồi khởi động lại server.") from e
                cache["accounts"] = Accounts(cache["svc"].db)
            return cache["svc"]

    def accounts() -> Accounts:
        svc()
        return cache["accounts"]

    # ---------------- bảo vệ ----------------

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # Cookie đã SameSite=Lax; chặn thêm request ghi dữ liệu gửi từ trang web khác (CSRF).
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Yêu cầu từ trang web khác bị chặn"}, status_code=403)
        return await call_next(request)

    def current_user(request: Request) -> User:
        user = accounts().user_for_token(request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise HTTPException(401, "Chưa đăng nhập hoặc phiên đăng nhập đã hết hạn")
        return user

    def admin_user(user: User = Depends(current_user)) -> User:
        if not user.is_admin:
            raise HTTPException(403, "Chỉ admin mới làm được việc này")
        return user

    def own_collection(cid: int, user: User) -> Collection:
        col = svc().db.get_collection(cid)
        # Bộ của người khác cũng trả 404 để không lộ là nó có tồn tại.
        if col is None or col.owner_id != user.id:
            raise HTTPException(404, "Bộ tài liệu không tồn tại")
        return col

    def setup_allowed(request: Request) -> bool:
        return not local_setup_only or (request.client is not None and request.client.host in LOCAL_HOSTS)

    def start_session(request: Request, response: Response, user: User) -> dict:
        session = accounts().start_session(user)
        response.set_cookie(
            SESSION_COOKIE, session.token, max_age=session.max_age, path="/",
            httponly=True, samesite="lax", secure=request.url.scheme == "https",
        )
        return {"user": user_json(user)}

    # ---------------- đăng nhập ----------------

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        acc = accounts()
        return {
            "setup_required": acc.setup_required(),
            "setup_allowed": setup_allowed(request),
            "user": user_json(acc.user_for_token(request.cookies.get(SESSION_COOKIE))),
        }

    @app.post("/api/auth/setup")
    def setup(body: Credentials, request: Request, response: Response):
        acc = accounts()
        with setup_lock:
            if not acc.setup_required():
                raise HTTPException(409, "Đã có tài khoản, hãy đăng nhập")
            if not setup_allowed(request):
                raise HTTPException(403, "Chỉ tạo được tài khoản admin đầu tiên khi mở trang trên chính máy chạy server "
                                         "(localhost), hoặc dùng lệnh: python server.py create-user <tên> --admin")
            try:
                user = acc.create(body.username, body.password, is_admin=True)
            except AuthError as e:
                raise HTTPException(400, str(e)) from None
            svc().db.claim_orphan_collections(user.id)
        return start_session(request, response, user)

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        try:
            user = accounts().authenticate(body.username, body.password, client)
        except TooManyAttempts as e:
            raise HTTPException(429, str(e)) from None
        except AuthError as e:
            raise HTTPException(401, str(e)) from None
        return start_session(request, response, user)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        accounts().end_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request, user: User = Depends(current_user)):
        try:
            accounts().change_password(user, body.current_password, body.new_password,
                                       keep_token=request.cookies.get(SESSION_COOKIE))
        except AuthError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    # ---------------- quản lý tài khoản (admin) ----------------

    @app.get("/api/users")
    def list_users(_: User = Depends(admin_user)):
        db = svc().db
        return [{**asdict(u), "collections": len(db.list_collections(u.id))} for u in db.list_users()]

    @app.post("/api/users", status_code=201)
    def create_user(body: NewUser, _: User = Depends(admin_user)):
        try:
            user = accounts().create(body.username, body.password, body.is_admin)
        except AuthError as e:
            raise HTTPException(400, str(e)) from None
        return {**asdict(user), "collections": 0}

    @app.delete("/api/users/{uid}", status_code=204)
    def delete_user(uid: int, admin: User = Depends(admin_user)):
        s = svc()
        if uid == admin.id:
            raise HTTPException(400, "Không thể tự xóa tài khoản đang đăng nhập")
        if s.db.get_user(uid) is None:
            raise HTTPException(404, "Tài khoản không tồn tại")
        for col in s.db.list_collections(uid):
            s.ingestor.delete_collection(col.id)
        s.db.delete_user(uid)
        return Response(status_code=204)

    @app.post("/api/users/{uid}/password")
    def reset_password(uid: int, body: PasswordReset, _: User = Depends(admin_user)):
        if svc().db.get_user(uid) is None:
            raise HTTPException(404, "Tài khoản không tồn tại")
        try:
            accounts().reset_password(uid, body.new_password)
        except AuthError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    # ---------------- thông tin & bộ tài liệu ----------------

    @app.get("/api/info")
    def info(_: User = Depends(current_user)):
        s = svc()
        return {"llm": s.llm.name, "embedder": s.embedder.model_id, "web_search": s.web is not None}

    @app.get("/api/collections")
    def list_collections(user: User = Depends(current_user)):
        s = svc()
        return [
            {"id": c.id, "name": c.name, "embed_model": c.embed_model,
             "documents": len(s.db.list_documents(c.id)), "needs_reindex": s.ingestor.needs_reindex(c.id)}
            for c in s.db.list_collections(user.id)
        ]

    @app.post("/api/collections", status_code=201)
    def create_collection(body: NewCollection, user: User = Depends(current_user)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Tên bộ tài liệu không được để trống")
        try:
            col = svc().db.create_collection(name, owner_id=user.id)
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Bạn đã có bộ tài liệu tên '{name}'") from None
        return {"id": col.id, "name": col.name, "embed_model": None, "documents": 0, "needs_reindex": False}

    @app.delete("/api/collections/{cid}", status_code=204)
    def delete_collection(cid: int, user: User = Depends(current_user)):
        own_collection(cid, user)
        svc().ingestor.delete_collection(cid)
        return Response(status_code=204)

    @app.post("/api/collections/{cid}/reindex")
    def reindex(cid: int, user: User = Depends(current_user)):
        own_collection(cid, user)
        ingestor = svc().ingestor

        def work(emit: Emit) -> dict:
            errors = ingestor.reindex_collection(cid, lambda m: emit({"type": "progress", "message": m}))
            return {"type": "done", "errors": errors}

        return stream(work)

    # ---------------- tài liệu ----------------

    def ingest_items(items: list[tuple[str, Callable[[Callable[[str], None]], IngestResult]]]):
        def work(emit: Emit) -> dict:
            ok = 0
            for label, run in items:
                emit({"type": "start", "item": label})

                def progress(message: str, label=label) -> None:
                    event = {"type": "progress", "item": label, "message": message}
                    if m := OCR_PROGRESS.search(message):
                        event["ratio"] = int(m.group(1)) / int(m.group(2))
                    emit(event)

                try:
                    result = run(progress)
                    ok += 1
                    emit({"type": "item_done", "item": label, "document": asdict(result.document),
                          "n_ocr_pages": result.n_ocr_pages})
                except Exception as e:
                    emit({"type": "item_error", "item": label, "message": friendly_error(e)})
            return {"type": "done", "ok": ok, "total": len(items)}

        return stream(work)

    @app.get("/api/collections/{cid}/documents")
    def list_documents(cid: int, user: User = Depends(current_user)):
        own_collection(cid, user)
        return [asdict(d) for d in svc().db.list_documents(cid)]

    @app.delete("/api/collections/{cid}/documents/{doc_id}", status_code=204)
    def delete_document(cid: int, doc_id: str, user: User = Depends(current_user)):
        own_collection(cid, user)
        if not any(d.id == doc_id for d in svc().db.list_documents(cid)):
            raise HTTPException(404, "Tài liệu không tồn tại")
        svc().ingestor.delete_document(cid, doc_id)
        return Response(status_code=204)

    @app.post("/api/collections/{cid}/files")
    async def upload_files(cid: int, files: list[UploadFile] = File(...), user: User = Depends(current_user)):
        own_collection(cid, user)
        ingestor = svc().ingestor
        uploads = [(f.filename or "file", await f.read()) for f in files]
        return ingest_items([
            (name, lambda p, name=name, data=data: ingestor.ingest_file(cid, name, data, p)) for name, data in uploads
        ])

    @app.post("/api/collections/{cid}/urls")
    def add_urls(cid: int, body: UrlList, user: User = Depends(current_user)):
        own_collection(cid, user)
        ingestor = svc().ingestor
        urls = [u.strip() for u in body.urls if u.strip()]
        if not urls:
            raise HTTPException(400, "Chưa có link nào")
        return ingest_items([(u, lambda p, u=u: ingestor.ingest_url(cid, u, p)) for u in urls])

    # ---------------- cuộc trò chuyện & hỏi đáp ----------------

    def own_conversation(conv_id: int, user: User) -> Conversation:
        db = svc().db
        conv = db.get_conversation(conv_id)
        if conv is None or db.get_collection(conv.collection_id).owner_id != user.id:
            raise HTTPException(404, "Cuộc trò chuyện không tồn tại")
        return conv

    def offer_message(conv_id: int, mid: int) -> dict:
        msg = svc().db.get_message(conv_id, mid)
        if msg is None or not msg.get("offer"):
            raise HTTPException(404, "Không có lời mời tìm trên internet này")
        if msg["offer"]["state"] != "open":
            raise HTTPException(409, "Lời mời tìm trên internet này đã đóng")
        return msg

    @app.get("/api/collections/{cid}/conversations")
    def list_conversations(cid: int, user: User = Depends(current_user)):
        own_collection(cid, user)
        return [asdict(c) for c in svc().db.list_conversations(cid)]

    @app.post("/api/collections/{cid}/conversations", status_code=201)
    def create_conversation(cid: int, body: NewConversation, user: User = Depends(current_user)):
        own_collection(cid, user)
        db = svc().db
        conv = db.create_conversation(cid, conversation_title(body.title))
        for m in body.messages:
            db.add_message(conv.id, m.model_dump(exclude_none=True))
        return asdict(db.get_conversation(conv.id))

    @app.get("/api/conversations/{conv_id}")
    def get_conversation(conv_id: int, user: User = Depends(current_user)):
        conv = own_conversation(conv_id, user)
        return {**asdict(conv), "messages": svc().db.list_messages(conv_id)}

    @app.delete("/api/conversations/{conv_id}", status_code=204)
    def delete_conversation(conv_id: int, user: User = Depends(current_user)):
        own_conversation(conv_id, user)
        svc().db.delete_conversation(conv_id)
        return Response(status_code=204)

    @app.post("/api/conversations/{conv_id}/ask")
    def ask(conv_id: int, body: Question, user: User = Depends(current_user)):
        conv = own_conversation(conv_id, user)
        s = svc()
        if s.ingestor.needs_reindex(conv.collection_id):
            raise HTTPException(409, "Bộ tài liệu cần re-index trước khi hỏi")
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Câu hỏi trống")
        history = history_of(s.db.list_messages(conv_id))
        # Đã sang câu hỏi mới thì các lời mời tìm web trước đó hết hiệu lực.
        s.db.set_offer_state(conv_id, "expired")
        s.db.add_message(conv_id, {"role": "user", "text": question})

        def work(emit: Emit) -> dict:
            try:
                ans = answer_from_docs(
                    s.llm, s.retriever, conv.collection_id, question, history, s.settings.top_k,
                    s.settings.min_similarity, progress=lambda m: emit({"type": "status", "message": m}),
                )
            except Exception as e:
                s.db.add_message(conv_id, {"role": "assistant", "kind": "error", "text": friendly_error(e),
                                           "question": question})
                raise
            return {"type": "answer", "message": s.db.add_message(conv_id, answer_message(ans))}

        return stream(work)

    @app.post("/api/conversations/{conv_id}/messages/{mid}/web")
    def web_search(conv_id: int, mid: int, body: WebQuery, user: User = Depends(current_user)):
        own_conversation(conv_id, user)
        s = svc()
        if s.web is None:
            raise HTTPException(503, "Chưa có TAVILY_API_KEY trong file .env")
        msg = offer_message(conv_id, mid)
        query = body.query.strip() or msg["question"]

        def work(emit: Emit) -> dict:
            ans = answer_from_web(s.llm, s.web, query, msg["question"],
                                  progress=lambda m: emit({"type": "status", "message": m}))
            s.db.set_offer_state(conv_id, "done", mid, query)
            return {"type": "answer", "message": s.db.add_message(conv_id, answer_message(ans))}

        return stream(work)

    @app.post("/api/conversations/{conv_id}/messages/{mid}/decline", status_code=204)
    def decline_web(conv_id: int, mid: int, user: User = Depends(current_user)):
        own_conversation(conv_id, user)
        offer_message(conv_id, mid)
        svc().db.set_offer_state(conv_id, "declined", mid)
        return Response(status_code=204)

    # ---------------- giao diện ----------------

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
