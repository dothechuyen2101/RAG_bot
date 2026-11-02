from dataclasses import dataclass

from .config import Settings
from .ingest import Ingestor
from .providers import LLM, Embedder, MissingKeyError, get_embedder, get_llm
from .retrieval import Retriever
from .store import Database, VectorStore
from .websearch import TavilySearch


@dataclass
class Services:
    settings: Settings
    db: Database
    vectors: VectorStore
    llm: LLM  # trả lời câu hỏi
    embedder: Embedder
    ingestor: Ingestor
    retriever: Retriever
    web: TavilySearch | None  # None nếu chưa có TAVILY_API_KEY
    condense_llm: LLM | None = None  # viết lại câu hỏi nối tiếp; None: dùng llm


def build_services(settings: Settings) -> Services:
    llm = get_llm(settings)
    condense_llm = get_llm(settings, settings.condense_model) if settings.condense_model else llm
    ocr_llm = get_llm(settings, settings.ocr_model) if settings.ocr_model else llm
    embedder = get_embedder(settings)
    db = Database(settings.data_dir / "ragbot.db")
    vectors = VectorStore(settings.data_dir / "chroma")
    try:
        web = TavilySearch(settings.tavily_api_key, settings.tavily_max_results)
    except MissingKeyError:
        web = None
    ingestor = Ingestor(
        db, vectors, embedder, ocr_llm, settings.data_dir, settings.ocr_workers,
        ocr_confirm_pages=settings.ocr_confirm_pages, allow_private_urls=settings.allow_private_urls,
        max_bytes=int(settings.max_upload_mb * 1024 * 1024),
    )
    ingestor.backfill_legal_info()
    return Services(
        settings=settings,
        db=db,
        vectors=vectors,
        llm=llm,
        embedder=embedder,
        ingestor=ingestor,
        retriever=Retriever(db, vectors, embedder),
        web=web,
        condense_llm=condense_llm,
    )
