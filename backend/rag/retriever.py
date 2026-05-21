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


def _semantic_retriever(k: int = 8) -> BaseRetriever:
    """ChromaDB similarity search с MMR для разнообразия результатов."""
    embeddings = get_embeddings()
    db = Chroma(
        persist_directory=str(CHROMA_DIR),
        embedding_function=embeddings,
    )
    # fetch_k побольше — даём MMR пул для отбора, lambda_mult ниже = разнообразие
    return db.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": k * 5, "lambda_mult": 0.55},
    )


def _bm25_retriever(k: int = 6) -> BaseRetriever | None:
    """BM25 keyword retriever поверх тех же документов, что попадают в индекс.

    Загружает все форматы, что поддерживает индексатор (md/txt/html/docx),
    применяет те же правила исключения и дедупликации. Если BM25 недоступен —
    возвращает None, тогда get_retriever() использует только semantic.
    """
    try:
        from langchain_community.retrievers import BM25Retriever
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        # Переиспользуем парсеры индексатора — единая точка истины
        from rag.indexer import (
            DOCS_DIRS as _DOCS_DIRS,
            EXCLUDED_FILENAMES,
            SUPPORTED_EXTENSIONS,
            _load_file,
        )

        docs = []
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        seen_names: set[str] = set()
        for docs_dir in _DOCS_DIRS:
            if not docs_dir.exists():
                continue
            is_cvm_copilot = docs_dir.parent.name == "cvmCopilot"
            prefix = "cvmCopilot-docs" if is_cvm_copilot else "source-docs"
            for filepath in sorted(docs_dir.rglob("*")):
                if any(part.endswith("_files") for part in filepath.parts):
                    continue
                if not (filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS):
                    continue
                if filepath.name in EXCLUDED_FILENAMES or filepath.name in seen_names:
                    continue
                seen_names.add(filepath.name)
                label = f"{prefix}/{filepath.relative_to(docs_dir)}"
                try:
                    raw = _load_file(filepath, label)
                    if not raw:
                        continue
                    chunks = splitter.split_documents(raw)
                    for c in chunks:
                        c.metadata.setdefault("source", label)
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

    semantic = _semantic_retriever(k=8)
    bm25 = _bm25_retriever(k=6)

    if bm25:
        # Hybrid: 55% semantic + 45% keyword.
        # Документация AdTarget сильно зависит от точных терминов («роль», «таргет-группа»,
        # «faultCode»), поэтому keyword-плечо у нас почти равноценно семантическому.
        return EnsembleRetriever(
            retrievers=[semantic, bm25],
            weights=[0.55, 0.45],
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
