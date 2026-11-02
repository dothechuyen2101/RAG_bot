import pytest
from conftest import LEGAL_TEXT, FakeLLM

from ragbot.evaluation import Case, format_report, load_cases, run_eval, summarize


def _ingest(stack):
    stack["ingestor"].ingest_file(stack["cid"], "nd100.txt", LEGAL_TEXT.encode())


def test_load_cases(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text('# bộ câu hỏi mẫu\n\n{"question": "Phạt nồng độ cồn?", "article": "Điều 5", "doc": "100/2019"}\n',
                    encoding="utf-8")
    assert load_cases(path) == [Case("Phạt nồng độ cồn?", "Điều 5", "100/2019")]
    for bad in ('{"article": "Điều 5"}', "không phải json", '{"question": ""}'):
        path.write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Dòng 1"):
            load_cases(path)


def test_retrieval_eval_reports_hits_and_misses(stack):
    _ingest(stack)
    cases = [
        Case("phạt nồng độ cồn người điều khiển xe ô tô", article="Điều 5", doc="100/2019"),
        Case("Điều 2 áp dụng cho ai?", article="Điều 2"),
        Case("đối tượng áp dụng", article="Điều 2", doc="45/2020"),  # sai văn bản: không tính là đúng
    ]
    results = run_eval(stack["retriever"], stack["cid"], cases, top_k=3)
    assert [r.rank for r in results] == [1, 1, None]
    summary = summarize(results, 3)
    assert summary["hit@1"] == pytest.approx(2 / 3) and summary["mrr"] == pytest.approx(2 / 3)
    report = format_report(results, 3)
    assert "2/3 (67%)" in report and "cần: Điều 2 · 45/2020 → không tìm được đoạn đúng" in report


def test_answer_eval_checks_citations(stack):
    _ingest(stack)
    llm = FakeLLM({"coverage": "full", "answer": "Phạt [1].", "citations": [1], "web_query": ""})
    case = Case("Điều 5 phạt nồng độ cồn thế nào?", article="Điều 5")
    results = run_eval(stack["retriever"], stack["cid"], [case], 3, llm=llm)
    assert results[0].cited is True and summarize(results, 3)["cited"] == 1.0

    llm.json_result = {"coverage": "full", "answer": "Phạt [2].", "citations": [2], "web_query": ""}
    results = run_eval(stack["retriever"], stack["cid"], [case], 3, llm=llm)
    assert results[0].cited is False and "câu trả lời không trích" in format_report(results, 3)
