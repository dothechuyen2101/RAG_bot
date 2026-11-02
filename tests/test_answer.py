import pytest
from conftest import LEGAL_TEXT, FakeLLM

from ragbot.answer import answer_from_docs, answer_from_web, partial_answer
from ragbot.websearch import WebResult


def _ready(stack, llm):
    stack["ingestor"].ingest_file(stack["cid"], "nd100.txt", LEGAL_TEXT.encode())
    return dict(llm=llm, retriever=stack["retriever"], collection_id=stack["cid"], top_k=4)


def test_full_answer_maps_citations_to_sources(stack):
    llm = FakeLLM({"coverage": "full", "answer": "Phạt 6-8 triệu đồng [1].", "citations": [1, 99], "web_query": ""})
    ans = answer_from_docs(question="Phạt nồng độ cồn ô tô bao nhiêu?", history=[], **_ready(stack, llm))
    assert ans.coverage == "full" and not ans.offer_web
    assert [s.n for s in ans.sources] == [1]  # số trích dẫn ngoài phạm vi bị bỏ
    assert ans.sources[0].location == "Trang 1" or ans.sources[0].location.startswith("Điều")
    # Ngữ cảnh gửi LLM được đánh số và có tên nguồn.
    assert "[1] (Nguồn: nd100" in llm.json_calls[0]


def test_not_found_offers_web_search_without_searching(stack):
    llm = FakeLLM({"coverage": "none", "answer": "", "citations": [], "web_query": "thuế thu nhập cá nhân 2026"})
    ans = answer_from_docs(question="Thuế TNCN năm 2026 thế nào?", history=[], **_ready(stack, llm))
    assert ans.coverage == "none" and ans.offer_web
    assert ans.text == "" and ans.sources == []
    assert ans.web_query == "thuế thu nhập cá nhân 2026"


def test_partial_answer_keeps_text_and_offers_web(stack):
    llm = FakeLLM({"coverage": "partial", "answer": "Chỉ có mức phạt ô tô [1].", "citations": [1],
                   "web_query": "mức phạt xe máy"})
    ans = answer_from_docs(question="Phạt ô tô và xe máy?", history=[], **_ready(stack, llm))
    assert ans.offer_web and ans.text and ans.sources


def test_empty_collection_skips_llm(stack):
    llm = FakeLLM()
    ans = answer_from_docs(llm, stack["retriever"], stack["cid"], "Bất kỳ câu hỏi", [], 4)
    assert ans.coverage == "none" and ans.offer_web
    assert llm.json_calls == []


def test_similarity_gate_skips_llm(stack):
    llm = FakeLLM({"coverage": "full", "answer": "x [1]", "citations": [1], "web_query": ""})
    ans = answer_from_docs(question="Phạt nồng độ cồn", history=[], min_similarity=1.01, **_ready(stack, llm))
    assert ans.coverage == "none" and llm.json_calls == []


def test_followup_question_is_condensed(stack):
    llm = FakeLLM(text="Mức phạt nồng độ cồn với xe máy là bao nhiêu?")
    history = [{"role": "user", "content": "Phạt nồng độ cồn ô tô?"}, {"role": "assistant", "content": "6-8 triệu [1]."}]
    ans = answer_from_docs(question="còn xe máy thì sao?", history=history, **_ready(stack, llm))
    assert ans.question == "Mức phạt nồng độ cồn với xe máy là bao nhiêu?"
    assert "còn xe máy thì sao?" in llm.complete_calls[0]


def test_web_answer_lists_sources():
    class FakeSearch:
        def search(self, query):
            assert query == "truy vấn"
            return [WebResult("Cổng TTĐT Chính phủ", "https://chinhphu.vn/a", "Nội dung về thuế")]

    llm = FakeLLM(text="Theo nguồn [1] ...")
    ans = answer_from_web(llm, FakeSearch(), "truy vấn", "Câu hỏi gốc?")
    assert ans.kind == "web" and not ans.offer_web
    assert ans.sources[0].url == "https://chinhphu.vn/a" and ans.sources[0].location == "chinhphu.vn"
    assert "CÂU HỎI: Câu hỏi gốc?" in llm.complete_calls[0]


def test_web_no_results():
    class EmptySearch:
        def search(self, query):
            return []

    llm = FakeLLM()
    ans = answer_from_web(llm, EmptySearch(), "q", "Q?")
    assert "Không tìm thấy" in ans.text and llm.complete_calls == []


@pytest.mark.parametrize("raw, expected", [
    (r'{"coverage": "full", "answer": "Phạt 6\n- 8 triệu [1', "Phạt 6\n- 8 triệu [1"),
    (r'{"answer": "xong [1].", "citations": [1]}', "xong [1]."),
    ('{"answer": "dở' + "\\", "dở"),  # escape bị cắt ở cuối
    (r'{"answer": "Điều \"5\""', 'Điều "5"'),
    (r'{"answer": "vui 😀!', "vui \U0001F600!"),
    (r'{"answer": "vui \ud83d', "vui "),  # nửa cặp surrogate: chờ phần sau
    (r'{"coverage": "no', ""),
])
def test_partial_answer_reads_streaming_json(raw, expected):
    assert partial_answer(raw) == expected


def test_answer_text_streams_while_generating(stack):
    final = "Phạt từ 6.000.000 đồng đến 8.000.000 đồng [1]."
    llm = FakeLLM({"coverage": "full", "answer": final, "citations": [1], "web_query": ""})
    seen = []
    ans = answer_from_docs(question="Phạt nồng độ cồn?", history=[], on_text=seen.append, **_ready(stack, llm))
    assert len(seen) > 3 and seen[-1] == final == ans.text
    assert all(final.startswith(s) for s in seen)


def test_condense_uses_its_own_model(stack):
    condense = FakeLLM(text="Mức phạt nồng độ cồn với xe máy là bao nhiêu?")
    main = FakeLLM({"coverage": "full", "answer": "x [1]", "citations": [1], "web_query": ""})
    history = [{"role": "user", "content": "Phạt nồng độ cồn ô tô?"}, {"role": "assistant", "content": "6-8 triệu [1]."}]
    ans = answer_from_docs(question="còn xe máy?", history=history, condense_llm=condense, **_ready(stack, main))
    assert ans.question == "Mức phạt nồng độ cồn với xe máy là bao nhiêu?"
    assert len(condense.complete_calls) == 1 and main.complete_calls == [] and len(main.json_calls) == 1


def test_sources_point_to_document(stack):
    llm = FakeLLM({"coverage": "full", "answer": "Phạt [1].", "citations": [1], "web_query": ""})
    ans = answer_from_docs(question="Phạt nồng độ cồn ô tô?", history=[], **_ready(stack, llm))
    doc = stack["db"].list_documents(stack["cid"])[0]
    assert ans.sources[0].doc_id == doc.id
    assert ans.sources[0].status == ""  # văn bản không có gì cần lưu ý


def test_web_answer_streams():
    class FakeSearch:
        def search(self, query):
            return [WebResult("Cổng TTĐT", "https://chinhphu.vn/a", "Nội dung")]

    seen = []
    answer_from_web(FakeLLM(text="Theo nguồn [1], mức phạt là ..."), FakeSearch(), "q", "Q?", on_text=seen.append)
    assert seen[-1] == "Theo nguồn [1], mức phạt là ..."
