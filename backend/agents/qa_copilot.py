"""
F1 — CVM Copilot Agent

RAG + LLM chain на LangChain LCEL.
Отвечает на вопросы пользователя в контексте текущего экрана/кампании,
используя:
  1. Документацию из RAG-индекса (ChromaDB + BM25 hybrid)
  2. Живые данные из AdTarget API (кампания, статистика, ошибки)

Точка входа: answer(request: CopilotRequest) -> CopilotResponse
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from llm import get_llm
from schemas import CopilotRequest, CopilotResponse, SourceCitation
from rag.retriever import get_retriever
from tools.adtarget import get_campaign, get_campaign_flow, get_campaign_statistics


# ── Системный промпт ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — CVM Copilot, AI-ассистент платформы AdTarget (CVM telecom campaign manager).
Помогаешь аналитикам, менеджерам и админам разобраться с кампаниями, сегментацией, доступом, отчётами и ошибками.

## Что ты знаешь о платформе (фоновые знания)

### Структура кампании
Каждая кампания = набор **Activities** (активностей), связанных цепочкой через `nextActivityId`.
Обязательная структура:
1. **CommonActivity** — корень. Задаёт имя, приоритет, группу, тип, расписание, настройки.
2. **TargetGroupActivity** — аудитория (ЦГ / ClientDataSource).
3. Одна или несколько действующих активностей.

### Типы активностей
| Тип | Назначение |
|-----|-----------|
| CommonActivity | Заголовок/настройки кампании, всегда первый |
| TargetGroupActivity | Выбор аудитории (ЦГ) |
| EventActivity | Триггер по событию (DataPackageUtilization и др.) |
| PushCommunicationActivity | Push (SMS / USSD / Email / Flash / Text / Json) |
| PullCommunicationActivity | Pull (входящие) |
| ResponseActivity | Обработка отклика клиента |
| BusinessTransactionActivity | Активация продукта/скидки |
| RealTimeCheckActivity | Real-time проверка параметра |
| OrJoinActivity | Слияние веток flow |

### Ошибки валидации (faultCodes)
| Код | Что означает |
|-----|-------------|
| TargetGroupNotSet | Не выбрана целевая группа |
| InvalidSchedule / EndDateIsLessThanNow | Дата окончания в прошлом |
| BranchWithControlActivitiesOnly | Ветка только из управляющих блоков |
| TestGroupNotFound | Тестовая группа не найдена |
| FinalActivityInBranchHasNoFilters | Последняя активность ветки без фильтров |

### Терминология
- «таргет-группа» / «целевая группа» / «ЦГ» / «target group» — одно и то же.
- «модель сегментации» — дерево решений, из которого получают таргет-группы.
- «роль» — набор прав доступа, назначается пользователю в разделе **Configuration → Roles**.
- «канал» (Channel) — способ доставки сообщения (SMS push, USSD push, Email push и т. д.).

## Как отвечать (это важно)

1. **Сначала прочитай ВСЕ блоки в «Документация из базы знаний» ниже целиком.**
   Ответ почти всегда там есть, даже если формулировка вопроса отличается от текста в документации.
   Если хоть один блок описывает запрошенную процедуру, экран или сущность — используй его.

2. **Отвечай по делу:**
   - Сначала 1–2 предложения с прямым ответом.
   - Если это инструкция — пронумерованный список шагов (Шаг 1, Шаг 2…), коротко и предметно: куда нажать,
     что выбрать, что указать. Названия пунктов меню, кнопок, разделов давай как в документации
     (по-русски и/или на английском — как там написано).
   - Если это объяснение сущности — короткое определение + назначение + 1–2 ключевых свойства.
   - Если это диагностика ошибки — что произошло + как починить.

3. **Опирайся только на документацию и фоновые знания выше.**
   Не выдумывай названия кнопок, эндпоинтов, кодов ошибок, имён ролей.
   Если в документации сказано «нажмите кнопку **+**» — так и пиши, не придумывай альтернатив.

4. **Не пиши «не знаю», если ответ есть в контексте.**
   Сначала ещё раз перечитай блоки. Только если в документации действительно нет нужной информации —
   честно скажи: «В документации нет описания X. Ближайшее, что есть, — Y».

5. **Не вставляй markdown-ссылки на источники в текст ответа.**
   UI показывает источники отдельной панелью «Источники». Внутри ответа их повторять не нужно
   (никаких `[docs/...](http://...)`, никаких `(см. файл X)`). Просто отвечай содержательно.

6. **Учитывай контекст экрана.** Если пользователь спрашивает «как тут …» — он почти всегда имеет в виду
   текущий экран. Привязывай ответ к нему.

7. **Язык ответа = язык вопроса.** Если спросили по-русски — отвечай по-русски.

## Документация из базы знаний
{rag_context}

## Контекст текущего экрана
{screen_context}
"""


