from datetime import date

from ragbot.ingest.legal import analyze, collection_statuses
from ragbot.store import Document

REPLACING = """CHÍNH PHỦ
Số: 168/2024/NĐ-CP
Hà Nội, ngày 26 tháng 12 năm 2024
NGHỊ ĐỊNH
Quy định xử phạt vi phạm hành chính về trật tự, an toàn giao thông
Căn cứ Luật Xử lý vi phạm hành chính ngày 20 tháng 6 năm 2012;
Căn cứ Nghị định số 123/2021/NĐ-CP sửa đổi, bổ sung Nghị định số 100/2019/NĐ-CP;
Chương I
QUY ĐỊNH CHUNG
Điều 1. Phạm vi điều chỉnh
Nghị định này quy định về hành vi vi phạm hành chính.
Điều 54. Hiệu lực thi hành
1. Nghị định này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2025.
2. Nghị định này thay thế Nghị định số 100/2019/NĐ-CP ngày 30 tháng 12 năm 2019 của Chính phủ
và Điều 2 của Nghị định số 123/2021/NĐ-CP ngày 28 tháng 12 năm 2021.
"""

AMENDING = """CHÍNH PHỦ
Số: 123/2021/NĐ-CP
Hà Nội, ngày 28 tháng 12 năm 2021
NGHỊ ĐỊNH
Sửa đổi, bổ sung một số điều của Nghị định số 100/2019/NĐ-CP
Căn cứ Luật Tổ chức Chính phủ ngày 19 tháng 6 năm 2015;
Điều 1. Sửa đổi, bổ sung một số điều của Nghị định số 100/2019/NĐ-CP
1. Sửa đổi khoản 2 Điều 5 như sau: ...
Điều 2. Hiệu lực thi hành
Nghị định này có hiệu lực kể từ ngày ký.
"""


def _doc(id_, so_hieu, effective="", replaces=(), amends=(), status="", note=""):
    return Document(id_, 1, f"Văn bản {id_}", f"{id_}.pdf", "pdf", id_, None, None, None, "", so_hieu=so_hieu,
                    effective_date=effective, replaces=list(replaces), amends=list(amends), status=status,
                    status_note=note)


def test_reads_number_dates_and_replacements():
    info = analyze(REPLACING)
    assert info.so_hieu == "168/2024/NĐ-CP"
    assert info.issued_date == "2024-12-26"
    assert info.effective_date == "2025-01-01"
    assert info.replaces == ["100/2019/NĐ-CP"]
    # Chỉ thay Điều 2 của 123/2021: văn bản đó bị sửa đổi chứ không hết hiệu lực.
    # Dòng "Căn cứ ... 123/2021 sửa đổi 100/2019" không được tính.
    assert info.amends == ["123/2021/NĐ-CP"]


def test_amending_decree_effective_from_signing():
    info = analyze(AMENDING)
    assert info.so_hieu == "123/2021/NĐ-CP"
    assert info.amends == ["100/2019/NĐ-CP"] and info.replaces == []
    assert info.effective_date == info.issued_date == "2021-12-28"


def test_plain_document_has_no_legal_info():
    info = analyze("Biên bản họp ngày 3 tháng 4.\nNội dung bình thường, không có số hiệu.")
    assert info.so_hieu == "" and info.replaces == [] and info.amends == [] and info.effective_date == ""


def test_statuses_follow_relations_between_documents():
    old = _doc("a", "100/2019/NĐ-CP", "2020-01-01")
    amending = _doc("b", "123/2021/NĐ-CP", "2022-01-01", amends=["100/2019/NĐ-CP"])
    new = _doc("c", "168/2024/NĐ-CP", "2025-01-01", replaces=["100/2019/NĐ-CP"], amends=["123/2021/NĐ-CP"])
    docs = [old, amending, new]

    before = collection_statuses(docs, date(2024, 12, 30))
    assert before["a"].code == "expiring" and "168/2024/NĐ-CP" in before["a"].note and "01/01/2025" in before["a"].note
    assert before["c"].code == "pending"
    assert before["b"].code == ""  # văn bản sửa đổi nó chưa có hiệu lực

    after = collection_statuses(docs, date(2025, 6, 1))
    assert after["a"].code == "expired" and after["a"].label == "Hết hiệu lực"
    assert after["a"].note == "Bị thay thế bởi 168/2024/NĐ-CP"
    assert after["b"].code == "amended" and after["c"].code == ""


def test_manual_status_wins():
    old = _doc("a", "100/2019/NĐ-CP", status="in_force", note="Đã kiểm tra trên cổng thông tin")
    new = _doc("c", "168/2024/NĐ-CP", "2025-01-01", replaces=["100/2019/NĐ-CP"])
    st = collection_statuses([old, new], date(2026, 1, 1))["a"]
    assert (st.code, st.note, st.manual) == ("in_force", "Đã kiểm tra trên cổng thông tin", True)
