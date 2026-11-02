# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A document Q&A bot (RAG) for Vietnamese documents, especially legal texts. It answers only from documents the user uploads and cites title, page and Điều (article). When the documents don't cover a question, it asks the user before searching the web with Tavily. It is a FastAPI backend plus a vanilla-JS frontend with no build step. It supports several accounts, and each account has its own isolated document sets ("bộ tài liệu" = collections).

Comments, docstrings, LLM prompts, user-facing error messages, the README and commit messages are all in **Vietnamese**. Keep it that way.

## Commands

Requires Python 3.12. The README uses POSIX venv paths. On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`; Windows also needs the Microsoft Visual C++ Redistributable, or pymupdf fails with `DLL load failed`.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # fill in API keys (see README table)

.venv/bin/python server.py                 # http://127.0.0.1:8000  (--host 0.0.0.0 --port N)
.venv/bin/python server.py create-user <name> --admin
.venv/bin/python server.py reset-password <name>
.venv/bin/python server.py list-users
.venv/bin/python server.py eval evals/mau.jsonl --collection "<name>" [--answer]   # retrieval quality report

.venv/bin/python -m pytest -q                                   # all tests, no API keys needed
.venv/bin/python -m pytest -q tests/test_api.py::test_serves_web_ui   # single test
```

There is no linter or formatter config. Restart the server after editing `.env`, because `get_settings()` is `lru_cache`d. Any env var overrides `.env`, so `DATA_DIR=<tmp> server.py --port 8765` runs a throwaway instance without touching `data/`.

`server.py` calls `truststore.inject_into_ssl()` before importing anything that does HTTP, so TLS is verified against the OS certificate store (needed behind Kaspersky or a corporate HTTPS proxy). Keep that call above the `ragbot` imports.

## Architecture

**Wiring.** `server.py` calls `ragbot.api.create_app(services_factory)`. `Services` in `ragbot/services.py` is the dependency container. `create_app` builds it **lazily on the first request**, so a missing API key becomes an HTTP 503 with a message instead of a startup crash. Tests inject fakes via `create_app(lambda: svc, local_setup_only=False)`. Each task can use its own model: `get_llm(settings, model)` takes the task model (`CONDENSE_MODEL`, `OCR_MODEL`) as primary and keeps the provider's fallback list. `Services.llm` answers, `Services.condense_llm` rewrites follow-ups (`None` means use `llm`), and `Ingestor.llm` is the OCR model.

**Ask flow** (`POST /api/conversations/{id}/ask`, `ragbot/answer.py`):
1. `condense_question` rewrites a follow-up into a standalone question using the last `HISTORY_TURNS` messages.
2. `Retriever.search` (`ragbot/retrieval.py`):
   - Hybrid search: Chroma cosine search plus SQLite FTS5 BM25, fused with Reciprocal Rank Fusion. `build_fts_query` ORs single tokens and adjacent syllable pairs, because Vietnamese words span several syllables.
   - "Điều N" in the question is fetched directly by chunk metadata and placed first (`pinned`). If the question also gives a document number like `100/2019`, only that document's article is pinned; otherwise pinned articles are capped at `top_k // 2`.
   - Parts of one Điều count as a single result. `_whole_article` merges the contiguous parts around the hit, up to `MAX_ARTICLE_CHARS`.
   - Chunks of documents whose legal status is `expired` get their RRF score multiplied by `EXPIRED_PENALTY`. Hits carry `legal_status`/`legal_label`/`legal_note` in `meta`.
3. `llm.complete_json(DOCS_SYSTEM, ..., DOCS_SCHEMA)` returns `coverage` (`full`/`partial`/`none`), `answer`, `citations` and `web_query`. Citation numbers `[n]` index into the hits. `format_context` adds legal warnings to each source line, and prompt rule 7 tells the model to prefer documents still in force.
4. If coverage is not `full`, the stored assistant message carries an `offer` (`open`→`done`/`declined`/`expired`). The web search (`answer_from_web` via Tavily) runs only when the user accepts via `/messages/{mid}/web`. Asking a new question expires every open offer in that conversation.

