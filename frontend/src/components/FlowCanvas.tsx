/**
 * FlowCanvas — визуализация campaign flow в виде графа нод.
 *
 * Принимает объект flow (из BuilderResponse.draft_flow) и рендерит
 * цепочку активностей как блок-схему с SVG-связями.
 */

import { useMemo, useState } from "react";
import type { CampaignFlow, CampaignOffer, FlowActivity } from "../types/api";

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
const NODE_EXPANDED_HEIGHT = 188;
const X = 40;
const Y_START = 32;
const Y_GAP = 48;

export function FlowCanvas({ flow }: FlowCanvasProps) {
  const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(() => new Set());

  const orderedActivities = useMemo(
    () => (flow?.activities ? orderActivities(flow.activities) : []),
    [flow],
  );

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

  const nodeTops = new Map<string, number>();
  let cursorY = Y_START;
  for (const act of orderedActivities) {
    nodeTops.set(act.id, cursorY);
    cursorY += (expandedNodeIds.has(act.id) ? NODE_EXPANDED_HEIGHT : NODE_HEIGHT) + Y_GAP;
  }
  const canvasHeight = cursorY + 40;

  const toggleNode = (nodeId: string) => {
    setExpandedNodeIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

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
        {orderedActivities.slice(0, -1).map((act, i) => {
          const nextAct = orderedActivities[i + 1];
          const nodeHeight = expandedNodeIds.has(act.id) ? NODE_EXPANDED_HEIGHT : NODE_HEIGHT;
          const x1 = X + NODE_WIDTH / 2;
          const y1 = (nodeTops.get(act.id) ?? Y_START) + nodeHeight;
          const x2 = X + NODE_WIDTH / 2;
          const y2 = nodeTops.get(nextAct.id) ?? Y_START;
          return (
            <path
              key={`link-${act.id}-${nextAct.id}`}
              className="canvas-link"
              d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
            />
          );
        })}
      </svg>

      {/* Activity nodes */}
      {orderedActivities.map((act) => {
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
        const offerDetails = getCommunicationOfferDetails(act, flow.offers ?? []);
        const isExpanded = expandedNodeIds.has(act.id);
        const isExpandable = offerDetails.length > 0;

        return (
          <div
            key={act.id}
            className={`canvas-node ${statusClass}${isExpanded ? " canvas-node-expanded" : ""}`}
            style={{
              position: "absolute",
              left: X,
              top: nodeTops.get(act.id) ?? Y_START,
              width: NODE_WIDTH,
              minHeight: isExpanded ? NODE_EXPANDED_HEIGHT : NODE_HEIGHT,
            }}
          >
            <span className="canvas-node-type">
              {icon} {label}
            </span>
            <span className="canvas-node-name">{act.name || label}</span>
            {subtitle && (
              <span className="canvas-node-subtitle">
                {subtitle}
              </span>
            )}
            {hasErrors && (
              <span className="canvas-node-issues">
                ⚠ {(act.errors as Array<{errorMessage?: string}>)[0]?.errorMessage ?? "Ошибка"}
              </span>
            )}
            {isExpandable && (
              <button
                className="canvas-node-expand"
                onClick={() => toggleNode(act.id)}
                type="button"
                aria-expanded={isExpanded}
              >
                {isExpanded ? "Скрыть офферы" : "Показать офферы"}
              </button>
            )}
            {isExpanded && (
              <div className="canvas-node-offers">
                {offerDetails.map((detail, index) => (
                  <div key={`${detail.label}-${index}`} className="canvas-node-offer-row">
                    <span>{detail.label}</span>
                    <strong title={detail.value}>{detail.value}</strong>
                  </div>
                ))}
              </div>
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

function getCommunicationOfferDetails(
  act: FlowActivity,
  offers: CampaignOffer[],
): Array<{ label: string; value: string }> {
  if (act.type !== "PushCommunicationActivity" && act.type !== "PullCommunicationActivity") {
    return [];
  }

  const generatedOffer = offers.find((offer) => offer.activityId === act.id);
  const parameters = act.content?.parameters ?? [];
  const text = generatedOffer?.text ?? getParameterValue(parameters, "Text");
  const sender = generatedOffer?.sender ?? getParameterValue(parameters, "Sender");
  const channel = generatedOffer?.contentType ?? act.contentType;

  const details: Array<{ label: string; value: string }> = [];
  if (channel) details.push({ label: "Канал", value: CHANNEL_LABELS[channel] ?? channel });
  if (text) details.push({ label: "Оффер", value: text });
  if (sender) details.push({ label: "Отправитель", value: sender });
  if (generatedOffer?.offerTemplateId) details.push({ label: "Шаблон", value: `#${generatedOffer.offerTemplateId}` });
  if (generatedOffer?.businessOperationId) details.push({ label: "Операция", value: generatedOffer.businessOperationId });
  return details;
}

function getParameterValue(
  parameters: NonNullable<FlowActivity["content"]>["parameters"],
  name: string,
): string | null {
  const param = parameters?.find((item) => item.name === name);
  if (param?.value === undefined || param.value === null) return null;
  return String(param.value);
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
