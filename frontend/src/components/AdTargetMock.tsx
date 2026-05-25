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
  TargetGroupActivity:           { label: "Target Group",          color: "#64748b" },
  EventActivity:                 { label: "Event",                 color: "#ff48e7" },
  FilterActivity:                { label: "Filter",                color: "#94a3b8" },
  WaitActivity:                  { label: "Wait",                  color: "#ffcc00" },
  PushCommunicationActivity:     { label: "Push communication",    color: "#5257ff" },
  PullCommunicationActivity:     { label: "Pull communication",    color: "#5257ff" },
  BusinessTransactionActivity:   { label: "Business transaction",  color: "#611eb7" },
  ResponseActivity:              { label: "Response",              color: "#ffcc00" },
  InteractiveResponseActivity:   { label: "Interactive response",  color: "#ffcc00" },
  RealTimeCheckActivity:         { label: "Real-time check",       color: "#21cf18" },
  OrJoinActivity:                { label: "Or join",               color: "#611eb7" },
  SplitActivity:                 { label: "Split",                 color: "#611eb7" },
  TransferToCampaignActivity:    { label: "Transfer to campaign",  color: "#ff8b17" },
  ExcludeFromCampaignActivity:   { label: "Exclude from campaign", color: "#ff8b17" },
};

// Лейбл Push/Pull-нод — название канала (SMS, Email, Push, USSD, Custom)
// без дублирующего «push/pull» суффикса. Тип коммуникации передаёт цветовая
// полоска и сам факт ноды; имя в subtitle уточняет назначение.
function resolveNodeLabel(activity: FlowActivity): string {
  const meta = NODE_META[activity.type];
  if (!meta) return activity.type;
  if (activity.type === "PushCommunicationActivity" || activity.type === "PullCommunicationActivity") {
    const ct = activity.content?.type ?? activity.contentType ?? "";
    if (/sms/i.test(ct))     return "SMS";
    if (/email/i.test(ct))   return "Email";
    if (/ussd/i.test(ct))    return "USSD";
    if (/push/i.test(ct))    return "Push";
    if (/custom/i.test(ct))  return "Custom";
  }
  return meta.label;
}

function resolveNodeColor(activity: FlowActivity): string {
  return NODE_META[activity.type]?.color ?? "#94a3b8";
}

const NODE_W = 260;
const NODE_H = 72;
const H_GAP = 56;   // horizontal gap between parallel branches
const V_GAP = 40;   // vertical gap between rows — точно как Connector Arrow в макете

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
}: Props) {
  // Эффективный flow: либо переданный, либо дефолтный скелет (Common + Target Group).
  const effectiveFlow = flow && flow.activities?.length > 0 ? flow : SKELETON_FLOW;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = effectiveFlow.activities.find(a => a.id === selectedId) ?? null;
  // Side-panel открывается только для раскрываемых нод (сейчас — только Event).
  // У остальных типов клик меняет только border.
  const showSidePanel = !!selected && isExpandableActivity(selected.type);

  return (
    <div className="adt-shell">
      <AdtTopNav />
      <div className="adt-body">
        <AdtSidebar />
        <div className="adt-canvas-wrap">
          <div className="adt-canvas">
            <AdtFlowCanvas
              flow={effectiveFlow}
              selectedId={selectedId}
              onSelect={(id) => setSelectedId(prev => prev === id ? null : id)}
            />
          </div>
          <AdtRightToolbar />
          <AdtNotificationTab />
        </div>
        {showSidePanel && selected && (
          <AdtSidePanel activity={selected} onClose={() => setSelectedId(null)} />
        )}
      </div>
    </div>
  );
}

// ── Top navigation bar ────────────────────────────────────────────────────────