# ── Загрузка живого контекста кампании ───────────────────────────────────────

async def _load_screen_context(request: CopilotRequest) -> str:
    """Подгружает живые данные из API если есть campaign_id в контексте."""
    ctx = request.context
    lines = [f"Экран: {ctx.screen}", f"Роль пользователя: {ctx.user_role}"]

    if not ctx.campaign_id:
        return "\n".join(lines)

    try:
        campaign = await get_campaign(ctx.campaign_id)
        stats = await get_campaign_statistics(ctx.campaign_id)
        flow = await get_campaign_flow(ctx.campaign_id)

        activities = flow.get("activities", [])
        activity_types = [a.get("type", "?") for a in activities]
        errors = [e for a in activities for e in a.get("errors", [])]

        lines += [
            f"\nКампания #{ctx.campaign_id}: «{campaign.get('name', '')}»",
            f"Статус: {campaign.get('status', '?')} | Приоритет: {campaign.get('priority', '?')}",
            f"Расписание: {campaign.get('schedule', {}).get('period', {}).get('beginDate', '?')} → "
            f"{campaign.get('schedule', {}).get('period', {}).get('endDate', '?')}",
            f"Активностей в flow: {len(activities)} ({', '.join(activity_types)})",
            f"Участников кампании: {stats.get('campaignParticipantsNumber', 'н/д')}",
        ]

        if errors:
            error_msgs = [f"  • {e.get('invalidActivityFaultCode', e.get('flowFaultCode', '?'))}: "
                          f"{e.get('errorMessage', '')}" for e in errors[:5]]
            lines.append("\nОшибки валидации:\n" + "\n".join(error_msgs))

    except Exception as e:
        lines.append(f"\n[Не удалось загрузить данные кампании: {e}]")

    return "\n".join(lines)


# ── Форматирование RAG-контекста ──────────────────────────────────────────────

def _heading_path(meta: dict) -> list[str]:
    return [v for k in ("h1", "h2", "h3") if (v := meta.get(k, ""))]


def _group_docs_by_source(docs: list[Document]) -> list[tuple[str, str, list[Document]]]:
    """Группирует чанки по source-файлу, сохраняя порядок появления (= relevance order).

    Возвращает список (source, title, chunks).
    """
    order: list[str] = []
    grouped: dict[str, list[Document]] = defaultdict(list)
    titles: dict[str, str] = {}
    for doc in docs:
        src = doc.metadata.get("source", "")
        if not src:
            continue
        if src not in grouped:
            order.append(src)
            titles[src] = doc.metadata.get("title", "") or _title_from_source(src)
        grouped[src].append(doc)
    return [(src, titles[src], grouped[src]) for src in order]


