// RAG Bot: giao diện web (không cần build). Gọi API trong ragbot/api.py.

// ================= Tiện ích =================
const $ = (sel, root = document) => root.querySelector(sel);

const ICONS = {
  bot: '<svg viewBox="0 0 24 24"><path d="M7 5h6a4 4 0 0 1 0 8h-2l3 5" /><path d="M7 5v13" /></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>',
  chevron: '<svg class="chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5" /></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12" /></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" /></svg>',
  alert: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16h.01" /></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1" /><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" /></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>',
  retry: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></svg>',
  key: '<svg viewBox="0 0 24 24"><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l3 3M15 8l2 2" /></svg>',
};

/** Tạo phần tử: el("div", {class: "x", text: "..."}, child1, child2). Không bao giờ nhận HTML từ dữ liệu. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "icon") node.insertAdjacentHTML("afterbegin", ICONS[v]); // chỉ SVG tĩnh ở trên
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c);
  return node;
}

const escapeHtml = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  },
  remove(key) {
    try {
      localStorage.removeItem(key);
    } catch {}
  },
};

const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const uid = () => Math.random().toString(36).slice(2, 10);

// ================= Markdown (an toàn: escape HTML trước) =================
const LIST_RE = /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/;

function inlineMd(s) {
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/\[(\d{1,2})\]/g, '<button type="button" class="cite" data-n="$1" title="Xem nguồn $1">$1</button>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, k) => `<code>${codes[k]}</code>`);
}

const isTableStart = (lines, i) => lines[i].includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1]);
const splitRow = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const isBlockStart = (lines, i) =>
  /^(#{1,6}\s|```|\s*&gt;)/.test(lines[i]) || LIST_RE.test(lines[i]) || isTableStart(lines, i);

function renderMarkdown(src) {
  const lines = escapeHtml(src.replace(/\r\n?/g, "\n")).split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (line.startsWith("```")) {
      const code = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const tag = h[1].length <= 2 ? "h3" : "h4";
      out.push(`<${tag}>${inlineMd(h[2])}</${tag}>`);
      i++;
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(lines[i]);
      const rows = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(splitRow(lines[i]));
      out.push(
        `<table><thead><tr>${head.map((c) => `<th>${inlineMd(c)}</th>`).join("")}</tr></thead><tbody>` +
          rows.map((r) => `<tr>${r.map((c) => `<td>${inlineMd(c)}</td>`).join("")}</tr>`).join("") +
          "</tbody></table>",
      );
      continue;
    }
    if (/^\s*&gt;/.test(line)) {
      const quote = [];
      for (; i < lines.length && /^\s*&gt;/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*&gt;\s?/, ""));
      out.push(`<blockquote>${inlineMd(quote.join("<br>"))}</blockquote>`);
      continue;
    }
    if (LIST_RE.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(LIST_RE);
        if (m) items.push({ indent: m[1].length, ordered: /\d/.test(m[2]), text: m[3] });
        else if (items.length && /^\s{2,}\S/.test(lines[i])) items[items.length - 1].text += "<br>" + lines[i].trim();
        else break;
        i++;
      }
      let html = "";
      const stack = [];
      for (const it of items) {
        const tag = it.ordered ? "ol" : "ul";
        if (!stack.length || it.indent > stack[stack.length - 1].indent) {
          html += `<${tag}>`;
          stack.push({ indent: it.indent, tag });
        } else {
          while (stack.length > 1 && it.indent < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
          html += "</li>";
        }
        html += `<li>${inlineMd(it.text)}`;
      }
      while (stack.length) html += `</li></${stack.pop().tag}>`;
      out.push(html);
      continue;
    }
    const para = [];
    for (; i < lines.length && lines[i].trim() && (para.length === 0 || !isBlockStart(lines, i)); i++) para.push(lines[i]);
    out.push(`<p>${inlineMd(para.join("<br>"))}</p>`);
  }
  return out.join("");
}

// ================= API =================
async function errorText(res) {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

/** auth: true cho các API đăng nhập (lỗi 401 ở đó là sai mật khẩu, không phải hết phiên). */
async function api(path, { method = "GET", body, auth = false } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !auth) onUnauthorized();
  if (!res.ok) throw new Error(await errorText(res));
  return res.status === 204 ? null : res.json();
}

/** Đọc NDJSON: gọi onEvent cho từng sự kiện, trả về sự kiện cuối. Sự kiện "error" ném lỗi. */
async function stream(path, init, onEvent) {
  const res = await fetch(path, init);
  if (res.status === 401) onUnauthorized();
  if (!res.ok) throw new Error(await errorText(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let last = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "error") throw new Error(event.message);
      last = event;
      onEvent(event);
    }
  }
  return last;
}
const jsonInit = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ================= Trạng thái =================
const state = {
  user: null,
  info: null,
  collections: [],
  currentId: null,
  docs: [],
  docsLoading: false,
  pending: new Map(), // cid -> Map(label -> {message, ratio, error})
  chats: new Map(), // cid -> danh sách tin nhắn
  thinking: new Map(), // cid -> dòng trạng thái đang xử lý
  flashDocs: new Set(),
};

const current = () => state.collections.find((c) => c.id === state.currentId) || null;
// Lịch sử chat lưu trong trình duyệt, tách theo tài khoản để người dùng chung máy không thấy của nhau.
const chatKey = (cid) => `ragbot.chat.${state.user?.username}.${cid}`;
const collectionKey = () => `ragbot.collection.${state.user?.username}`;

function messagesOf(cid) {
  if (!state.chats.has(cid)) {
    const msgs = store.get(chatKey(cid), []);
    // Trang bị tải lại giữa lúc đang tìm web: mở lại lời mời thay vì kẹt ở "Đang tìm...".
    for (const m of msgs) if (m.offer?.state === "searching") m.offer.state = "open";
    state.chats.set(cid, msgs);
  }
  return state.chats.get(cid);
}
const saveChat = (cid) => store.set(chatKey(cid), messagesOf(cid).slice(-100));
const pendingOf = (cid) => {
  if (!state.pending.has(cid)) state.pending.set(cid, new Map());
  return state.pending.get(cid);
};

