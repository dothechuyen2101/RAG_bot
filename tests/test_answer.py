from conftest import LEGAL_TEXT, FakeLLM

from ragbot.answer import answer_from_docs, answer_from_web
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
