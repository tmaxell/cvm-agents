/**
 * FloatingWidget — плавающий AI-ассистент поверх AdTarget.
 *
 * Вкладки:
 *   💬 CVM Copilot    — вопросы по платформе
 *   🛠 Campaign Builder — draft-сборка flow
 *   🧩 Segments        — подбор целевых сегментов
 *   📊 Monitoring      — метрики и рекомендации
 *
 * Фичи:
 *   - Все панели всегда смонтированы (state сохраняется при смене вкладок)
 *   - Кнопка ⤢/⤡ для переключения размера панели
 *   - Кнопка RU/EN для переключения языка
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { ChatPanel } from "./ChatPanel";
import { CampaignBuilderChat } from "./CampaignBuilderChat";
import { MonitoringPanel } from "./MonitoringPanel";
import { SegmentPanel } from "./SegmentPanel";
import type {
  BuilderResponse,
  CampaignRuntimeStatus,
  SelectedSegmentForBuilder,
} from "../types/api";

interface FloatingWidgetProps {
  onFlowUpdate: (response: BuilderResponse | null) => void;
  hasErrors: boolean;
  builderResponse: BuilderResponse | null;
  campaignStatus: CampaignRuntimeStatus;
}

type Tab = "copilot" | "segments" | "builder" | "monitoring";
type Size = "normal" | "large";
type Lang = "ru" | "en";
const PANEL_SIZES: Record<Size, { width: number; height: number }> = {
  normal: { width: 480, height: 560 },
  large: { width: 660, height: 760 },
};

const COPILOT_PLACEHOLDER: Record<Lang, string> = {
  ru: "Спросите о кампании, ошибках, настройках…",
  en: "Ask about campaigns, errors, settings…",
};

const TAB_LABELS: Record<
  Tab,
  { icon: string; label: string; shortLabel: string }
> = {
  copilot: { icon: "💬", label: "CVM Copilot", shortLabel: "Copilot" },
  segments: { icon: "🧩", label: "Segments", shortLabel: "Segments" },
  builder: { icon: "🛠", label: "Campaign Builder", shortLabel: "Builder" },
  monitoring: { icon: "📊", label: "Monitoring", shortLabel: "Monitor" },
};

const QUICK_PRESETS = {
  segments: [
    {
      label: "Family Max",
      product: "Тариф Family Max",
      campaignGoal: "Апсейл семейной аудитории",
      audienceConstraints: "Исключить opt-out и клиентов с контактом за последние 7 дней",
    },
    {
      label: "Travel Roaming",
      product: "Travel Roaming",
      campaignGoal: "Подключение роуминг-пакета перед поездкой",
      audienceConstraints: "Путешествующие клиенты, исключить opt-out",
    },
  ],
  builder: [
    {
      label: "Собрать draft flow",
      prompt:
        "Собери draft flow по заполненным параметрам. Используй выбранный сегмент, SMS/Push как каналы и верни готовый draft flow для проверки.",
    },
  ],
  monitoring: [
    {
      label: "Проверить перед запуском",
      action: "review" as const,
    },
  ],
};

export function FloatingWidget({
  onFlowUpdate,
  hasErrors,
  builderResponse,
  campaignStatus,
}: FloatingWidgetProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("copilot");
  const [size, setSize] = useState<Size>("normal");
  const [lang, setLang] = useState<Lang>("ru");
  const [selectedSegment, setSelectedSegment] =
    useState<SelectedSegmentForBuilder | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const hasMonitorData = Boolean(builderResponse?.campaign_id);

  const handleBuilderResponse = useCallback(
    (response: BuilderResponse | null) => {
      onFlowUpdate(response);
    },
    [onFlowUpdate],
  );

  const btnColor = hasErrors ? "var(--widget-error)" : "var(--widget-accent)";
  const btnGlow = hasErrors
    ? "0 0 0 4px rgba(239,68,68,0.25), 0 4px 20px rgba(239,68,68,0.4)"
    : "0 0 0 4px rgba(82,87,255,0.2), 0 4px 20px rgba(82,87,255,0.35)";

  const monitorFlowJson = builderResponse?.draft_flow
    ? JSON.stringify(builderResponse.draft_flow)
    : null;

  const { width, height } = PANEL_SIZES[size];

  const renderTabButton = (tabId: Tab) => {
    const monitoringHint =
      lang === "en"
        ? "Monitoring will open; create a campaign to see metrics"
        : "Monitoring откроется; для метрик создайте кампанию";

    return (
      <button
        key={tabId}
        className={`fw-tab${tab === tabId ? " active" : ""}`}
        onClick={() => setTab(tabId)}
        title={
          tabId === "monitoring" && !hasMonitorData
            ? monitoringHint
            : TAB_LABELS[tabId].label
        }
        style={tabId === "monitoring" ? { position: "relative" } : undefined}
      >
        <span className="fw-tab-icon" aria-hidden="true">
          {TAB_LABELS[tabId].icon}
        </span>
        <span className="fw-tab-label">{TAB_LABELS[tabId].shortLabel}</span>
        {tabId === "monitoring" && hasMonitorData && tab !== "monitoring" && (
          <span className="fw-tab-badge" />
        )}
      </button>
    );
  };

  const activePanelStyle = (tabId: Tab) => ({
    display: tab === tabId ? "contents" : "none",
  });

  const renderHeaderActions = () => (
    <div className="fw-header-actions">
      <button
        className="fw-action-btn"
        onClick={() => setLang((l) => (l === "ru" ? "en" : "ru"))}
        title={lang === "ru" ? "Switch to English" : "Переключить на русский"}
      >
        {lang === "ru" ? "EN" : "RU"}
      </button>
      <button
        className="fw-action-btn"
        onClick={() => setSize((s) => (s === "normal" ? "large" : "normal"))}
        title={
          size === "normal"
            ? lang === "en"
              ? "Expand"
              : "Развернуть"
            : lang === "en"
              ? "Collapse"
              : "Свернуть"
        }
      >
        {size === "normal" ? "⤢" : "⤡"}
      </button>
      <button
        className="fw-close"
        onClick={() => setOpen(false)}
        title={lang === "en" ? "Close" : "Закрыть"}
      >
        ✕
      </button>
    </div>
  );

  return (
    <div className="fw-root" ref={panelRef}>
      {/* ── Widget Panel ─────────────────────────────────────────── */}
      <div
        className={open ? "fw-panel" : "fw-panel fw-panel-hidden"}
        style={{ width, height }}
        aria-hidden={!open}
      >
        {/* Header */}
        <div className="fw-header">
          <div className="fw-tabs">
            {(["copilot", "segments", "builder", "monitoring"] as Tab[]).map(
              renderTabButton,
            )}
          </div>
          {renderHeaderActions()}
        </div>

        {/* Content — all panels always mounted; hidden via display:none to preserve state */}
        <div className="fw-body">
          <div className="fw-panel-slot" style={activePanelStyle("copilot")}>
            <ChatPanel
              title="CVM Copilot"
              endpoint="/api/copilot"
              messageKey="question"
              placeholder={COPILOT_PLACEHOLDER[lang]}
              suggestions={[]}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("segments")}>
            <SegmentPanel
              lang={lang}
              demoPlaybook={QUICK_PRESETS.segments}
              onSegmentSelected={setSelectedSegment}
              onUseInBuilder={() => setTab("builder")}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("builder")}>
            <CampaignBuilderChat
              onResponse={handleBuilderResponse}
              onOpenMonitoring={() => setTab("monitoring")}
              lang={lang}
              selectedSegment={selectedSegment}
              variant="demo"
              demoPlaybook={QUICK_PRESETS.builder}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("monitoring")}>
            <MonitoringPanel
              campaignId={builderResponse?.campaign_id ?? null}
              draftFlowJson={monitorFlowJson}
              campaignStatus={campaignStatus}
              lang={lang}
              variant="demo"
              demoPlaybook={QUICK_PRESETS.monitoring}
              onOpenBuilder={() => setTab("builder")}
            />
          </div>
        </div>
      </div>

      {/* ── Toggle Button ─────────────────────────────────────────── */}
      <button
        className="fw-toggle"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: btnColor,
          boxShadow: open ? "none" : btnGlow,
        }}
        title={
          open
            ? lang === "en"
              ? "Close assistant"
              : "Закрыть ассистента"
            : lang === "en"
              ? "Open AI assistant"
              : "Открыть AI-ассистент"
        }
        aria-label="AI Assistant"
      >
        {open ? (
          <span style={{ fontSize: 20, lineHeight: 1 }}>✕</span>
        ) : (
          <span className="fw-toggle-icon">{hasErrors ? "⚠" : "✦"}</span>
        )}
        {!open && hasErrors && <span className="fw-error-badge" />}
      </button>
    </div>
  );
}
