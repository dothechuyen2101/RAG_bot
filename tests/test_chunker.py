from conftest import LEGAL_TEXT

from ragbot.ingest.chunker import MAX_CHARS, TARGET_CHARS, chunk_document, find_so_hieu
from ragbot.ingest.loaders import Page


def test_legal_document_one_chunk_per_article():
    chunks = chunk_document("Nghị định xử phạt", [Page(1, LEGAL_TEXT)])
    articles = [c.article for c in chunks if c.article]
    assert articles == ["Điều 1", "Điều 2", "Điều 5", "Điều 12"]

    dieu5 = next(c for c in chunks if c.article == "Điều 5")
    assert dieu5.section == "Chương II. HÀNH VI VI PHẠM, HÌNH THỨC XỬ PHẠT > Mục 1. VI PHẠM QUY TẮC GIAO THÔNG ĐƯỜNG BỘ"
    # Dòng ngữ cảnh ở đầu đoạn có tên văn bản + số hiệu.
    assert dieu5.text.startswith("[Nghị định xử phạt (100/2019/NĐ-CP) > Chương II.")
    assert "6.000.000 đồng" in dieu5.text

    preamble = chunks[0]
    assert preamble.article == "" and "Căn cứ Luật" in preamble.text


def test_page_numbers_tracked_across_pages():
    first, second = LEGAL_TEXT.split("Điều 5.")
    chunks = chunk_document("ND", [Page(1, first), Page(2, "Điều 5." + second)])
    by_article = {c.article: c for c in chunks}
    assert by_article["Điều 1"].page_start == 1
    assert by_article["Điều 5"].page_start == 2
    assert by_article["Điều 12"].page_end == 2


def test_long_article_split_by_clause_keeps_title():
    clauses = "\n".join(f"{i}. Khoản số {i} " + "quy định chi tiết " * 30 for i in range(1, 12))
    text = f"Điều 1. Điều ngắn\nNội dung.\nĐiều 7. Điều rất dài\n{clauses}"
    chunks = [c for c in chunk_document("Luật", [Page(1, text)]) if c.article == "Điều 7"]
    assert len(chunks) > 1
    assert all("Điều 7. Điều rất dài" in c.text for c in chunks)
    assert all(len(c.text) <= MAX_CHARS + 100 for c in chunks)
    # Không khoản nào bị cắt ngang giữa hai đoạn.
    for i in range(1, 12):
        assert sum(f"{i}. Khoản số {i} " in c.text for c in chunks) == 1


def test_plain_document_packed_with_overlap():
    lines = [f"Dòng thứ {i}: nội dung thông thường không phải văn bản luật." for i in range(200)]
    chunks = chunk_document("Ghi chú", [Page(None, "\n".join(lines))])
    assert len(chunks) > 3
    assert all(c.article == "" for c in chunks)
    assert all(len(c.text) <= TARGET_CHARS + 200 for c in chunks)
    # Dòng cuối đoạn trước được lặp lại ở đầu đoạn sau.
    last_line = chunks[0].text.splitlines()[-1]
    assert last_line in chunks[1].text


def test_find_so_hieu():
    assert find_so_hieu("Số: 45/2019/QH14 ngày...") == "45/2019/QH14"
    assert find_so_hieu("Thông tư 01/2021/TT-BCA hướng dẫn") == "01/2021/TT-BCA"
    assert find_so_hieu("không có số hiệu") == ""


def test_empty_pages_give_no_chunks():
    assert chunk_document("Rỗng", [Page(1, "   \n  ")]) == []