**Streaming.**
- Long operations (ingest, re-index, ask, web search) use `stream()` in `api.py`. The work runs in a thread, events go through a queue, and the response is NDJSON. The last line is always `{"type": "done"|"answer"|"error"}`.
- Progress travels as `ProgressFn` callbacks. `api.py` parses the progress text `OCR n/m` with a regex (`OCR_PROGRESS`) to drive the progress bar, so don't reword that message.
- Errors in `USER_ERRORS` are shown verbatim; any other error is prefixed with its class name.
- Answer text streams as `{"type": "delta", "text", "reset"?}` events:
  - `LLM.complete`/`complete_json` take `on_text`, which receives the **whole text so far**. Providers call `on_text("")` when they restart on a fallback model, and `text_stream()` in `api.py` turns that into a `reset` delta.
  - For JSON answers, `partial_answer()` pulls the `"answer"` string out of the incomplete JSON.
  - `ChatCompletionsLLM` retries without streaming on HTTP 400/422/501.
  - Wellflow currently buffers the whole response and sends it in one burst (measured 2026-11-02). The frontend detects a burst under 400 ms and falls back to its typewriter animation.

**Ingest** (`ragbot/ingest/`):
- `loaders.py` produces `list[Page]`:
  - A PDF page goes to LLM OCR when it has fewer than 50 chars of text, when it looks like legacy TCVN3-font garble (`looks_garbled`), or when it is a full-page image with only a thin text overlay. Pages are rendered on the main thread because pymupdf is not thread-safe, then OCR'd in a bounded thread pool.
  - More than `OCR_CONFIRM_PAGES` pages to OCR raises `OcrConfirmationNeeded` before any OCR call. The API emits `item_confirm`, and the UI re-sends the item with `confirm_ocr`.
  - `load_url` refuses non-public IPs through `check_public_host`, run by an httpx request hook so it also covers every redirect hop, unless `ALLOW_PRIVATE_URLS`. Downloads are capped at `max_bytes`.
- `chunker.py` treats any document with 2 or more "Điều" headings as a legal text. Each Điều becomes one chunk, prefixed with a context line `[Title (số hiệu) > Phần > Chương > Mục]`. An oversized Điều is split by khoản, and later parts repeat the heading as `Điều N. ... (tiếp)`. Other documents are packed into chunks of about 1200 chars with overlap.
- `legal.py`:
  - `analyze(text)` regex-extracts the document number, issued date, effective date, and the numbers it `replaces` or `amends`. "Căn cứ" lines are skipped because they cite other documents. A reference preceded by "Điều/khoản/điểm ... của" counts as an amendment, not a replacement.
  - `collection_statuses(docs)` computes each document's status (`expired`/`expiring`/`pending`/`amended`) **at read time** from relations between documents in the same collection. A manual `status` set from the UI always wins.
- `pipeline.py` (`Ingestor`) embeds the chunks, writes to Chroma and SQLite, and rolls back on failure. It also saves the extracted text, including OCR output, to `data/texts/{doc_id}.json`. That file is what lets a re-index skip OCR and what `backfill_legal_info()` reads at startup, so any change to ingest must keep it written. Raw files are kept in `data/files/{doc_id}.<ext>`; HTML pages keep only their text.

**Embedding model tracking.** Each collection stores `embed_model = embedder.model_id`, for example `gemini:gemini-embedding-2:1536`. A mismatch with the current embedder makes `needs_reindex` true, which blocks both ingest (`EmbedModelMismatch`) and asking (409) until the user re-indexes. Changing the format of a `model_id` string therefore forces every existing collection to re-index. Changing `LLM_PROVIDER` does not.

**Storage** (everything under `data/`, which is gitignored):
- `store/db.py` uses one shared SQLite connection (`check_same_thread=False`) guarded by an `RLock`. It holds users, sessions (only a SHA-256 of each token is stored), collections, documents (including the legal columns in `LEGAL_COLUMNS`), conversations, messages and the `chunks_fts` table.
- The FTS5 tokenizer uses `remove_diacritics 0` on purpose, so that "điều" does not match "điêu"/"dieu".
- Messages are stored as JSON blobs in `messages.data`, in exactly the shape the frontend renders (`answer_message()` in `api.py`). Sources include `doc_id`, `page` and legal status.
- Migrations are ad hoc. `Database.__init__` runs `_migrate_*` before `PRAGMA foreign_keys = ON` and `executescript(SCHEMA)`. `SCHEMA` only uses `CREATE ... IF NOT EXISTS`, so a new column on an existing table needs its own migration (see `_migrate_documents_legal`). `documents.so_hieu IS NULL` means the legal info hasn't been read yet.
- `store/vector.py` keeps one Chroma collection `col_<collection_id>` per document set, with cosine distance and `embedding_function=None`, so vectors are always computed by us. Chunk IDs are `{doc_id}:{i:04d}`, sortable by position. `find(where)` fetches by metadata (`article`, `doc_id`). Chroma metadata can't hold `None`, so page `0` means "no page".

