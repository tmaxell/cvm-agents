import type { CampaignFlow } from "../../types/api";

/**
 * Скелет flow — отображается в пустом состоянии canvas AdTarget по дефолтному
 * макету Eastwind UI. Три обязательные ноды (Common → Target Group → Event)
 * с warning на Target Group и активной Event-нодой (errors → красный X-badge).
 */
export const SKELETON_FLOW: CampaignFlow = {
  activities: [
    {
      id: "common",
      type: "CommonActivity",
      name: "Common",
      nextActivityId: "tg",
    },
    {
      id: "tg",
      type: "TargetGroupActivity",
      name: "Target Group",
      nextActivityId: "event",
      warnings: ["target-group-empty"],
    },
    {
      id: "event",
      type: "EventActivity",
      name: "Event",
      nextActivityId: null,
      errors: ["event-not-configured"],
    },
  ],
  offers: [],
};
