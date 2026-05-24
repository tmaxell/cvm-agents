"""OfferAgent — генерация 2-3 вариантов оффера для кампании.

Берёт продукт, канал и аудиторию из ctx.inputs (приходят либо явно из
BuilderAgent, либо вынимаются из текста). Просит LLM сгенерировать 3
варианта с разными «крючками» (скидка / срочность / выгода), сохраняет
их как артефакт offer_variants и предлагает кнопки выбора. Выбранный
вариант сохраняется как offer_choice — BuilderAgent подставит его текст
в шаг коммуникации при сборке flow.

Учитывает лимиты длины по каналу:
  SMS  ≤ 160 символов
  Push ≤ 90 символов
  Email ≤ 300 символов (тело без шапки)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentContext, AgentResult
from llm import get_llm
from schemas import ChatAction

logger = logging.getLogger(__name__)

NAME = "offer"
DESCRIPTION = "Генерирует 2-3 варианта оффера под продукт/канал/аудиторию с разными hook'ами."
SUPPORTED_INTENTS = ("generate_offers",)


_CHANNEL_LIMITS = {
    "sms": 160,
    "smscontent": 160,
    "push": 90,
    "pushcontent": 90,
    "email": 300,
    "emailcontent": 300,
    "ussd": 180,
}


def _channel_limit(channel: str) -> int:
    return _CHANNEL_LIMITS.get((channel or "").lower(), 160)


_SYSTEM_PROMPT = """Ты — копирайтер CVM-кампаний оператора связи.
Сгенерируй РОВНО 3 варианта оффера для одной кампании. Они должны отличаться
ключевым «крючком» (hook), а не быть переформулировками одного текста.

Возможные hook'и: персональная скидка, ограничение по времени, прямая выгода
(деньги/гигабайты/минуты), социальное доказательство, FOMO, забота/сервис.

Жёсткие требования:
- Текст соответствует лимиту канала (учти максимум символов, заданный в запросе).
- Текст уместен для указанной аудитории и логично связан с продуктом.
- Без emoji, кроме случаев, когда канал — push (тогда максимум 1 emoji в начале).
- Никаких claim'ов про «гарантировано», «лучшая цена», «единственное предложение».
- Ничего не выдумывай про CTA, которого нет в реальности — пиши общими формулировками.

Верни строго JSON одной строкой:
{"variants":[
  {"id":"v1","hook":"<короткое описание крючка>","text":"<полный текст оффера>",
   "tone":"<нейтральный|промо|сервисный>","length_chars":<число>,
   "why_relevant":"<1 предложение — почему сработает на этой аудитории>"},
  {"id":"v2",...},
  {"id":"v3",...}
]}

