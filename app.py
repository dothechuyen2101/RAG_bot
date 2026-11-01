import sqlite3
from collections.abc import Callable
from dataclasses import asdict

import streamlit as st

from ragbot.answer import Answer, answer_from_docs, answer_from_web
from ragbot.config import get_settings
from ragbot.ingest import SUPPORTED_EXTENSIONS, EmbedModelMismatch, LoadError
from ragbot.providers import MissingKeyError
from ragbot.services import Services, build_services

st.set_page_config(page_title="RAG Bot", page_icon="📚", layout="wide")


@st.cache_resource(show_spinner=False)
def get_services() -> Services:
    return build_services(get_settings())


try:
    svc = get_services()
except MissingKeyError as e:
    st.error(f"**{e}.** Sao chép `.env.example` thành `.env`, điền API key rồi khởi động lại ứng dụng.")
    st.stop()

state = st.session_state
state.setdefault("chats", {})  # collection_id -> danh sách tin nhắn
state.setdefault("uploader_key", 0)
state.setdefault("flash", [])  # thông báo hiện một lần sau st.rerun()
state.setdefault("added_urls", set())


def flash(kind: str, message: str) -> None:
    state.flash.append((kind, message))


def show_flash() -> None:
    for kind, message in state.flash:
        {"success": st.success, "error": st.error, "warning": st.warning}[kind](message)
    state.flash = []


def ingest_many(items: list[tuple[str, Callable]]) -> None:
    """items: (nhãn, hàm(progress) -> IngestResult). Nạp lần lượt, ghi kết quả vào flash."""
    ok = 0
    with st.status("Đang nạp tài liệu...", expanded=True) as status:
        for label, run in items:
            st.markdown(f"**{label}**")
            line = st.empty()
            try:
                result = run(lambda msg: line.caption(msg))
                ok += 1
                ocr_note = f", OCR {result.n_ocr_pages} trang" if result.n_ocr_pages else ""
                flash("success", f"Đã nạp **{result.document.title}** ({result.document.n_chunks} đoạn{ocr_note})")
            except (LoadError, EmbedModelMismatch, MissingKeyError) as e:
                flash("error", str(e))
            except Exception as e:
                flash("error", f"Lỗi khi nạp {label}: {e}")
        status.update(label=f"Đã nạp {ok}/{len(items)} tài liệu", state="complete")


def answer_to_message(ans: Answer) -> dict:
    if ans.kind == "docs" and ans.coverage == "none":
        content = "Không tìm thấy thông tin này trong tài liệu của bạn."
    else:
        content = ans.text
    return {
        "role": "assistant",
        "kind": ans.kind,
        "coverage": ans.coverage,
        "content": content,
        "sources": [asdict(s) for s in ans.sources],
        "question": ans.question,
        "web_query": ans.web_query,
        "offer_open": ans.offer_web,
    }


def chat_history(messages: list[dict]) -> list[dict]:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m["role"] == "user" or m.get("kind") in ("docs", "web")
    ]


# ---------------- Sidebar: bộ tài liệu ----------------

