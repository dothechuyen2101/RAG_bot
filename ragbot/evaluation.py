"""Đo chất lượng tìm kiếm (và trả lời) bằng bộ câu hỏi kiểm tra.

File JSONL, mỗi dòng một câu hỏi kèm đoạn tài liệu đúng:
    {"question": "Không đội mũ bảo hiểm bị phạt bao nhiêu?", "article": "Điều 6", "doc": "100/2019"}
- article: số Điều của đoạn đúng (bỏ trống nếu tài liệu không chia theo Điều)
- doc: một phần tên tài liệu hoặc số hiệu, để phân biệt khi nhiều văn bản cùng có Điều đó
- contains: (tùy chọn) cụm từ mà đoạn đúng phải chứa
Dòng trống và dòng bắt đầu bằng # được bỏ qua.

Chạy: python server.py eval <file.jsonl> --collection "<tên bộ tài liệu>" [--answer]
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from .answer import answer_from_docs
from .providers import LLM
from .retrieval import Hit, Retriever


@dataclass
class Case:
    question: str
    article: str = ""
    doc: str = ""
    contains: str = ""


@dataclass
class CaseResult:
    case: Case
    rank: int | None  # vị trí (từ 1) của đoạn đúng trong kết quả tìm kiếm; None nếu không có
    found: list[str]  # nhãn các đoạn tìm được, để xem khi sai
    cited: bool | None = None  # câu trả lời có trích đoạn đúng không (chỉ khi chạy --answer)
    coverage: str = ""


def load_cases(path: Path) -> list[Case]:
    names = {f.name for f in fields(Case)}
    cases = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
            case = Case(**{k: str(v).strip() for k, v in data.items() if k in names})
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            raise ValueError(f"Dòng {n} của {path.name} không hợp lệ: {e}") from e
        if not case.question:
            raise ValueError(f"Dòng {n} của {path.name} thiếu câu hỏi")
        cases.append(case)
    return cases


def hit_matches(case: Case, hit: Hit) -> bool:
    if case.article and hit.meta.get("article", "").lower() != " ".join(case.article.split()).lower():
        return False
    # Dòng đầu mỗi đoạn là "[Tên văn bản (số hiệu) > Chương ...]".
    doc_text = f"{hit.meta.get('title', '')} {hit.text.split(chr(10), 1)[0]}".lower()
    if case.doc and case.doc.lower() not in doc_text:
        return False
    return not case.contains or case.contains.lower() in hit.text.lower()


def _label(hit: Hit) -> str:
    return f"{hit.meta.get('article') or '—'} · {hit.meta.get('title', '')}"


def run_eval(retriever: Retriever, collection_id: int, cases: list[Case], top_k: int, llm: LLM | None = None,
             condense_llm: LLM | None = None, progress: Callable[[str], None] = lambda _: None) -> list[CaseResult]:
    """llm: nếu có, hỏi thêm LLM để xem câu trả lời có trích đúng đoạn không (tốn 1 lượt gọi mỗi câu)."""
    results = []
    for i, case in enumerate(cases, 1):
        progress(f"[{i}/{len(cases)}] {case.question}")
        hits = retriever.search(collection_id, case.question, top_k)
        rank = next((k for k, h in enumerate(hits, 1) if hit_matches(case, h)), None)
        result = CaseResult(case, rank, [_label(h) for h in hits])
        if llm is not None:
            ans = answer_from_docs(llm, retriever, collection_id, case.question, [], top_k,
                                   condense_llm=condense_llm)
            expected = hits[rank - 1] if rank else None
            article = expected.meta.get("article", "") if expected else ""
            result.coverage = ans.coverage
            result.cited = expected is not None and any(
                s.doc_id == expected.meta.get("doc_id")
                and (not article or re.search(rf"{re.escape(article)}\b", s.location))
                for s in ans.sources
            )
        results.append(result)
    return results


def summarize(results: list[CaseResult], top_k: int) -> dict[str, float]:
    n = len(results) or 1
    out = {f"hit@{k}": sum(1 for r in results if r.rank and r.rank <= k) / n for k in sorted({1, 3, top_k})}
    out["mrr"] = sum(1 / r.rank for r in results if r.rank) / n
    answered = [r for r in results if r.cited is not None]
    if answered:
        out["cited"] = sum(1 for r in answered if r.cited) / len(answered)
    return out


def format_report(results: list[CaseResult], top_k: int) -> str:
    n = len(results)
    summary = summarize(results, top_k)

    def line(label: str, rate: float) -> str:
        return f"  {label:<32}{round(rate * n)}/{n} ({rate:.0%})"

    lines = [f"Kết quả trên {n} câu hỏi (TOP_K={top_k}):"]
    for k in sorted({1, 3, top_k}):
        lines.append(line("Đoạn đúng đứng đầu" if k == 1 else f"Đoạn đúng trong {k} kết quả đầu", summary[f"hit@{k}"]))
    lines.append(f"  {'MRR (1 là tốt nhất)':<32}{summary['mrr']:.2f}")
    if "cited" in summary:
        lines.append(line("Câu trả lời trích đúng đoạn", summary["cited"]))

    misses = [r for r in results if not r.rank or r.cited is False]
    if misses:
        lines.append("\nCâu chưa đạt:")
        for r in misses:
            want = " · ".join(x for x in (r.case.article, r.case.doc, r.case.contains) if x)
            why = "không tìm được đoạn đúng" if not r.rank else f"tìm được (vị trí {r.rank}) nhưng câu trả lời không trích"
            lines.append(f"- {r.case.question}\n    cần: {want} → {why}")
            if not r.rank:
                lines.append(f"    tìm được: {'; '.join(r.found[:3]) or '(không có)'}")
    return "\n".join(lines)
