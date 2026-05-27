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
  if (positions.size === 0) return { width: NODE_W, height: NODE_H, minX: 0, minY: 0 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const { x, y } of positions.values()) {
    minX = Math.min(minX, x - NODE_W / 2);
    maxX = Math.max(maxX, x + NODE_W / 2);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y + NODE_H);
  }
  // Чистая ширина/высота bounding-box нод. Внешний padding добавляется
  // в render через `pad * 2`, симметрично слева/справа и сверху/снизу.
  return {
    width: maxX - minX,
    height: maxY - minY,
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
  // Side-panel — только для Event. Communication раскрывается inline в самой
  // карточке (детали оффера), без правой панели.
  const showSidePanel = !!selected && isEventActivity(selected.type);

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

// SVG-иконки групп — точные варианты из дизайн-системы Eastwind UI.
// Все 20×20, fill="none", stroke="currentColor" — цвет берётся из родителя.
function MegaphoneIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M12.3532 2.45427C11.0698 3.41262 9.59016 4.07488 8.02045 4.39348L3.59838 5.2779C3.23035 5.35108 2.89904 5.54953 2.66085 5.83948C2.42268 6.12944 2.29235 6.49299 2.29205 6.86822V10.698C2.28475 11.0787 2.41161 11.45 2.65044 11.7467C2.88926 12.0433 3.22482 12.2466 3.59838 12.3207L8.02045 13.2052C9.5875 13.5141 11.067 14.1652 12.3532 15.1119C12.4738 15.2023 12.6172 15.2574 12.7672 15.2709C12.9173 15.2845 13.0682 15.2559 13.2029 15.1886C13.3377 15.1212 13.4511 15.0176 13.5303 14.8894C13.6095 14.7612 13.6515 14.6135 13.6515 14.4628V3.10338C13.6515 2.95269 13.6095 2.80499 13.5303 2.67681C13.4511 2.54863 13.3377 2.44504 13.2029 2.37765C13.0682 2.31027 12.9173 2.28174 12.7672 2.29527C12.6172 2.3088 12.4738 2.36386 12.3532 2.45427Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M7.00606 12.9699L8.36916 16.613C8.41491 16.7354 8.43041 16.8669 8.41441 16.9964C8.39841 17.126 8.35141 17.2498 8.27731 17.3574C8.20325 17.4649 8.10432 17.5529 7.98895 17.614C7.87358 17.6751 7.74516 17.7075 7.6146 17.7084H6.66527C6.33371 17.7094 6.00976 17.6089 5.73704 17.4204C5.46432 17.2318 5.25591 16.9642 5.13986 16.6536L3.51709 12.2559" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M10.4058 6.34863V11.217" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M15.4365 5.33476L17.5462 4.11768" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M15.4365 12.2314L17.5462 13.4485" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M15.2742 8.78271H17.7083" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function BoltIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M10.8819 2.14743L4.26403 11.0981C4.21269 11.1663 4.18007 11.2495 4.17004 11.3377C4.16002 11.4258 4.173 11.5153 4.20746 11.5957C4.24191 11.6759 4.2964 11.7436 4.36447 11.7906C4.43254 11.8376 4.51135 11.8621 4.59157 11.861H9.58017L8.82433 17.7133C8.821 17.7526 8.829 17.7919 8.84716 17.8258C8.86533 17.8597 8.89266 17.8862 8.92541 17.9017C8.95808 17.9173 8.99433 17.9209 9.029 17.9122C9.06366 17.9033 9.09492 17.8826 9.11825 17.8529L15.7362 8.90225C15.7875 8.834 15.8201 8.75083 15.8301 8.66267C15.8402 8.5745 15.8272 8.48491 15.7927 8.40466C15.7582 8.32443 15.7037 8.25679 15.6357 8.20976C15.5676 8.16273 15.4888 8.13829 15.4086 8.13933H10.42L11.1758 2.28699C11.1792 2.24777 11.1712 2.2084 11.153 2.17453C11.1348 2.14067 11.1075 2.11407 11.0747 2.09855C11.0421 2.08302 11.0058 2.0794 10.9712 2.08818C10.9365 2.09697 10.9052 2.11772 10.8819 2.14743Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ThumbsUpIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4.79229 7.8457H3.80604C2.989 7.8457 2.32666 8.50808 2.32666 9.32508V16.2288C2.32666 17.0459 2.989 17.7082 3.80604 17.7082H4.79229C5.60933 17.7082 6.27167 17.0459 6.27167 16.2288V9.32508C6.27167 8.50808 5.60933 7.8457 4.79229 7.8457Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M17.6334 10.2024L16.4499 16.1199C16.3596 16.574 16.1126 16.9819 15.752 17.2722C15.3914 17.5625 14.9402 17.7167 14.4774 17.7078H8.24423C7.72109 17.7078 7.21938 17.5 6.84946 17.1301C6.47955 16.7602 6.27173 16.2584 6.27173 15.7353V9.81784C6.28758 9.438 6.38365 9.06592 6.55364 8.72592C6.72363 8.386 6.96367 8.08584 7.25798 7.8453L8.07657 2.91405C8.09502 2.799 8.14039 2.68995 8.20898 2.59576C8.27756 2.50158 8.36746 2.42493 8.47121 2.37205C8.57504 2.31918 8.68988 2.29158 8.80646 2.2915C8.92296 2.29143 9.0378 2.31887 9.14171 2.3716L10.059 2.82529C10.6649 3.1249 11.15 3.62299 11.4335 4.23667C11.717 4.85035 11.7819 5.54261 11.6172 6.19826L11.203 7.83544H15.7003C15.9928 7.83538 16.2817 7.90038 16.546 8.02575C16.8103 8.15111 17.0435 8.33367 17.2285 8.56025C17.4135 8.78684 17.5458 9.05175 17.6159 9.33575C17.6859 9.61975 17.6919 9.91584 17.6334 10.2024Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function BackArrowIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M2.77195 8.55426H12.8912C18.6736 8.55426 18.6736 16.5051 12.8912 16.5051M7.83158 13.6139L2.77195 8.55426L7.83158 3.49463" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ChatIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M15.1091 13.5494C12.9715 15.2752 10.8337 14.7822 9.76479 14.7822C8.69596 14.7822 8.63187 15.0072 7.85157 15.8542C7.75171 15.9596 7.63152 16.0435 7.4983 16.1009C7.36507 16.1582 7.22162 16.1878 7.07664 16.1878C6.93166 16.1878 6.7882 16.1582 6.65497 16.1009C6.52176 16.0435 6.40157 15.9596 6.30171 15.8542L4.16398 13.7102C4.05887 13.61 3.97518 13.4895 3.91798 13.3559C3.86079 13.2222 3.8313 13.0784 3.8313 12.933C3.8313 12.7876 3.86079 12.6437 3.91798 12.5101C3.97518 12.3765 4.05887 12.2559 4.16398 12.1558C5.01907 11.3732 5.23285 11.2232 5.23285 10.2369C5.23285 8.98266 4.64497 6.6136 7.08198 4.16946C7.68324 3.56147 8.40346 3.08484 9.19729 2.76955C9.99112 2.45425 10.8414 2.30711 11.6947 2.33737C12.548 2.36763 13.3858 2.57466 14.1555 2.94541C14.925 3.31616 15.6099 3.84262 16.1669 4.49168C16.7238 5.14074 17.1408 5.89837 17.3916 6.7169C17.6425 7.53542 17.7217 8.39716 17.6244 9.24791C17.527 10.0987 17.2552 10.92 16.826 11.6602C16.3969 12.4005 15.8195 13.0437 15.1305 13.5494H15.1091Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M3.91813 13.5386L2.66756 14.7928C2.32552 15.1358 2.22933 15.6075 2.46448 15.8648L4.20672 17.5371C4.44187 17.7729 4.91217 17.6765 5.27559 17.3334L6.45134 16.0792" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M13.26 5.42358C13.0197 5.18146 12.734 4.98933 12.4194 4.85824C12.1048 4.72715 11.7675 4.65967 11.4269 4.65967C11.0863 4.65967 10.7489 4.72715 10.4343 4.85824C10.1198 4.98933 9.83408 5.18146 9.59375 5.42358" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ClockCheckIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M17.5 10C17.5 11.4834 17.0601 12.9334 16.236 14.1668C15.4119 15.4001 14.2406 16.3614 12.8701 16.9291C11.4997 17.4968 9.99168 17.6453 8.53683 17.3559C7.08197 17.0665 5.7456 16.3522 4.6967 15.3033C3.64781 14.2544 2.9335 12.918 2.64411 11.4632C2.35472 10.0083 2.50325 8.50032 3.07091 7.12987C3.63856 5.75943 4.59986 4.58809 5.83323 3.76398C7.0666 2.93987 8.51664 2.5 10 2.5C12.1 2.5 14.1083 3.33333 15.6167 4.78333L17.5 6.66667" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M17.4999 2.5V6.66667H13.3333" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M9.34766 6.6665V11.7565L12.737 10.0599" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function ShieldIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M14.1166 3.91455H14.1666C14.8296 3.91455 15.4655 4.17101 15.9343 4.62751C16.4032 5.08402 16.6666 5.70317 16.6666 6.34876V14.4628C16.6666 15.3236 16.3154 16.1491 15.6903 16.7578C15.0652 17.3665 14.2173 17.7084 13.3333 17.7084H6.66659C5.78253 17.7084 4.93469 17.3665 4.30956 16.7578C3.68444 16.1491 3.33325 15.3236 3.33325 14.4628V6.34876C3.33312 5.71151 3.58964 5.09963 4.04772 4.6445C4.5058 4.18937 5.12891 3.92729 5.78325 3.91455" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M13.2832 2.2915H6.61654C6.1563 2.2915 5.7832 2.65478 5.7832 3.1029V4.72571L9.94985 7.65771L14.1165 4.72571V3.1029C14.1165 2.65478 13.7434 2.2915 13.2832 2.2915Z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

const SIDEBAR_GROUPS: Array<{ label: string; color: string; icon: () => JSX.Element; hasArrow: boolean }> = [
  { label: "Communication",         color: "#5257ff", icon: MegaphoneIcon,  hasArrow: true  },
  { label: "Custom communication",  color: "#5257ff", icon: MegaphoneIcon,  hasArrow: true  },
  { label: "Product action",        color: "#5257ff", icon: BoltIcon,       hasArrow: true  },
  { label: "Response",              color: "#ffcc00", icon: ThumbsUpIcon,   hasArrow: false },
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

  // Shift all positions so bounds.minX/minY → pad.
  // pos.x хранится как центр ноды, левый край = pos.x + offsetX - NODE_W/2.
  // Чтобы node-left самой левой ноды попадал в pad, нужно offsetX = pad - minX.
  const offsetX = pad - bounds.minX;
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

// Event раскрывается с Plate Actions + side-panel.
// Communication раскрывается с inline-блоком деталей оффера (без side-panel).
// Остальные типы (Business, Response, …) показывают цветной индикатор всегда,
// но при клике только подсвечиваются border'ом — без раскрытия.
function isEventActivity(type: string): boolean {
  return type === "EventActivity";
}
function isCommunicationActivity(type: string): boolean {
  return type === "PushCommunicationActivity" || type === "PullCommunicationActivity";
}
function isExpandableActivity(type: string): boolean {
  return isEventActivity(type) || isCommunicationActivity(type);
}

function AdtNode({ activity, offers, x, y, animDelay, selected, onSelect }: {
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
  const isEvent = isEventActivity(activity.type);
  const isCommunication = isCommunicationActivity(activity.type);
  const expanded = active && isExpandableActivity(activity.type);
  const showIndicator = !structural;

  const label = resolveNodeLabel(activity);
  const color = resolveNodeColor(activity);
  const hasError = Array.isArray(activity.errors) && activity.errors.length > 0;
  const subtitleText = activity.name && activity.name !== label ? activity.name : label;
  const subtitle = subtitleText.length > 30 ? subtitleText.slice(0, 28) + "…" : subtitleText;

  const offerDetails = isCommunication ? getCommunicationDetails(activity, offers) : [];

  return (
    <div
      className={`adt-node${active ? " adt-node-active" : ""}${expanded ? " adt-node-expanded" : ""}${hasError ? " adt-node-error" : ""}`}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: NODE_W,
        minHeight: NODE_H,
        animationDelay: `${animDelay}ms`,
      }}
      onClick={onSelect}
    >
      {/* Indicator strip — всегда виден у не-structural нод */}
      {showIndicator && (
        <span className="adt-node-indicator" style={{ background: color }} />
      )}

      {/* Title row */}
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
      {expanded && isEvent && (
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

      {/* Offer details — inline-блок у раскрытой communication-ноды */}
      {expanded && isCommunication && offerDetails.length > 0 && (
        <div className="adt-node-offer-block">
          <div className="adt-node-offer-title">Оффер</div>
          {offerDetails.map((d, i) => (
            <div key={i} className="adt-node-offer-row">
              <span className="adt-node-offer-key">{d.label}</span>
              <span className="adt-node-offer-val" title={d.value}>{d.value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Bottom spacer 4px */}
      <span className="adt-node-spacer" />

      {/* Error badge */}
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

// Достать список деталей оффера для communication-ноды.
// Используется и в inline expanded-блоке самой ноды, и (потенциально) в
// side-panel будущих типов.
function getCommunicationDetails(
  activity: FlowActivity,
  offers: CampaignOffer[],
): Array<{ label: string; value: string }> {
  if (!isCommunicationActivity(activity.type)) return [];

  const generatedOffer = offers.find((offer) => offer.activityId === activity.id);
  const parameters = activity.content?.parameters ?? [];
  const text = generatedOffer?.text ?? getParameterValue(parameters, "Text");
  const sender = generatedOffer?.sender ?? getParameterValue(parameters, "Sender");
  const rawChannel = generatedOffer?.contentType ?? activity.contentType ?? activity.content?.type;

  const details: Array<{ label: string; value: string }> = [];
  if (rawChannel) details.push({ label: "Канал", value: formatContentType(rawChannel) });
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

