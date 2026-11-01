# RAG Bot: hỏi đáp theo tài liệu của bạn

Bot trả lời câu hỏi **chỉ dựa trên tài liệu bạn nạp vào** (PDF, kể cả bản scan; Word; TXT; link web) và ghi rõ nguồn: tên tài liệu, trang, Điều.
Khi tài liệu không có câu trả lời, bot **hỏi bạn trước** rồi mới tìm trên internet (Tavily). Câu trả lời từ internet được gắn nhãn riêng.

- **Đăng nhập**, nhiều tài khoản: mỗi người có bộ tài liệu riêng, người khác (kể cả admin) không xem được.
- Nhiều **bộ tài liệu** tách biệt (ví dụ "Pháp luật giao thông", "Thuế"), mỗi lần hỏi chỉ tìm trong bộ đang chọn.
- Chia đoạn theo cấu trúc **văn bản pháp luật** (Phần, Chương, Mục, Điều, khoản) và nhận diện số hiệu (`100/2019/NĐ-CP`).
- Tìm kiếm kết hợp **ngữ nghĩa** (embedding) và **từ khóa** (giữ dấu tiếng Việt), nên hỏi "Điều 12 Nghị định 100/2019" vẫn trúng.
- Trang PDF scan, hoặc PDF dùng font cũ TCVN3 bị lỗi chữ, được **OCR bằng LLM**.
- Trả lời và OCR dùng **Wellflow** (mặc định, Claude qua gateway), **Gemini** hoặc **OpenAI**. Embedding dùng **Gemini** (mặc định) hoặc OpenAI.
  Model treo, quá tải hoặc hết lượt thì bot tự chuyển sang model dự phòng.

## Cài đặt

Yêu cầu Python 3.12.