// ================= Thông báo & hộp thoại =================
function toast(type, text, life = 4500) {
  const icon = { success: "check", error: "alert", info: "info" }[type];
  const node = el("div", { class: `toast ${type}`, role: "status", style: `--life:${life}ms` },
    el("span", { class: "t-icon", icon }), el("div", { text }));
  const box = $("#toasts");
  box.append(node);
  // Popover nằm ở lớp trên cùng: hiện lại để luôn nổi trên hộp thoại đang mở.
  try {
    if (box.matches(":popover-open")) box.hidePopover();
    box.showPopover();
  } catch {}
  const close = () => {
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  };
  node.addEventListener("click", close);
  setTimeout(close, life);
}

function confirmDialog(title, text, okLabel = "Xóa") {
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = okLabel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

// ================= Thông tin model =================
function renderInfo() {
  const box = $("#model-info");
  box.replaceChildren();
  const info = state.info;
  if (!info) return;
  const row = (on, label, value) =>
    el("div", {}, el("span", { class: `status-dot ${on ? "on" : ""}` }), `${label}: `, el("b", { text: value, title: value }));
  box.append(
    row(true, "Trả lời", info.llm.replace(":", " · ")),
    row(true, "Embedding", info.embedder.split(":").slice(1, 2).join("") || info.embedder),
    row(info.web_search, "Tìm web", info.web_search ? "Tavily" : "chưa cấu hình"),
  );
}

// ================= Bộ tài liệu =================
function renderCollections() {
  const list = $("#collection-list");
  list.replaceChildren(
    ...state.collections.map((c, i) =>
      el("li", {},
        el("button", {
          class: `collection-item ${c.id === state.currentId ? "active" : ""}`,
          style: `animation-delay:${i * 30}ms`,
          onclick: () => selectCollection(c.id),
        },
          el("span", { class: "dot" }),
          el("span", { class: "name", text: c.name, title: c.name }),
          c.needs_reindex ? el("span", { class: "warn", icon: "warn", title: "Cần re-index" }) : null,
          el("span", { class: "pill", text: String(c.documents) }),
        ),
      ),
    ),
  );
}

async function loadCollections() {
  state.collections = await api("/api/collections");
  if (!state.collections.some((c) => c.id === state.currentId)) {
    const saved = store.get(collectionKey(), null);
    state.currentId = state.collections.some((c) => c.id === saved) ? saved : state.collections[0]?.id ?? null;
  }
  renderCollections();
}

async function selectCollection(id) {
  state.currentId = id;
  store.set(collectionKey(), id);
  closeSidebar();
  renderCollections();
  renderHeader();
  renderMessages();
  await loadDocs();
}

async function createCollection(name) {
  name = name.trim();
  if (!name) return;
  try {
    const col = await api("/api/collections", { method: "POST", body: { name } });
    await loadCollections();
    await selectCollection(col.id);
    toast("success", `Đã tạo bộ “${col.name}”`);
    return true;
  } catch (e) {
    toast("error", e.message);
    return false;
  }
}

async function deleteCollection() {
  const col = current();
  if (!col) return;
  const ok = await confirmDialog("Xóa bộ tài liệu?", `Bộ “${col.name}” cùng ${col.documents} tài liệu và lịch sử hội thoại sẽ bị xóa vĩnh viễn.`);
  if (!ok) return;
  try {
    await api(`/api/collections/${col.id}`, { method: "DELETE" });
    store.remove(chatKey(col.id));
    state.chats.delete(col.id);
    state.currentId = null;
    await loadCollections();
    renderHeader();
    renderMessages();
    await loadDocs();
    toast("success", `Đã xóa bộ “${col.name}”`);
  } catch (e) {
    toast("error", e.message);
  }
}

// ================= Tài liệu =================
const BADGE = { pdf: "PDF", docx: "DOC", txt: "TXT" };

function docBadge(type, label = "") {
  if (type === "url") return el("span", { class: "doc-badge url", icon: "link" });
  const ext = (label.split(".").pop() || "").toLowerCase();
  const kind = type || (ext === "pdf" ? "pdf" : ext === "docx" ? "docx" : /^https?:/.test(label) ? "url" : "txt");
  return kind === "url" ? el("span", { class: "doc-badge url", icon: "link" }) : el("span", { class: `doc-badge ${kind}`, text: BADGE[kind] || "TXT" });
}

function docRow(d) {
  const meta = [];
  if (d.n_pages) meta.push(`${d.n_pages} trang`);
  meta.push(`${d.n_chunks} đoạn`);
  const row = el("li", { class: `doc-item ${state.flashDocs.has(d.id) ? "done-flash" : ""}` },
    docBadge(d.source_type, d.source),
    el("div", { class: "doc-main" },
      el("div", { class: "doc-title", text: d.title, title: d.source }),
      el("div", { class: "doc-meta" }, meta.join(" · "), d.n_ocr_pages ? el("span", { class: "ocr", text: ` · OCR ${d.n_ocr_pages} trang` }) : null),
    ),
    el("button", { class: "icon-btn small danger-hover", icon: "trash", title: "Xóa tài liệu", "aria-label": `Xóa ${d.title}`, onclick: () => deleteDoc(d, row) }),
  );
  return row;
}

function pendingRow(label, p) {
  const bar = el("div", { class: `progress ${p.ratio == null ? "indeterminate" : ""}` },
    el("span", { style: p.ratio == null ? "" : `width:${Math.round(p.ratio * 100)}%` }));
  return el("li", { class: `doc-item ${p.error ? "error" : "pending"}`, "data-pending": label },
    docBadge(null, label),
    el("div", { class: "doc-main" },
      el("div", { class: "doc-title", text: label.replace(/^https?:\/\//, ""), title: label }),
      el("div", { class: "doc-meta", text: p.error || p.message || "Đang chờ..." }),
      p.error ? null : bar,
    ),
    p.error ? el("button", { class: "icon-btn small", icon: "x", title: "Ẩn", onclick: () => { pendingOf(state.currentId).delete(label); renderDocs(); } }) : null,
  );
}

function renderDocs() {
  const col = current();
  const list = $("#doc-list");
  $("#documents-section").hidden = !col;
  if (!col) return;
  const pending = [...pendingOf(col.id)];
  $("#doc-count").textContent = String(state.docs.length);
  $("#dropzone").classList.toggle("disabled", col.needs_reindex);
  if (state.docsLoading && !state.docs.length) {
    list.replaceChildren(...[0, 1, 2].map(() => el("li", { class: "skeleton" })));
    return;
  }
  const rows = [...pending.map(([label, p]) => pendingRow(label, p)), ...state.docs.map(docRow)];
  list.replaceChildren(...(rows.length ? rows : [el("li", { class: "empty-docs", text: "Chưa có tài liệu nào trong bộ này." })]));
  state.flashDocs.clear();
}

/** Cập nhật dòng đang nạp tại chỗ (không vẽ lại cả danh sách) để thanh tiến trình chạy mượt. */
function updatePendingRow(cid, label) {
  if (cid !== state.currentId) return;
  const p = pendingOf(cid).get(label);
  const node = [...document.querySelectorAll("[data-pending]")].find((n) => n.dataset.pending === label);
  if (!node || !p || p.error) return renderDocs();
  node.querySelector(".doc-meta").textContent = p.message || "Đang xử lý...";
  const bar = node.querySelector(".progress");
  bar.classList.toggle("indeterminate", p.ratio == null);
  bar.firstElementChild.style.width = p.ratio == null ? "" : `${Math.round(p.ratio * 100)}%`;
}

async function loadDocs() {
  const col = current();
  if (!col) {
    state.docs = [];
    renderDocs();
    return;
  }
  state.docsLoading = true;
  renderDocs();
  try {
    const docs = await api(`/api/collections/${col.id}/documents`);
    if (col.id === state.currentId) state.docs = docs;
  } catch (e) {
    toast("error", e.message);
  } finally {
    state.docsLoading = false;
    renderDocs();
    renderHeader();
    refreshComposer();
    if (state.currentId == null || !messagesOf(state.currentId).length) renderMessages();
  }
}

async function refreshAfterIngest(cid, newIds = []) {
  newIds.forEach((id) => state.flashDocs.add(id));
  await loadCollections();
  if (cid === state.currentId) await loadDocs();
}

/** Nạp file hoặc link. kind: "files" | "urls". Trả về số tài liệu nạp thành công. */
async function ingest(kind, items, cid = state.currentId) {
  if (!cid || !items.length) return 0;
  const pending = pendingOf(cid);
  const labels = kind === "files" ? items.map((f) => f.name) : items;
  labels.forEach((l) => pending.set(l, { message: "Đang chờ...", ratio: null }));
  if (cid === state.currentId) renderDocs();

  let init;
  if (kind === "files") {
    const form = new FormData();
    items.forEach((f) => form.append("files", f, f.name));
    init = { method: "POST", body: form };
  } else {
    init = jsonInit({ urls: items });
  }
  const added = [];
  try {
    await stream(`/api/collections/${cid}/${kind}`, init, (ev) => {
      const p = pending.get(ev.item);
      if (ev.type === "start" && p) {
        p.message = kind === "files" ? "Đang đọc file..." : "Đang tải trang...";
        updatePendingRow(cid, ev.item);
      } else if (ev.type === "progress" && p) {
        p.message = ev.message;
        if (ev.ratio != null) p.ratio = ev.ratio;
        updatePendingRow(cid, ev.item);
      } else if (ev.type === "item_done") {
        pending.delete(ev.item);
        added.push(ev.document.id);
        const ocr = ev.n_ocr_pages ? `, OCR ${ev.n_ocr_pages} trang` : "";
        toast("success", `Đã nạp “${ev.document.title}” (${ev.document.n_chunks} đoạn${ocr})`);
        refreshAfterIngest(cid, [ev.document.id]);
      } else if (ev.type === "item_error") {
        pending.set(ev.item, { error: ev.message });
        toast("error", ev.message, 7000);
        if (cid === state.currentId) renderDocs();
      }
    });
  } catch (e) {
    labels.forEach((l) => pending.has(l) && pending.set(l, { error: e.message }));
    toast("error", e.message, 7000);
  }
  if (cid === state.currentId) renderDocs();
  return added.length;
}

async function deleteDoc(d, row) {
  const ok = await confirmDialog("Xóa tài liệu?", `“${d.title}” sẽ bị xóa khỏi bộ tài liệu. Bot sẽ không dùng tài liệu này để trả lời nữa.`);
  if (!ok) return;
  try {
    await api(`/api/collections/${state.currentId}/documents/${d.id}`, { method: "DELETE" });
    row.classList.add("removing");
    await new Promise((r) => setTimeout(r, reduceMotion.matches ? 0 : 260));
    toast("success", `Đã xóa “${d.title}”`);
    await refreshAfterIngest(state.currentId);
  } catch (e) {
    toast("error", e.message);
  }
}

// ================= Header & banner =================
function renderHeader() {
  const col = current();
  $("#collection-title").textContent = col ? col.name : "RAG Bot";
  $("#collection-sub").textContent = col
    ? `${state.docs.length || col.documents} tài liệu · trả lời có trích dẫn nguồn`
    : "Hỏi đáp theo tài liệu của bạn";
  $("#delete-collection-btn").hidden = !col;
  $("#clear-chat-btn").hidden = !col || !messagesOf(col.id).length;
  document.title = col ? `${col.name} · RAG Bot` : "RAG Bot";

  const banner = $("#reindex-banner");
  banner.hidden = !col?.needs_reindex;
  if (col?.needs_reindex && !banner.dataset.busy) {
    $("#reindex-text").textContent =
      `Bộ này được index bằng ${col.embed_model}, khác model embedding hiện tại (${state.info?.embedder ?? "?"}). Cần re-index trước khi hỏi hoặc nạp thêm.`;
  }
}

async function reindex() {
  const col = current();
  if (!col) return;
  const banner = $("#reindex-banner");
  const btn = $("#reindex-btn");
  banner.dataset.busy = "1";
  btn.disabled = true;
  try {
    const done = await stream(`/api/collections/${col.id}/reindex`, { method: "POST" }, (ev) => {
      if (ev.type === "progress") $("#reindex-text").textContent = ev.message;
    });
    (done?.errors || []).forEach((e) => toast("error", e, 8000));
    toast("success", "Re-index xong");
  } catch (e) {
    toast("error", `Re-index lỗi: ${e.message}`, 8000);
  } finally {
    delete banner.dataset.busy;
    btn.disabled = false;
    await loadCollections();
    renderHeader();
    await loadDocs();
  }
}

// ================= Tin nhắn =================
const chat = $("#chat");
const messagesBox = $("#messages");
const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 140;
const scrollToBottom = (smooth = true) => chat.scrollTo({ top: chat.scrollHeight, behavior: smooth && !reduceMotion.matches ? "smooth" : "auto" });

const SUGGESTIONS = [
  "Tóm tắt nội dung chính",
  "Những điểm cần lưu ý nhất?",
  "Các mốc thời hạn quan trọng",
];

function welcome() {
  const col = current();
  const mark = el("div", { class: "welcome-mark", icon: "bot" });
  if (!col) {
    const input = el("input", { placeholder: "Ví dụ: Pháp luật giao thông", maxlength: "80", "aria-label": "Tên bộ tài liệu" });
    const form = el("form", { class: "inline-form", onsubmit: async (e) => { e.preventDefault(); await createCollection(input.value); } },
      input, el("button", { class: "btn primary", type: "submit", text: "Tạo bộ tài liệu" }));
    setTimeout(() => input.focus(), 50);
    return el("div", { class: "welcome" }, mark,
      el("h2", { text: "Chào mừng đến với RAG Bot" }),
      el("p", { text: "Tạo một bộ tài liệu theo lĩnh vực, nạp PDF, Word hoặc link, rồi đặt câu hỏi. Bot chỉ trả lời từ tài liệu của bạn và hỏi trước khi tìm trên internet." }),
      form);
  }
  if (!state.docs.length && !state.docsLoading) {
    return el("div", { class: "welcome" }, mark,
      el("h2", { text: `Bộ “${col.name}” chưa có tài liệu` }),
      el("p", { text: "Kéo thả file vào đây hoặc vào thanh bên trái, hoặc dán link văn bản. PDF bản scan sẽ được tự động nhận dạng chữ (OCR)." }),
      el("button", { class: "btn primary", icon: "file", onclick: () => $("#file-input").click() }, "Chọn file để nạp"));
  }
  return el("div", { class: "welcome" }, mark,
    el("h2", { text: `Hỏi gì về “${col.name}”?` }),
    el("p", { text: `Bot trả lời dựa trên ${state.docs.length} tài liệu trong bộ này, kèm trích dẫn nguồn. Nếu tài liệu không có thông tin, bot sẽ hỏi bạn trước khi tìm trên internet.` }),
    el("div", { class: "suggestions" },
      SUGGESTIONS.map((s) => el("button", { class: "chip", type: "button", text: s, onclick: () => askQuestion(s) }))));
}

function renderMessages() {
  const cid = state.currentId;
  const msgs = cid ? messagesOf(cid) : [];
  messagesBox.replaceChildren(...(msgs.length ? msgs.map((m) => messageEl(m, cid)) : [welcome()]));
  if (cid && state.thinking.has(cid)) messagesBox.append(thinkingEl(state.thinking.get(cid)));
  $("#clear-chat-btn").hidden = !cid || !msgs.length;
  scrollToBottom(false);
}

function thinkingEl(text) {
  return el("div", { class: "msg bot thinking", id: "thinking" },
    el("div", { class: "avatar", icon: "bot" }),
    el("div", { class: "body" },
      el("span", { class: "dots" }, el("i"), el("i"), el("i")),
      el("span", { class: "status-text", text })));
}

function setThinking(cid, text) {
  if (text == null) {
    state.thinking.delete(cid);
    if (cid === state.currentId) $("#thinking")?.remove();
    return;
  }
  const isNew = !state.thinking.has(cid);
  state.thinking.set(cid, text);
  if (cid !== state.currentId) return;
  const node = $("#thinking");
  if (isNew || !node) {
    messagesBox.append(thinkingEl(text));
    scrollToBottom();
    return;
  }
  const label = node.querySelector(".status-text");
  label.classList.add("swap");
  setTimeout(() => {
    label.textContent = text;
    label.classList.remove("swap");
  }, reduceMotion.matches ? 0 : 180);
}

function messageEl(m, cid) {
  if (m.role === "user") return el("div", { class: "msg user" }, el("div", { class: "bubble", text: m.text }));

  const kindClass = m.kind === "web" ? "web" : m.kind === "error" ? "error" : "";
  const body = el("div", { class: "body" });
  const node = el("div", { class: `msg bot ${kindClass}`, "data-id": m.id }, el("div", { class: "avatar", icon: m.kind === "error" ? "alert" : m.kind === "web" ? "globe" : "bot" }), body);

  if (m.kind === "error") {
    body.append(el("div", { text: m.text }));
    if (m.question) body.append(el("button", { class: "btn ghost small retry", icon: "retry", onclick: () => askQuestion(m.question) }, "Thử lại"));
    return node;
  }
  if (m.kind === "web") body.append(el("div", { class: "tag web", icon: "globe" }, "Nguồn internet · không có trong tài liệu của bạn"));
  if (m.kind === "docs" && m.coverage === "partial") body.append(el("div", { class: "tag partial", icon: "warn" }, "Tài liệu chỉ trả lời được một phần"));

  const content = el("div", { class: "md" });
  if (m.kind === "docs" && m.coverage === "none") content.append(el("p", { text: "Không tìm thấy thông tin này trong tài liệu của bạn." }));
  else content.innerHTML = renderMarkdown(m.text); // renderMarkdown escape HTML trước khi định dạng
  body.append(content);

  if (m.sources?.length) body.append(sourcesEl(m, cid));
  if (m.offer) body.append(offerEl(m, cid));
  return node;
}

function sourcesEl(m, cid) {
  const wrap = el("div", { class: "sources" });
  const toggle = el("button", { class: "sources-toggle", type: "button", "aria-expanded": "false" },
    el("span", { text: `${m.sources.length} nguồn` }));
  toggle.insertAdjacentHTML("beforeend", ICONS.chevron);
  toggle.addEventListener("click", () => {
    wrap.classList.toggle("open");
    toggle.setAttribute("aria-expanded", wrap.classList.contains("open"));
  });
  const grid = el("div", { class: "source-grid" },
    m.sources.map((s, i) => {
      const title = s.url
        ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.title })
        : document.createTextNode(s.title);
      const excerpt = el("div", { class: "source-excerpt", text: s.excerpt, title: "Bấm để xem đầy đủ" });
      excerpt.addEventListener("click", () => excerpt.classList.toggle("expanded"));
      const actions = s.url ? el("div", { class: "source-actions" }, addSourceButton(m, s, cid)) : null;
      return el("div", { class: "source-card", "data-n": s.n, style: `--i:${i}` },
        el("span", { class: "source-num", text: s.n }),
        el("div", { class: "source-body" },
          el("div", { class: "source-title" }, title),
          s.location ? el("div", { class: "source-loc", text: s.location }) : null,
          excerpt,
          actions));
    }));
  wrap.append(toggle, el("div", { class: "sources-panel" }, el("div", {}, grid)));
  return wrap;
}

function addSourceButton(m, s, cid) {
  const btn = el("button", { class: "btn ghost small", type: "button", icon: s.added ? "check" : "plus", disabled: s.added },
    s.added ? "Đã thêm vào bộ tài liệu" : "Thêm vào bộ tài liệu");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.lastChild.textContent = "Đang thêm...";
    const ok = await ingest("urls", [s.url], cid);
    if (ok) {
      s.added = true;
      saveChat(cid);
      btn.replaceChildren();
      btn.insertAdjacentHTML("afterbegin", ICONS.check);
      btn.append("Đã thêm vào bộ tài liệu");
    } else {
      btn.disabled = false;
      btn.lastChild.textContent = "Thêm vào bộ tài liệu";
    }
  });
  return btn;
}

