"""
RAG Retriever — гибридный поиск (semantic + BM25 keyword).

Используется в qa_copilot.py.
Singleton: индекс загружается один раз при первом обращении.
"""

import os
from pathlib import Path
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.retrievers import BaseRetriever
from langchain_classic.retrievers.ensemble import EnsembleRetriever

from rag.embeddings import get_embeddings

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"


def _semantic_retriever(k: int = 6) -> BaseRetriever:
    """ChromaDB similarity search."""
    embeddings = get_embeddings()
    db = Chroma(
        persist_directory=str(CHROMA_DIR),
        embedding_function=embeddings,
    )
    return db.as_retriever(search_type="mmr", search_kwargs={"k": k, "fetch_k": k * 3})


def _bm25_retriever(k: int = 4) -> BaseRetriever | None:
    """BM25 keyword retriever поверх тех же документов.

    Требует langchain_community. Если недоступен — возвращает None,
    тогда get_retriever() использует только semantic.
    """
    try:
        from langchain_community.retrievers import BM25Retriever
        from langchain_community.document_loaders import TextLoader
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from pathlib import Path
        import glob

        PROJECT_ROOT = Path(__file__).parent.parent.parent
        DOCS_DIRS = [
            PROJECT_ROOT / "docs",
            PROJECT_ROOT.parent / "cvmCopilot" / "docs",
        ]

        docs = []
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        for docs_dir in DOCS_DIRS:
            for filepath in docs_dir.glob("**/*.md") if docs_dir.exists() else []:
                try:
                    loader = TextLoader(str(filepath), encoding="utf-8")
                    loaded = loader.load()
                    chunks = splitter.split_documents(loaded)
                    for c in chunks:
                        c.metadata["source"] = f"{docs_dir.name}/{filepath.relative_to(docs_dir)}"
                    docs.extend(chunks)
                except Exception:
                    pass

        if not docs:
            return None

        bm25 = BM25Retriever.from_documents(docs)
        bm25.k = k
        return bm25
    except Exception as e:
        print(f"[retriever] BM25 недоступен ({e}), используем только semantic")
        return None


@lru_cache(maxsize=1)
def get_retriever() -> BaseRetriever:
    """Возвращает кешированный ретривер (singleton).

    Если ChromaDB индекс не построен — возвращает пустой fallback
    (не падает при запуске без RAG).
    """
    if not CHROMA_DIR.exists():
        print("[retriever] ⚠️  Индекс не найден. Запусти: python -m rag.indexer")
        return _empty_retriever()

    semantic = _semantic_retriever(k=6)
    bm25 = _bm25_retriever(k=4)

    if bm25:
        # Hybrid: 60% semantic + 40% keyword
        return EnsembleRetriever(
            retrievers=[semantic, bm25],
            weights=[0.6, 0.4],
        )
    return semantic


def _empty_retriever() -> BaseRetriever:
    """Заглушка — возвращает пустой список документов."""
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.documents import Document

    class EmptyRetriever(BaseRetriever):
        def _get_relevant_documents(
            self, query: str, *, run_manager: CallbackManagerForRetrieverRun
        ) -> list[Document]:
            return []

    return EmptyRetriever()