function AdtLogo() {
  // Точный SVG-логотип AdTarget из Eastwind UI: буквы «AdTarget» белым
  // как single-path glyph + двухсегментная стрелка #9AAEFF с треугольным
  // наконечником под текстом. viewBox 110×28.
  return (
    <svg
      width="110"
      height="28"
      viewBox="0 0 110 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="AdTarget"
    >
      <path d="M0.444092 18.2523L4.69932 3.44629H10.2396L14.4948 18.2523H11.988L8.44898 5.6672H6.44776L2.90876 18.2523H0.444092ZM2.97195 14.4451L3.68818 12.1184H11.2507L11.9669 14.4451H2.97195Z" fill="white"/>
      <path d="M20.504 18.4638C19.5912 18.4638 18.7556 18.2312 17.9973 17.7658C17.2529 17.3005 16.6561 16.6448 16.2067 15.7988C15.7713 14.9386 15.5537 13.9586 15.5537 12.8587C15.5537 11.7588 15.7713 10.7859 16.2067 9.9398C16.6561 9.07964 17.2529 8.41689 17.9973 7.95156C18.7556 7.48622 19.5912 7.25356 20.504 7.25356C21.2905 7.25356 21.9927 7.42277 22.6106 7.76119C23.2426 8.09962 23.699 8.49444 23.9799 8.94568H24.0852V3.44629H26.2971V18.2523H24.1905V16.666H24.0852C23.7622 17.1877 23.2777 17.6178 22.6317 17.9562C21.9997 18.2946 21.2905 18.4638 20.504 18.4638ZM21.0307 16.4545C21.6346 16.4545 22.1612 16.3134 22.6106 16.0314C23.074 15.7494 23.4322 15.3405 23.6849 14.8046C23.9518 14.2688 24.0852 13.6202 24.0852 12.8587C24.0852 12.0972 23.9518 11.4486 23.6849 10.9128C23.4322 10.3769 23.074 9.968 22.6106 9.68598C22.1612 9.40396 21.6346 9.26295 21.0307 9.26295C20.3987 9.26295 19.844 9.40396 19.3665 9.68598C18.889 9.9539 18.5169 10.3628 18.25 10.9128C17.9973 11.4486 17.8709 12.0972 17.8709 12.8587C17.8709 13.6202 17.9973 14.2759 18.25 14.8258C18.5169 15.3616 18.889 15.7706 19.3665 16.0526C19.844 16.3205 20.3987 16.4545 21.0307 16.4545Z" fill="white"/>
      <path d="M33.0405 18.2523V5.77295H28.3008V3.44629H40.2028V5.77295H35.463V18.2523H33.0405Z" fill="white"/>
      <path d="M44.429 18.4638C43.5162 18.4638 42.6806 18.2312 41.9222 17.7658C41.1779 17.3005 40.5811 16.6448 40.1317 15.7988C39.6963 14.9386 39.4786 13.9586 39.4786 12.8587C39.4786 11.7588 39.6963 10.7859 40.1317 9.9398C40.5811 9.07964 41.1779 8.41689 41.9222 7.95156C42.6806 7.48622 43.5162 7.25356 44.429 7.25356C45.2155 7.25356 45.9247 7.42277 46.5566 7.76119C47.2027 8.09962 47.6872 8.5297 48.0102 9.05143H48.1155V7.46507H50.222V18.2523H48.1155V16.666H48.0102C47.6872 17.1877 47.2027 17.6178 46.5566 17.9562C45.9247 18.2946 45.2155 18.4638 44.429 18.4638ZM44.9557 16.4545C45.5595 16.4545 46.0862 16.3134 46.5356 16.0314C46.999 15.7494 47.3571 15.3405 47.6099 14.8046C47.8768 14.2688 48.0102 13.6202 48.0102 12.8587C48.0102 12.0972 47.8768 11.4486 47.6099 10.9128C47.3571 10.3769 46.999 9.968 46.5356 9.68598C46.0862 9.40396 45.5595 9.26295 44.9557 9.26295C44.3237 9.26295 43.769 9.40396 43.2915 9.68598C42.814 9.9539 42.4419 10.3628 42.175 10.9128C41.9222 11.4486 41.7958 12.0972 41.7958 12.8587C41.7958 13.6202 41.9222 14.2759 42.175 14.8258C42.4419 15.3616 42.814 15.7706 43.2915 16.0526C43.769 16.3205 44.3237 16.4545 44.9557 16.4545Z" fill="white"/>
      <path d="M53.1737 18.2523V7.46507H55.2802V9.05143H55.3856C55.5962 8.6284 55.9614 8.24063 56.481 7.8881C57.0146 7.53558 57.6326 7.35931 58.3347 7.35931H59.8093V9.47446H58.3347C57.4781 9.47446 56.7689 9.74943 56.2071 10.2994C55.6594 10.8352 55.3856 11.5473 55.3856 12.4357V18.2523H53.1737Z" fill="white"/>
      <path d="M60.4413 24.5311V22.5217H68.9728V20.7767V16.7717H68.8675C68.5866 17.223 68.1302 17.6178 67.4982 17.9562C66.8803 18.2946 66.1781 18.4638 65.3917 18.4638C64.4788 18.4638 63.6432 18.2312 62.8849 17.7658C62.1406 17.3005 61.5437 16.6448 61.0943 15.7988C60.659 14.9386 60.4413 13.9586 60.4413 12.8587C60.4413 11.7588 60.659 10.7859 61.0943 9.9398C61.5437 9.07964 62.1406 8.41689 62.8849 7.95156C63.6432 7.48622 64.4788 7.25356 65.3917 7.25356C66.1781 7.25356 66.8873 7.42277 67.5193 7.76119C68.1653 8.09962 68.6498 8.5297 68.9728 9.05143H69.0781V7.46507H71.1847V20.7238V24.5311H60.4413ZM65.9183 16.4545C66.5222 16.4545 67.0488 16.3134 67.4982 16.0314C67.9617 15.7494 68.3198 15.3405 68.5726 14.8046C68.8394 14.2688 68.9728 13.6202 68.9728 12.8587C68.9728 12.0972 68.8394 11.4486 68.5726 10.9128C68.3198 10.3769 67.9617 9.968 67.4982 9.68598C67.0488 9.40396 66.5222 9.26295 65.9183 9.26295C65.2864 9.26295 64.7316 9.40396 64.2541 9.68598C63.7767 9.9539 63.4045 10.3628 63.1377 10.9128C62.8849 11.4486 62.7585 12.0972 62.7585 12.8587C62.7585 13.6202 62.8849 14.2759 63.1377 14.8258C63.4045 15.3616 63.7767 15.7706 64.2541 16.0526C64.7316 16.3205 65.2864 16.4545 65.9183 16.4545Z" fill="white"/>
      <path d="M79.1289 18.4638C78.0896 18.4638 77.1417 18.2171 76.285 17.7235C75.4283 17.23 74.7472 16.5602 74.2417 15.7142C73.7501 14.854 73.5044 13.9022 73.5044 12.8587C73.5044 11.8152 73.7501 10.8705 74.2417 10.0244C74.7472 9.16424 75.4283 8.48739 76.285 7.99386C77.1417 7.50032 78.0896 7.25356 79.1289 7.25356C80.154 7.25356 81.0809 7.49327 81.9095 7.97271C82.7521 8.45214 83.4192 9.13604 83.9107 10.0244C84.4023 10.8987 84.648 11.9139 84.648 13.0702C84.648 13.1689 84.648 13.2606 84.648 13.3452C84.648 13.4157 84.648 13.5003 84.648 13.599H74.7472V11.8011H83.2788L82.4151 12.3722C82.3589 11.8082 82.1904 11.2935 81.9095 10.8282C81.6427 10.3487 81.2705 9.968 80.793 9.68598C80.3155 9.40396 79.7608 9.26295 79.1289 9.26295C78.4828 9.26295 77.9071 9.41806 77.4015 9.72828C76.91 10.0244 76.5238 10.4474 76.2429 10.9974C75.962 11.5332 75.8216 12.1537 75.8216 12.8587C75.8216 13.5638 75.962 14.1912 76.2429 14.7412C76.5238 15.277 76.91 15.7001 77.4015 16.0103C77.9071 16.3064 78.4828 16.4545 79.1289 16.4545C79.8872 16.4545 80.4981 16.2993 80.9616 15.9891C81.439 15.6789 81.8112 15.27 82.078 14.7623H84.3952C84.1986 15.3969 83.8686 15.9962 83.4052 16.5602C82.9417 17.1243 82.3449 17.5825 81.6146 17.9351C80.8984 18.2876 80.0698 18.4638 79.1289 18.4638Z" fill="white"/>
      <path d="M87.9309 18.2523V4.82114H90.1428V16.2429H93.5133V18.2523H87.9309ZM85.6137 9.47446V7.46507H92.8813V9.47446H85.6137Z" fill="white"/>
      <path d="M0.444092 23.5547H57.2105" stroke="#9AAEFF" strokeWidth="2"/>
      <path d="M74.4236 23.5547H96.4661" stroke="#9AAEFF" strokeWidth="2"/>
      <path d="M105.556 24.5541L96.3877 24.5541L96.3877 18.3262L105.556 24.5541Z" fill="#9AAEFF"/>
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
        {/* Active state — иконка дерева в фирменном accent #5257FF */}
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <rect x="6.5" y="2.5" width="7" height="5" rx="1" fill="#5257FF"/>
          <rect x="2" y="12.5" width="6" height="5" rx="1" fill="#5257FF"/>
          <rect x="12" y="12.5" width="6" height="5" rx="1" fill="#5257FF"/>
          <path d="M10 7.5v3M5 12.5v-2h10v2" stroke="#5257FF" strokeWidth="1.4" strokeLinecap="round"/>
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

// ── Flow canvas ───────────────────────────────────────────────────────────────

function AdtFlowCanvas({ flow, selectedId, onSelect }: {
  flow: CampaignFlow;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
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
            selected={act.id === selectedId}
            onSelect={() => onSelect?.(act.id)}
          />
        );
      })}
    </div>
  );
}

