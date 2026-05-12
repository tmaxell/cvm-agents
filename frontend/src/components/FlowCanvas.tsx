/**
 * FlowCanvas — визуализация campaign flow в виде графа нод.
 *
 * Принимает объект flow (из BuilderResponse.draft_flow) и рендерит
 * цепочку активностей как блок-схему с SVG-связями.
 */

import type { CampaignFlow, FlowActivity } from "../types/api";

interface FlowCanvasProps {
  flow: CampaignFlow | null;
}

// Activity type → short label
const TYPE_LABELS: Record<string, string> = {
  CommonActivity: "Кампания",
  TargetGroupActivity: "Аудитория",
  EventActivity: "Событие",
  PushCommunicationActivity: "Push",
  PullCommunicationActivity: "Pull",
  ResponseActivity: "Отклик",
  BusinessTransactionActivity: "Транзакция",
  RealTimeCheckActivity: "RT Check",
  OrJoinActivity: "Слияние",
};

// Activity type → emoji icon
const TYPE_ICONS: Record<string, string> = {
  CommonActivity: "⚙️",
  TargetGroupActivity: "👥",
  EventActivity: "⚡",
  PushCommunicationActivity: "📤",
  PullCommunicationActivity: "📥",
  ResponseActivity: "↩️",
  BusinessTransactionActivity: "🎁",
  RealTimeCheckActivity: "🔍",
  OrJoinActivity: "🔀",
};

// Content type → channel label
const CHANNEL_LABELS: Record<string, string> = {
  SmsContent: "SMS",
  EmailContent: "Email",
  UssdContent: "USSD",
  CustomContent: "Push мобильный",
  FlashSmsContent: "Flash SMS",
};

const NODE_WIDTH = 216;
const NODE_HEIGHT = 68;
const X = 40;
const Y_START = 32;
const Y_STEP = 116;

export function FlowCanvas({ flow }: FlowCanvasProps) {
  if (!flow || !flow.activities || flow.activities.length === 0) {
    return (
      <div className="canvas-workspace canvas-empty" style={{ height: "100%" }}>
        <div className="canvas-empty-state">
          <h2>Нет данных</h2>
          <p>Попросите агента создать кампанию — здесь появится её структура</p>
        </div>
      </div>
    );
  }

  // Sort activities by chain order starting from CommonActivity
  const orderedActivities = orderActivities(flow.activities);
  const canvasHeight = Y_START + orderedActivities.length * Y_STEP + 40;

  return (
    <div
      className="canvas-workspace"
      style={{ height: "100%", minHeight: canvasHeight, position: "relative", overflow: "auto" }}
    >
      {/* SVG connection lines */}
      <svg
        className="canvas-links"
        style={{ position: "absolute", top: 0, left: 0, width: "100%", height: canvasHeight, pointerEvents: "none" }}
      >
        {orderedActivities.slice(0, -1).map((_act, i) => {
          const x1 = X + NODE_WIDTH / 2;
          const y1 = Y_START + i * Y_STEP + NODE_HEIGHT;
          const x2 = X + NODE_WIDTH / 2;
          const y2 = Y_START + (i + 1) * Y_STEP;
          return (
            <path
              key={`link-${i}`}
              className="canvas-link"
              d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
            />
          );
        })}
      </svg>

      {/* Activity nodes */}
      {orderedActivities.map((act, i) => {
        const hasErrors = Array.isArray(act.errors) && act.errors.length > 0;
        const hasWarnings = Array.isArray(act.warnings) && act.warnings.length > 0;
        const statusClass = hasErrors
          ? "canvas-node-error"
          : hasWarnings
          ? "canvas-node-warning"
          : "canvas-node-ok";

        const label = TYPE_LABELS[act.type] ?? act.type;
        const icon = TYPE_ICONS[act.type] ?? "▪️";
        const subtitle = getSubtitle(act);

        return (
          <div
            key={act.id}
            className={`canvas-node ${statusClass}`}
            style={{
              position: "absolute",
              left: X,
              top: Y_START + i * Y_STEP,
              width: NODE_WIDTH,
            }}
          >
            <span className="canvas-node-type">
              {icon} {label}
            </span>
            <span className="canvas-node-name">{act.name || label}</span>
            {subtitle && (
              <span
                style={{
                  display: "block",
                  fontSize: 11,
                  color: "var(--text-secondary)",
                  marginTop: 2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {subtitle}
              </span>
            )}
            {hasErrors && (
              <span className="canvas-node-issues">
                ⚠ {(act.errors as Array<{errorMessage?: string}>)[0]?.errorMessage ?? "Ошибка"}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function getSubtitle(act: FlowActivity): string {
  if (act.type === "PushCommunicationActivity" && act.contentType) {
    return CHANNEL_LABELS[act.contentType] ?? act.contentType;
  }
  if (act.type === "EventActivity" && act.eventCode) {
    return act.eventCode;
  }
  if (act.type === "TargetGroupActivity" && act.clientSourceId) {
    return `ЦГ #${act.clientSourceId}`;
  }
  return "";
}

/** Sorts activities in chain order, starting from CommonActivity */
function orderActivities(activities: FlowActivity[]): FlowActivity[] {
  const byId = new Map(activities.map((a) => [a.id, a]));

  // Find root (CommonActivity or the one not referenced by any nextActivityId)
  const referenced = new Set(
    activities.map((a) => a.nextActivityId).filter(Boolean)
  );
  let current = activities.find(
    (a) => a.type === "CommonActivity" || !referenced.has(a.id)
  );

  const ordered: FlowActivity[] = [];
  const seen = new Set<string>();

  while (current && !seen.has(current.id)) {
    ordered.push(current);
    seen.add(current.id);
    current = current.nextActivityId ? byId.get(current.nextActivityId) : undefined;
  }

  // Append any remaining (branching scenarios)
  for (const a of activities) {
    if (!seen.has(a.id)) ordered.push(a);
  }

  return ordered;
}
