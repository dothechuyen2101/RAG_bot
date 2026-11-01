"""Chia văn bản thành đoạn.

Văn bản pháp luật (có từ 2 "Điều" trở lên): mỗi Điều một đoạn, kèm ngữ cảnh Phần/Chương/Mục.
Điều quá dài thì tách theo khoản, mỗi phần tách vẫn mang dòng tiêu đề Điều.
Tài liệu khác: gom các dòng thành đoạn khoảng TARGET_CHARS ký tự, có chồng lấn.
"""

import re
from dataclasses import dataclass

from .loaders import Page

TARGET_CHARS = 1200
MAX_CHARS = 2000
OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 20
MAX_HEADING_LEN = 150

_DECORATION = re.compile(r"^[#>*_\s]+|[*_\s]+$")
PHAN_RE = re.compile(r"^phần\s+(thứ\s+\w+|[IVXLC]+)\b", re.I)
CHUONG_RE = re.compile(r"^chương\s+([IVXLC]+|\d+)\b", re.I)
MUC_RE = re.compile(r"^mục\s+\d+\b", re.I)
DIEU_RE = re.compile(r"^điều\s+(\d+[a-zđ]?)\s*[.:]", re.I)
KHOAN_RE = re.compile(r"^\d+\.\s")
# Số hiệu văn bản: 100/2019/NĐ-CP, 45/2019/QH14, 01/2021/TT-BCA ...
SO_HIEU_RE = re.compile(r"\b\d+/\d{4}/[A-ZĐ][A-ZĐ0-9]*(?:-[A-ZĐ0-9]+)*\b")

Unit = tuple[str, int | None]  # (dòng văn bản, số trang)


@dataclass
class Chunk:
    text: str  # đã gắn dòng ngữ cảnh ở đầu; dùng để embed, tìm từ khóa và đưa cho LLM
    page_start: int | None
    page_end: int | None
    section: str  # "Chương II > Mục 1"
    article: str  # "Điều 5" hoặc ""


@dataclass
class _Block:
    article: str
    context: tuple[str, str, str]  # (phần, chương, mục)
    lines: list[Unit]


def find_so_hieu(text: str) -> str:
    m = SO_HIEU_RE.search(text[:3000])
    return m.group(0) if m else ""


def _strip(line: str) -> str:
    return _DECORATION.sub("", line).strip()


def _is_heading(s: str, pattern: re.Pattern) -> bool:
    return len(s) <= MAX_HEADING_LEN and bool(pattern.match(s))


def _is_upper_title(s: str) -> bool:
    return any(c.isalpha() for c in s) and s == s.upper() and len(s) <= MAX_HEADING_LEN


def chunk_document(title: str, pages: list[Page]) -> list[Chunk]:
    lines: list[Unit] = [(ln.rstrip(), p.number) for p in pages for ln in p.text.splitlines() if ln.strip()]
    if not lines:
        return []

    full_text = "\n".join(ln for ln, _ in lines)
    so_hieu = find_so_hieu(full_text)
    label = title if not so_hieu or so_hieu in title else f"{title} ({so_hieu})"

    n_articles = sum(1 for ln, _ in lines if DIEU_RE.match(_strip(ln)))
    if n_articles >= 2:
        chunks = _chunk_legal(label, lines)
    else:
        chunks = [_make_chunk(label, "", "", piece) for piece in _pack(_split_long_units(lines, TARGET_CHARS), OVERLAP_CHARS)]
    return [c for c in chunks if len(c.text) - len(c.text.split("\n", 1)[0]) >= MIN_CHUNK_CHARS]


