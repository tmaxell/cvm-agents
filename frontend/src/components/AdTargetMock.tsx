/**
 * AdTargetMock — статичный CSS/SVG-макет интерфейса AdTarget.
 * Воссоздан по макетам из Figma (Design System Eastwind UI).
 *
 * Когда передан campaign flow — отображает его ноды на холсте в стиле AdTarget
 * с SVG-соединителями и tree-layout алгоритмом.
 */

import { useState } from "react";
import type { CampaignFlow, CampaignOffer, CampaignRuntimeStatus, FlowActivity } from "../types/api";
import { SKELETON_FLOW } from "./flow/skeletonFlow";

interface Props {
  flow: CampaignFlow | null;
  campaignId?: number | null;
  campaignStatus: CampaignRuntimeStatus;
  isActionPending?: boolean;
  actionError?: string | null;
  canStartCampaign?: boolean;
  onStartCampaign: () => void | Promise<void>;
  onPauseCampaign: () => void | Promise<void>;
}

// ── Node type metadata ────────────────────────────────────────────────────────

// Соответствие типу активности — лейбл и цвет (по палитре design-system Eastwind UI).
const NODE_META: Record<string, { label: string; color: string }> = {
  CommonActivity:                { label: "Common",                color: "#64748b" },
  TargetGroupActivity:           { label: "Target group",          color: "#64748b" },
  EventActivity:                 { label: "Event",                 color: "#ff48e7" },
  FilterActivity:                { label: "Filter",                color: "#94a3b8" },
  WaitActivity:                  { label: "Wait",                  color: "#ffcc00" },
  PushCommunicationActivity:     { label: "Push communication",    color: "#5257ff" },
  PullCommunicationActivity:     { label: "Pull communication",    color: "#5257ff" },
  BusinessTransactionActivity:   { label: "Business transaction",  color: "#611eb7" },
  ResponseActivity:              { label: "Response",              color: "#ffcc00" },
  InteractiveResponseActivity:   { label: "Interactive response",  color: "#ffcc00" },
  RealTimeCheckActivity:         { label: "Real-time check",       color: "#21cf18" },
  OrJoinActivity:                { label: "Or",                    color: "#611eb7" },
  SplitActivity:                 { label: "Split",                 color: "#611eb7" },
  TransferToCampaignActivity:    { label: "Transfer to campaign",  color: "#ff8b17" },
  ExcludeFromCampaignActivity:   { label: "Exclude from campaign", color: "#ff8b17" },
};

// Лейбл Push/Pull зависит от contentType: SmsContent → «SMS push», PushContent → «Push push» и т.д.
function resolveNodeLabel(activity: FlowActivity): string {
  const meta = NODE_META[activity.type];
  if (!meta) return activity.type;
  if (activity.type === "PushCommunicationActivity" || activity.type === "PullCommunicationActivity") {
    const ct = activity.content?.type ?? activity.contentType ?? "";
    const kind = activity.type === "PushCommunicationActivity" ? "push" : "pull";
    if (/sms/i.test(ct))     return `SMS ${kind}`;
    if (/email/i.test(ct))   return `Email ${kind}`;
    if (/ussd/i.test(ct))    return `USSD ${kind}`;
    if (/push/i.test(ct))    return `Push ${kind}`;
    if (/custom/i.test(ct))  return `Custom ${kind}`;
  }
  return meta.label;
}

function resolveNodeColor(activity: FlowActivity): string {
  return NODE_META[activity.type]?.color ?? "#94a3b8";
}

const NODE_W = 200;
const NODE_H = 96;
const H_GAP = 48;   // horizontal gap between parallel branches
const V_GAP = 40;   // vertical gap between rows

// ── Tree layout ───────────────────────────────────────────────────────────────

interface Pos { x: number; y: number }

function buildAdjacency(activities: FlowActivity[]): Map<string, string[]> {
  const adj = new Map<string, string[]>();
  for (const act of activities) {
    const children: string[] = [];
    if (act.nextActivityId) children.push(act.nextActivityId);
    if (act.defaultSuccessActivityId) children.push(act.defaultSuccessActivityId);
    if (act.defaultFailActivityId) children.push(act.defaultFailActivityId);
    if (act.cases) {
      for (const targetId of Object.values(act.cases)) {
        if (targetId && !children.includes(targetId)) children.push(targetId);
      }
    }
    adj.set(act.id, children);
  }
  return adj;
}