// ── Single node card ──────────────────────────────────────────────────────────
//
// Canvas Plate из макета:
//   - 260×72 (default), 260×116 (active/expanded — с indicator+Plate Actions)
//   - bg #FFFFFF, border 1px #E2E8F0 rounded 6
//   - padding-top 12, gap 8 между title/subtitle (по 20px), spacer 4 снизу
//   - title: Tilda Sans 600 14/20 #1E293B (или цвет ноды в active)
//   - subtitle: Tilda Sans 600 14/20 #64748B
//   - active state — индикаторная полоса 4×16 слева top:14 в цвете ноды
//   - error → красный X badge (#E4575F) поверх левого-верхнего угла
//   - warning → жёлтый ⚠ (#FDBD1A) поверх левого-верхнего угла

function AdtPlateActionIcon({ kind }: { kind: "calendar" | "clock" | "check" }) {
  if (kind === "calendar") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <rect x="2" y="3.5" width="12" height="10.5" rx="1.5" stroke="#94A3B8" strokeWidth="1.3"/>
        <line x1="2" y1="6.5" x2="14" y2="6.5" stroke="#94A3B8" strokeWidth="1.3"/>
        <line x1="5.5" y1="2" x2="5.5" y2="4.5" stroke="#94A3B8" strokeWidth="1.3" strokeLinecap="round"/>
        <line x1="10.5" y1="2" x2="10.5" y2="4.5" stroke="#94A3B8" strokeWidth="1.3" strokeLinecap="round"/>
      </svg>
    );
  }
  if (kind === "clock") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="5.5" stroke="#94A3B8" strokeWidth="1.3"/>
        <path d="M8 5v3l2 1.2" stroke="#94A3B8" strokeWidth="1.3" strokeLinecap="round"/>
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8.5l3 3 7-7" stroke="#94A3B8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