function offerEl(m, cid) {
  const offer = m.offer;
  if (offer.state === "done") return el("div", { class: "offer-done", icon: "check" }, "Đã tìm trên internet");
  if (offer.state === "declined" || offer.state === "expired") return el("div", { class: "offer-done", icon: "x" }, "Đã bỏ qua, không tìm trên internet");

  const searching = offer.state === "searching";
  const webOn = state.info?.web_search;
  const input = el("input", { value: offer.query, "aria-label": "Truy vấn tìm kiếm", disabled: searching });
  input.addEventListener("input", () => { offer.query = input.value; saveChat(cid); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); yes.click(); } });
  const yes = el("button", { class: "btn primary small", type: "button", icon: "globe", disabled: searching || !webOn },
    searching ? "Đang tìm..." : "Tìm trên internet");
  const no = el("button", { class: "btn ghost small", type: "button", disabled: searching }, "Không, cảm ơn");
  yes.addEventListener("click", () => webSearch(m, cid));
  no.addEventListener("click", () => {
    offer.state = "declined";
    saveChat(cid);
    rerenderMessage(m, cid);
  });
  const partial = m.coverage === "partial";
  return el("div", { class: "offer" },
    el("div", { class: "offer-head" },
      el("span", { class: "offer-icon", icon: "search" }),
      el("div", {},
        el("b", { text: partial ? "Tài liệu chưa trả lời đủ câu hỏi" : "Không tìm thấy trong tài liệu của bạn" }),
        el("span", { text: "Bạn có muốn tìm trên internet không? Bot chỉ tìm khi bạn đồng ý." }))),
    el("label", { class: "offer-query" }, el("span", { icon: "search" }), input),
    el("div", { class: "offer-actions" }, yes, no),
    webOn ? null : el("div", { class: "offer-note", text: "Chưa có TAVILY_API_KEY trong file .env nên chưa tìm web được." }));
}

