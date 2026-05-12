"""
F1 — CVM Copilot Agent

RAG + LLM chain на LangChain LCEL.
Отвечает на вопросы пользователя в контексте текущего экрана/кампании,
используя:
  1. Документацию из RAG-индекса (ChromaDB + BM25 hybrid)
  2. Живые данные из AdTarget API (кампания, статистика, ошибки)

Точка входа: answer(request: CopilotRequest) -> CopilotResponse
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from llm import get_llm
from schemas import CopilotRequest, CopilotResponse, SourceCitation
from rag.retriever import get_retriever
from tools.adtarget import get_campaign, get_campaign_flow, get_campaign_statistics


# ── Системный промпт ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — CVM Copilot, AI-ассистент встроенный в платформу AdTarget (CVM telecom campaign manager).
Помогаешь аналитикам и менеджерам разобраться с кампаниями, настройками, ошибками и метриками.

## Что ты знаешь о платформе

### Структура кампании
Каждая кампания = набор **Activities** (активностей), связанных цепочкой через `nextActivityId`.
Обязательная структура:
1. **CommonActivity** — корень. Задаёт имя, приоритет, группу, тип, расписание, настройки.
2. **TargetGroupActivity** — аудитория (ЦГ / ClientDataSource).
3. Одна или несколько действующих активностей.

### Типы активностей
| Тип | Назначение | Иконка в UI |
|-----|-----------|-------------|
| CommonActivity | Заголовок/настройки кампании | всегда первый |
| TargetGroupActivity | Выбор аудитории (ЦГ) | Target group |
| EventActivity | Триггер по событию (DataPackageUtilization и др.) | событие |
| PushCommunicationActivity | Push-коммуникация — тип канала выбирается из Channel.contentType | SMS push, USSD push, Email push, Flash SMS, Text push, Json push |
| PullCommunicationActivity | Pull-коммуникация (входящие) | Text pull, Json pull, USSD pull |
| ResponseActivity | Обработка отклика клиента | Response |
| InteractiveResponseActivity | Интерактивный отклик | |
| BusinessTransactionActivity | Бизнес-транзакция (активация продукта/скидки) | Product action |
| RealTimeCheckActivity | Real-time проверка параметра | |
| OrJoinActivity | Слияние веток flow | |

### Каналы коммуникации (Channel)
SMS push, Flash SMS push, USSD push, USSD menu push, Email push, Text push, Json push,
Text pull, Json pull, USSD pull, USSD menu pull, USSD with header pull.

### Ошибки валидации (faultCodes)
| Код | Что означает |
|-----|-------------|
| TargetGroupNotSet | Не выбрана целевая группа в TargetGroupActivity |
| InvalidSchedule / EndDateIsLessThanNow | Дата окончания в прошлом |
| BranchWithControlActivitiesOnly | Ветка только из управляющих блоков, без действий |
| TestGroupNotFound | Тестовая группа не найдена |
| FinalActivityInBranchHasNoFilters | Последняя активность ветки без фильтров |

### Жизненный цикл кампании
Edit → (валидация) → Active → Stopped/Completed → Archived
Запуск: `PUT /Campaigns/start?campaignIdsInfo.campaignIds={id}`

## Как отвечать
- Отвечай кратко и конкретно на вопрос пользователя
- Если вопрос про конкретную кампанию — используй данные из контекста ниже
- Если объясняешь ошибку — сразу давай решение
- Если не знаешь точного ответа — скажи об этом честно, не выдумывай
- Цитируй источник из документации если он есть (вида [docs/PLATFORM_API.md])

## Контекст из документации
{rag_context}

## Контекст текущего экрана
{screen_context}
"""

PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    *[("human" if m["role"] == "user" else "assistant", "{history_placeholder}")
      for m in []],  # history добавляется динамически
    ("human", "{question}"),
])


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
            lines.append(f"\nОшибки валидации:\n" + "\n".join(error_msgs))

    except Exception as e:
        lines.append(f"\n[Не удалось загрузить данные кампании: {e}]")

    return "\n".join(lines)


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

    rag_context = "\n\n---\n\n".join(
        f"[{d.metadata.get('source', 'doc')}]\n{d.page_content}" for d in docs
    ) or "Документация не загружена. Запусти `python -m rag.indexer`."

    # Строим citations из метаданных retriever docs (дедупликация по source+heading)
    seen_keys: set[str] = set()
    citations: list[SourceCitation] = []
    for i, doc in enumerate(docs):
        meta = doc.metadata
        source = meta.get("source", "")
        if not source:
            continue
        title = meta.get("title", "") or _title_from_source(source)
        heading_path = [
            v for k in ("h1", "h2", "h3")
            if (v := meta.get(k, ""))
        ]
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

    # Строим messages вручную для поддержки истории диалога
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    # Используем simple replace вместо .format() — промпт содержит {id} и другие фигурные скобки
    system_content = (
        SYSTEM_PROMPT
        .replace("{rag_context}", rag_context)
        .replace("{screen_context}", screen_context)
    )
    messages = [SystemMessage(content=system_content)]

    # История
    for msg in request.history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=request.question))

    response = await llm.ainvoke(messages)
    answer_text = response.content if hasattr(response, "content") else str(response)

    return CopilotResponse(answer=answer_text, citations=citations)


def _title_from_source(source: str) -> str:
    """Читаемый заголовок из пути к файлу."""
    import re as _re
    from pathlib import Path as _Path
    name = _Path(source).stem
    name = _re.sub(r"\.md$", "", name)
    name = _re.sub(r"\s*—\s*документация AdTarget.*$", "", name, flags=_re.I)
    return name.strip() or source


async def _gather(*coros):
    """Запускает корутины параллельно."""
    import asyncio
    return await asyncio.gather(*coros)