with st.sidebar:
    st.header("📚 Bộ tài liệu")
    show_flash()

    if "pending_collection" in state:
        state.collection_id = state.pop("pending_collection")

    collections = svc.db.list_collections()
    names = {c.id: c.name for c in collections}
    if names:
        if state.get("collection_id") not in names:
            state.collection_id = next(iter(names))
        st.selectbox("Đang hỏi trong bộ", options=list(names), format_func=names.get, key="collection_id")

    with st.expander("➕ Tạo bộ mới", expanded=not names):
        with st.form("new_collection", clear_on_submit=True, border=False):
            new_name = st.text_input("Tên bộ", placeholder="Ví dụ: Pháp luật giao thông")
            if st.form_submit_button("Tạo bộ") and new_name.strip():
                try:
                    state.pending_collection = svc.db.create_collection(new_name).id
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Tên bộ này đã tồn tại.")

    if not names:
        st.stop()

    cid: int = state.collection_id
    collection = svc.db.get_collection(cid)
    needs_reindex = svc.ingestor.needs_reindex(cid)

    if needs_reindex:
        st.warning(
            f"Bộ này được index bằng `{collection.embed_model}`, khác cấu hình hiện tại "
            f"`{svc.embedder.model_id}`. Cần re-index trước khi hỏi hoặc nạp thêm."
        )
        if st.button("🔄 Re-index", type="primary", width="stretch"):
            with st.status("Đang re-index...", expanded=True) as status:
                line = st.empty()
                try:
                    errors = svc.ingestor.reindex_collection(cid, lambda m: line.caption(m))
                    for err in errors:
                        flash("warning", err)
                    flash("success", "Re-index xong.")
                except Exception as e:
                    flash("error", f"Re-index lỗi: {e}. Bấm Re-index để thử lại.")
                status.update(state="complete")
            st.rerun()

    st.subheader("Nạp tài liệu")
    uploads = st.file_uploader(
        "PDF (kể cả bản scan), Word, TXT",
        type=[e.lstrip(".") for e in sorted(SUPPORTED_EXTENSIONS)],
        accept_multiple_files=True,
        key=f"uploader_{state.uploader_key}",
        disabled=needs_reindex,
    )
    if st.button("Nạp file", disabled=not uploads or needs_reindex, width="stretch"):
        ingest_many([
            (f.name, lambda p, f=f: svc.ingestor.ingest_file(cid, f.name, f.getvalue(), p)) for f in uploads
        ])
        state.uploader_key += 1
        st.rerun()

    url_text = st.text_area("Link (mỗi dòng một link)", key=f"urls_{state.uploader_key}", disabled=needs_reindex)
    urls = [u.strip() for u in url_text.splitlines() if u.strip()]
    if st.button("Nạp link", disabled=not urls or needs_reindex, width="stretch"):
        ingest_many([(u, lambda p, u=u: svc.ingestor.ingest_url(cid, u, p)) for u in urls])
        state.uploader_key += 1
        st.rerun()

    documents = svc.db.list_documents(cid)
    st.subheader(f"Tài liệu ({len(documents)})")
    for d in documents:
        left, right = st.columns([6, 1], vertical_alignment="center")
        details = [f"{d.n_chunks} đoạn"]
        if d.n_pages:
            details.insert(0, f"{d.n_pages} trang")
        if d.n_ocr_pages:
            details.append(f"OCR {d.n_ocr_pages} trang")
        left.markdown(f"**{d.title}**")
        left.caption(" · ".join(details) + (f" · [link]({d.source})" if d.source_type == "url" else ""))
        if right.button("🗑", key=f"del_{d.id}", help="Xóa tài liệu này"):
            svc.ingestor.delete_document(cid, d.id)
            flash("success", f"Đã xóa **{d.title}**")
            st.rerun()

    st.divider()
    if st.button("🧹 Xóa lịch sử chat", width="stretch"):
        state.chats[cid] = []
        st.rerun()
    with st.expander("⚠️ Xóa bộ tài liệu"):
        confirm = st.checkbox(f"Xóa bộ '{collection.name}' và toàn bộ tài liệu bên trong", key=f"confirm_{cid}")
        if st.button("Xóa bộ này", disabled=not confirm):
            svc.ingestor.delete_collection(cid)
            state.chats.pop(cid, None)
            del state["collection_id"]
            flash("success", f"Đã xóa bộ **{collection.name}**")
            st.rerun()

    st.caption(
        f"LLM: `{svc.llm.name}`  \nEmbedding: `{svc.embedder.model_id}`  \n"
        f"Tìm web: {'Tavily' if svc.web else 'chưa có TAVILY_API_KEY'}"
    )


# ---------------- Khung chat ----------------

st.title(f"💬 {collection.name}")
messages: list[dict] = state.chats.setdefault(cid, [])


