import io

import docx
import pymupdf
import pytest

from ragbot.ingest.loaders import LoadError, load_file, load_pdf, looks_garbled

TEXT = "Article 1. This page has a real text layer with more than fifty characters of content."


def _pdf_with_scanned_page() -> bytes:
    """Trang 1 có lớp chữ; trang 2 chỉ là ảnh (giả lập bản scan)."""
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 72), TEXT, fontsize=9)
    png = page.get_pixmap(dpi=100).tobytes("png")

    page2 = src.new_page()
    page2.insert_image(page2.rect, stream=png)
    return src.tobytes()


def test_scanned_page_goes_to_ocr_only():
    calls = []

    def fake_ocr(png: bytes) -> str:
        calls.append(png[:8])
        return "Nội dung OCR của trang scan"

    progress = []
    pages = load_pdf(_pdf_with_scanned_page(), fake_ocr, progress.append, workers=2)
    assert [p.number for p in pages] == [1, 2]
    assert pages[0].ocr is False and "real text layer" in pages[0].text
    assert pages[1].ocr is True and pages[1].text == "Nội dung OCR của trang scan"
    assert len(calls) == 1 and calls[0] == b"\x89PNG\r\n\x1a\n"
    assert any("OCR 1/1" in m for m in progress)


def test_signed_scan_with_text_overlay_still_ocr():
    """Bản scan có chữ ký số: ảnh cả trang + một dòng "Người ký..." dài hơn MIN_TEXT_CHARS."""
    src = pymupdf.open(stream=_pdf_with_scanned_page())
    src[1].insert_text((20, 20), "Signed by: Cong thong tin dien tu Chinh phu, Email: thongtinchinhphu@chinhphu.vn", fontsize=6)
    pages = load_pdf(src.tobytes(), lambda png: "OCR text", workers=1)
    assert pages[0].ocr is False
    assert pages[1].ocr is True and pages[1].text == "OCR text"


def test_many_scanned_pages_all_ocr():
    src = pymupdf.open(stream=_pdf_with_scanned_page())
    scan = src[1].get_pixmap(dpi=50).tobytes("png")
    for _ in range(10):
        src.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=scan)
    calls, progress = [], []
    pages = load_pdf(src.tobytes(), lambda png: calls.append(1) or "x" * 60, progress.append, workers=3)
    assert len(pages) == 12 and len(calls) == 11
    assert [p.ocr for p in pages] == [False] + [True] * 11
    assert progress[-1] == "OCR 11/11 trang"


def test_scanned_pdf_without_llm_fails_clearly():
    with pytest.raises(LoadError, match="cần OCR"):
        load_pdf(_pdf_with_scanned_page(), None)


def test_garbled_tcvn3_detected():
    tcvn3 = "§iÒu 1. Ph¹m vi ®iÒu chØnh NghÞ ®Þnh nµy quy ®Þnh vÒ hµnh vi vi ph¹m hµnh chÝnh trong lÜnh vùc"
    unicode_vi = "Điều 1. Phạm vi điều chỉnh Nghị định này quy định về hành vi vi phạm hành chính trong lĩnh vực"
    assert looks_garbled(tcvn3)
    assert not looks_garbled(unicode_vi)


def test_load_docx_includes_tables():
    d = docx.Document()
    d.add_paragraph("Điều 1. Quy định chung")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Mức phạt"
    table.rows[0].cells[1].text = "200.000 đồng"
    buf = io.BytesIO()
    d.save(buf)
    doc = load_file("quy_dinh_chung.docx", buf.getvalue(), None)
    assert doc.title == "quy dinh chung"
    assert "Điều 1. Quy định chung" in doc.text and "Mức phạt | 200.000 đồng" in doc.text


def test_unsupported_extension():
    with pytest.raises(LoadError, match="Chưa hỗ trợ"):
        load_file("bang.xlsx", b"x", None)
