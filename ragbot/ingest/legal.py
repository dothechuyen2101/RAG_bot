"""Hiệu lực văn bản pháp luật.

Khi nạp, đọc từ nội dung văn bản: số hiệu, ngày ban hành, ngày có hiệu lực, và các văn bản mà nó
thay thế hoặc sửa đổi. Trạng thái của từng tài liệu được tính lại mỗi lần dùng, từ quan hệ giữa các
tài liệu trong cùng bộ: nạp Nghị định mới thay thế Nghị định cũ thì bản cũ tự thành "Hết hiệu lực".

Nhận diện dựa vào mẫu câu quen thuộc nên có thể sai; người dùng sửa được trên giao diện,
và trạng thái nhập tay luôn được ưu tiên.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .chunker import DIEU_RE, SO_HIEU_RE, _strip, find_so_hieu

OWN_NUMBER_RE = re.compile(r"\bSố\s*:\s*(" + SO_HIEU_RE.pattern[2:-2] + r")", re.I)
_DATE = r"(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})|(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"
DATE_RE = re.compile(r"ngày\s+(?:" + _DATE + ")", re.I)
EFFECTIVE_RE = re.compile(
    r"có\s+hiệu\s+lực(?:\s+thi\s+hành)?(?:\s+kể)?\s+từ\s+ngày\s+(?:(ký)|" + _DATE + ")", re.I
)
# "Nghị định này", "Luật này"...: câu nói về chính văn bản đang đọc.
SELF_REF_RE = re.compile(r"\b(?:nghị định|luật|bộ luật|thông tư|quyết định|nghị quyết|pháp lệnh|văn bản)\s+này\b", re.I)
# Chỉ thay/bỏ một phần ("Điều 11 của Nghị định số ...") thì văn bản kia vẫn còn hiệu lực, chỉ là bị sửa đổi.
PARTIAL_RE = re.compile(r"\b(?:điều|khoản|chương|mục)\s+(?:\d|[ivxlc]+\b)|\bđiểm\s+[a-zđ]\b|\bphụ lục\b|\bcụm từ\b", re.I)
REPLACE_WORDS = ("thay thế",)
AMEND_WORDS = ("sửa đổi", "bổ sung", "bãi bỏ", "đính chính")
HEAD_MAX_LINES = 25

STATUS_LABELS = {
    "in_force": "Còn hiệu lực",
    "pending": "Chưa có hiệu lực",
    "expiring": "Sắp hết hiệu lực",
    "amended": "Đã được sửa đổi",
    "expired": "Hết hiệu lực",
}
# Trạng thái người dùng tự chọn được; "" = để app tự nhận diện.
MANUAL_STATUSES = ("in_force", "amended", "expired")
# Khi xếp hạng đoạn tìm được, đoạn của văn bản hết hiệu lực bị nhân điểm với hệ số này.
EXPIRED_PENALTY = 0.8


@dataclass
class LegalInfo:
    so_hieu: str = ""
    issued_date: str = ""  # YYYY-MM-DD
    effective_date: str = ""
    replaces: list[str] = field(default_factory=list)  # số hiệu các văn bản bị văn bản này thay thế
    amends: list[str] = field(default_factory=list)  # ... bị văn bản này sửa đổi, bổ sung một phần


@dataclass
class LegalStatus:
    code: str = ""  # "" (chưa rõ) | in_force | pending | expiring | amended | expired
    note: str = ""
    manual: bool = False

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.code, "")

    @property
    def warning(self) -> bool:
        """Có điều cần lưu ý khi trích dẫn văn bản này."""
        return self.code in ("pending", "expiring", "amended", "expired")


def _iso(groups: tuple) -> str:
    nums = [g for g in groups if g]
    if len(nums) != 3:
        return ""
    d, m, y = (int(x) for x in nums)
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def vn_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _sentences(lines: list[str]) -> list[str]:
    # Ghép dòng trước rồi mới tách câu: tiêu đề và câu dài hay bị ngắt xuống dòng giữa chừng.
    return [s for s in re.split(r"(?<=[.;])\s+", " ".join(lines)) if s]


def _relations(sentence: str, own: str, replaces: dict, amends: dict) -> None:
    numbers = [m for m in SO_HIEU_RE.finditer(sentence) if m.group(0) != own]
    if not numbers:
        return
    low = sentence.lower()
    replacing = any(w in low for w in REPLACE_WORDS) or ("hết hiệu lực" in low and SELF_REF_RE.search(sentence))
    if replacing:
        for m in numbers:
            # Xét đoạn ngay trước số hiệu: "... Điều 11 của Nghị định số 41/2017/NĐ-CP" là thay một phần.
            partial = PARTIAL_RE.search(low[max(0, m.start() - 60) : m.start()])
            (amends if partial else replaces)[m.group(0)] = True
    elif any(w in low for w in AMEND_WORDS):
        for m in numbers:
            amends[m.group(0)] = True


def analyze(text: str) -> LegalInfo:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return LegalInfo()
    own_match = OWN_NUMBER_RE.search(text[:3000])
    own = own_match.group(1) if own_match else find_so_hieu(text)

    basis = next((i for i, ln in enumerate(lines) if ln.lower().startswith("căn cứ")), None)
    first_article = next((i for i, ln in enumerate(lines) if DIEU_RE.match(_strip(ln))), len(lines))
    # Phần đầu (quốc hiệu, số hiệu, ngày, tên văn bản) và phần thân từ Điều đầu tiên. Bỏ qua các dòng
    # "Căn cứ ...": chúng nhắc tới nhiều văn bản khác (kể cả văn bản sửa đổi) mà không phải quan hệ của văn bản này.
    head = lines[: min(basis if basis is not None else first_article, HEAD_MAX_LINES)]
    body = lines[first_article:]

    issued = DATE_RE.search(" ".join(head))
    info = LegalInfo(so_hieu=own, issued_date=_iso(issued.groups()) if issued else "")

    replaces: dict[str, bool] = {}
    amends: dict[str, bool] = {}
    _relations(" ".join(head), own, replaces, amends)
    for s in _sentences(body):
        _relations(s, own, replaces, amends)
        if not info.effective_date and SELF_REF_RE.search(s) and (m := EFFECTIVE_RE.search(s)):
            info.effective_date = info.issued_date if m.group(1) else _iso(m.groups()[1:])
    info.replaces = list(replaces)
    info.amends = [n for n in amends if n not in replaces]
    return info


def collection_statuses(docs: list, today: date | None = None) -> dict[str, LegalStatus]:
    """Trạng thái hiệu lực của từng tài liệu (theo id), dựa trên quan hệ giữa các tài liệu trong bộ."""
    now = (today or date.today()).isoformat()
    replaced_by: dict[str, list] = defaultdict(list)
    amended_by: dict[str, list] = defaultdict(list)
    for d in docs:
        for n in d.replaces:
            replaced_by[n].append(d)
        for n in d.amends:
            amended_by[n].append(d)

    def names(ds: list) -> str:
        return ", ".join(d.so_hieu or d.title for d in ds)

    out: dict[str, LegalStatus] = {}
    for d in docs:
        if d.status in MANUAL_STATUSES:
            out[d.id] = LegalStatus(d.status, d.status_note, manual=True)
            continue
        newer = [r for r in replaced_by.get(d.so_hieu or "", []) if r.id != d.id]
        in_force = [r for r in newer if not r.effective_date or r.effective_date <= now]
        amenders = [r for r in amended_by.get(d.so_hieu or "", [])
                    if r.id != d.id and (not r.effective_date or r.effective_date <= now)]
        if in_force:
            out[d.id] = LegalStatus("expired", f"Bị thay thế bởi {names(in_force)}")
        elif newer:
            start = min(r.effective_date for r in newer)
            out[d.id] = LegalStatus("expiring", f"Sẽ bị thay thế bởi {names(newer)} từ ngày {vn_date(start)}")
        elif d.effective_date and d.effective_date > now:
            out[d.id] = LegalStatus("pending", f"Có hiệu lực từ ngày {vn_date(d.effective_date)}")
        elif amenders:
            out[d.id] = LegalStatus("amended", f"Được sửa đổi, bổ sung bởi {names(amenders)}")
        else:
            out[d.id] = LegalStatus()
    return out