function rerenderMessage(m, cid) {
  if (cid !== state.currentId) return;
  const old = messagesBox.querySelector(`[data-id="${m.id}"]`);
  if (!old) return;
  const fresh = messageEl(m, cid);
  fresh.style.animation = "none";
  old.replaceWith(fresh);
}

/** Hiện câu trả lời dần dần như đang gõ, rồi mới hiện nguồn và lời mời tìm web. */
function typewrite(m, cid) {
  const node = messageEl(m, cid);
  messagesBox.append(node);
  const content = node.querySelector(".md");
  const extras = [...node.querySelectorAll(".sources, .offer")];
  const text = m.text || "";
  if (!text || reduceMotion.matches || (m.kind === "docs" && m.coverage === "none")) {
    scrollToBottom();
    return;
  }
  extras.forEach((x) => (x.hidden = true));
  const duration = Math.min(2200, Math.max(500, text.length * 9));
  const start = performance.now();
  const stick = nearBottom();
  const frame = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 2);
    let n = Math.floor(text.length * eased);
    const space = text.indexOf(" ", n);
    n = t >= 1 ? text.length : space === -1 ? text.length : space;
    content.innerHTML = renderMarkdown(text.slice(0, n)) + (t < 1 ? '<span class="caret"></span>' : "");
    if (t < 1 && content.lastElementChild?.previousElementSibling) {
      // Đặt con trỏ vào cuối khối cuối cùng thay vì xuống dòng riêng.
      const caret = content.lastElementChild;
      const lastBlock = caret.previousElementSibling;
      const items = lastBlock.matches("ul, ol") ? lastBlock.querySelectorAll("li") : [];
      const target = items.length ? items[items.length - 1] : lastBlock;
      target.append(caret);
    }
    if (stick) chat.scrollTop = chat.scrollHeight;
    if (t < 1) requestAnimationFrame(frame);
    else {
      extras.forEach((x) => (x.hidden = false));
      if (stick) scrollToBottom();
    }
  };
  requestAnimationFrame(frame);
}