function computeTreeLayout(activities: FlowActivity[]): Map<string, Pos> {
  if (activities.length === 0) return new Map();

  const adj = buildAdjacency(activities);
  const byId = new Map(activities.map(a => [a.id, a]));

  // Find root (CommonActivity or node not referenced by anyone)
  const referenced = new Set<string>();
  for (const children of adj.values()) {
    for (const c of children) referenced.add(c);
  }
  const root = activities.find(a => a.type === "CommonActivity" || !referenced.has(a.id));
  if (!root) return new Map(activities.map((a, i) => [a.id, { x: 0, y: i * (NODE_H + V_GAP) }]));

  // Count leaf descendants (for width allocation)
  const leafCount = new Map<string, number>();
  const visited = new Set<string>();

  function countLeaves(id: string): number {
    if (visited.has(id)) return 1;
    visited.add(id);
    const children = adj.get(id) ?? [];
    if (children.length === 0) {
      leafCount.set(id, 1);
      return 1;
    }
    const total = children.reduce((sum, c) => sum + countLeaves(c), 0);
    leafCount.set(id, total);
    return total;
  }
  countLeaves(root.id);

  // Assign positions top-down
  const positions = new Map<string, Pos>();
  const placed = new Set<string>();

  function place(id: string, centerX: number, depth: number) {
    if (placed.has(id) || !byId.has(id)) return;
    placed.add(id);
    positions.set(id, { x: centerX, y: depth * (NODE_H + V_GAP) });

    const children = (adj.get(id) ?? []).filter(c => byId.has(c));
    if (children.length === 0) return;

    const totalLeaves = children.reduce((s, c) => s + (leafCount.get(c) ?? 1), 0);
    const totalWidth = totalLeaves * NODE_W + (totalLeaves - 1) * H_GAP;
    let startX = centerX - totalWidth / 2;

    for (const child of children) {
      const leaves = leafCount.get(child) ?? 1;
      const childWidth = leaves * NODE_W + (leaves - 1) * H_GAP;
      place(child, startX + childWidth / 2, depth + 1);
      startX += childWidth + H_GAP;
    }
  }

  place(root.id, 0, 0);

  // Place any unreachable nodes below
  let unreachableY = (placed.size) * (NODE_H + V_GAP);
  for (const act of activities) {
    if (!placed.has(act.id)) {
      positions.set(act.id, { x: 0, y: unreachableY });
      unreachableY += NODE_H + V_GAP;
    }
  }

  return positions;
}

function computeBounds(positions: Map<string, Pos>): { width: number; height: number; minX: number; minY: number } {
  if (positions.size === 0) return { width: 200, height: 80, minX: 0, minY: 0 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const { x, y } of positions.values()) {
    minX = Math.min(minX, x - NODE_W / 2);
    maxX = Math.max(maxX, x + NODE_W / 2);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y + NODE_H);
  }
  return {
    width: maxX - minX + 48,
    height: maxY - minY + 48,
    minX,
    minY,
  };
}


// ── Main component ────────────────────────────────────────────────────────────

export function AdTargetMock({
  flow,
  campaignId,
  campaignStatus,
  isActionPending = false,
  actionError,
  canStartCampaign = true,
  onStartCampaign,
  onPauseCampaign,
}: Props) {
  return (
    <div className="adt-shell">
      <AdtTopNav />
      <AdtCampaignBar
        flow={flow}
        campaignId={campaignId}
        campaignStatus={campaignStatus}
        isActionPending={isActionPending}
        actionError={actionError}
        canStartCampaign={canStartCampaign}
        onStartCampaign={onStartCampaign}
        onPauseCampaign={onPauseCampaign}
      />
      <div className="adt-body">
        <AdtSidebar />
        <div className="adt-canvas">
          {flow && flow.activities?.length > 0
            ? <AdtFlowCanvas flow={flow} />
            : <AdtCanvasEmpty />
          }
        </div>
        <AdtRightToolbar />
      </div>
    </div>
  );
}

// ── Top navigation bar ────────────────────────────────────────────────────────