// Структурные ноды — Common и Target Group: всегда дефолтный 72px вид,
// никакого indicator. На клике только border меняется.
function isStructuralActivity(type: string): boolean {
  return type === "CommonActivity" || type === "TargetGroupActivity";
}

// Только Event раскрывается на клике (показывает Plate Actions + side-panel).
// Остальные типы (Push/Pull, Business, Response, …) показывают цветной
// индикатор всегда, но при клике только подсвечиваются border'ом.
function isExpandableActivity(type: string): boolean {
  return type === "EventActivity";
}

function AdtNode({ activity, offers: _offers, x, y, animDelay, selected, onSelect }: {
  activity: FlowActivity;
  offers: CampaignOffer[];
  x: number;
  y: number;
  animDelay: number;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const active = !!selected;
  const structural = isStructuralActivity(activity.type);
  const expandable = isExpandableActivity(activity.type);
  // В expanded переходим только если нода раскрываемая И выбрана.
  const expanded = active && expandable;
  // Цветной индикатор слева — у всех не-structural нод (всегда).
  const showIndicator = !structural;

  const label = resolveNodeLabel(activity);
  const color = resolveNodeColor(activity);
  const hasError = Array.isArray(activity.errors) && activity.errors.length > 0;
  const subtitleText = activity.name && activity.name !== label ? activity.name : label;
  const subtitle = subtitleText.length > 30 ? subtitleText.slice(0, 28) + "…" : subtitleText;

  const nodeHeight = expanded ? 116 : NODE_H;

  return (
    <div
      className={`adt-node${active ? " adt-node-active" : ""}${expanded ? " adt-node-expanded" : ""}${hasError ? " adt-node-error" : ""}`}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: NODE_W,
        height: nodeHeight,
        animationDelay: `${animDelay}ms`,
      }}
      onClick={onSelect}
    >
      {/* Indicator strip — всегда виден у не-structural нод. */}
      {showIndicator && (
        <span className="adt-node-indicator" style={{ background: color }} />
      )}

      {/* Title row — цвет = цвет ноды у всех с индикатором */}
      <div className="adt-node-title">
        <span
          className="adt-node-title-text"
          style={showIndicator ? { color } : undefined}
        >
          {label}
        </span>
      </div>

      {/* Subtitle row */}
      <div className="adt-node-subtitle">
        <span className="adt-node-subtitle-text">{subtitle}</span>
      </div>

      {/* Plate Actions — только у раскрытой Event-ноды */}
      {expanded && (
        <div className="adt-node-plate-actions">
          <button className="adt-node-action-tab" type="button" onClick={(e) => e.stopPropagation()}>
            <AdtPlateActionIcon kind="calendar" />
          </button>
          <button className="adt-node-action-tab" type="button" onClick={(e) => e.stopPropagation()}>
            <AdtPlateActionIcon kind="clock" />
          </button>
          <button className="adt-node-action-tab" type="button" onClick={(e) => e.stopPropagation()}>
            <AdtPlateActionIcon kind="check" />
          </button>
        </div>
      )}

      {/* Bottom spacer 4px */}
      <span className="adt-node-spacer" />

      {/* Error badge — красный X, левый верхний угол. Только для не-structural,
          чтобы Common/Target Group в дефолтном скелете оставались чистыми. */}
      {hasError && !structural && (
        <span className="adt-node-badge adt-node-badge-error" title={`${(activity.errors as unknown[]).length} ошибок`}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path
              d="M6 1.7h8L18.3 6v8L14 18.3H6L1.7 14V6L6 1.7z"
              fill="#E4575F"
            />
            <path d="M7 7l6 6M13 7l-6 6" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </span>
      )}
    </div>
  );
}