function pushMessage(cid, m, { animate = true } = {}) {
  messagesOf(cid).push(m);
  saveChat(cid);
  if (cid !== state.currentId) return;
  if (!messagesBox.querySelector(".msg")) messagesBox.replaceChildren();
  $("#thinking")?.remove();
  if (m.role === "assistant" && animate) typewrite(m, cid);
  else {
    messagesBox.append(messageEl(m, cid));
    scrollToBottom();
  }
  if (state.thinking.has(cid)) messagesBox.append(thinkingEl(state.thinking.get(cid)));
  $("#clear-chat-btn").hidden = false;
}

function historyFor(cid) {
  return messagesOf(cid)
    .filter((m) => m.text && (m.role === "user" || m.kind === "docs" || m.kind === "web"))
    .slice(-6)
    .map((m) => ({ role: m.role, content: m.text }));
}

function answerMessage(a, question) {
  return {
    id: uid(), role: "assistant", kind: a.kind, coverage: a.coverage, text: a.text, question: a.question || question,
    sources: a.sources, offer: a.offer_web ? { query: a.web_query, state: "open" } : null,
  };
}

async function askQuestion(text) {
  const cid = state.currentId;
  const question = text.trim();
  if (!cid || !question || state.thinking.has(cid)) return;
  const history = historyFor(cid);
  for (const m of messagesOf(cid)) if (m.offer?.state === "open") m.offer.state = "expired";
  if (cid === state.currentId) messagesBox.querySelectorAll(".offer").forEach((o) => {
    const msg = messagesOf(cid).find((m) => m.id === o.closest(".msg")?.dataset.id);
    if (msg) rerenderMessage(msg, cid);
  });
  pushMessage(cid, { id: uid(), role: "user", text: question });
  setThinking(cid, "Đang đọc câu hỏi...");
  refreshComposer();
  try {
    const result = await stream(`/api/collections/${cid}/ask`, jsonInit({ question, history }), (ev) => {
      if (ev.type === "status") setThinking(cid, ev.message);
    });
    setThinking(cid, null);
    pushMessage(cid, answerMessage(result.answer, question));
  } catch (e) {
    setThinking(cid, null);
    pushMessage(cid, { id: uid(), role: "assistant", kind: "error", text: e.message, question });
  }
  refreshComposer();
}

