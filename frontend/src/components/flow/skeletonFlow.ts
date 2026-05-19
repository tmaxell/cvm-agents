import type { CampaignFlow } from "../../types/api";

/**
 * Скелет flow — отображается полупрозрачно в пустом состоянии canvas AdTarget,
 * подсказывает структуру обязательных первых двух нод (Common + Target group).
 */
export const SKELETON_FLOW: CampaignFlow = {
  activities: [
    { id: "common", type: "CommonActivity", name: "New campaign", nextActivityId: "tg" },
    { id: "tg", type: "TargetGroupActivity", name: "Target group", nextActivityId: null },
  ],
  offers: [],
};