Никакого markdown, никаких комментариев — только JSON."""


def _extract_product(message: str) -> str | None:
    if not message:
        return None
    for pattern in (r"продукт[:\s]+([^.,;\n]+)", r"product[:\s]+([^.,;\n]+)"):
        m = re.search(pattern, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(
        r"\b(тариф\w*|пакет\w*|услуг\w*|подписк\w*|опци\w*)\s+([^.,;\n]{2,60})",
        message, re.IGNORECASE,
    )
    if m:
        return f"{m.group(1)} {m.group(2)}".strip()
    return None


def _extract_channel(message: str) -> str | None:
    if not message:
        return None
    m = re.search(r"\b(sms|смс|push|пуш|email|имейл|ussd|юссд)\b", message, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).lower()
    return {"смс": "sms", "пуш": "push", "имейл": "email", "юссд": "ussd"}.get(raw, raw)


async def execute(ctx: AgentContext) -> AgentResult:
    inputs = ctx.inputs
    product = (
        inputs.get("product")
        or _extract_product(ctx.message)
        or "продукт"
    )
    channel_raw = (
        inputs.get("channel")
        or _extract_channel(ctx.message)
        or "sms"
    )
    channel = channel_raw.lower()
    audience = (
        inputs.get("audience")
        or inputs.get("target_group_name")
        or "общая аудитория"
    )
    occasion = inputs.get("occasion") or ""
    tone = inputs.get("tone") or "промо"
    char_limit = _channel_limit(channel)

    await ctx.emit(
        "step_started",
        detail=f"OfferAgent: product={product}, channel={channel}, audience={audience[:40]}",
    )
    started = time.perf_counter()
    payload = {
        "product": product,
        "channel": channel,
        "audience": audience,
        "tone": tone,
        "occasion": occasion,
        "char_limit": char_limit,
    }
    try:
        llm = get_llm(temperature=0.4)
        result = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        variants = _parse_variants(text, char_limit)
    except Exception as exc:
        logger.warning("OfferAgent LLM failed: %s", exc)
        variants = _fallback_variants(product=product, channel=channel, audience=audience, char_limit=char_limit)

    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit(
        "step_completed",
        detail=f"OfferAgent: {len(variants)} вариантов",
        metadata={"latency_ms": latency, "count": len(variants), "channel": channel},
    )

    if not variants:
        return AgentResult(
            assistant_message="Не удалось сгенерировать варианты оффера. Попробуйте уточнить продукт и аудиторию.",
            status="error",
        )

    # Сохраняем все варианты как артефакт.
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="offer_variants",
        content_json={
            "product": product,
            "channel": channel,
            "audience": audience,
            "char_limit": char_limit,
            "variants": variants,
        },
        metadata_json={"count": len(variants), "channel": channel},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    # Рендерим карточки с кнопками «Выбрать этот оффер».
    lines = [
        f"Подобрал {len(variants)} вариант(а/ов) оффера для **{product}** "
        f"(канал {channel.upper()}, лимит {char_limit} символов):",
        "",
    ]
    actions: list[ChatAction] = []
    for i, v in enumerate(variants, start=1):
        lines.append(f"### Вариант {i}: {v.get('hook') or '—'}")
        lines.append(f"> {v.get('text') or ''}")
        why = v.get("why_relevant")
        if why:
            lines.append(f"— Почему сработает: {why}")
        meta_bits = [f"тональность: {v.get('tone') or 'нейтральная'}"]
        length = v.get("length_chars")
        if isinstance(length, int) and length > 0:
            meta_bits.append(f"длина: {length} симв.")
        lines.append(f"— {', '.join(meta_bits)}")
        lines.append("")
        actions.append(ChatAction(
            id="select_offer",
            label=f"Выбрать вариант {i}",
            kind="runtime",
            payload={
                "variant_id": v.get("id") or f"v{i}",
                "variant_text": v.get("text") or "",
                "product": product,
                "channel": channel,
            },
        ))

    sticky_stage = "collect_offer" if inputs.get("from_builder") else None
    metadata: dict[str, Any] = {"count": len(variants), "channel": channel, "product": product}
    if sticky_stage:
        metadata["stage"] = sticky_stage

    return AgentResult(
        assistant_message="\n".join(lines).rstrip(),
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata=metadata,
    )


def _parse_variants(text: str, char_limit: int) -> list[dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    variants = payload.get("variants") if isinstance(payload, dict) else payload
    if not isinstance(variants, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for i, v in enumerate(variants[:3], start=1):
        if not isinstance(v, dict):
            continue
        text_val = str(v.get("text") or "").strip()
        if not text_val:
            continue
        # Жёстко режем до лимита канала (хвост — многоточием).
        if len(text_val) > char_limit:
            text_val = text_val[: char_limit - 1].rstrip() + "…"
        cleaned.append({
            "id": str(v.get("id") or f"v{i}"),
            "hook": str(v.get("hook") or "").strip(),
            "text": text_val,
            "tone": str(v.get("tone") or "нейтральный").strip(),
            "length_chars": len(text_val),
            "why_relevant": str(v.get("why_relevant") or "").strip(),
        })
    return cleaned


def _fallback_variants(*, product: str, channel: str, audience: str, char_limit: int) -> list[dict[str, Any]]:
    """Резервный набор оффера, если LLM недоступен."""
    base = [
        (
            "Скидка",
            f"Включите «{product}» со скидкой 30% на 3 месяца. Подробности и активация — в приложении.",
            "промо",
            "Скидка снижает порог входа для тёплых клиентов.",
        ),
        (
            "Срочность",
            f"Только до конца недели: «{product}» с приветственным бонусом. Активируйте, чтобы не упустить.",
            "промо",
            "Ограничение по времени повышает вероятность отклика.",
        ),
        (
            "Сервис",
            f"Для вас доступен «{product}» — продукт, который оптимально подходит вашему профилю. Активация — за 1 минуту.",
            "сервисный",
            "Сервисная подача снижает риск отписки.",
        ),
    ]
    variants: list[dict[str, Any]] = []
    for i, (hook, text, tone, why) in enumerate(base, start=1):
        if len(text) > char_limit:
            text = text[: char_limit - 1].rstrip() + "…"
        variants.append({
            "id": f"v{i}",
            "hook": hook,
            "text": text,
            "tone": tone,
            "length_chars": len(text),
            "why_relevant": why,
        })
    return variants