async function webSearch(m, cid) {
  const query = (m.offer.query || "").trim() || m.question;
  m.offer.state = "searching";
  rerenderMessage(m, cid);
  setThinking(cid, "Đang tìm trên internet...");
  refreshComposer();
  try {
    const result = await stream("/api/web", jsonInit({ question: m.question, query }), (ev) => {
      if (ev.type === "status") setThinking(cid, ev.message);
    });
    m.offer.state = "done";
    rerenderMessage(m, cid);
    setThinking(cid, null);
    pushMessage(cid, answerMessage(result.answer, m.question));
  } catch (e) {
    m.offer.state = "open";
    rerenderMessage(m, cid);
    setThinking(cid, null);
    toast("error", e.message, 7000);
  }
  saveChat(cid);
  refreshComposer();
}

// Bấm số trích dẫn [n]: mở danh sách nguồn, cuộn tới thẻ nguồn và làm nó sáng lên.
messagesBox.addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  const msg = cite.closest(".msg");
  const card = msg?.querySelector(`.source-card[data-n="${cite.dataset.n}"]`);
  if (!card) return;
  const sources = card.closest(".sources");
  sources.classList.add("open");
  sources.querySelector(".sources-toggle").setAttribute("aria-expanded", "true");
  setTimeout(() => {
    card.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "nearest" });
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
  }, 120);
});

// ================= Ô nhập câu hỏi =================
const textarea = $("#question");
const sendBtn = $("#send-btn");

function refreshComposer() {
  const col = current();
  const busy = col && state.thinking.has(col.id);
  const ready = col && !col.needs_reindex && state.docs.length > 0;
  textarea.disabled = !ready;
  textarea.placeholder = !col
    ? "Tạo bộ tài liệu để bắt đầu"
    : col.needs_reindex
      ? "Cần re-index trước khi hỏi"
      : !state.docs.length
        ? "Nạp tài liệu vào bộ này để bắt đầu hỏi"
        : "Hỏi về tài liệu trong bộ này...";
  sendBtn.disabled = !ready || busy || !textarea.value.trim();
  sendBtn.classList.toggle("busy", Boolean(busy));
}

function autoGrow() {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
}

textarea.addEventListener("input", () => { autoGrow(); refreshComposer(); });
textarea.addEventListener("keydown", (e) => {
  // isComposing: bộ gõ tiếng Việt (Telex/VNI) đang ghép dấu thì Enter không phải là gửi.
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (sendBtn.disabled) return;
  const text = textarea.value;
  textarea.value = "";
  autoGrow();
  askQuestion(text);
});

// ================= Sidebar, kéo thả, theme =================
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
}
$("#menu-btn").addEventListener("click", () => {
  $("#sidebar").classList.add("open");
  $("#scrim").classList.add("show");
});
$("#scrim").addEventListener("click", closeSidebar);

$("#new-collection-btn").addEventListener("click", () => {
  const form = $("#new-collection-form");
  form.hidden = !form.hidden;
  if (!form.hidden) $("#new-collection-name").focus();
});
$("#new-collection-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#new-collection-name");
  if (await createCollection(input.value)) {
    input.value = "";
    e.target.hidden = true;
  }
});

$("#file-input").addEventListener("change", (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  ingest("files", files);
});
$("#url-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#url-input");
  const urls = input.value.split(/\s+/).filter((u) => /^https?:\/\//i.test(u));
  if (!urls.length) return toast("error", "Link phải bắt đầu bằng http:// hoặc https://");
  input.value = "";
  ingest("urls", urls);
});

