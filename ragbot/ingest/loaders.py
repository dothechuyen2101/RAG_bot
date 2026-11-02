import io
import ipaddress
import re
import socket
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import docx
import httpx
import pymupdf
import trafilatura

# Trang có ít ký tự hơn mức này được coi là trang scan (ảnh) và đưa đi OCR.
MIN_TEXT_CHARS = 50
# Trang có ảnh phủ >= 90% diện tích và ít hơn SCAN_OVERLAY_CHARS ký tự cũng là trang scan.
FULL_PAGE_IMAGE = 0.9
SCAN_OVERLAY_CHARS = 500
OCR_DPI = 200
OCR_MAX_PX = 2400
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

OCR_INSTRUCTION = """Đây là ảnh một trang tài liệu. Hãy chép lại NGUYÊN VĂN toàn bộ chữ trong ảnh.
- Giữ đúng chính tả và dấu tiếng Việt, không sửa, không tóm tắt, không thêm lời bình.
- Giữ cấu trúc dòng: mỗi tiêu đề (Phần, Chương, Mục, Điều), mỗi khoản (1., 2.) và điểm (a), b)) nằm trên một dòng riêng.
- Bảng biểu chép thành bảng markdown.
- Bỏ qua con dấu, chữ ký, hình trang trí. Chữ không đọc được thì ghi [không rõ].
- Nếu trang không có chữ, trả về chuỗi rỗng.
Chỉ trả về nội dung đã chép."""

ProgressFn = Callable[[str], None]
OcrFn = Callable[[bytes], str]


class LoadError(RuntimeError):
    pass


class OcrConfirmationNeeded(LoadError):
    """PDF cần OCR nhiều trang hơn mức cho phép: hỏi người dùng trước khi tốn nhiều lượt gọi model."""

    def __init__(self, ocr_pages: int, total_pages: int):
        super().__init__(f"PDF có {ocr_pages}/{total_pages} trang scan cần OCR, mỗi trang tốn 1 lượt gọi model")
        self.ocr_pages = ocr_pages
        self.total_pages = total_pages


@dataclass
class Page:
    number: int | None  # số trang (bắt đầu từ 1); None nếu nguồn không có khái niệm trang
    text: str
    ocr: bool = False


@dataclass
class LoadedDoc:
    title: str
    source: str
    source_type: str  # pdf | docx | txt | url
    pages: list[Page]
    raw: bytes | None = field(default=None, repr=False)
    extension: str = ""

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


def _noop(_: str) -> None:
    pass


def looks_garbled(text: str) -> bool:
    """Phát hiện PDF dùng font TCVN3/ABC cũ: có lớp chữ nhưng ra ký tự rác như "§iÒu".

    Văn bản tiếng Việt Unicode hầu như không có ký hiệu vùng U+00A0–U+00BF
    (§ ¬ ª µ ¶ · ¸ ¹ ...), còn font TCVN3 dùng dày đặc vùng này thay cho chữ có dấu.
    """
    letters = sum(c.isalpha() for c in text)
    if letters < MIN_TEXT_CHARS:
        return False
    symbols = sum("\u00a0" < c <= "\u00bf" for c in text)
    return symbols / letters > 0.02


def _needs_ocr(page: "pymupdf.Page", text: str) -> bool:
    if len(text) < MIN_TEXT_CHARS or looks_garbled(text):
        return True
    # Bản scan có chữ ký số thường có thêm vài dòng chữ ("Người ký: ...") phủ trên ảnh cả trang.
    area = abs(page.rect) or 1.0
    image_cover = sum(abs(pymupdf.Rect(im["bbox"]) & page.rect) for im in page.get_image_info()) / area
    return image_cover >= FULL_PAGE_IMAGE and len(text) < SCAN_OVERLAY_CHARS


def _render(page: "pymupdf.Page") -> bytes:
    # Ảnh xám nhẹ hơn khoảng 2 lần so với ảnh màu mà không ảnh hưởng tới việc đọc chữ.
    longest_inch = max(page.rect.width, page.rect.height) / 72 or 1.0
    dpi = min(OCR_DPI, int(OCR_MAX_PX / longest_inch))
    return page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("png")


