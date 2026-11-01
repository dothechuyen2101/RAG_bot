import hashlib
import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragbot.ingest import Ingestor  # noqa: E402
from ragbot.retrieval import Retriever  # noqa: E402
from ragbot.store import Database, VectorStore  # noqa: E402

LEGAL_TEXT = """CHÍNH PHỦ
Số: 100/2019/NĐ-CP
NGHỊ ĐỊNH
Quy định xử phạt vi phạm hành chính trong lĩnh vực giao thông đường bộ và đường sắt
Căn cứ Luật Tổ chức Chính phủ ngày 19 tháng 6 năm 2015;
Chương I
NHỮNG QUY ĐỊNH CHUNG
Điều 1. Phạm vi điều chỉnh
1. Nghị định này quy định về hành vi vi phạm hành chính trong lĩnh vực giao thông đường bộ.
2. Các hành vi vi phạm hành chính khác không quy định tại Nghị định này thì áp dụng quy định khác.
Điều 2. Đối tượng áp dụng
Cá nhân, tổ chức Việt Nam và nước ngoài có hành vi vi phạm hành chính trong lĩnh vực giao thông.
Chương II
HÀNH VI VI PHẠM, HÌNH THỨC XỬ PHẠT
Mục 1
VI PHẠM QUY TẮC GIAO THÔNG ĐƯỜNG BỘ
Điều 5. Xử phạt người điều khiển xe ô tô vi phạm quy tắc giao thông đường bộ
1. Phạt tiền từ 200.000 đồng đến 400.000 đồng đối với người điều khiển xe không chấp hành hiệu lệnh.
2. Phạt tiền từ 6.000.000 đồng đến 8.000.000 đồng đối với người điều khiển xe có nồng độ cồn vượt quá mức.
Điều 12. Xử phạt các hành vi vi phạm quy định về sử dụng, khai thác trong phạm vi đất dành cho đường bộ
1. Phạt tiền từ 100.000 đồng đến 200.000 đồng đối với cá nhân bày bán hàng hóa trên lòng đường.
"""


class FakeEmbedder:
    """Vector túi-từ băm: đủ để kiểm tra luồng tìm kiếm mà không gọi API."""

    model_id = "fake:bow:64"
    dim = 64

    def __init__(self):
        self.calls = 0

    def embed(self, texts, kind):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class FakeLLM:
    name = "fake:llm"

    def __init__(self, json_result=None, text="", ocr_text="Điều 1. Văn bản OCR giả lập đủ dài để vượt ngưỡng."):
        self.json_result = json_result or {"coverage": "none", "answer": "", "citations": [], "web_query": "q"}
        self.text = text
        self.ocr_text = ocr_text
        self.json_calls = []
        self.complete_calls = []
        self.ocr_calls = 0

    def complete(self, system, user):
        self.complete_calls.append(user)
        return self.text

    def complete_json(self, system, user, schema):
        self.json_calls.append(user)
        return self.json_result

    def ocr(self, png, instruction):
        self.ocr_calls += 1
        return self.ocr_text


@pytest.fixture
def stack(tmp_path):
    db = Database(tmp_path / "ragbot.db")
    vectors = VectorStore(tmp_path / "chroma")
    embedder = FakeEmbedder()
    llm = FakeLLM()
    ingestor = Ingestor(db, vectors, embedder, llm, tmp_path, ocr_workers=2)
    retriever = Retriever(db, vectors, embedder)
    collection = db.create_collection("Pháp luật")
    return {"db": db, "vectors": vectors, "embedder": embedder, "llm": llm, "ingestor": ingestor,
            "retriever": retriever, "cid": collection.id, "dir": tmp_path}