```bash
cd RAG_bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Mở `.env` và điền:

| Biến | Lấy ở đâu | Bắt buộc |
|---|---|---|
| `WELLFLOW_API_KEY` | https://wellflow.dev/dashboard | Có (khi `LLM_PROVIDER=wellflow`, mặc định) |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Có (embedding mặc định dùng Gemini) |
| `TAVILY_API_KEY` | https://app.tavily.com (miễn phí 1.000 lượt/tháng) | Chỉ khi cần tìm internet |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | Chỉ khi `LLM_PROVIDER=openai` hoặc `EMBED_PROVIDER=openai` |

## Chạy

```bash
.venv/bin/python server.py
```

Mở http://localhost:8000 rồi làm theo các bước:

1. **Lần đầu**: tạo tài khoản quản trị (admin) ngay trên trang. Các lần sau thì đăng nhập.
2. **Tạo bộ tài liệu**, ví dụ "Pháp luật giao thông".
3. **Nạp tài liệu**: kéo thả file vào bất kỳ đâu trên trang, hoặc dán link vào ô bên trái rồi Enter.
   Thanh bên trái hiện tiến trình của từng file, kể cả số trang đã OCR.
4. **Đặt câu hỏi** ở ô dưới cùng (Enter gửi, Shift+Enter xuống dòng). Bấm số trích dẫn như **[1]** để mở đoạn trích gốc.
5. Nếu tài liệu không có (hoặc chỉ có một phần) câu trả lời, bot hiện thẻ hỏi ý kiến: sửa truy vấn nếu cần rồi bấm
   **Tìm trên internet**, hoặc **Không, cảm ơn**. Trang web tìm được có thể đưa vào bộ tài liệu bằng nút **Thêm vào bộ tài liệu**.

Giao diện tự theo chế độ sáng/tối của hệ điều hành, đổi bằng nút ở góc dưới thanh bên trái.

Lịch sử trò chuyện lưu trên server, nên đổi trình duyệt hay máy khác vẫn còn. Mỗi bộ tài liệu có nhiều cuộc trò chuyện: nút **Hội thoại mới** bắt đầu cuộc mới (cuộc cũ vẫn giữ), nút **Lịch sử** để mở lại hoặc xóa cuộc cũ. Lịch sử từ phiên bản cũ (chỉ nằm trong trình duyệt) được tự chuyển lên server ở lần mở đầu tiên.
Muốn mở từ máy khác trong mạng LAN: `.venv/bin/python server.py --host 0.0.0.0`.

## Đăng nhập & tài khoản

- **Tài khoản đầu tiên** là admin. Vì lý do bảo mật, màn hình tạo admin chỉ dùng được khi mở trang **trên chính máy chạy server**
  (localhost). Server chạy ở máy khác thì tạo bằng lệnh `create-user` bên dưới.
  Các bộ tài liệu tạo trước khi có đăng nhập sẽ thuộc về tài khoản admin đầu tiên.
- **Mỗi tài khoản chỉ thấy bộ tài liệu của mình.** Admin thêm tài khoản, đặt lại mật khẩu, xóa tài khoản trong menu
  *Quản lý tài khoản* (bấm tên mình ở góc dưới bên trái), nhưng cũng không xem được tài liệu của người khác.
  Xóa một tài khoản sẽ xóa luôn các bộ tài liệu của tài khoản đó.
- Mật khẩu ít nhất 8 ký tự, được băm bằng scrypt. Phiên đăng nhập kéo dài 30 ngày; đổi hoặc đặt lại mật khẩu sẽ đăng xuất các phiên khác.
  Sai mật khẩu 5 lần liên tiếp thì bị khóa tạm 1 phút (tăng dần tới 15 phút).
- Quản lý bằng dòng lệnh (dùng khi quên mật khẩu admin, hoặc server chạy trên máy chủ từ xa):

```bash
.venv/bin/python server.py create-user an --admin   # tạo tài khoản, hỏi mật khẩu
.venv/bin/python server.py reset-password an        # đặt lại mật khẩu
.venv/bin/python server.py list-users
```

- Mở ra **internet** thì nên đặt sau reverse proxy có **HTTPS** (Caddy, Nginx...), vì qua HTTP thường mật khẩu và cookie đi dạng không mã hóa.
  Khi truy cập qua HTTPS, cookie phiên tự bật cờ `Secure`.

Sửa `.env` xong thì phải khởi động lại server (Ctrl+C rồi chạy lại).

## Lưu ý

- **Đổi model embedding** (`EMBED_PROVIDER`, `GEMINI_EMBED_MODEL`, `GEMINI_EMBED_DIM`, `OPENAI_EMBED_MODEL`): vector của model cũ và model mới không so sánh được với nhau, nên app sẽ yêu cầu bấm **Re-index**. Re-index dùng văn bản đã lưu, không phải OCR lại.
  Đổi `LLM_PROVIDER` (model trả lời) thì không cần re-index.
- **PDF scan** tốn **1 lượt gọi API cho mỗi trang**. Khi thử với `gemini-3.6-flash` và 4 luồng (`OCR_WORKERS`), 6 trang mất 12 giây, nên một Nghị định 247 trang mất khoảng 8–10 phút. Chi phí ước tính khoảng 1–3 USD ở gói trả phí.
  Gói miễn phí của Gemini **không đủ** để OCR tài liệu dài (xem mục dưới).
- **Link web**: một số trang thư viện pháp luật bắt đăng nhập, chặn bot, hoặc tải nội dung bằng JavaScript nên không lấy được chữ. Khi đó hãy tải file PDF/Word của văn bản về rồi upload.
- **Bảo mật**: với gói **miễn phí** của Gemini, Google có thể dùng dữ liệu gửi lên để cải thiện sản phẩm. Tài liệu nhạy cảm nên dùng gói trả phí.
- Câu trả lời chỉ mang tính tham khảo. Với văn bản pháp luật, hãy kiểm tra **hiệu lực** của văn bản tại nguồn chính thức.

## Gemini: gói miễn phí và model dự phòng

Gói miễn phí chỉ cho khoảng **20 lượt gọi mỗi ngày cho mỗi model**. Hạn mức đặt lại lúc 0h giờ Pacific, tức khoảng 15h giờ Việt Nam.
Mỗi câu hỏi tốn 1–2 lượt, mỗi trang PDF scan tốn 1 lượt.

Bot dùng `GEMINI_CHAT_MODEL` trước. Nếu model đó báo quá tải (503), giới hạn tốc độ (429), hết lượt trong ngày, hoặc không trả lời trong 30 giây (OCR là 90 giây),
bot chuyển ngay sang model kế tiếp trong `GEMINI_FALLBACK_MODELS`. Mỗi lần chuyển đều được ghi log ra terminal.
Khi mọi model đều hết lượt, bot báo rõ trên giao diện.

Mặc định là `gemini-3.6-flash`, rồi đến `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`.
Khi thử, `gemini-3.8-flash` trả lời tốt nhất nhưng chậm (90–180 giây mỗi câu), nên được để cuối danh sách.

Muốn dùng thường xuyên thì nên **bật thanh toán** trong Google AI Studio (Tier 1: chỉ cần liên kết tài khoản thanh toán).
Gói trả phí có hạn mức cao hơn nhiều, và dữ liệu không bị dùng để cải thiện sản phẩm của Google.

## Wellflow (mặc định cho trả lời và OCR)

Wellflow là gateway theo chuẩn OpenAI (`https://api.wellflow.dev/v1/chat/completions`), có Claude, GPT, Gemini, DeepSeek...
Xem danh sách model bằng `GET /v1/models`. Wellflow **chưa có API embedding** (`/v1/embeddings` trả 501), nên embedding dùng Gemini.

