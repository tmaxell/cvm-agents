/**
 * FloatingWidget — плавающий AI-ассистент поверх AdTarget.
 *
 * Вкладки:
 *   💬 CVM Copilot    — вопросы по платформе
 *   🛠 Campaign Builder — создание кампании
 *   🧩 Segments        — подбор целевых сегментов
 *   📊 Monitoring      — метрики и рекомендации
 *
 * Фичи:
 *   - Все три панели всегда смонтированы (state сохраняется при смене вкладок)
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
type UiMode = "classic" | "demo";

const UI_MODE_KEY = "cvm_ui_mode";

const isUiMode = (value: string | null): value is UiMode =>
  value === "classic" || value === "demo";

const getInitialUiMode = (): UiMode => {
  if (typeof window === "undefined") {
    return "classic";
  }

  const storedUiMode = window.localStorage.getItem(UI_MODE_KEY);
  return isUiMode(storedUiMode) ? storedUiMode : "classic";
};
type DemoStepState = "pending" | "active" | "completed" | "attention";

interface DemoStep {
  key: string;
  label: string;
  statusText: string;
  tab: Tab;
  state: DemoStepState;
}

interface DemoPlaybookItem {
  label: string;
  description: string;
  prompt?: string;
  product?: string;
  campaignGoal?: string;
  audienceConstraints?: string;
}

const PANEL_SIZES: Record<Size, { width: number; height: number }> = {
  normal: { width: 480, height: 560 },
  large: { width: 660, height: 760 },
};

const COPILOT_SUGGESTIONS: Record<Lang, string[]> = {
  ru: [
    "Какие типы активностей бывают в кампании?",
    "Что означает ошибка TargetGroupNotSet?",
    "Как запустить кампанию через API?",
    "Чем отличается Push от Pull коммуникации?",
  ],
  en: [
    "What activity types exist in a campaign?",
    "What does the TargetGroupNotSet error mean?",
    "How do I start a campaign via API?",
    "What is the difference between Push and Pull communication?",
  ],
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

const DEMO_SCENARIOS: Record<
  Tab,
  Record<Lang, { eyebrow: string; title: string; text: string; metric: string }>
> = {
  copilot: {
    ru: {
      eyebrow: "Self-service support",
      title: "AI объяснит правила AdTarget и подскажет следующий шаг",
      text: "Задавайте вопросы по кампаниям, ошибкам и API — Copilot отвечает в контексте текущего сценария.",
      metric: "быстрый onboarding",
    },
    en: {
      eyebrow: "Self-service support",
      title: "AI explains AdTarget rules and suggests the next step",
      text: "Ask about campaigns, errors, and APIs — Copilot answers in the current workflow context.",
      metric: "faster onboarding",
    },
  },
  segments: {
    ru: {
      eyebrow: "Audience intelligence",
      title: "AI соберёт оптимальную аудиторию для вашей кампании",
      text: "Используйте существующую Target Group или создайте новый demo-сегмент с понятными критериями отбора.",
      metric: "2 сценария подбора",
    },
    en: {
      eyebrow: "Audience intelligence",
      title: "AI assembles the optimal audience for your campaign",
      text: "Use an existing Target Group or create a new demo segment with transparent selection criteria.",
      metric: "2 audience paths",
    },
  },
  builder: {
    ru: {
      eyebrow: "Campaign launch",
      title: "Builder превратит цель и аудиторию в готовый flow",
      text: "Выбранный сегмент передаётся без сброса истории, чтобы быстро собрать коммуникацию и проверить ошибки. Flow appears on the AdTarget canvas behind the assistant.",
      metric: "draft-to-flow",
    },
    en: {
      eyebrow: "Campaign launch",
      title: "Builder turns a goal and audience into a ready flow",
      text: "The selected segment is passed without losing history, making it quick to assemble and validate a campaign. Flow appears on the AdTarget canvas behind the assistant.",
      metric: "draft-to-flow",
    },
  },
  monitoring: {
    ru: {
      eyebrow: "Independent review",
      title: "Monitoring — независимая проверка кампании перед запуском",
      text: "Reviewer agent проверяет риски доставки, структуру flow, готовность к запуску и лучший следующий шаг.",
      metric: "pre-launch review",
    },
    en: {
      eyebrow: "Independent review",
      title: "Monitoring is an independent campaign check before launch",
      text: "Reviewer agent checks delivery risk, flow structure, launch readiness, and the next best action.",
      metric: "pre-launch review",
    },
  },
};

const DEMO_PLAYBOOK: Record<Tab, Record<Lang, DemoPlaybookItem[]>> = {
  copilot: {
    ru: COPILOT_SUGGESTIONS.ru.slice(0, 3).map((label) => ({
      label,
      description: "Отправить вопрос в Copilot",
      prompt: label,
    })),
    en: COPILOT_SUGGESTIONS.en.slice(0, 3).map((label) => ({
      label,
      description: "Send this question to Copilot",
      prompt: label,
    })),
  },
  segments: {
    ru: [
      {
        label: "Тариф Family Max",
        description: "Продукт + цель для апсейла семейной аудитории",
        product: "Тариф Family Max",
        campaignGoal: "Апсейл семейных абонентов на пакет с большим интернетом и shared benefits",
        audienceConstraints: "Семейные клиенты 25–45, 2+ SIM, высокий расход мобильного интернета; исключить opt-out и контакты за последние 14 дней",
      },
      {
        label: "Travel Roaming Pack",
        description: "Цель — активация роуминг-пакета перед поездкой",
        product: "Travel Roaming Pack",
        campaignGoal: "Активировать роуминг-пакет у клиентов с высокой вероятностью поездки",
        audienceConstraints: "Клиенты с международными звонками или роумингом за 12 месяцев; исключить корпоративные номера и недавние промо-контакты",
      },
    ],
    en: [
      {
        label: "Family Max tariff",
        description: "Product + goal for family-audience upsell",
        product: "Family Max tariff",
        campaignGoal: "Upsell family subscribers to a larger internet bundle with shared benefits",
        audienceConstraints: "Family customers 25–45, 2+ SIMs, high mobile data usage; exclude opt-outs and contacts from the last 14 days",
      },
      {
        label: "Travel Roaming Pack",
        description: "Goal — activate roaming bundle before travel",
        product: "Travel Roaming Pack",
        campaignGoal: "Activate a roaming pack for customers with a high travel propensity",
        audienceConstraints: "Customers with international calls or roaming in the last 12 months; exclude corporate numbers and recent promo contacts",
      },
    ],
  },
  builder: {
    ru: [
      {
        label: "Собрать flow",
        description: "Подготовить полный draft кампании из текущего контекста",
        prompt: "Собери готовый Campaign Builder flow: используй выбранный сегмент, добавь entry criteria, 2 канала коммуникации, offer activation и финальную проверку ошибок.",
      },
      {
        label: "Доработать контент",
        description: "Улучшить тексты и тон коммуникаций",
        prompt: "Доработай контент в текущем flow: сделай тон более персональным, сократи push до 90 символов и добавь premium-вариант текста для SMS.",
      },
      {
        label: "Проверить ошибки",
        description: "Найти блокеры запуска и предложить исправления",
        prompt: "Проверь текущий draft flow на ошибки запуска: Target Group, channels, offer activation, schedule, frequency cap. Верни список проблем и точные исправления.",
      },
    ],
    en: [
      {
        label: "Build flow",
        description: "Prepare a complete campaign draft from current context",
        prompt: "Build a ready Campaign Builder flow: use the selected segment, add entry criteria, 2 communication channels, offer activation, and a final error check.",
      },
      {
        label: "Refine content",
        description: "Improve message copy and tone",
        prompt: "Refine the content in the current flow: make the tone more personal, keep push under 90 characters, and add a premium SMS copy variant.",
      },
      {
        label: "Check errors",
        description: "Find launch blockers and propose fixes",
        prompt: "Check the current draft flow for launch errors: Target Group, channels, offer activation, schedule, frequency cap. Return issues and exact fixes.",
      },
    ],
  },
  monitoring: {
    ru: [
      {
        label: "Оценить готовность",
        description: "Запустить pre-launch review кампании",
      },
      {
        label: "Показать рекомендации",
        description: "Открыть delivery, structure и launch-рекомендации",
      },
    ],
    en: [
      {
        label: "Evaluate readiness",
        description: "Run the campaign pre-launch review",
      },
      {
        label: "Show recommendations",
        description: "Open delivery, structure, and launch recommendations",
      },
    ],
  },
};

interface DemoWorkingContextProps {
  selectedSegment: SelectedSegmentForBuilder | null;
  builderResponse: BuilderResponse | null;
  hasErrors: boolean;
  campaignStatus: CampaignRuntimeStatus;
  onSelectTab: (tab: Tab) => void;
}

function DemoWorkingContext({
  selectedSegment,
  builderResponse,
  hasErrors,
  campaignStatus,
  onSelectTab,
}: DemoWorkingContextProps) {
  const emptyValue = "ещё не выбран";
  const flowPlaceholder = "flow ещё не собран";
  const segmentName = selectedSegment?.hypothesis.name || emptyValue;
  const product = selectedSegment?.product || emptyValue;
  const goal = selectedSegment?.goal || emptyValue;
  const campaignId = builderResponse?.campaign_id ?? emptyValue;
  const flowStatus = builderResponse?.draft_flow ? "flow собран" : flowPlaceholder;
  const validationState = hasErrors ? "Needs attention" : "Ready";

  return (
    <section className="fw-demo-context" aria-label="Working context">
      <div className="fw-demo-context-header">
        <div>
          <span>Demo-only snapshot</span>
          <h3>Working context</h3>
        </div>
        <strong>{validationState}</strong>
      </div>

      <dl className="fw-demo-context-grid">
        <div>
          <dt>Выбранный сегмент</dt>
          <dd>{segmentName}</dd>
        </div>
        <div>
          <dt>Продукт</dt>
          <dd>{product}</dd>
        </div>
        <div>
          <dt>Цель</dt>
          <dd>{goal}</dd>
        </div>
        <div>
          <dt>Campaign ID</dt>
          <dd>{campaignId}</dd>
        </div>
        <div>
          <dt>Flow status</dt>
          <dd>{flowStatus}</dd>
        </div>
        <div>
          <dt>Validation state</dt>
          <dd>{validationState}</dd>
        </div>
        <div>
          <dt>Runtime status</dt>
          <dd>{campaignStatus}</dd>
        </div>
      </dl>

      <div className="fw-demo-context-actions">
        <button type="button" onClick={() => onSelectTab("segments")}>
          Выбрать аудиторию
        </button>
        <button type="button" onClick={() => onSelectTab("builder")}>
          Собрать flow
        </button>
        <button type="button" onClick={() => onSelectTab("monitoring")}>
          Проверить мониторинг
        </button>
      </div>
    </section>
  );
}

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
  const [uiMode, setUiMode] = useState<UiMode>(getInitialUiMode);
  const [selectedSegment, setSelectedSegment] =
    useState<SelectedSegmentForBuilder | null>(null);
  const [showCanvasUpdatedBadge, setShowCanvasUpdatedBadge] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const hasDraftFlow = builderResponse?.draft_flow != null;
  const previousHasDraftFlowRef = useRef(hasDraftFlow);

  useEffect(() => {
    window.localStorage.setItem(UI_MODE_KEY, uiMode);
  }, [uiMode]);

  useEffect(() => {
    if (uiMode !== "demo") {
      previousHasDraftFlowRef.current = hasDraftFlow;
      setShowCanvasUpdatedBadge(false);
      return;
    }

    if (hasDraftFlow && !previousHasDraftFlowRef.current) {
      setShowCanvasUpdatedBadge(true);
      const timeoutId = window.setTimeout(() => {
        setShowCanvasUpdatedBadge(false);
      }, 4200);
      previousHasDraftFlowRef.current = hasDraftFlow;
      return () => window.clearTimeout(timeoutId);
    }

    previousHasDraftFlowRef.current = hasDraftFlow;
  }, [hasDraftFlow, uiMode]);

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

  const hasMonitorData = builderResponse?.campaign_id != null;

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
  const activeScenario = DEMO_SCENARIOS[tab][lang];
  const activePlaybook = DEMO_PLAYBOOK[tab][lang];
  const hasSelectedAudience = selectedSegment != null;
  const hasCampaign = builderResponse?.campaign_id != null;
  const activeDemoStepKey: string =
    tab === "copilot"
      ? "brief"
      : tab === "segments"
        ? "audience"
        : tab === "builder"
          ? hasDraftFlow || hasErrors
            ? "validate"
            : "flow"
          : hasCampaign
            ? "monitor"
            : "launch";
  const campaignStatusLabel =
    campaignStatus === "active"
      ? lang === "en"
        ? "Launched"
        : "Запущена"
      : campaignStatus === "paused"
        ? lang === "en"
          ? "Paused"
          : "На паузе"
        : lang === "en"
          ? "Editing"
          : "Редактирование";
  const demoSteps: DemoStep[] = [
    {
      key: "brief",
      label: lang === "en" ? "Brief" : "Бриф",
      statusText: lang === "en" ? "Ready" : "Готов",
      tab: "copilot",
      state: activeDemoStepKey === "brief" ? "active" : "completed",
    },
    {
      key: "audience",
      label: lang === "en" ? "Audience" : "Аудитория",
      statusText: hasSelectedAudience
        ? lang === "en"
          ? "Completed"
          : "Готово"
        : lang === "en"
          ? "Select"
          : "Выбрать",
      tab: "segments",
      state: hasSelectedAudience ? "completed" : activeDemoStepKey === "audience" ? "active" : "pending",
    },
    {
      key: "flow",
      label: lang === "en" ? "Build flow" : "Сборка flow",
      statusText: hasDraftFlow
        ? lang === "en"
          ? "Completed"
          : "Готово"
        : lang === "en"
          ? "Draft"
          : "Черновик",
      tab: "builder",
      state: hasDraftFlow ? "completed" : activeDemoStepKey === "flow" ? "active" : "pending",
    },
    {
      key: "validate",
      label: lang === "en" ? "Validate" : "Проверка",
      statusText: hasErrors
        ? lang === "en"
          ? "Needs attention"
          : "Нужно внимание"
        : hasDraftFlow
          ? lang === "en"
            ? "Passed"
            : "Пройдена"
          : lang === "en"
            ? "Waiting"
            : "Ожидает",
      tab: "builder",
      state: hasErrors
        ? "attention"
        : hasDraftFlow
          ? "completed"
          : activeDemoStepKey === "validate"
            ? "active"
            : "pending",
    },
    {
      key: "launch",
      label: lang === "en" ? "Launch" : "Запуск",
      statusText: hasCampaign
        ? lang === "en"
          ? "Campaign created"
          : "Кампания создана"
        : lang === "en"
          ? "Create"
          : "Создать",
      tab: hasCampaign ? "monitoring" : "builder",
      state: hasCampaign ? "completed" : activeDemoStepKey === "launch" ? "active" : "pending",
    },
    {
      key: "monitor",
      label: lang === "en" ? "Monitor" : "Мониторинг",
      statusText: campaignStatusLabel,
      tab: "monitoring",
      state: activeDemoStepKey === "monitor" ? "active" : hasCampaign ? "completed" : "pending",
    },
  ];

  const renderTabButton = (tabId: Tab) => (
    <button
      key={tabId}
      className={`fw-tab${tab === tabId ? " active" : ""}`}
      onClick={() => setTab(tabId)}
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

  const activePanelStyle = (tabId: Tab) => ({
    display: tab === tabId ? (uiMode === "demo" ? "flex" : "contents") : "none",
  });

  const renderHeaderActions = () => (
    <div className="fw-header-actions">
      <button
        className="fw-action-btn fw-mode-toggle"
        onClick={() =>
          setUiMode((mode) => (mode === "classic" ? "demo" : "classic"))
        }
        title={
          uiMode === "classic" ? "Switch to Demo UX" : "Switch to Classic UX"
        }
      >
        {uiMode === "classic" ? "Demo" : "Classic"}
      </button>
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
        className={`${open ? "fw-panel" : "fw-panel fw-panel-hidden"}${uiMode === "demo" ? " demo" : ""}`}
        style={{ width, height }}
        aria-hidden={!open}
      >
        {/* Header */}
        {uiMode === "classic" ? (
          <div className="fw-header">
            <div className="fw-tabs">
              {(["copilot", "segments", "builder", "monitoring"] as Tab[]).map(
                renderTabButton,
              )}
            </div>
            {renderHeaderActions()}
          </div>
        ) : (
          <div className="fw-header fw-demo-header">
            <div className="fw-demo-title">
              <span className="fw-demo-mark" aria-hidden="true">
                ✦
              </span>
              <div>
                <strong>
                  {lang === "en" ? "CVM AI Assistant" : "CVM AI Ассистент"}
                </strong>
                <span>
                  {lang === "en"
                    ? "Demo UX · guided launch"
                    : "Demo UX · запуск с подсказками"}
                </span>
              </div>
            </div>
            <div className={`fw-demo-status${showCanvasUpdatedBadge ? " canvas-updated" : ""}`}>
              <span aria-hidden="true" />
              {showCanvasUpdatedBadge
                ? "Canvas updated"
                : hasErrors
                  ? lang === "en"
                    ? "Needs attention"
                    : "Нужно внимание"
                  : lang === "en"
                    ? "Ready"
                    : "Готов"}
            </div>
            {renderHeaderActions()}
          </div>
        )}

        {/* Content — all panels always mounted; hidden via display:none to preserve state */}
        <div className={`fw-body${uiMode === "demo" ? " fw-demo-body" : ""}`}>
          {uiMode === "demo" && (
            <>
              <div
                className="fw-demo-stepper"
                aria-label={
                  lang === "en" ? "Demo launch progress" : "Прогресс demo-запуска"
                }
              >
                {demoSteps.map((step, index) => (
                  <button
                    key={step.key}
                    type="button"
                    className={`fw-demo-step ${step.state}`}
                    onClick={() => setTab(step.tab)}
                    aria-current={step.state === "active" ? "step" : undefined}
                  >
                    <span className="fw-demo-step-index" aria-hidden="true">
                      {step.state === "completed" ? "✓" : index + 1}
                    </span>
                    <span className="fw-demo-step-copy">
                      <strong>{step.label}</strong>
                      <small>{step.statusText}</small>
                    </span>
                  </button>
                ))}
              </div>
              <nav
                className="fw-demo-nav"
                aria-label={
                  lang === "en" ? "Assistant sections" : "Разделы ассистента"
                }
              >
                {(
                  ["copilot", "segments", "builder", "monitoring"] as Tab[]
                ).map((tabId) => (
                  <button
                    key={tabId}
                    className={`fw-demo-nav-item${tab === tabId ? " active" : ""}`}
                    onClick={() => setTab(tabId)}
                  >
                    <span aria-hidden="true">{TAB_LABELS[tabId].icon}</span>
                    {TAB_LABELS[tabId].shortLabel}
                    {tabId === "monitoring" &&
                      hasMonitorData &&
                      tab !== "monitoring" && (
                        <span className="fw-demo-nav-badge" />
                      )}
                  </button>
                ))}
              </nav>
              <section className="fw-demo-scenario" aria-live="polite">
                <div>
                  <span>{activeScenario.eyebrow}</span>
                  <h2>{activeScenario.title}</h2>
                  <p>{activeScenario.text}</p>
                </div>
                <strong>{activeScenario.metric}</strong>
              </section>
              <DemoWorkingContext
                selectedSegment={selectedSegment}
                builderResponse={builderResponse}
                hasErrors={hasErrors}
                campaignStatus={campaignStatus}
                onSelectTab={setTab}
              />
            </>
          )}
          <div className="fw-panel-slot" style={activePanelStyle("copilot")}>
            <ChatPanel
              title="CVM Copilot"
              endpoint="/api/copilot"
              messageKey="question"
              placeholder={COPILOT_PLACEHOLDER[lang]}
              suggestions={uiMode === "demo" ? activePlaybook.map((item) => item.prompt ?? item.label) : []}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("segments")}>
            <SegmentPanel
              lang={lang}
              variant={uiMode}
              onSegmentSelected={setSelectedSegment}
              onUseInBuilder={() => setTab("builder")}
              demoPlaybook={DEMO_PLAYBOOK.segments[lang]}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("builder")}>
            <CampaignBuilderChat
              onResponse={handleBuilderResponse}
              onOpenMonitoring={() => setTab("monitoring")}
              lang={lang}
              selectedSegment={selectedSegment}
              variant={uiMode}
              demoPlaybook={DEMO_PLAYBOOK.builder[lang]}
            />
          </div>
          <div className="fw-panel-slot" style={activePanelStyle("monitoring")}>
            <MonitoringPanel
              campaignId={builderResponse?.campaign_id ?? null}
              draftFlowJson={monitorFlowJson}
              campaignStatus={campaignStatus}
              lang={lang}
              variant={uiMode}
              demoPlaybook={DEMO_PLAYBOOK.monitoring[lang]}
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
