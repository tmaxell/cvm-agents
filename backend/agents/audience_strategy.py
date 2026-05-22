"""Резолвер сигналов для подбора аудитории под продукт.

Когда нужно порекомендовать таргет-группу под продвигаемый продукт, мы НЕ
выбираем одну стратегию молча. Вместо этого собираем ВСЕ доступные сигналы
из продуктового каталога и отдаём их сегментатору — он строит 2–3 варианта
аудитории, каждый с пояснением, на каком сигнале основан.

Сигналы (в порядке приоритета для пояснений пользователю):
  1. NBO            — продукт является Next Best Offer для конкретной аудитории.
  2. Подключившие   — есть абоненты, уже купившие продукт → look-alike по ним.
  3. Похожие продукты — продукт новый/без подключивших, но есть похожие в каталоге
                        → look-alike по их аудитории.
  4. (нет сигналов) — расспросить пользователя о свойствах продукта.

Механизм универсален: достаточно, чтобы продукт был в каталоге с полями
subscribers / nbo_audience / similar_to — никакого хардкода под конкретный продукт.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tools import adtarget


# ── Модель сигналов ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class AudienceSignals:
    """Сигналы для подбора аудитории под один продукт."""
    product: str
    found_in_catalog: bool = False
    catalog_product: dict[str, Any] | None = None
    nbo: dict[str, Any] | None = None                       # NBO-аудитория продукта
    existing_subscribers: int | None = None                 # сколько уже подключили
    similar_products: list[dict[str, Any]] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def methods(self) -> list[str]:
        """Применимые методы подбора по собранным сигналам."""
        methods: list[str] = []
        if self.nbo:
            methods.append("nbo")
        if self.existing_subscribers:
            methods.append("lookalike_existing")
        if self.similar_products:
            methods.append("lookalike_similar")
        return methods or ["ask_properties"]

    @property
    def has_data(self) -> bool:
        """Есть ли хоть один сигнал для рекомендации (иначе — расспросить)."""
        return bool(self.nbo or self.existing_subscribers or self.similar_products)

    def llm_context(self) -> dict[str, Any]:
        """Компактный контекст для промпта сегментатора."""
        return {
            "product": self.product,
            "found_in_catalog": self.found_in_catalog,
            "available_methods": self.methods,
            "nbo": self.nbo,
            "existing_subscribers": self.existing_subscribers,
            "similar_products": [
                {"name": p.get("name"), "subscribers": p.get("subscribers"),
                 "category": p.get("category")}
                for p in self.similar_products
            ],
            "product_properties": self.properties,
            "guidance": _method_guidance(self.methods),
        }

    def human_summary(self) -> str:
        """Короткое пояснение для пользователя, на чём строятся рекомендации."""
        if self.nbo:
            return (
                f"Продукт «{self.product}» определён моделью NBO как лучшее предложение "
                "для конкретной аудитории — предложу её и пару смежных вариантов."
            )
        if self.existing_subscribers:
            return (
                f"У продукта «{self.product}» есть {self.existing_subscribers:,} подключивших — "
                "построю варианты look-alike по их профилю."
            ).replace(",", " ")
        if self.similar_products:
            names = ", ".join(p.get("name", "") for p in self.similar_products[:3])
            return (
                f"Продукт «{self.product}» новый, подобрал похожие продукты ({names}) — "
                "построю варианты look-alike по их аудитории."
            )
        return (
            f"По продукту «{self.product}» нет данных в каталоге (ни NBO, ни подключивших, "
            "ни похожих продуктов) — нужно уточнить его свойства."
        )


def _method_guidance(methods: list[str]) -> str:
    """Текстовая подсказка LLM, как использовать доступные методы."""
    parts: list[str] = []
    if "nbo" in methods:
        parts.append(
            "nbo: одна из гипотез ДОЛЖНА быть NBO-аудиторией продукта (поле nbo) — "
            "это самый сильный сигнал, размер бери из nbo.estimated_size."
        )
    if "lookalike_existing" in methods:
        parts.append(
            "lookalike_existing: построй гипотезу как look-alike по уже подключившим продукт "
            "(existing_subscribers) — похожие по профилю абоненты, ещё не купившие продукт."
        )
    if "lookalike_similar" in methods:
        parts.append(
            "lookalike_similar: продукт новый — построй гипотезу look-alike по аудитории "
            "похожих продуктов (similar_products), опираясь на их subscribers."
        )
    if "ask_properties" in methods:
        parts.append(
            "ask_properties: данных нет — гипотезы строй максимально осторожно, "
            "опираясь только на свойства продукта и общий профиль базы."
        )
    return " ".join(parts)


# ── Резолвер ──────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").strip().lower())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-я0-9]+", _norm(text)))


def _match_product(product: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Находит продукт в каталоге по имени (точное вхождение или пересечение токенов)."""
    if not product or not catalog:
        return None
    target_norm = _norm(product)
    target_tokens = _tokens(product)

    # 1. Точное / подстрочное совпадение.
    for item in catalog:
        name = _norm(str(item.get("name") or ""))
        if not name:
            continue
        if name == target_norm or name in target_norm or target_norm in name:
            return item

    # 2. Лучшее совпадение по пересечению значимых токенов (без стоп-слов).
    stop = {"тариф", "пакет", "услуга", "подписка", "продукт"}
    best, best_score = None, 0.0
    for item in catalog:
        name_tokens = _tokens(str(item.get("name") or ""))
        meaningful = (target_tokens & name_tokens) - stop
        if not meaningful:
            continue
        score = len(meaningful) / max(1, len((target_tokens | name_tokens) - stop))
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 0.34 else None