```
LLM_PROVIDER=wellflow
EMBED_PROVIDER=gemini
WELLFLOW_API_KEY=wf_...
WELLFLOW_BASE_URL=https://api.wellflow.dev/v1
WELLFLOW_CHAT_MODEL=claude-opus-5-5
WELLFLOW_FALLBACK_MODELS=claude-sonnet-5-5,claude-haiku-5.5
```

Đo thử ngày 01/11/2026 (cả ba đều OCR đúng dấu tiếng Việt và trích dẫn đúng):

| Model | Chat | Trả lời từ tài liệu (JSON) | OCR 1 trang |
|---|---|---|---|
| claude-opus-5-5 | 8 giây | 14–47 giây | 17 giây |
| claude-sonnet-5-5 | 10–42 giây | 6 giây | 22 giây |
| claude-haiku-5.5 | 5 giây | 5 giây | 36 giây |

Đôi khi một request bị treo quá 30 giây. Khi đó bot chuyển sang model kế tiếp trong `WELLFLOW_FALLBACK_MODELS`.

## Dữ liệu được lưu ở đâu

Mọi thứ nằm trong thư mục `data/` (không đưa vào git):

```
data/ragbot.db   tài khoản, phiên đăng nhập, bộ tài liệu, tài liệu, lịch sử trò chuyện, chỉ mục từ khóa (SQLite FTS5)
data/chroma/     vector embedding (ChromaDB)
data/files/      file gốc đã upload
data/texts/      văn bản đã trích (kể cả kết quả OCR), dùng khi re-index
```

Muốn xóa sạch thì dừng app rồi xóa thư mục `data/`.

## Cấu trúc mã nguồn

```
server.py                    chạy web server (FastAPI + uvicorn)
web/                         giao diện: index.html, style.css, app.js (không cần build)
ragbot/api.py                API cho giao diện; thao tác dài trả tiến trình dạng NDJSON
ragbot/auth.py               tài khoản, băm mật khẩu, phiên đăng nhập, chống dò mật khẩu
ragbot/config.py             đọc cấu hình .env
ragbot/providers/            Gemini / OpenAI: chat, JSON có schema, OCR ảnh, embedding
ragbot/ingest/loaders.py     đọc PDF (OCR trang scan), DOCX, TXT, link
ragbot/ingest/chunker.py     chia đoạn theo Phần/Chương/Mục/Điều/khoản
ragbot/ingest/pipeline.py    nạp, xóa, re-index tài liệu
ragbot/store/                SQLite (danh mục + FTS5) và ChromaDB
ragbot/retrieval.py          tìm kết hợp vector + từ khóa (Reciprocal Rank Fusion)
ragbot/answer.py             trả lời từ tài liệu (full/partial/none) và từ internet
ragbot/prompts.py            prompt tiếng Việt
```

## Kiểm thử

```bash
.venv/bin/python -m pytest -q
```

Test dùng LLM và embedding giả lập nên không cần API key, gồm cả test API của giao diện web, đăng nhập,
phân quyền và tách dữ liệu giữa các tài khoản (FastAPI TestClient).