// Kéo thả file vào bất kỳ đâu trên trang.
let dragDepth = 0;
const dropzone = $("#dropzone");
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e) || !current()) return;
  dragDepth++;
  dropzone.classList.add("dragover");
});
window.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropzone.classList.remove("dragover");
});
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove("dragover");
  const col = current();
  if (!col) return toast("error", "Hãy tạo bộ tài liệu trước khi nạp file");
  if (col.needs_reindex) return toast("error", "Bộ này cần re-index trước khi nạp thêm");
  const files = [...e.dataTransfer.files].filter((f) => /\.(pdf|docx|txt|md)$/i.test(f.name));
  const skipped = e.dataTransfer.files.length - files.length;
  if (skipped) toast("error", `Bỏ qua ${skipped} file không hỗ trợ (chỉ nhận PDF, DOCX, TXT, MD)`);
  ingest("files", files);
});

$("#theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try {
    localStorage.setItem("ragbot.theme", root.dataset.theme);
  } catch {}
});

$("#clear-chat-btn").addEventListener("click", () => {
  const cid = state.currentId;
  if (!cid || state.thinking.has(cid)) return;
  state.chats.set(cid, []);
  saveChat(cid);
  renderMessages();
  textarea.focus();
});
$("#delete-collection-btn").addEventListener("click", deleteCollection);
$("#reindex-btn").addEventListener("click", reindex);

// ================= Đăng nhập & tài khoản =================
const authBox = $("#auth");
const appBox = $(".app");
let authMode = "login";

function setFormError(sel, message) {
  const node = $(sel);
  node.textContent = message || "";
  node.hidden = !message;
  if (message) {
    node.style.animation = "none";
    void node.offsetWidth;
    node.style.animation = "";
  }
}

function showAuthError(message, shake = false) {
  setFormError("#auth-error", message);
  if (!shake || reduceMotion.matches) return;
  const card = $("#auth-form");
  card.classList.remove("shake");
  void card.offsetWidth;
  card.classList.add("shake");
}

const AUTH_TEXT = {
  login: ["Đăng nhập", "Hỏi đáp theo tài liệu của bạn", "Đăng nhập"],
  setup: ["Tạo tài khoản quản trị", "Lần đầu sử dụng: tài khoản này quản lý các tài khoản khác. Mỗi tài khoản có bộ tài liệu riêng.", "Tạo tài khoản và bắt đầu"],
  "setup-blocked": ["Chưa có tài khoản nào", "Cần tạo tài khoản quản trị trước khi sử dụng.", ""],
  error: ["Chưa khởi động được", "", ""],
};

/** mode: login | setup | setup-blocked | error */
function showAuth(mode, notice = "") {
  authMode = mode;
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  appBox.hidden = true;
  authBox.hidden = false;
  const [title, sub, submit] = AUTH_TEXT[mode];
  const form = mode === "login" || mode === "setup";
  $("#auth-title").textContent = title;
  $("#auth-sub").textContent = mode === "error" ? notice : sub;
  $("#auth-fields").hidden = !form;
  $("#auth-confirm-field").hidden = mode !== "setup";
  $("#auth-password").autocomplete = mode === "setup" ? "new-password" : "current-password";
  $("#auth-submit .label").textContent = submit;
  const foot = $("#auth-foot");
  foot.replaceChildren();
  if (mode === "setup-blocked") {
    foot.append(el("div", { class: "auth-notice" },
      "Vì lý do bảo mật, tài khoản quản trị đầu tiên chỉ tạo được khi mở trang trên chính máy chạy server (localhost), hoặc bằng lệnh:",
      el("code", { text: "python server.py create-user <tên> --admin" })));
  } else if (mode === "login") {
    foot.append("Quên mật khẩu? Nhờ admin đặt lại, hoặc chạy ", el("code", { text: "python server.py reset-password <tên>" }));
  }
  showAuthError(mode === "error" ? "" : notice);
  if (form) setTimeout(() => $("#auth-username").focus(), 60);
}

let unauthorizedShown = false;
function onUnauthorized() {
  if (!state.user || unauthorizedShown) return;
  unauthorizedShown = true;
  resetApp();
  showAuth("login", "Phiên đăng nhập đã hết hạn, hãy đăng nhập lại.");
}

function resetApp() {
  Object.assign(state, { user: null, info: null, collections: [], currentId: null, docs: [] });
  state.pending.clear();
  state.chats.clear();
  state.thinking.clear();
  messagesBox.replaceChildren();
  closeUserMenu();
}

function renderUser() {
  const u = state.user;
  $("#user-avatar").textContent = u.username[0];
  $("#user-name").textContent = u.username;
  $("#user-role").textContent = u.is_admin ? "Quản trị viên" : "Người dùng";
  $("#menu-users").hidden = !u.is_admin;
}

async function startApp(user) {
  state.user = user;
  unauthorizedShown = false;
  authBox.hidden = true;
  appBox.hidden = false;
  renderUser();
  messagesBox.replaceChildren(el("div", { class: "welcome" }, el("div", { class: "welcome-mark", icon: "bot" })));
  try {
    state.info = await api("/api/info");
    renderInfo();
    await loadCollections();
  } catch (e) {
    if (!state.user) return;
    messagesBox.replaceChildren(el("div", { class: "welcome" },
      el("div", { class: "welcome-mark", icon: "alert" }),
      el("h2", { text: "Chưa khởi động được" }),
      el("p", { text: e.message })));
    return;
  }
  renderHeader();
  renderMessages();
  await loadDocs();
  textarea.focus();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (authMode !== "login" && authMode !== "setup") return;
  const username = $("#auth-username").value.trim();
  const password = $("#auth-password").value;
  if (!username || !password) return showAuthError("Nhập tên đăng nhập và mật khẩu", true);
  if (authMode === "setup" && password !== $("#auth-confirm").value) return showAuthError("Hai lần nhập mật khẩu không khớp", true);
  const btn = $("#auth-submit");
  btn.classList.add("busy");
  btn.disabled = true;
  try {
    const res = await api(authMode === "setup" ? "/api/auth/setup" : "/api/auth/login",
      { method: "POST", body: { username, password }, auth: true });
    $("#auth-password").value = "";
    $("#auth-confirm").value = "";
    showAuthError("");
    await startApp(res.user);
  } catch (err) {
    showAuthError(err.message, true);
    $("#auth-password").select();
  } finally {
    btn.classList.remove("busy");
    btn.disabled = false;
  }
});