// ── Side panel (properties для выбранной ноды) ────────────────────────────────
//
// Декоративный right-panel из макета: Name / Tags / Event-pill / Add filter /
// Filter rows / checkboxes / Event relevance + Ok/Cancel. Реальной логики нет —
// это статичный визуальный аналог Eastwind UI.

function AdtSidePanel({ activity, onClose }: { activity: FlowActivity; onClose: () => void }) {
  const isEvent = activity.type === "EventActivity";
  const title = isEvent ? "Event" : resolveNodeLabel(activity);

  return (
    <aside className="adt-side-panel" aria-label={`Свойства ${title}`}>
      <div className="adt-sp-body">
        <h3 className="adt-sp-title">{title}</h3>

        <div className="adt-sp-field">
          <label className="adt-sp-label">Name</label>
          <div className="adt-sp-input">{activity.name || title}</div>
        </div>

        <div className="adt-sp-field">
          <label className="adt-sp-label">
            Tags
            <button type="button" className="adt-sp-plus" title="Добавить тег">+</button>
          </label>
        </div>

        {isEvent && (
          <>
            <div className="adt-sp-field">
              <label className="adt-sp-label">Event</label>
              <div className="adt-sp-pill">
                <span className="adt-sp-pill-dot" />
                <span className="adt-sp-pill-label">AddedExampleFile</span>
                <span className="adt-sp-pill-close" title="Удалить">×</span>
              </div>
            </div>

            <div className="adt-sp-field">
              <label className="adt-sp-label">
                Add filter
                <button type="button" className="adt-sp-plus" title="Добавить фильтр">+</button>
              </label>
            </div>

            <div className="adt-sp-filter">
              <div className="adt-sp-filter-head">
                <span>Filter 1</span>
                <button type="button" className="adt-sp-icon-btn" title="Удалить фильтр">×</button>
              </div>
              <div className="adt-sp-filter-param">
                <label className="adt-sp-label">
                  Parameter
                  <button type="button" className="adt-sp-plus" title="Добавить параметр">+</button>
                </label>
              </div>
              <div className="adt-sp-filter-row">
                <div className="adt-sp-select"><span>ExampleParSelect</span><span className="adt-sp-caret">▾</span></div>
                <div className="adt-sp-select"><span>Is null</span><span className="adt-sp-caret">▾</span></div>
                <button type="button" className="adt-sp-icon-btn" title="Удалить">
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M4 3V2.5A1 1 0 015 1.5h4A1 1 0 0110 2.5V3M2.5 3.5h9M3.5 3.5v8a1 1 0 001 1h5a1 1 0 001-1v-8" stroke="#94A3B8" strokeWidth="1.2" strokeLinecap="round"/>
                  </svg>
                </button>
              </div>
              <div className="adt-sp-filter-row">
                <div className="adt-sp-select"><span>ExampleParSelect</span><span className="adt-sp-caret">▾</span></div>
                <div className="adt-sp-select"><span>Is null</span><span className="adt-sp-caret">▾</span></div>
                <button type="button" className="adt-sp-icon-btn" title="Удалить">
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M4 3V2.5A1 1 0 015 1.5h4A1 1 0 0110 2.5V3M2.5 3.5h9M3.5 3.5v8a1 1 0 001 1h5a1 1 0 001-1v-8" stroke="#94A3B8" strokeWidth="1.2" strokeLinecap="round"/>
                  </svg>
                </button>
              </div>
            </div>

            <label className="adt-sp-check">
              <span className="adt-sp-checkbox" />
              <span>Consider campaign schedule</span>
            </label>
            <label className="adt-sp-check">
              <span className="adt-sp-checkbox" />
              <span>Wait time parameters</span>
            </label>

            <div className="adt-sp-field">
              <label className="adt-sp-label">Event relevance</label>
              <div className="adt-sp-input">15</div>
            </div>
          </>
        )}
      </div>
      <div className="adt-sp-footer">
        <button type="button" className="adt-sp-btn adt-sp-btn-primary" onClick={onClose}>
          Ok
        </button>
        <button type="button" className="adt-sp-btn adt-sp-btn-secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
    </aside>
  );
}

// ── Notification list tab (bottom-left of canvas) ─────────────────────────────

function AdtNotificationTab() {
  return (
    <div className="adt-notif-tab" aria-label="Уведомления">
      <button className="adt-notif-cell" type="button" title="Уведомления">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M6 18.5V11a6 6 0 1112 0v7.5"
            stroke="#64748B"
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M3.5 18.5h17"
            stroke="#64748B"
            strokeWidth="1.3"
            strokeLinecap="round"
          />
          <path
            d="M10 21h4"
            stroke="#64748B"
            strokeWidth="1.3"
            strokeLinecap="round"
          />
        </svg>
        <span className="adt-notif-badge">3</span>
      </button>
      <button className="adt-notif-cell" type="button" title="Обновить">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M19.5 12a7.5 7.5 0 11-2.2-5.3"
            stroke="#CBD5E1"
            strokeWidth="1.3"
            strokeLinecap="round"
          />
          <path
            d="M19.5 4.5v3.5h-3.5"
            stroke="#CBD5E1"
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}