function AdtLogo() {
  // Точно по макету Eastwind UI: «AdTarget» + двухсегментная стрелка #9AAEFF
  // под текстом, заканчивающаяся треугольным наконечником.
  // viewBox 110×28 — соответствует Adt Logo фрейму.
  return (
    <svg width="110" height="28" viewBox="0 0 110 28" fill="none" aria-label="AdTarget">
      <text
        x="0"
        y="20"
        fill="#FFFFFF"
        fontFamily="Tilda Sans, Inter, -apple-system, sans-serif"
        fontWeight="700"
        fontSize="20"
        letterSpacing="-0.2"
      >
        AdTarget
      </text>
      {/* Arrow 2 — длинная палочка стрелки */}
      <line x1="0.44" y1="23.55" x2="57.21" y2="23.55" stroke="#9AAEFF" strokeWidth="2" strokeLinecap="square" />
      {/* Arrow 3 — короткая палочка справа */}
      <line x1="74.42" y1="23.55" x2="96.46" y2="23.55" stroke="#9AAEFF" strokeWidth="2" strokeLinecap="square" />
      {/* Polygon 1 — треугольный наконечник (повернут на 90deg) */}
      <polygon points="96.39,18.97 105.56,23.55 96.39,28.14" fill="#9AAEFF" />
    </svg>
  );
}

function AdtTopNav() {
  const NAV = ["Segmentation", "Campaigns", "Reporting", "Approval", "Templates", "System", "Configuration"];
  return (
    <header className="adt-topnav">
      <div className="adt-topnav-logo">
        <AdtLogo />
      </div>
      <nav className="adt-topnav-nav">
        {NAV.map(item => (
          <span key={item} className={`adt-nav-item${item === "Campaigns" ? " active" : ""}`}>
            <span className="adt-nav-item-label">{item}</span>
            <svg className="adt-nav-arrow" width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true">
              <path d="M1 1l4 4 4-4" stroke="#94A3B8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </span>
        ))}
        <span className="adt-clock">14:48 (UTC+05:00)</span>
        <div className="adt-avatar">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            {/* Вектор-голова (top: 11.46%, bottom: 52.27%) → круг сверху */}
            <circle cx="10" cy="7.27" r="3.64" stroke="#FFFFFF" strokeWidth="1.3"/>
            {/* Вектор-тело (top: 61.34%, bottom: 11.46%) → дуга снизу */}
            <path d="M3.65 17.71a6.35 6.35 0 0112.7 0" stroke="#FFFFFF" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
        </div>
      </nav>
    </header>
  );
}

// ── Campaign breadcrumb bar ───────────────────────────────────────────────────

function AdtCampaignBar({
  flow,
  campaignId,
  campaignStatus,
  isActionPending = false,
  actionError,
  canStartCampaign = true,
  onStartCampaign,
  onPauseCampaign,
}: {
  flow: CampaignFlow | null;
  campaignId?: number | null;
  campaignStatus: CampaignRuntimeStatus;
  isActionPending?: boolean;
  actionError?: string | null;
  canStartCampaign?: boolean;
  onStartCampaign: () => void | Promise<void>;
  onPauseCampaign: () => void | Promise<void>;
}) {
  const name = flow?.activities?.find(a => a.type === "CommonActivity")?.name ?? "Demo campaign";
  const idStr = campaignId ? ` | ${campaignId}` : "";
  const statusLabels: Record<CampaignRuntimeStatus, string> = {
    editing: "Редактирование",
    active: "Активна",
    paused: "На паузе",
  };
  const canStart = Boolean(campaignId)
    && !isActionPending
    && canStartCampaign
    && (campaignStatus === "editing" || campaignStatus === "paused");
  const canPause = !isActionPending && campaignStatus === "active";

  return (
    <div className="adt-campaign-bar">
      <div className="adt-campaign-bar-left">
        <span className="adt-back-btn">‹</span>
        <span className="adt-campaign-crumb">Campaigns</span>
        <span className="adt-crumb-sep">/</span>
        <span className="adt-campaign-name">{name}{idStr}</span>
        <span className={`adt-campaign-status ${campaignStatus}`}>
          <span className="adt-status-dot" />
          {statusLabels[campaignStatus]}
        </span>
      </div>
      <div className="adt-campaign-bar-right">
        {actionError && <span className="adt-action-error" title={actionError}>{actionError}</span>}
        <button
          type="button"
          className="adt-toolbar-btn adt-toolbar-btn-start"
          onClick={onStartCampaign}
          disabled={!canStart}
          title={!campaignId ? "Сначала создайте кампанию" : !canStartCampaign ? "Review checklist должен быть green или acknowledged warnings" : "Запустить кампанию"}
        >
          {isActionPending ? "…" : "▶"} Запустить
        </button>
        <button
          type="button"
          className="adt-toolbar-btn adt-toolbar-btn-pause"
          onClick={onPauseCampaign}
          disabled={!canPause}
          title="Поставить кампанию на паузу"
        >
          {isActionPending ? "…" : "⏸"} Пауза
        </button>
      </div>
    </div>
  );
}