$("#pw-toggle").addEventListener("click", () => {
  const input = $("#auth-password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  const toggle = $("#pw-toggle");
  toggle.classList.toggle("on", show);
  toggle.setAttribute("aria-label", show ? "Ẩn mật khẩu" : "Hiện mật khẩu");
  toggle.title = toggle.getAttribute("aria-label");
});

async function logout() {
  closeUserMenu();
  try {
    await api("/api/auth/logout", { method: "POST", auth: true });
  } catch {}
  resetApp();
  showAuth("login");
}

// Menu người dùng
const userMenu = $("#user-menu");
const userBtn = $("#user-btn");
function closeUserMenu() {
  userMenu.hidden = true;
  userBtn.setAttribute("aria-expanded", "false");
}
userBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const open = userMenu.hidden;
  userMenu.hidden = !open;
  userBtn.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (e) => {
  if (!userMenu.hidden && !userMenu.contains(e.target)) closeUserMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeUserMenu(); });
$("#menu-logout").addEventListener("click", logout);
$("#menu-password").addEventListener("click", () => { closeUserMenu(); openPasswordDialog(); });
$("#menu-users").addEventListener("click", () => { closeUserMenu(); openUsersDialog(); });
document.querySelectorAll("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

// Đổi mật khẩu
function openPasswordDialog() {
  ["#pw-current", "#pw-new", "#pw-confirm"].forEach((sel) => ($(sel).value = ""));
  setFormError("#pw-error", "");
  $("#password-dialog").showModal();
  $("#pw-current").focus();
}
$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const next = $("#pw-new").value;
  if (next !== $("#pw-confirm").value) return setFormError("#pw-error", "Hai lần nhập mật khẩu mới không khớp");
  try {
    await api("/api/auth/password", { method: "POST", body: { current_password: $("#pw-current").value, new_password: next } });
    $("#password-dialog").close();
    toast("success", "Đã đổi mật khẩu");
  } catch (err) {
    setFormError("#pw-error", err.message);
  }
});

// Quản lý tài khoản (admin)
const formatDate = (s) => {
  const d = new Date(String(s).replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString("vi-VN");
};

async function openUsersDialog() {
  setFormError("#user-add-error", "");
  $("#users-dialog").showModal();
  $("#new-username").focus();
  await loadUsers();
}

async function loadUsers() {
  const list = $("#user-list");
  if (!list.children.length) list.replaceChildren(...[0, 1].map(() => el("li", { class: "skeleton" })));
  try {
    const users = await api("/api/users");
    list.replaceChildren(...users.map(userRow));
  } catch (e) {
    list.replaceChildren(el("li", { class: "empty-docs", text: e.message }));
  }
}

function userRow(u) {
  const me = u.id === state.user?.id;
  const meta = el("div", { class: "meta" },
    el("b", { text: u.username }),
    u.is_admin ? el("span", { class: "role admin", text: "Admin" }) : null,
    me ? el("span", { class: "role me", text: "Bạn" }) : null,
    el("small", { text: `${u.collections} bộ tài liệu · tạo ngày ${formatDate(u.created_at)}` }));
  const row = el("li", { class: "user-item" }, el("span", { class: "user-avatar", text: u.username[0] }), meta);
  if (!me) {
    row.append(
      el("button", { class: "btn ghost small", type: "button", icon: "key", onclick: () => toggleResetRow(meta, u) }, "Đặt lại mật khẩu"),
      el("button", { class: "icon-btn small danger-hover", type: "button", icon: "trash", title: "Xóa tài khoản",
        "aria-label": `Xóa tài khoản ${u.username}`, onclick: () => deleteUser(u, row) }));
  }
  return row;
}

function toggleResetRow(meta, u) {
  const existing = meta.querySelector(".reset-row");
  if (existing) return existing.remove();
  const input = el("input", { class: "input", type: "password", placeholder: "Mật khẩu mới (≥ 8 ký tự)", autocomplete: "new-password" });
  const save = el("button", { class: "btn primary small", type: "button" }, "Lưu");
  const row = el("div", { class: "reset-row" }, input, save);
  const submit = async () => {
    try {
      await api(`/api/users/${u.id}/password`, { method: "POST", body: { new_password: input.value } });
      row.remove();
      toast("success", `Đã đặt lại mật khẩu cho “${u.username}” và đăng xuất các phiên của họ`);
    } catch (e) {
      toast("error", e.message);
    }
  };
  save.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); submit(); } });
  meta.append(row);
  input.focus();
}

async function deleteUser(u, row) {
  const ok = await confirmDialog("Xóa tài khoản?", `Tài khoản “${u.username}” cùng ${u.collections} bộ tài liệu của họ sẽ bị xóa vĩnh viễn.`);
  if (!ok) return;
  try {
    await api(`/api/users/${u.id}`, { method: "DELETE" });
    row.classList.add("removing");
    setTimeout(() => row.remove(), reduceMotion.matches ? 0 : 280);
    toast("success", `Đã xóa tài khoản “${u.username}”`);
  } catch (e) {
    toast("error", e.message);
  }
}

$("#user-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const u = await api("/api/users", { method: "POST", body: {
      username: $("#new-username").value, password: $("#new-password").value, is_admin: $("#new-is-admin").checked,
    } });
    e.target.reset();
    setFormError("#user-add-error", "");
    await loadUsers();
    toast("success", `Đã tạo tài khoản “${u.username}”`);
    $("#new-username").focus();
  } catch (err) {
    setFormError("#user-add-error", err.message);
  }
});

// ================= Khởi động =================
async function boot() {
  try {
    const status = await api("/api/auth/status", { auth: true });
    if (status.user) return startApp(status.user);
    showAuth(status.setup_required ? (status.setup_allowed ? "setup" : "setup-blocked") : "login");
  } catch (e) {
    showAuth("error", e.message);
  }
}

boot();