def run_web_search(msg: dict, query: str) -> None:
    msg["offer_open"] = False
    if svc.web is None:
        messages.append({"role": "assistant", "kind": "error", "content": "Chưa có `TAVILY_API_KEY` trong file `.env`."})
        return
    try:
        with st.spinner("Đang tìm trên internet..."):
            ans = answer_from_web(svc.llm, svc.web, query, msg["question"])
        messages.append(answer_to_message(ans))
    except Exception as e:
        messages.append({"role": "assistant", "kind": "error", "content": f"Lỗi khi tìm trên internet: {e}"})


def add_web_source(url: str) -> None:
    with st.spinner("Đang nạp trang vào bộ tài liệu..."):
        try:
            result = svc.ingestor.ingest_url(cid, url)
            state.added_urls.add(url)
            flash("success", f"Đã thêm **{result.document.title}** vào bộ tài liệu")
        except (LoadError, EmbedModelMismatch) as e:
            flash("error", str(e))
        except Exception as e:
            flash("error", f"Lỗi khi nạp {url}: {e}")


def render_sources(msg: dict, idx: int) -> None:
    sources = msg.get("sources") or []
    if not sources:
        return
    with st.expander(f"📎 Nguồn ({len(sources)})"):
        for s in sources:
            title = f"[{s['title']}]({s['url']})" if s["url"] else s["title"]
            st.markdown(f"**[{s['n']}]** {title}" + (f" · {s['location']}" if s["location"] else ""))
            st.caption(s["excerpt"])
            if msg["kind"] == "web":
                added = s["url"] in state.added_urls
                if st.button("✅ Đã thêm" if added else "➕ Thêm vào bộ tài liệu", key=f"add_{idx}_{s['n']}",
                             disabled=added or needs_reindex):
                    add_web_source(s["url"])
                    st.rerun()


def render_assistant(msg: dict, idx: int, is_last: bool) -> None:
    kind = msg.get("kind")
    if kind == "error":
        st.error(msg["content"])
        return
    if kind == "web":
        st.markdown("🌐 **Nguồn internet**, không có trong tài liệu của bạn")
    st.markdown(msg["content"])
    render_sources(msg, idx)

    if not (msg.get("offer_open") and is_last):
        return
    if msg["coverage"] == "partial":
        st.info("Tài liệu chỉ trả lời được một phần. Bạn có muốn tìm phần còn thiếu trên internet không?")
    else:
        st.info("Bạn có muốn tìm trên internet không?")
    query = st.text_input("Truy vấn tìm kiếm (có thể sửa)", value=msg["web_query"], key=f"web_query_{idx}")
    yes, no = st.columns(2)
    if yes.button("🌐 Tìm trên internet", key=f"web_yes_{idx}", type="primary", width="stretch"):
        run_web_search(msg, query)
        st.rerun()
    if no.button("Không, cảm ơn", key=f"web_no_{idx}", width="stretch"):
        msg["offer_open"] = False
        messages.append({"role": "assistant", "kind": "info", "content": "Đã bỏ qua, không tìm trên internet."})
        st.rerun()


if not messages:
    if documents:
        st.caption("Đặt câu hỏi bên dưới. Bot chỉ trả lời dựa trên tài liệu trong bộ này, "
                   "và sẽ hỏi bạn trước khi tìm trên internet.")
    else:
        st.info("Bộ này chưa có tài liệu. Hãy nạp file hoặc link ở thanh bên trái.")

for i, m in enumerate(messages):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            render_assistant(m, i, i == len(messages) - 1)

placeholder = "Cần re-index trước khi hỏi" if needs_reindex else "Hỏi về tài liệu trong bộ này..."
if question := st.chat_input(placeholder, disabled=needs_reindex or not documents):
    history = chat_history(messages)
    for m in messages:
        m["offer_open"] = False  # câu hỏi mới thì bỏ lời mời tìm web của câu trước
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Đang tìm trong tài liệu..."):
                ans = answer_from_docs(
                    svc.llm, svc.retriever, cid, question, history, svc.settings.top_k, svc.settings.min_similarity
                )
            messages.append(answer_to_message(ans))
        except Exception as e:
            messages.append({"role": "assistant", "kind": "error", "content": f"Lỗi: {e}"})
    st.rerun()