// ── Left sidebar ──────────────────────────────────────────────────────────────

// SVG-иконки групп (стилизованы под Eastwind product icons,
// 16×16, currentColor — каждая группа берёт цвет из родителя).
function MegaphoneIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      {/* Корпус мегафона */}
      <path
        d="M2.5 6.5L9 4v8L2.5 9.5a1 1 0 01-.6-.93V7.43a1 1 0 01.6-.93z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill="currentColor"
        fillOpacity="0.12"
      />
      {/* Ручка снизу */}
      <path d="M4.5 9.5L5.5 13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      {/* Звуковые волны */}
      <path d="M11 6.5l1.2-.8M11 8h1.5M11 9.5l1.2.8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
    </svg>
  );
}
function BoltIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M9 1.5L3.5 9h3.5l-.5 5.5L12.5 7H9l.5-5.5z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill="currentColor"
      />
    </svg>
  );
}
function ThumbsUpIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M2 7.5h2.2v6.5H2.5a.5.5 0 01-.5-.5V7.5z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path
        d="M4.2 7.5l2.3-4.8c.2-.5.8-.7 1.3-.5.4.2.6.6.6 1l-.3 2.8h4.4a1.2 1.2 0 011.18 1.42l-.95 5.1A1.2 1.2 0 0111.6 13.5H4.2V7.5z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function BackArrowIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M6.5 3L2.5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M2.5 7h7a4 4 0 014 4v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ChatIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M2.5 4a2 2 0 012-2h7a2 2 0 012 2v5a2 2 0 01-2 2H7l-3.2 2.5a.3.3 0 01-.5-.23V11a1 1 0 01-.8-.98V4z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function ClockCheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M14 8a6 6 0 11-1.76-4.24" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <path d="M5.5 8l1.7 1.7L10.5 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ShieldIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M8 1.5l5 2v4.2c0 3-2.1 5.5-5 6.3-2.9-.8-5-3.3-5-6.3V3.5l5-2z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill="currentColor"
        fillOpacity="0.12"
      />
    </svg>
  );
}

const SIDEBAR_GROUPS: Array<{ label: string; color: string; icon: () => JSX.Element; hasArrow: boolean }> = [
  { label: "Communication",         color: "#5257ff", icon: MegaphoneIcon,  hasArrow: true  },
  { label: "Custom communication",  color: "#5257ff", icon: MegaphoneIcon,  hasArrow: true  },
  { label: "Product action",        color: "#5257ff", icon: BoltIcon,       hasArrow: true  },
  { label: "Responce",              color: "#ffcc00", icon: ThumbsUpIcon,   hasArrow: false },
  { label: "Business transaction",  color: "#611eb7", icon: BackArrowIcon,  hasArrow: false },
  { label: "Event",                 color: "#ff48e7", icon: ChatIcon,       hasArrow: false },
  { label: "Real-time check",       color: "#21cf18", icon: ClockCheckIcon, hasArrow: false },
  { label: "Control",               color: "#ff8b17", icon: ShieldIcon,     hasArrow: true  },
];