def load_pdf(data: bytes, ocr_fn: OcrFn | None, progress: ProgressFn = _noop, workers: int = 4,
             max_ocr_pages: int | None = None) -> list[Page]:
    """max_ocr_pages: cần OCR nhiều trang hơn mức này thì ném OcrConfirmationNeeded (None: không giới hạn)."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise LoadError(f"Không mở được file PDF: {e}") from e

    with doc:
        pages: list[Page] = []
        need_ocr: list[int] = []
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            pages.append(Page(number=i + 1, text=text))
            if _needs_ocr(page, text):
                need_ocr.append(i)

        if not need_ocr:
            return pages
        if ocr_fn is None:
            raise LoadError(f"{len(need_ocr)} trang cần OCR nhưng chưa cấu hình LLM")
        if max_ocr_pages and len(need_ocr) > max_ocr_pages:
            raise OcrConfirmationNeeded(len(need_ocr), len(pages))

        progress(f"PDF có {len(need_ocr)}/{len(pages)} trang scan, đang OCR...")
        workers = max(1, workers)
        done = 0
        # Render ảnh trong thread chính (pymupdf không an toàn đa luồng) và chỉ giữ tối đa
        # 2*workers ảnh trong bộ nhớ: PDF scan vài trăm trang render hết một lúc sẽ tốn hàng trăm MB.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: dict = {}
            todo = iter(need_ocr)
            while True:
                while len(pending) < 2 * workers and (i := next(todo, None)) is not None:
                    pending[pool.submit(ocr_fn, _render(doc[i]))] = i
                if not pending:
                    break
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in finished:
                    i = pending.pop(fut)
                    pages[i] = Page(number=i + 1, text=fut.result().strip(), ocr=True)
                    done += 1
                    # Dạng "OCR n/m" được api.py đọc để vẽ thanh tiến trình.
                    progress(f"OCR {done}/{len(need_ocr)} trang")
    return pages


def load_docx(data: bytes) -> list[Page]:
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return [Page(number=None, text="\n".join(parts).strip())]


def load_text(data: bytes) -> list[Page]:
    return [Page(number=None, text=data.decode("utf-8", errors="replace").strip())]


def load_file(
    filename: str, data: bytes, ocr_fn: OcrFn | None, progress: ProgressFn = _noop, workers: int = 4,
    max_ocr_pages: int | None = None,
) -> LoadedDoc:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        pages, kind = load_pdf(data, ocr_fn, progress, workers, max_ocr_pages), "pdf"
    elif ext == ".docx":
        pages, kind = load_docx(data), "docx"
    elif ext in {".txt", ".md"}:
        pages, kind = load_text(data), "txt"
    else:
        raise LoadError(f"Chưa hỗ trợ định dạng {ext or '(không có đuôi)'}")
    title = clean_title(Path(filename).stem)
    return LoadedDoc(title=title, source=filename, source_type=kind, pages=pages, raw=data, extension=ext)


def check_public_host(url: str) -> None:
    """Chặn link tới địa chỉ nội bộ (localhost, mạng LAN, địa chỉ metadata của cloud...), để người dùng
    không mượn server đọc các trang trong mạng nội bộ rồi xem nội dung qua trích dẫn."""
    host = urlparse(url).hostname
    if not host:
        raise LoadError(f"Link không hợp lệ: {url}")
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        raise LoadError(f"Không tìm thấy tên miền {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])  # bỏ "%scope" của IPv6 link-local
        mapped = getattr(ip, "ipv4_mapped", None)
        if not (mapped or ip).is_global:
            raise LoadError(f"Không nạp được link tới địa chỉ nội bộ ({host})")


def _read_limited(resp: httpx.Response, max_bytes: int | None, url: str) -> bytes:
    def too_big() -> LoadError:
        return LoadError(f"Nội dung tại {url} lớn hơn giới hạn {max_bytes / 1024 / 1024:g} MB")

    if max_bytes and int(resp.headers.get("content-length") or 0) > max_bytes:
        raise too_big()
    buf = bytearray()
    for chunk in resp.iter_bytes():
        buf += chunk
        if max_bytes and len(buf) > max_bytes:
            raise too_big()
    return bytes(buf)


def load_url(url: str, ocr_fn: OcrFn | None, progress: ProgressFn = _noop, workers: int = 4,
             max_ocr_pages: int | None = None, allow_private: bool = False, max_bytes: int | None = None,
             transport: httpx.BaseTransport | None = None) -> LoadedDoc:
    """transport: chỉ dùng trong test."""
    url = url.strip()
    if urlparse(url).scheme not in {"http", "https"}:
        raise LoadError(f"Link không hợp lệ: {url}")

    def guard(request: httpx.Request) -> None:
        # Chạy trước MỖI request, kể cả khi trang chuyển hướng sang địa chỉ khác.
        if not allow_private:
            check_public_host(str(request.url))

    progress(f"Đang tải {url}")
    try:
        with httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": USER_AGENT},
                          event_hooks={"request": [guard]}, transport=transport) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content = _read_limited(resp, max_bytes, url)
    except httpx.HTTPStatusError as e:
        raise LoadError(f"Trang trả về lỗi {e.response.status_code}: {url}") from e
    except httpx.HTTPError as e:
        raise LoadError(f"Không tải được {url}: {e}") from e

    content_type = resp.headers.get("content-type", "").lower()
    name = Path(urlparse(str(resp.url)).path).name or urlparse(url).netloc

    if "pdf" in content_type or content[:5] == b"%PDF-":
        pages = load_pdf(content, ocr_fn, progress, workers, max_ocr_pages)
        return LoadedDoc(Path(name).stem or url, url, "url", pages, raw=content, extension=".pdf")
    if "officedocument.wordprocessingml" in content_type:
        return LoadedDoc(Path(name).stem or url, url, "url", load_docx(content), raw=content, extension=".docx")

    html = content.decode(resp.encoding or "utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, include_tables=True, favor_recall=True, output_format="markdown")
    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise LoadError(
            f"Không lấy được nội dung từ {url}. Trang có thể yêu cầu đăng nhập, chặn bot, "
            "hoặc tải nội dung bằng JavaScript. Hãy tải file PDF/Word của văn bản rồi upload."
        )
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta and meta.title else "") or name
    return LoadedDoc(title=title.strip(), source=url, source_type="url", pages=[Page(None, text.strip())])


def clean_title(title: str) -> str:
    return re.sub(r"[_\s]+", " ", title).strip()