**Providers** (`ragbot/providers/`):
- `LLM` and `Embedder` are Protocols in `base.py`. `LLM` has `complete`, `complete_json` (both with optional `on_text`) and `ocr`, so the OCR model must accept images. `Embedder` has `model_id` and `embed(texts, kind)`, where `kind` is `"document"` or `"query"`.
- Wellflow (the default LLM) is an OpenAI-compatible gateway handled by `ChatCompletionsLLM` (`/chat/completions`). It sometimes wraps JSON in code fences, which `parse_json` strips. `OpenAILLM` uses the OpenAI Responses API instead. Wellflow has no embeddings endpoint, so embedding defaults to Gemini.
- Resilience works in two layers:
  - `call_with_fallback` walks the model list (primary plus `*_FALLBACK_MODELS`). A transient error (408/429/5xx, connection failure, or a timeout of 30s for chat / 90s for OCR) moves on to the next model. A per-day quota 429 skips that model. A non-transient error is raised immediately.
  - The outer tenacity decorator `api_retry` retries the whole list with backoff. SDK clients are created with `max_retries=0` so that tenacity owns retries. Tavily (`websearch.py`, which uses requests) has its own small retry for connection errors and timeouts.
- Gemini embedding quirks:
  - `gemini-embedding-2` takes text prefixes instead of `task_type`.
  - Each text must be its own `Content`, otherwise the texts are merged into one vector.
  - Older models need manual L2 normalization.

**Auth and isolation** (`ragbot/auth.py`, `api.py`):
- Passwords are hashed with scrypt. Sessions are cookie-based and last 30 days. Failed logins are rate-limited per client IP and username together, and the throttle state lives in memory.
- Every collection has an `owner_id`. All endpoints must reach collections, documents and conversations through `own_collection` / `own_document` / `own_conversation`, which return **404 (not 403)** for another user's resources. `GET /api/documents/{doc_id}/file` checks ownership through the document's collection. `tests/test_auth.py::test_collections_are_private_per_user` lists every endpoint that must 404 for another user, so add new endpoints there.
- Admins manage accounts but cannot see other users' documents.
- The first admin can only be created from localhost (`local_setup_only`). The rest go through the `server.py create-user` CLI.
- Middleware rejects any non-GET request whose `Origin` host differs from `Host` (CSRF protection).

**Evaluation** (`ragbot/evaluation.py`): `server.py eval` loads JSONL cases (`question`, `article`, `doc`, `contains`). It reports hit@1/3/k and MRR for retrieval and, with `--answer`, whether the answer cites the expected chunk (matched by `doc_id` + article).

**Frontend** (`web/`):
- Plain `index.html`/`style.css`/`app.js`, served at `/` and `/static`.
- `app.js` builds DOM through the `el()` helper, which never accepts HTML from data, and renders markdown with its own `renderMarkdown` plus `escapeHtml`. Keep both of those properties.
- While answering, `state.thinking` (status line, which also blocks sending) and `state.streaming` (partial answer) are tracked per conversation. `pushMessage` swaps the `#streaming` node for the final message.
- Colors come from CSS tokens on `:root` with dark-mode overrides, so new styles should use the tokens.
- `localStorage` holds only UI conveniences such as the selected collection and the open conversation. Chat history lives on the server.

## Tests

`tests/conftest.py` provides:
- `FakeEmbedder` (hashed bag-of-words vectors)
- `FakeLLM` (canned JSON, text and OCR output; it simulates streaming when given `on_text`)
- `scanned_pdf(n)`
- sample legal text `LEGAL_TEXT`
- a `stack` fixture that builds a real `Database`, `VectorStore` and `Ingestor` on `tmp_path`

API tests use FastAPI's `TestClient` with `Settings(_env_file=None, data_dir=tmp_path)` so the real `.env` and `data/` are never touched. Test modules import the fakes with `from conftest import ...`. Network-facing loader tests use `httpx.MockTransport` (the `transport` argument of `load_url`) and monkeypatch `socket.getaddrinfo`.

## Conventions

- A new setting goes in `Settings` (`ragbot/config.py`) and in `.env.example` with a Vietnamese comment. If users need to know about it, it also goes in the README.
- `app.py` (the old Streamlit UI) was removed.
