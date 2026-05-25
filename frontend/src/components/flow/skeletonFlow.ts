import type { CampaignFlow } from "../../types/api";

/**
 * Дефолтный скелет flow для пустого canvas AdTarget — две обязательные
 * структурные ноды Common и Target Group, без warning/error. Все остальные
 * типы активностей (Event, Communication, …) подмешиваются только когда
 * соответствующая нода есть в реальной собранной кампании.
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
      nextActivityId: null,
    },
  ],
  offers: [],
};