def _chunk_legal(label: str, lines: list[Unit]) -> list[Chunk]:
    phan = chuong = muc = ""
    blocks: list[_Block] = []
    current = _Block("", ("", "", ""), [])

    i = 0
    while i < len(lines):
        raw, _page = lines[i]
        s = _strip(raw)
        level = next(
            (name for name, pat in (("phan", PHAN_RE), ("chuong", CHUONG_RE), ("muc", MUC_RE)) if _is_heading(s, pat)),
            None,
        )
        if level:
            heading = s
            # Tiêu đề chương thường nằm ở dòng kế tiếp, viết hoa: "Chương I" / "NHỮNG QUY ĐỊNH CHUNG".
            if i + 1 < len(lines):
                nxt = _strip(lines[i + 1][0])
                if _is_upper_title(nxt) and not DIEU_RE.match(nxt):
                    heading = f"{s}. {nxt}" if not s.endswith((".", ":")) else f"{s} {nxt}"
                    i += 1
            if level == "phan":
                phan, chuong, muc = heading, "", ""
            elif level == "chuong":
                chuong, muc = heading, ""
            else:
                muc = heading
            blocks.append(current)
            current = _Block("", (phan, chuong, muc), [])
        elif m := DIEU_RE.match(s):
            blocks.append(current)
            current = _Block(f"Điều {m.group(1)}", (phan, chuong, muc), [lines[i]])
        else:
            current.lines.append(lines[i])
        i += 1
    blocks.append(current)

    chunks: list[Chunk] = []
    for b in blocks:
        if not b.lines:
            continue
        section = " > ".join(x for x in b.context if x)
        body_len = sum(len(ln) + 1 for ln, _ in b.lines)
        if body_len <= MAX_CHARS:
            chunks.append(_make_chunk(label, section, b.article, b.lines))
            continue
        if not b.article:
            # Phần mở đầu (căn cứ ban hành...) dài: gom như văn bản thường.
            chunks += [_make_chunk(label, section, "", p) for p in _pack(_split_long_units(b.lines, TARGET_CHARS), OVERLAP_CHARS)]
            continue
        # Điều dài: gom theo khoản, phần sau lặp lại dòng tiêu đề Điều để không mất ngữ cảnh.
        title_line = b.lines[0]
        groups: list[list[Unit]] = []
        for unit in b.lines[1:]:
            if not groups or KHOAN_RE.match(_strip(unit[0])):
                groups.append([])
            groups[-1].append(unit)
        units = [("\n".join(ln for ln, _ in g), g[0][1]) for g in groups]
        budget = MAX_CHARS - len(title_line[0]) - 12
        for n, piece in enumerate(_pack(_split_long_units(units, budget), 0, budget)):
            head = title_line if n == 0 else (f"{title_line[0]} (tiếp)", piece[0][1])
            chunks.append(_make_chunk(label, section, b.article, [head, *piece]))
    return chunks


def _make_chunk(label: str, section: str, article: str, units: list[Unit]) -> Chunk:
    pages = [p for _, p in units if p is not None]
    header = " > ".join(x for x in (label, section) if x)
    body = "\n".join(ln for ln, _ in units)
    return Chunk(
        text=f"[{header}]\n{body}",
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        section=section,
        article=article,
    )


def _split_long_units(units: list[Unit], limit: int = MAX_CHARS) -> list[Unit]:
    out: list[Unit] = []
    for text, page in units:
        out += [(piece, page) for piece in _split_text(text, limit)]
    return out


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    for sep in ("\n", ". ", "; ", " "):
        parts = text.split(sep)
        if len(parts) > 1:
            out, buf = [], ""
            for part in parts:
                candidate = f"{buf}{sep}{part}" if buf else part
                if len(candidate) <= limit:
                    buf = candidate
                else:
                    if buf:
                        out.append(buf)
                    buf = part
            if buf:
                out.append(buf)
            return [piece for p in out for piece in _split_text(p, limit)]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _pack(units: list[Unit], overlap: int, limit: int = TARGET_CHARS) -> list[list[Unit]]:
    """Gom các dòng liên tiếp thành đoạn dài tối đa ~limit ký tự, lặp lại ~overlap ký tự cuối đoạn trước."""
    pieces: list[list[Unit]] = []
    buf: list[Unit] = []
    size = 0
    for unit in units:
        if buf and size + len(unit[0]) > limit:
            pieces.append(buf)
            carry: list[Unit] = []
            carried = 0
            for prev in reversed(buf):
                if carried + len(prev[0]) > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev[0]) + 1
            buf, size = carry, carried
        buf.append(unit)
        size += len(unit[0]) + 1
    if buf:
        pieces.append(buf)
    return pieces
