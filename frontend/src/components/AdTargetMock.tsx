/**
 * AdTargetMock — статичный CSS/SVG-макет интерфейса AdTarget.
 * Воссоздан по скриншоту реального AdTarget. Не функционален.
 *
 * Когда передан campaign flow — отображает его ноды на холсте в стиле AdTarget
 * с SVG-соединителями и tree-layout алгоритмом.
 */

import { useState } from "react";
import type { CampaignFlow, CampaignOffer, FlowActivity } from "../types/api";

interface Props {
  flow: CampaignFlow | null;
  campaignId?: number | null;
}

// ── Node type metadata ────────────────────────────────────────────────────────

const NODE_META: Record<string, { label: string; color: string }> = {
  CommonActivity:               { label: "Common",               color: "#6366f1" },
  TargetGroupActivity:          { label: "Target group",          color: "#f97316" },
  EventActivity:                { label: "Event",                 color: "#eab308" },
  FilterActivity:               { label: "Filter",                color: "#f97316" },
  WaitActivity:                 { label: "Wait",                  color: "#06b6d4" },
  PushCommunicationActivity:    { label: "Communication",         color: "#ef4444" },
  PullCommunicationActivity:    { label: "Pull",                  color: "#ef4444" },
  BusinessTransactionActivity:  { label: "Business transaction",  color: "#3b82f6" },
  ResponseActivity:             { label: "Response",              color: "#10b981" },
  RealTimeCheckActivity:        { label: "RT Check",              color: "#10b981" },
  SplitActivity:                { label: "Split",                 color: "#8b5cf6" },
};

const NODE_W = 158;
const NODE_H = 68;
const H_GAP = 56;   // horizontal gap between parallel branches
const V_GAP = 52;   // vertical gap between rows

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

