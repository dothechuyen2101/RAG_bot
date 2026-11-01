# RAG Bot: hỏi đáp theo tài liệu của bạn

Bot trả lời câu hỏi **chỉ dựa trên tài liệu bạn nạp vào** (PDF, kể cả bản scan; Word; TXT; link web) và ghi rõ nguồn: tên tài liệu, trang, Điều.
Khi tài liệu không có câu trả lời, bot **hỏi bạn trước** rồi mới tìm trên internet (Tavily). Câu trả lời từ internet được gắn nhãn riêng.

- Nhiều **bộ tài liệu** tách biệt (ví dụ "Pháp luật giao thông", "Thuế"), mỗi lần hỏi chỉ tìm trong bộ đang chọn.
- Chia đoạn theo cấu trúc **văn bản pháp luật** (Phần, Chương, Mục, Điều, khoản) và nhận diện số hiệu (`100/2019/NĐ-CP`).
- Tìm kiếm kết hợp **ngữ nghĩa** (embedding) và **từ khóa** (giữ dấu tiếng Việt), nên hỏi "Điều 12 Nghị định 100/2019" vẫn trúng.
- Trang PDF scan, hoặc PDF dùng font cũ TCVN3 bị lỗi chữ, được **OCR bằng LLM** (Gemini hoặc OpenAI).
- Chọn **Gemini** (mặc định) hoặc **OpenAI** trong file `.env`.

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
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Có (khi dùng Gemini) |
| `TAVILY_API_KEY` | https://app.tavily.com (miễn phí 1.000 lượt/tháng) | Chỉ khi cần tìm internet |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | Chỉ khi `LLM_PROVIDER=openai` hoặc `EMBED_PROVIDER=openai` |

## Chạy

```bash
.venv/bin/streamlit run app.py
```

Mở http://localhost:8501 rồi làm theo các bước:

1. **Tạo bộ tài liệu** ở thanh bên trái.
2. **Nạp tài liệu**: kéo thả file, hoặc dán link (mỗi dòng một link) rồi bấm *Nạp*.
3. **Đặt câu hỏi** ở khung chat. Bấm *📎 Nguồn* để xem đoạn trích gốc.
4. Nếu bot báo không tìm thấy (hoặc chỉ trả lời được một phần), bạn có thể sửa truy vấn rồi bấm **🌐 Tìm trên internet**, hoặc bấm **Không, cảm ơn**.
   Trang web tìm được có thể đưa vào bộ tài liệu bằng nút **➕ Thêm vào bộ tài liệu**.

Sửa `.env` xong thì phải khởi động lại ứng dụng (Ctrl+C rồi chạy lại).

## Lưu ý

- **Đổi model embedding** (`EMBED_PROVIDER`, `GEMINI_EMBED_MODEL`, `GEMINI_EMBED_DIM`, `OPENAI_EMBED_MODEL`): vector của model cũ và model mới không so sánh được với nhau, nên app sẽ yêu cầu bấm **Re-index**. Re-index dùng văn bản đã lưu, không phải OCR lại.
  Đổi `LLM_PROVIDER` (model trả lời) thì không cần re-index.
- **PDF scan** được OCR từng trang. Ví dụ một Nghị định scan 247 trang mất khoảng 10–20 phút với 4 luồng (`OCR_WORKERS`). Chi phí là ước tính: khoảng 1–3 USD với `gemini-3.8-flash`, tùy độ dày chữ trên trang.
  Nếu bị giới hạn tốc độ (gói miễn phí), app tự chờ rồi thử lại. Có thể giảm `OCR_WORKERS` xuống 1–2.
- **Link web**: một số trang thư viện pháp luật bắt đăng nhập, chặn bot, hoặc tải nội dung bằng JavaScript nên không lấy được chữ. Khi đó hãy tải file PDF/Word của văn bản về rồi upload.
- **Bảo mật**: với gói **miễn phí** của Gemini, Google có thể dùng dữ liệu gửi lên để cải thiện sản phẩm. Tài liệu nhạy cảm nên dùng gói trả phí.
- Câu trả lời chỉ mang tính tham khảo. Với văn bản pháp luật, hãy kiểm tra **hiệu lực** của văn bản tại nguồn chính thức.

## Dữ liệu được lưu ở đâu

Mọi thứ nằm trong thư mục `data/` (không đưa vào git):

```
data/ragbot.db   danh mục bộ tài liệu, tài liệu, chỉ mục từ khóa (SQLite FTS5)
data/chroma/     vector embedding (ChromaDB)
data/files/      file gốc đã upload
data/texts/      văn bản đã trích (kể cả kết quả OCR), dùng khi re-index
```

Muốn xóa sạch thì dừng app rồi xóa thư mục `data/`.

## Cấu trúc mã nguồn

```
app.py                       giao diện Streamlit
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

Test dùng LLM và embedding giả lập nên không cần API key, gồm cả test giao diện bằng Streamlit AppTest.