def _format_rag_context(docs: list[Document]) -> str:
    """Складывает чанки в нумерованные блоки по источникам.

    LLM лучше использует контекст, когда видит: «Источник #1 — Сегментация / Создание таргет-групп»,
    а не безликий список разрозненных кусков.
    """
    if not docs:
        return "Документация не загружена. Запусти `python -m rag.indexer`."

    groups = _group_docs_by_source(docs)
    if not groups:
        return "Документация не загружена."

    blocks: list[str] = []
    for i, (src, title, chunks) in enumerate(groups, start=1):
        # Собираем уникальные heading-paths внутри источника для подсказки о структуре
        seen_paths: set[tuple[str, ...]] = set()
        path_lines: list[str] = []
        body_parts: list[str] = []
        for chunk in chunks:
            path = tuple(_heading_path(chunk.metadata))
            if path and path not in seen_paths:
                seen_paths.add(path)
                path_lines.append(" / ".join(path))
            body_parts.append(chunk.page_content.strip())

        header = f"### Источник #{i} — {title}"
        if path_lines:
            header += "\n   Разделы: " + "; ".join(path_lines[:4])
        body = "\n\n".join(body_parts)
        blocks.append(f"{header}\n\n{body}")

    return "\n\n---\n\n".join(blocks)


# ── Основная функция ──────────────────────────────────────────────────────────

async def answer(request: CopilotRequest) -> CopilotResponse:
    """Отвечает на вопрос пользователя используя RAG + живой контекст."""
    llm = get_llm(for_tools=False)
    retriever = get_retriever()

    # Загружаем контекст параллельно
    screen_context, docs = await _gather(
        _load_screen_context(request),
        retriever.ainvoke(request.question),
    )

    rag_context = _format_rag_context(docs)

    # Citations: дедупликация по (source, heading_path).
    seen_keys: set[str] = set()
    citations: list[SourceCitation] = []
    for i, doc in enumerate(docs):
        meta = doc.metadata
        source = meta.get("source", "")
        if not source:
            continue
        title = meta.get("title", "") or _title_from_source(source)
        heading_path = _heading_path(meta)
        key = f"{source}::{':'.join(heading_path)}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # Pseudo-score: первые результаты — выше
        score = round(max(0.0, 1.0 - i * 0.07), 2)
        citations.append(SourceCitation(
            id=f"cite_{i}",
            title=title,
            source=source,
            heading_path=heading_path,
            score=score,
        ))

    # Используем simple replace вместо .format() — промпт содержит { } которые format ломают.
    system_content = (
        SYSTEM_PROMPT
        .replace("{rag_context}", rag_context)
        .replace("{screen_context}", screen_context)
    )
    messages: list = [SystemMessage(content=system_content)]

    # История диалога
    for msg in request.history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=request.question))

    response = await llm.ainvoke(messages)
    answer_text = response.content if hasattr(response, "content") else str(response)
    answer_text = _strip_inline_citations(answer_text)

    return CopilotResponse(answer=answer_text, citations=citations)


# ── Утилиты ───────────────────────────────────────────────────────────────────

# Подстраховка от рудиментарных inline-цитат вида [cvmCopilot-docs/foo.txt](...)
# или (см. docs/PLATFORM_API.md) — на случай, если LLM всё-таки попробует их вставить.
_INLINE_LINK_RE = re.compile(
    r"\[[^\]]*(?:cvmCopilot-docs|source-docs|/docs/|\.md|\.txt|\.html|\.docx)[^\]]*\]"
    r"\([^)]+\)"
)
_PAREN_REF_RE = re.compile(
    r"\s*\((?:см\.?|see)\s+[^)]*(?:cvmCopilot-docs|source-docs|/docs/|\.md|\.txt|\.html|\.docx)[^)]*\)",
    flags=re.IGNORECASE,
)


def _strip_inline_citations(text: str) -> str:
    text = _INLINE_LINK_RE.sub("", text)
    text = _PAREN_REF_RE.sub("", text)
    # Убираем двойные пробелы и пустоту перед знаками препинания, которые могли остаться
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip()


def _title_from_source(source: str) -> str:
    """Читаемый заголовок из пути к файлу."""
    name = Path(source).stem
    name = re.sub(r"\.md$", "", name)
    name = re.sub(r"\s*—\s*документация AdTarget.*$", "", name, flags=re.I)
    return name.strip() or source


async def _gather(*coros):
    """Запускает корутины параллельно."""
    import asyncio
    return await asyncio.gather(*coros)
