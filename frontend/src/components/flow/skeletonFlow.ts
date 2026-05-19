import type { FlowActivity } from "../../types/api";

export const SKELETON_ACTIVITIES: FlowActivity[] = [
  { id: "sk-common", type: "CommonActivity", name: "Campaign", nextActivityId: "sk-tg" },
  { id: "sk-tg", type: "TargetGroupActivity", name: "Target group", nextActivityId: undefined },
];

export const SKELETON_FLOW = { activities: SKELETON_ACTIVITIES };
