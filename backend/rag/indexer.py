"""
RAG Indexer — строит векторный индекс из документации.

Запуск вручную из папки backend/:
    python -m rag.indexer

Поддерживаемые форматы:
  .md   — Markdown с разбивкой по заголовкам
  .txt  — Простой текст
  .html — Официальная документация AdTarget (Sphinx HTML)
  .docx — DOCX-документы (БФТ, функциональные требования)

Сохраняет в ./chroma_db/ (ChromaDB auto-persist).
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.documents import Document

from rag.embeddings import get_embeddings

# ── Пути ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.parent

DOCS_DIRS = [
    PROJECT_ROOT / "docs",                              # cvm-agents/docs/
    PROJECT_ROOT.parent / "cvmCopilot" / "docs",        # cvmCopilot/docs/
]

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

SUPPORTED_EXTENSIONS = {".md", ".txt", ".html", ".htm", ".docx"}

# Документы про сам AI-агентный проект (планы, миграции, мастерплан) —
# в продуктовый RAG не попадают, иначе агент ссылается на них вместо
# документации AdTarget.
EXCLUDED_FILENAMES = {
    "AI_AGENTS_MASTERPLAN.md",
    "BUILDER_SIMPLIFICATION_PLAN.md",
    "FRONTEND_UNIFIED_CHAT_MIGRATION.md",
    "MVP_INITIATIVES_REQUIREMENTS.md",
}

HEADERS_TO_SPLIT = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]


# ── Парсеры ───────────────────────────────────────────────────────────────────

def _title_from_path(path: Path) -> str:
    """Извлекает читаемый заголовок из имени файла."""
    name = path.stem
    # Убираем суффиксы вида ".md" из ".md.txt"
    name = re.sub(r"\.md$", "", name)
    # Убираем типичный Sphinx-суффикс
    name = re.sub(r"\s*—\s*документация AdTarget.*$", "", name, flags=re.I)
    return name.strip() or path.stem


def _load_txt(path: Path, label: str, title: str) -> list[Document]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Document(page_content=text, metadata={"source": label, "title": title})]


def _load_html(path: Path, label: str, title: str) -> list[Document]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print(f"[indexer] Пропускаю {path.name}: нет beautifulsoup4 (pip install beautifulsoup4)")
        return []

    raw = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "lxml")

    # Извлекаем заголовок из <title> если есть
    if soup.title and soup.title.get_text(strip=True):
        title_raw = soup.title.get_text(" ", strip=True)
        title = re.sub(r"\s*—\s*документация AdTarget.*$", "", title_raw, flags=re.I).strip() or title

    # Удаляем nav/footer/header/script/style
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text("\n")
    # Сворачиваем лишние пробелы и переносы
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return [Document(page_content=text, metadata={"source": label, "title": title})]


def _load_docx(path: Path, label: str, title: str) -> list[Document]:
    try:
        import mammoth
    except ImportError:
        print(f"[indexer] Пропускаю {path.name}: нет mammoth (pip install mammoth)")
        return []

    with open(path, "rb") as f:
        result = mammoth.extract_raw_text(f)
    return [Document(page_content=result.value, metadata={"source": label, "title": title})]


def _load_md(path: Path, label: str, title: str) -> list[Document]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Document(page_content=text, metadata={"source": label, "title": title})]


def _load_file(path: Path, label: str) -> list[Document]:
    title = _title_from_path(path)
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _load_md(path, label, title)
    elif suffix == ".txt":
        return _load_txt(path, label, title)
    elif suffix in {".html", ".htm"}:
        return _load_html(path, label, title)
    elif suffix == ".docx":
        return _load_docx(path, label, title)
    return []


# ── Разбивка на чанки ─────────────────────────────────────────────────────────

def _split_documents(docs: list[Document], is_markdown: bool = False) -> list[Document]:
    """Разбивает документы на чанки с сохранением метаданных."""
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

    if is_markdown:
        header_splitter = MarkdownHeaderTextSplitter(HEADERS_TO_SPLIT, strip_headers=False)
        chunks = []
        for doc in docs:
            header_chunks = header_splitter.split_text(doc.page_content)
            for hc in header_chunks:
                hc.metadata.update(doc.metadata)
            chunks.extend(text_splitter.split_documents(header_chunks))
        return chunks
    else:
        return text_splitter.split_documents(docs)


# ── Основная функция ──────────────────────────────────────────────────────────

def build_index() -> None:
    """Загружает документы всех поддерживаемых форматов, чанкует и сохраняет в ChromaDB."""
    all_files: list[tuple[Path, str]] = []  # (filepath, label)
    seen_names: set[str] = set()  # дедупликация по имени между source-docs и cvmCopilot-docs

    # Порядок DOCS_DIRS важен: первый победит при коллизии имён.
    # cvmCopilot/docs идёт после cvm-agents/docs, но продуктовая документация
    # (txt/html/docx) живёт ТОЛЬКО там, а PLATFORM_*.md идентичен в обеих копиях,
    # поэтому естественная победа первого вхождения нас устраивает.
    for docs_dir in DOCS_DIRS:
        if not docs_dir.exists():
            print(f"[indexer] Пропускаю (не найдено): {docs_dir}")
            continue
        # Уникальный префикс соответствует маунту в app.py:
        #   cvm-agents/docs  → "source-docs" (маунт /source-docs)
        #   cvmCopilot/docs  → "cvmCopilot-docs" (маунт /cvmCopilot-docs)
        is_cvm_copilot = docs_dir.parent.name == "cvmCopilot"
        prefix = "cvmCopilot-docs" if is_cvm_copilot else "source-docs"
        for filepath in sorted(docs_dir.rglob("*")):
            # Пропускаем папки _files (Sphinx assets)
            if any(part.endswith("_files") for part in filepath.parts):
                continue
            if not (filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS):
                continue
            if filepath.name in EXCLUDED_FILENAMES:
                print(f"[indexer] ⨯ пропускаю (agent-internal): {filepath.name}")
                continue
            if filepath.name in seen_names:
                print(f"[indexer] ⨯ пропускаю дубль: {prefix}/{filepath.relative_to(docs_dir)}")
                continue
            seen_names.add(filepath.name)
            label = f"{prefix}/{filepath.relative_to(docs_dir)}"
            all_files.append((filepath, label))

    if not all_files:
        print("[indexer] Нет файлов для индексирования")
        return

    print(f"[indexer] Найдено файлов: {len(all_files)}")

    all_chunks: list[Document] = []
    for filepath, label in all_files:
        try:
            raw_docs = _load_file(filepath, label)
        except Exception as e:
            print(f"[indexer] Ошибка чтения {filepath.name}: {e}")
            continue

        if not raw_docs:
            continue

        is_md = filepath.suffix.lower() == ".md"
        chunks = _split_documents(raw_docs, is_markdown=is_md)

        # Нормализуем метаданные: убедимся что source и title есть в каждом чанке
        for chunk in chunks:
            chunk.metadata.setdefault("source", label)
            chunk.metadata.setdefault("title", _title_from_path(filepath))

        all_chunks.extend(chunks)
        print(f"[indexer]   {filepath.name}: {len(chunks)} чанков")

    print(f"[indexer] Всего чанков: {len(all_chunks)}")

    embeddings = get_embeddings()

    # Удаляем старый индекс
    if CHROMA_DIR.exists():
        import shutil
        shutil.rmtree(CHROMA_DIR)
        print(f"[indexer] Старый индекс удалён")

    db = Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DIR),
    )

    count = db._collection.count()
    print(f"[indexer] ✅ Индекс сохранён в {CHROMA_DIR} ({count} векторов)")


if __name__ == "__main__":
    build_index()
