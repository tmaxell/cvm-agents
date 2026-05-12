"""
Embeddings factory — единственное место выбора embedding-модели.

Порядок приоритетов:
1. ANTHROPIC_API_KEY → AnthropicEmbeddings (Voyage AI, voyage-3-lite)
2. fallback → локальная HuggingFace модель (all-MiniLM-L6-v2, ~80MB, без API)

Меняй EMBEDDING_PROVIDER в .env чтобы форсировать нужный вариант:
  EMBEDDING_PROVIDER=anthropic   (по умолчанию если есть ключ)
  EMBEDDING_PROVIDER=local       (HuggingFace, без ключа)
"""

import os
from langchain_core.embeddings import Embeddings


def get_embeddings() -> Embeddings:
    provider = os.getenv("EMBEDDING_PROVIDER", "").lower()
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if provider == "local" or (not api_key and provider != "anthropic"):
        return _local_embeddings()

    # Anthropic → Voyage AI embeddings
    try:
        from langchain_anthropic import AnthropicEmbeddings
        print("[embeddings] Используем AnthropicEmbeddings (voyage-3-lite)")
        return AnthropicEmbeddings(model="voyage-3-lite")
    except (ImportError, Exception) as e:
        print(f"[embeddings] AnthropicEmbeddings недоступен ({e}), fallback → local")
        return _local_embeddings()


def _local_embeddings() -> Embeddings:
    """HuggingFace sentence-transformers — работает offline, без API-ключей."""
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        print("[embeddings] Используем HuggingFaceEmbeddings (all-MiniLM-L6-v2)")
        return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except ImportError:
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore
        return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