def _similar_products(
    matched: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Возвращает похожие продукты с подключившими.

    Сначала берёт явный список similar_to, затем дополняет продуктами той же
    категории, у которых есть subscribers (и которые сами не пустые/новые).
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = {_norm(str(matched.get("name") or ""))}

    explicit = {_norm(str(n)) for n in (matched.get("similar_to") or [])}
    for item in catalog:
        name_norm = _norm(str(item.get("name") or ""))
        if name_norm in explicit and name_norm not in seen:
            result.append(item)
            seen.add(name_norm)

    # Дополняем по категории — продукты с реальными подключившими.
    category = matched.get("category")
    if category:
        for item in catalog:
            name_norm = _norm(str(item.get("name") or ""))
            if name_norm in seen:
                continue
            if item.get("category") == category and (item.get("subscribers") or 0) > 0:
                result.append(item)
                seen.add(name_norm)

    return result


async def resolve_audience_signals(product: str) -> AudienceSignals:
    """Собирает все доступные сигналы для подбора аудитории под продукт.

    Не падает при недоступности каталога — возвращает пустые сигналы
    (что приведёт к стратегии ask_properties).
    """
    signals = AudienceSignals(product=str(product or "").strip() or "продукт")
    try:
        catalog = await adtarget.list_product_catalog()
    except Exception as exc:  # noqa: BLE001
        print(f"[audience_strategy] product catalog unavailable: {type(exc).__name__}: {exc}")
        return signals

    if not isinstance(catalog, list) or not catalog:
        return signals

    matched = _match_product(signals.product, catalog)
    if matched is None:
        return signals

    signals.found_in_catalog = True
    signals.catalog_product = matched
    signals.properties = dict(matched.get("properties") or {})

    nbo = matched.get("nbo_audience")
    if isinstance(nbo, dict) and nbo:
        signals.nbo = nbo

    subscribers = matched.get("subscribers")
    if isinstance(subscribers, (int, float)) and subscribers > 0:
        signals.existing_subscribers = int(subscribers)

    # Похожие продукты ищем, только если по самому продукту мало данных
    # (новый продукт без подключивших) — это и есть кейс lookalike_similar.
    if not signals.existing_subscribers:
        signals.similar_products = _similar_products(matched, catalog)

    return signals
