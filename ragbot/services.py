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
    llm: LLM
    embedder: Embedder
    ingestor: Ingestor
    retriever: Retriever
    web: TavilySearch | None  # None nếu chưa có TAVILY_API_KEY


def build_services(settings: Settings) -> Services:
    llm = get_llm(settings)
    embedder = get_embedder(settings)
    db = Database(settings.data_dir / "ragbot.db")
    vectors = VectorStore(settings.data_dir / "chroma")
    try:
        web = TavilySearch(settings.tavily_api_key, settings.tavily_max_results)
    except MissingKeyError:
        web = None
    return Services(
        settings=settings,
        db=db,
        vectors=vectors,
        llm=llm,
        embedder=embedder,
        ingestor=Ingestor(db, vectors, embedder, llm, settings.data_dir, settings.ocr_workers),
        retriever=Retriever(db, vectors, embedder),
        web=web,
    )
