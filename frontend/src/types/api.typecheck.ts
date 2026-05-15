import type {
  MatchedTargetGroup,
  SegmentHypothesis,
  SegmentSuggestResponse,
  SelectedSegmentForBuilder,
} from "./api";

export const matchedTargetGroupContract = {
  target_group_id: 105,
  name: "Утилизаторы пакета данных (≥80%)",
  clients_count: 67890,
  match_score: 0.92,
  match_reasons: ["Target Group id подтверждён справочником AdTarget"],
} satisfies MatchedTargetGroup;

export const recommendationOnlyHypothesisContract = {
  name: "Новый look-alike сегмент",
  audience_description: "Похожие клиенты без готовой Target Group.",
  relevance_reason: "Можно протестировать дополнительный спрос.",
  selection_criteria: { look_alike: true },
  risk_or_limitation: "Это только рекомендация: Target Group не найдена в справочнике.",
  matched_target_group: null,
  is_existing_target_group: false,
  confidence: 0.7,
  matched_target_groups: [],
  priority: 2,
} satisfies SegmentHypothesis;

export const matchedHypothesisContract = {
  name: "Утилизаторы интернет-пакета",
  audience_description: "Клиенты с высокой утилизацией пакета данных.",
  relevance_reason: "Высокая вероятность покупки дополнительного пакета.",
  selection_criteria: { usage: ">=80%" },
  risk_or_limitation: "Нужно исключить клиентов с недавним контактом.",
  matched_target_group: matchedTargetGroupContract,
  is_existing_target_group: true,
  confidence: 0.87,
  matched_target_groups: [matchedTargetGroupContract],
  priority: 1,
} satisfies SegmentHypothesis;

export const segmentSuggestResponseContract = {
  summary: "Подготовлено 2 гипотезы сегментов.",
  hypotheses: [matchedHypothesisContract, recommendationOnlyHypothesisContract],
  warnings: [],
  recommendation_only: true,
} satisfies SegmentSuggestResponse;

export const selectedSegmentForBuilderContract = {
  product: "Пакет данных 5 ГБ",
  goal: "увеличить продажи интернет-пакетов",
  hypothesis: recommendationOnlyHypothesisContract,
  recommendationOnly: true,
} satisfies SelectedSegmentForBuilder;