function AdtSidebar() {
  return (
    <aside className="adt-sidebar">
      <div className="adt-sidebar-search-container">
        <div className="adt-sidebar-search">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4"/>
            <line x1="10.5" y1="10.5" x2="14" y2="14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
          </svg>
          <span className="adt-search-placeholder">Search</span>
        </div>
      </div>
      <div className="adt-sidebar-groups-container">
        {SIDEBAR_GROUPS.map(g => (
          <div key={g.label} className="adt-sidebar-group">
            <div className="adt-group-left" style={{ color: g.color }}>
              <span className="adt-group-icon">{g.icon()}</span>
              <span className="adt-group-label">{g.label}</span>
            </div>
            {g.hasArrow && (
              <span className="adt-group-arrow">
                <svg width="12" height="8" viewBox="0 0 12 8" fill="none">
                  <path d="M1.5 1.75L6 6.25l4.5-4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </span>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}

// ── Right floating toolbar ────────────────────────────────────────────────────

function AdtRightToolbar() {
  return (
    <div className="adt-right-toolbar">
      <button className="adt-rt-btn" title="Save" type="button">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M4 3h10l3 3v11a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1zm3 0v5h6V3M6 12h8m-8 3h6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      <button className="adt-rt-btn" title="Export" type="button">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M4 13v3a1 1 0 001 1h10a1 1 0 001-1v-3M10 3v10M6 8l4-4 4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      <button className="adt-rt-btn active" title="Tree" type="button">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <rect x="7.5" y="2.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.4"/>
          <rect x="2.5" y="13.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.4"/>
          <rect x="12.5" y="13.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.4"/>
          <path d="M10 6.5v3M5 13.5v-2h10v2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        </svg>
      </button>
      <div className="adt-rt-zoom-group">
        <button className="adt-rt-btn" title="Zoom in" type="button">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <circle cx="9" cy="9" r="5.5" stroke="currentColor" strokeWidth="1.4"/>
            <line x1="13" y1="13" x2="17" y2="17" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
            <line x1="6.5" y1="9" x2="11.5" y2="9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
            <line x1="9" y1="6.5" x2="9" y2="11.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
          </svg>
        </button>
        <button className="adt-rt-btn" title="Zoom out" type="button">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <circle cx="9" cy="9" r="5.5" stroke="currentColor" strokeWidth="1.4"/>
            <line x1="13" y1="13" x2="17" y2="17" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
            <line x1="6.5" y1="9" x2="11.5" y2="9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
          </svg>
        </button>
      </div>
    </div>
  );
}

// ── Canvas empty state ────────────────────────────────────────────────────────

function AdtCanvasEmpty() {
  return (
    <div className="adt-canvas-empty">
      {/* Ghost skeleton of mandatory first two nodes */}
      <div style={{ opacity: 0.28, pointerEvents: "none", transform: "scale(0.92)", transformOrigin: "top center" }}>
        <AdtFlowCanvas flow={SKELETON_FLOW} />
      </div>
      <p className="adt-canvas-hint">Drag activities from the left panel<br/>or use the AI builder →</p>
    </div>
  );
}

// ── Flow canvas ───────────────────────────────────────────────────────────────

function AdtFlowCanvas({ flow }: { flow: CampaignFlow }) {
  const positions = computeTreeLayout(flow.activities);
  const bounds = computeBounds(positions);
  const pad = 24;

  // Shift all positions so minX,minY → pad
  const offsetX = pad - bounds.minX + NODE_W / 2;
  const offsetY = pad - bounds.minY;

  const canvasW = bounds.width + pad * 2;
  const canvasH = bounds.height + pad * 2;

  const adj = buildAdjacency(flow.activities);

  return (
    <div
      className="adt-flow-canvas-wrap"
      style={{ minWidth: canvasW, minHeight: canvasH, position: "relative" }}
    >
      {/* SVG connector layer */}
      <svg
        width={canvasW}
        height={canvasH}
        style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
      >
        <defs>
          <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="5" refY="5" orient="auto">
            <path d="M0 1.5 L5 5 L0 8.5" stroke="#94a3b8" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
          </marker>
        </defs>
        {flow.activities.map(act => {
          const fromPos = positions.get(act.id);
          if (!fromPos) return null;
          const fx = fromPos.x + offsetX;
          const fy = fromPos.y + offsetY + NODE_H;

          return (adj.get(act.id) ?? []).map(childId => {
            const toPos = positions.get(childId);
            if (!toPos) return null;
            const tx = toPos.x + offsetX;
            const ty = toPos.y + offsetY - 4;

            // Straight vertical line when same column; otherwise stepped path
            const d = fx === tx
              ? `M ${fx} ${fy} L ${tx} ${ty}`
              : `M ${fx} ${fy} L ${fx} ${(fy + ty) / 2} L ${tx} ${(fy + ty) / 2} L ${tx} ${ty}`;

            return (
              <path
                key={`${act.id}-${childId}`}
                d={d}
                stroke="#94a3b8"
                strokeWidth="1.4"
                fill="none"
                markerEnd="url(#arrowhead)"
              />
            );
          });
        })}
      </svg>

      {/* Node cards */}
      {flow.activities.map((act, i) => {
        const pos = positions.get(act.id);
        if (!pos) return null;
        const x = pos.x + offsetX - NODE_W / 2;
        const y = pos.y + offsetY;
        return (
          <AdtNode
            key={act.id}
            activity={act}
            offers={flow.offers ?? []}
            x={x}
            y={y}
            animDelay={i * 65}
          />
        );
      })}
    </div>
  );
}

// ── Single node card ──────────────────────────────────────────────────────────

function AdtNode({ activity, offers, x, y, animDelay }: {
  activity: FlowActivity; offers: CampaignOffer[]; x: number; y: number; animDelay: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const label = resolveNodeLabel(activity);
  const color = resolveNodeColor(activity);
  const hasError = Array.isArray(activity.errors) && activity.errors.length > 0;
  const subtitleText = activity.name && activity.name !== label ? activity.name : label;
  const communicationDetails = getCommunicationDetails(activity, offers);
  const isExpandable = communicationDetails.length > 0;

  const subtitle = subtitleText.length > 28 ? subtitleText.slice(0, 26) + "…" : subtitleText;

  return (
    <div
      className={`adt-node${hasError ? " adt-node-error" : ""}${expanded ? " adt-node-expanded" : ""}`}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: NODE_W,
        minHeight: NODE_H,
        animationDelay: `${animDelay}ms`,
      }}
    >
      <div className="adt-node-body">
        <div className="adt-node-type">
          <span className="adt-node-type-dot" style={{ background: color }} />
          {label}
        </div>
        <div className="adt-node-name">{subtitle}</div>
      </div>

      {expanded && (
        <div className="adt-node-offers">
          <div className="adt-node-offers-title">Сгенерированный оффер</div>
          {communicationDetails.map((detail, index) => (
            <div key={`${detail.label}-${index}`} className="adt-node-offer-row">
              <span>{detail.label}</span>
              <strong title={detail.value}>{detail.value}</strong>
            </div>
          ))}
        </div>
      )}

      {/* Action icons at bottom */}
      <div className="adt-node-actions">
        {isExpandable && (
          <button
            className="adt-node-expand"
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((value) => !value);
            }}
            title={expanded ? "Скрыть офферы" : "Показать офферы"}
            type="button"
          >
            {expanded ? "▴" : "▾"}
          </button>
        )}
        <span className="adt-node-act-icon">✎</span>
        <span className="adt-node-act-icon">⎘</span>
        <span className="adt-node-act-icon">✕</span>
      </div>

      {hasError && (
        <span className="adt-node-err-badge" title={`${(activity.errors as unknown[]).length} ошибок`}>
          ✕
        </span>
      )}
    </div>
  );
}

function getCommunicationDetails(
  activity: FlowActivity,
  offers: CampaignOffer[],
): Array<{ label: string; value: string }> {
  if (activity.type !== "PushCommunicationActivity" && activity.type !== "PullCommunicationActivity") {
    return [];
  }

  const generatedOffer = offers.find((offer) => offer.activityId === activity.id);
  const parameters = activity.content?.parameters ?? [];
  const text = generatedOffer?.text ?? getParameterValue(parameters, "Text");
  const sender = generatedOffer?.sender ?? getParameterValue(parameters, "Sender");
  const channel = generatedOffer?.contentType ?? activity.contentType ?? activity.name;

  const details: Array<{ label: string; value: string }> = [];
  if (channel) details.push({ label: "Канал", value: formatContentType(channel) });
  if (text) details.push({ label: "Оффер", value: text });
  if (sender) details.push({ label: "Отправитель", value: sender });
  if (generatedOffer?.offerTemplateId) {
    details.push({ label: "Шаблон", value: `#${generatedOffer.offerTemplateId}` });
  }
  if (generatedOffer?.businessOperationId) {
    details.push({ label: "Операция", value: generatedOffer.businessOperationId });
  }

  return details;
}

function formatContentType(contentType: string): string {
  return contentType.replace("Content", "");
}

function getParameterValue(
  parameters: NonNullable<FlowActivity["content"]>["parameters"],
  name: string,
): string | null {
  const param = parameters?.find((item) => item.name === name);
  if (param?.value === undefined || param.value === null) return null;
  return String(param.value);
}