export function AdTargetMock({ flow, campaignId }: Props) {
  return (
    <div className="adt-shell">
      <AdtTopNav />
      <AdtCampaignBar flow={flow} campaignId={campaignId} />
      <AdtTabBar />
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

function AdtTopNav() {
  const NAV = ["Segmentation", "Campaigns", "Reporting", "Approval", "Templates", "System", "Configuration"];
  return (
    <header className="adt-topnav">
      <div className="adt-topnav-logo">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <circle cx="10" cy="10" r="8.5" stroke="#4f8ef7" strokeWidth="1.5"/>
          <circle cx="10" cy="10" r="5" stroke="#4f8ef7" strokeWidth="1.5"/>
          <circle cx="10" cy="10" r="1.8" fill="#4f8ef7"/>
          <line x1="10" y1="1.5" x2="10" y2="0" stroke="#4f8ef7" strokeWidth="1.5" strokeLinecap="round"/>
          <line x1="10" y1="20" x2="10" y2="18.5" stroke="#4f8ef7" strokeWidth="1.5" strokeLinecap="round"/>
          <line x1="0" y1="10" x2="1.5" y2="10" stroke="#4f8ef7" strokeWidth="1.5" strokeLinecap="round"/>
          <line x1="20" y1="10" x2="18.5" y2="10" stroke="#4f8ef7" strokeWidth="1.5" strokeLinecap="round"/>
        </svg>
        <span className="adt-logo-text">AdTarget<span style={{color:"#4f8ef7"}}>.</span></span>
      </div>
      <nav className="adt-topnav-nav">
        {NAV.map(item => (
          <span key={item} className={`adt-nav-item${item === "Campaigns" ? " active" : ""}`}>
            {item} <span className="adt-nav-arrow">▾</span>
          </span>
        ))}
      </nav>
      <div className="adt-topnav-right">
        <span className="adt-clock">14:29 (UTC+05:00)</span>
        <div className="adt-avatar">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="5.5" r="2.8" stroke="rgba(255,255,255,0.85)" strokeWidth="1.3"/>
            <path d="M2 14.5c0-3.314 2.686-6 6-6s6 2.686 6 6" stroke="rgba(255,255,255,0.85)" strokeWidth="1.3" fill="none"/>
          </svg>
        </div>
      </div>
    </header>
  );
}

// ── Campaign breadcrumb bar ───────────────────────────────────────────────────

function AdtCampaignBar({ flow, campaignId }: { flow: CampaignFlow | null; campaignId?: number | null }) {
  const name = flow?.activities?.find(a => a.type === "CommonActivity")?.name ?? "Demo campaign";
  const idStr = campaignId ? ` | ${campaignId}` : "";
  return (
    <div className="adt-campaign-bar">
      <div className="adt-campaign-bar-left">
        <span className="adt-back-btn">‹</span>
        <span className="adt-campaign-crumb">Campaigns</span>
        <span className="adt-crumb-sep">/</span>
        <span className="adt-campaign-name">{name}{idStr}</span>
        <span className="adt-campaign-status">
          <span className="adt-status-dot" />
          Editing
        </span>
      </div>
      <div className="adt-campaign-bar-right">
        {["▶","⏸","⏹","🗓","⚙"].map(icon => (
          <button key={icon} className="adt-toolbar-btn">{icon}</button>
        ))}
      </div>
    </div>
  );
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

function AdtTabBar() {
  const TABS = ["FLOW", "OVERVIEW", "GOALS", "NBO", "CHANGE HISTORY", "STATUS HISTORY", "CAMPAIGN CLIENTS", "DETAILED REPORT", "REPORTS"];
  return (
    <div className="adt-tabbar">
      {TABS.map(tab => (
        <span key={tab} className={`adt-tab${tab === "FLOW" ? " active" : ""}`}>{tab}</span>
      ))}
    </div>
  );
}

// ── Left sidebar ──────────────────────────────────────────────────────────────

const SIDEBAR_GROUPS = [
  { label: "Communication",        color: "#4f8ef7", arrow: "◀", active: true  },
  { label: "Custom communication", color: "#4f8ef7", arrow: "◀", active: false },
  { label: "Response",             color: "#f97316", arrow: null, active: false },
  { label: "Business transaction", color: "#f97316", arrow: null, active: false },
  { label: "Product action",       color: "#f97316", arrow: null, active: false },
  { label: "Event",                color: "#eab308", arrow: null, active: false },
  { label: "Real-time check",      color: "#10b981", arrow: null, active: false },
  { label: "Control",              color: "#8b5cf6", arrow: "◀", active: false },
];

function AdtSidebar() {
  return (
    <aside className="adt-sidebar">
      <div className="adt-sidebar-search">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" opacity="0.45">
          <circle cx="5" cy="5" r="3.5" stroke="#6b7280" strokeWidth="1.2"/>
          <line x1="8" y1="8" x2="11" y2="11" stroke="#6b7280" strokeWidth="1.2" strokeLinecap="round"/>
        </svg>
        <span className="adt-search-placeholder">Search...</span>
      </div>

      {SIDEBAR_GROUPS.map(g => (
        <div key={g.label} className={`adt-sidebar-group${g.active ? " adt-sidebar-group-active" : ""}`}>
          <div className="adt-group-header" style={{ color: g.color }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span className="adt-group-dot" style={{ background: g.color }} />
              <span>{g.label}</span>
            </div>
            {g.arrow && <span className="adt-group-arrow">{g.arrow}</span>}
          </div>
        </div>
      ))}
    </aside>
  );
}

// ── Right vertical toolbar ────────────────────────────────────────────────────

function AdtRightToolbar() {
  const icons = [
    { d: "M3 2h10a1 1 0 011 1v10a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1zm3 0v4h4V2M5 9h6m-6 3h4", title: "Save" },
    { d: "M4 13V9h8v4M8 2v7M5.5 6.5L8 9l2.5-2.5", title: "Export" },
    { d: "M5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm0 1C2.51 6.5 0 8.79 0 12h10c0-3.21-2.51-5.5-5-5.5zm6-1a2 2 0 100-4 2 2 0 000 4zm2 1c1.38 0 2.5 1.12 2.5 2.5H11", title: "Users" },
  ];
  return (
    <div className="adt-right-toolbar">
      {icons.map(({ d, title }) => (
        <button key={title} className="adt-rt-btn" title={title}>
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <path d={d} stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      ))}
      <div className="adt-rt-divider" />
      <button className="adt-rt-btn" title="Zoom in">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3"/>
          <line x1="9.5" y1="9.5" x2="13" y2="13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          <line x1="4" y1="6" x2="8" y2="6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          <line x1="6" y1="4" x2="6" y2="8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>
      <button className="adt-rt-btn" title="Zoom out">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3"/>
          <line x1="9.5" y1="9.5" x2="13" y2="13" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          <line x1="4" y1="6" x2="8" y2="6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>
    </div>
  );
}

// ── Canvas empty state ────────────────────────────────────────────────────────

// Skeleton placeholder activities always shown on empty canvas
const SKELETON_ACTIVITIES: FlowActivity[] = [
  { id: "sk-common", type: "CommonActivity", name: "Campaign", nextActivityId: "sk-tg" },
  { id: "sk-tg", type: "TargetGroupActivity", name: "Target group", nextActivityId: undefined },
];
const SKELETON_FLOW = { activities: SKELETON_ACTIVITIES };

function AdtCanvasEmpty() {
  return (
    <div className="adt-canvas-empty">
      {/* Ghost skeleton of mandatory first two nodes */}
      <div style={{ opacity: 0.22, pointerEvents: "none", transform: "scale(0.88)", transformOrigin: "top center" }}>
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
          <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
            <path d="M0 1.5 L4 4 L0 6.5" stroke="rgba(40,50,70,0.4)" strokeWidth="1.2" fill="none" strokeLinecap="round"/>
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
            const ty = toPos.y + offsetY;

            // Cubic bezier: vertical departure + arrival
            const midY = (fy + ty) / 2;
            const d = `M ${fx} ${fy} C ${fx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`;

            return (
              <path
                key={`${act.id}-${childId}`}
                d={d}
                stroke="rgba(40,50,70,0.35)"
                strokeWidth="1.5"
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
  const meta = NODE_META[activity.type] ?? { label: activity.type, color: "#9ca3af" };
  const hasError = Array.isArray(activity.errors) && activity.errors.length > 0;
  const subtitleText = activity.name && activity.name !== meta.label ? activity.name : "";
  const communicationDetails = getCommunicationDetails(activity, offers);
  const isExpandable = communicationDetails.length > 0;

  // Shortened subtitle for display
  const subtitle = subtitleText.length > 22 ? subtitleText.slice(0, 20) + "…" : subtitleText;

  return (
    <div
      className={`adt-node${hasError ? " adt-node-error" : ""}${expanded ? " adt-node-expanded" : ""}`}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: NODE_W,
        height: expanded ? 154 : NODE_H,
        animationDelay: `${animDelay}ms`,
      }}
    >
      {/* Left colored strip */}
      <div className="adt-node-strip" style={{ background: hasError ? "#ef4444" : meta.color }} />

      {/* Colored dot */}
      <span className="adt-node-dot" style={{ background: hasError ? "#ef4444" : meta.color }} />

      <div className="adt-node-body">
        <div className="adt-node-type">{meta.label}</div>
        {subtitle && (
          <div className="adt-node-name">
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none" style={{ flexShrink: 0, opacity: 0.4, marginRight: 2 }}>
              <path d="M1 1h3.5l2.5 2.5-3.5 3.5L1 4.5V1z" stroke="currentColor" strokeWidth="1" strokeLinejoin="round"/>
            </svg>
            {subtitle}
          </div>
        )}
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
        {hasError && (
          <span className="adt-node-err-badge">{(activity.errors as unknown[]).length}</span>
        )}
      </div>
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
