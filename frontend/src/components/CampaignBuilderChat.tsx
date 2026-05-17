/**
 * CampaignBuilderChat — чат Campaign Builder без встроенного FlowCanvas.
 *
 * FlowCanvas теперь рендерится в фоновом AdTarget-макете через колбэк onResponse.
 * Это даёт эффект «AI собирает кампанию прямо в интерфейсе AdTarget».
 */

import { useState, useRef, useEffect, useCallback } from "react";
import type {
  AgentContext,
  BuilderPreferences,
  BuilderResponse,
  BuilderSession,
  BuilderSessionDetail,
  ChatMessage,
  SelectedSegmentForBuilder,
} from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const BUILDER_MESSAGES_KEY = "cvm.builder.messages.v1";
const BUILDER_RESPONSE_KEY = "cvm.builder.lastResponse.v1";
const BUILDER_PREFS_KEY = "cvm.builder.preferences.v1";
const BUILDER_SESSION_KEY = "cvm.builder.sessionId.v1";

const SUGGESTIONS: Record<"ru" | "en", string[]> = {
  ru: [
    "Запомни: продукт — тариф Family Max, цель — апсейл на семейную аудиторию",
    "Собери draft flow из введённых параметров",
    "Доработай текст: сделай тон более премиальным",
    "Добавь бизнес-транзакцию для активации оффера",
  ],
  en: [
    "Remember: product is Family Max, goal is family upsell",
    "Build draft flow from the parameters",
    "Refine the copy: make the tone more premium",
    "Add a business transaction for offer activation",
  ],
};

const STATUS_LABELS: Record<"ru" | "en", Record<string, string>> = {
  ru: {
    in_progress: "⏳ В процессе",
    created: "✅ Создана",
    started: "🚀 Запущена",
    error: "❌ Ошибка",
  },
  en: {
    in_progress: "⏳ In progress",
    created: "✅ Created",
    started: "🚀 Started",
    error: "❌ Error",
  },
};

const STATUS_COLORS: Record<string, string> = {
  in_progress: "#b7791f",
  created: "#5257ff",
  started: "#16a34a",
  error: "#dc2626",
};

interface BuilderDemoPlaybookItem {
  label: string;
  description?: string;
  prompt?: string;
}

interface Props {
  onResponse: (response: BuilderResponse | null) => void;
  onOpenMonitoring?: () => void;
  lang?: "ru" | "en";
  selectedSegment?: SelectedSegmentForBuilder | null;
  variant?: "classic" | "demo";
  demoPlaybook?: BuilderDemoPlaybookItem[];
}

function readStoredJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : fallback;
  } catch {
    return fallback;
  }
}

function readStoredString(key: string): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(key);
}

function hasPreferences(preferences: BuilderPreferences): boolean {
  return Object.values(preferences).some((value) => Boolean(value?.trim()));
}

function mergeResponsePreferences(
  current: BuilderPreferences,
  response: BuilderResponse,
): BuilderPreferences | null {
  if (response.builder_preferences) {
    return response.builder_preferences;
  }
  if (response.preference_patch) {
    return { ...current, ...response.preference_patch };
  }
  return null;
}

function stringifyCriteria(criteria: Record<string, unknown>): string[] {
  return Object.entries(criteria).map(([key, value]) => {
    if (Array.isArray(value)) return `${key}: ${value.join(", ")}`;
    if (value && typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
    return `${key}: ${String(value)}`;
  });
}

function getMatchedTargetGroupId(match: SelectedSegmentForBuilder["hypothesis"]["matched_target_group"]): string | null {
  if (!match) return null;
  const id = match.id ?? match.target_group_id;
  return id == null || id === "" ? null : String(id);
}

function formatSelectedSegmentTargetGroups(
  selectedSegment: SelectedSegmentForBuilder,
  lang: "ru" | "en",
): string {
  const { hypothesis } = selectedSegment;
  const match = hypothesis.matched_target_group;
  const matchId = getMatchedTargetGroupId(match);
  const criteria = stringifyCriteria(hypothesis.selection_criteria);
  const hasExistingTargetGroup = Boolean(match && hypothesis.is_existing_target_group);
  const isRecommendationOnly = Boolean(selectedSegment.recommendationOnly) || !hasExistingTargetGroup;

  const targetGroupLine = isRecommendationOnly
    ? (lang === "en"
      ? "Recommendation-only segment: no existing Target Group is attached or created yet."
      : "Сегмент-рекомендация: существующая Target Group не привязана и не создавалась.")
    : `Target Group: ${matchId ? `#${matchId} · ` : ""}${match?.name ?? hypothesis.name}`;

  const labels = lang === "en"
    ? {
      segment: "Segment",
      description: "Audience description",
      relevance: "Relevance",
      criteria: "Selection criteria",
      risk: "Risk / limitation",
    }
    : {
      segment: "Сегмент",
      description: "Описание аудитории",
      relevance: "Релевантность",
      criteria: "Критерии отбора",
      risk: "Риск / ограничение",
    };

  return [
    targetGroupLine,
    `${labels.segment}: ${hypothesis.name}`,
    hypothesis.audience_description ? `${labels.description}: ${hypothesis.audience_description}` : "",
    hypothesis.relevance_reason ? `${labels.relevance}: ${hypothesis.relevance_reason}` : "",
    criteria.length ? `${labels.criteria}: ${criteria.join("; ")}` : "",
    hypothesis.risk_or_limitation ? `${labels.risk}: ${hypothesis.risk_or_limitation}` : "",
  ].filter(Boolean).join("\n");
}

function preferencesFromSelectedSegment(
  selectedSegment: SelectedSegmentForBuilder,
  lang: "ru" | "en",
): Partial<BuilderPreferences> {
  return {
    ...(selectedSegment.product ? { product: selectedSegment.product } : {}),
    ...(selectedSegment.goal ? { goal: selectedSegment.goal } : {}),
    targetGroups: formatSelectedSegmentTargetGroups(selectedSegment, lang),
  };
}

function getSelectedSegmentMeta(selectedSegment: SelectedSegmentForBuilder, lang: "ru" | "en"): string {
  const product = getPlanValue(selectedSegment.product);
  const goal = getPlanValue(selectedSegment.goal);
  return lang === "en" ? `Product: ${product} · Goal: ${goal}` : `Продукт: ${product} · Цель: ${goal}`;
}


function getPlanValue(value?: string | null): string {
  const trimmed = value?.trim();
  return trimmed || "—";
}

function buildBuilderPrompt(preferences: BuilderPreferences, lang: "ru" | "en"): string {
  const fields = lang === "en"
    ? [
      ["Campaign goal", getPlanValue(preferences.goal)],
      ["Product", getPlanValue(preferences.product)],
      ["Audience", getPlanValue(preferences.targetGroups)],
      ["Channels", getPlanValue(preferences.channels)],
      ["Content constraints", getPlanValue(preferences.content)],
      ["Offer recommendations", getPlanValue(preferences.offerRecommendations)],
    ]
    : [
      ["Цель кампании", getPlanValue(preferences.goal)],
      ["Продукт", getPlanValue(preferences.product)],
      ["Аудитория", getPlanValue(preferences.targetGroups)],
      ["Каналы", getPlanValue(preferences.channels)],
      ["Контентные ограничения", getPlanValue(preferences.content)],
      ["Рекомендации по офферам", getPlanValue(preferences.offerRecommendations)],
    ];

  const intro = lang === "en"
    ? "Build a draft Campaign Builder flow using the plan below. Use existing Target Group details when provided and return a ready-to-review draft flow."
    : "Собери draft flow в Campaign Builder по плану ниже. Используй данные существующей Target Group, если они указаны, и верни готовый к проверке draft flow.";

  return [intro, "", ...fields.map(([label, value]) => `- ${label}: ${value}`)].join("\n");
}

function formatDate(value: string, lang: "ru" | "en"): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

type ResultPanelTone = "success" | "warning" | "pending";

function hasFlowIssues(response: BuilderResponse): boolean {
  if ((response.validation_errors?.length ?? 0) > 0) return true;
  return response.draft_flow?.activities?.some((activity) =>
    (activity.errors?.length ?? 0) > 0 || (activity.warnings?.length ?? 0) > 0
  ) ?? false;
}

function getResultPanelTone(response: BuilderResponse): ResultPanelTone {
  if (!response.draft_flow) return "pending";
  return hasFlowIssues(response) ? "warning" : "success";
}

function responseFromSession(session: BuilderSessionDetail): BuilderResponse | null {
  const lastAssistant = [...session.messages].reverse().find((message) => message.role === "assistant");
  const metadata = lastAssistant?.metadata ?? {};
  return {
    message: lastAssistant?.content ?? "",
    session_id: session.id,
    campaign_id: typeof metadata.campaign_id === "number" ? metadata.campaign_id : session.campaign_id ?? null,
    builder_preferences: metadata.builder_preferences as BuilderResponse["builder_preferences"] ?? null,
    preference_patch: metadata.preference_patch as BuilderResponse["preference_patch"] ?? null,
    draft_flow: metadata.draft_flow as BuilderResponse["draft_flow"] ?? null,
    validation_errors: Array.isArray(metadata.validation_errors) ? metadata.validation_errors : [],
    status: session.status as BuilderResponse["status"],
  };
}

export function CampaignBuilderChat({
  onResponse,
  onOpenMonitoring,
  lang = "ru",
  selectedSegment = null,
  variant = "classic",
  demoPlaybook = [],
}: Props) {
  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(() =>
    readStoredJson<BuilderResponse | null>(BUILDER_RESPONSE_KEY, null),
  );
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(() => readStoredString(BUILDER_SESSION_KEY));
  const [sessions, setSessions] = useState<BuilderSession[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [preferences, setPreferences] = useState<BuilderPreferences>(() =>
    readStoredJson<BuilderPreferences>(BUILDER_PREFS_KEY, {}),
  );
  const [targetGroupsSource, setTargetGroupsSource] = useState<"audience-builder" | "manual" | null>(null);

  const { messages, loading, error, send, clear, replaceMessages } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    storageKey: BUILDER_MESSAGES_KEY,
    extraPayload: () => ({
      session_id: currentSessionId,
      session_campaign_id: lastResponse?.campaign_id ?? null,
      session_flow_json: lastResponse?.draft_flow
        ? JSON.stringify(lastResponse.draft_flow)
        : null,
      builder_preferences: preferences,
    }),
  });

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const response = await fetch(`${API_BASE}/api/sessions`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setSessions(await response.json() as BuilderSession[]);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (lastResponse) {
      window.localStorage.setItem(BUILDER_RESPONSE_KEY, JSON.stringify(lastResponse));
    } else {
      window.localStorage.removeItem(BUILDER_RESPONSE_KEY);
    }
    onResponse(lastResponse);
  }, [lastResponse, onResponse]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (currentSessionId) {
      window.localStorage.setItem(BUILDER_SESSION_KEY, currentSessionId);
    } else {
      window.localStorage.removeItem(BUILDER_SESSION_KEY);
    }
  }, [currentSessionId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(BUILDER_PREFS_KEY, JSON.stringify(preferences));
  }, [preferences]);

  useEffect(() => {
    if (!selectedSegment) return;
    setPreferences((current) => ({
      ...current,
      ...preferencesFromSelectedSegment(selectedSegment, lang),
    }));
    setTargetGroupsSource("audience-builder");
  }, [selectedSegment, lang]);

  const handlePreferenceChange = (key: keyof BuilderPreferences, value: string) => {
    if (key === "targetGroups") {
      setTargetGroupsSource(value.trim() ? "manual" : null);
    }
    setPreferences((current) => ({ ...current, [key]: value }));
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const data = await send(text);
    if (data) {
      const builderResponse = data as BuilderResponse;
      setLastResponse(builderResponse);
      setPreferences((current) => mergeResponsePreferences(current, builderResponse) ?? current);
      setCurrentSessionId(builderResponse.session_id ?? currentSessionId);
      refreshSessions();
    }
  };

  const handleOpenSession = async (sessionId: string) => {
    setHistoryError(null);
    try {
      const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const session = await response.json() as BuilderSessionDetail;
      const loadedMessages: ChatMessage[] = session.messages
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) => ({ role: message.role as "user" | "assistant", content: message.content }));
      replaceMessages(loadedMessages);
      setCurrentSessionId(session.id);
      const loadedResponse = responseFromSession(session);
      setLastResponse(loadedResponse);
      if (loadedResponse) {
        setPreferences((current) => mergeResponsePreferences(current, loadedResponse) ?? current);
      }
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Failed to load session");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    clear();
    setCurrentSessionId(null);
    setLastResponse(null);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_RESPONSE_KEY);
      window.localStorage.removeItem(BUILDER_SESSION_KEY);
    }
  };

  const handleClearAll = () => {
    handleClear();
    setPreferences({});
    setTargetGroupsSource(null);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_PREFS_KEY);
    }
  };

  const handlePrepareBuilderCommand = () => {
    setInput(buildBuilderPrompt(preferences, lang));
  };

  const handleUseSelectedSegment = () => {
    if (!selectedSegment) return;
    const nextPreferences = {
      ...preferences,
      ...preferencesFromSelectedSegment(selectedSegment, lang),
    };
    setPreferences(nextPreferences);
    setTargetGroupsSource("audience-builder");
    setInput(buildBuilderPrompt(nextPreferences, lang));
  };

  const handleApplyDemoPlaybook = (item: BuilderDemoPlaybookItem) => {
    if (item.prompt) setInput(item.prompt);
  };

  const targetGroupsStatusLabel = variant === "demo" && targetGroupsSource
    ? targetGroupsSource === "audience-builder"
      ? "Applied from Audience Builder"
      : "Edited manually"
    : null;

  const resultPanelTone = lastResponse ? getResultPanelTone(lastResponse) : "pending";
  const resultPanelItems = lastResponse
    ? [
      { label: "status", value: STATUS_LABELS[lang][lastResponse.status] ?? lastResponse.status },
      { label: "campaign_id", value: lastResponse.campaign_id ? `#${lastResponse.campaign_id}` : "—" },
      { label: "activities", value: String(lastResponse.draft_flow?.activities?.length ?? 0) },
      { label: "validation errors", value: String(lastResponse.validation_errors?.length ?? 0) },
      { label: "preference_patch", value: lastResponse.preference_patch ? (lang === "en" ? "yes" : "есть") : "—" },
      { label: "draft_flow", value: lastResponse.draft_flow ? (lang === "en" ? "yes" : "есть") : "—" },
    ]
    : [];

  return (
    <div className="fw-builder-chat">
      <details className="builder-params-panel">
        <summary>
          {lang === "en" ? "Campaign parameters" : "Параметры для сборки"}
          {hasPreferences(preferences) && <span>{lang === "en" ? "filled" : "заполнено"}</span>}
        </summary>
        <div className="builder-params-grid">
          <label>
            {lang === "en" ? "Product / tariff" : "Продукт / тариф"}
            <input
              value={preferences.product ?? ""}
              onChange={(e) => handlePreferenceChange("product", e.target.value)}
              placeholder={lang === "en" ? "e.g. Family Max tariff" : "Напр. тариф Family Max"}
            />
          </label>
          <label>
            {lang === "en" ? "Campaign goal" : "Цель кампании"}
            <input
              value={preferences.goal ?? ""}
              onChange={(e) => handlePreferenceChange("goal", e.target.value)}
              placeholder={lang === "en" ? "upsell, retention, activation…" : "апсейл, удержание, активация…"}
            />
          </label>
          <label>
            {lang === "en" ? "Channels" : "Каналы"}
            <input
              value={preferences.channels ?? ""}
              onChange={(e) => handlePreferenceChange("channels", e.target.value)}
              placeholder={lang === "en" ? "SMS, Push, Email…" : "SMS, Push, Email…"}
            />
          </label>
          <label>
            {variant === "demo" ? (
              <span className="builder-field-label">
                {lang === "en" ? "Target groups" : "Целевые группы"}
                {targetGroupsStatusLabel && (
                  <em className={targetGroupsSource === "manual" ? "manual" : undefined}>
                    {targetGroupsStatusLabel}
                  </em>
                )}
              </span>
            ) : (
              lang === "en" ? "Target groups" : "Целевые группы"
            )}
            <input
              value={preferences.targetGroups ?? ""}
              onChange={(e) => handlePreferenceChange("targetGroups", e.target.value)}
              placeholder={lang === "en" ? "low ARPU, data users…" : "низкий ARPU, пользователи data…"}
            />
          </label>
          <label className="builder-params-wide">
            {lang === "en" ? "Content notes" : "Контент / тональность"}
            <textarea
              value={preferences.content ?? ""}
              onChange={(e) => handlePreferenceChange("content", e.target.value)}
              rows={2}
              placeholder={lang === "en" ? "message, tone, mandatory wording" : "текст, тональность, обязательные формулировки"}
            />
          </label>
          <label className="builder-params-wide">
            {lang === "en" ? "Offer recommendations" : "Рекомендации по офферам"}
            <textarea
              value={preferences.offerRecommendations ?? ""}
              onChange={(e) => handlePreferenceChange("offerRecommendations", e.target.value)}
              rows={2}
              placeholder={lang === "en" ? "discount, bundle, activation transaction…" : "скидка, пакет, транзакция активации…"}
            />
          </label>
        </div>
      </details>

      {variant === "demo" && lastResponse && (
        <section
          className={`builder-result-panel ${resultPanelTone}`}
          aria-label={lang === "en" ? "Builder result" : "Результат Builder"}
        >
          <div className="builder-result-panel-header">
            <div>
              <span>{lang === "en" ? "Last response" : "Последний ответ"}</span>
              <h3>{lang === "en" ? "Campaign assembly result" : "Результат сборки кампании"}</h3>
            </div>
            <strong>
              {resultPanelTone === "success"
                ? lang === "en" ? "Flow ready" : "Flow готов"
                : resultPanelTone === "warning"
                  ? lang === "en" ? "Review needed" : "Нужна проверка"
                  : lang === "en" ? "Collecting context" : "Сбор контекста"}
            </strong>
          </div>
          {lastResponse.draft_flow && (
            <div className="builder-canvas-hint" role="status">
              <span aria-hidden="true">✓</span>
              Canvas updated
            </div>
          )}
          <dl className="builder-result-panel-grid">
            {resultPanelItems.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          <div className="builder-result-panel-actions">
            <button type="button" className="secondary" onClick={() => onResponse(lastResponse)}>
              {lang === "en" ? "View flow" : "Посмотреть flow"}
              <span>{lang === "en" ? "Already mirrored in AdTarget canvas" : "Уже отображается в canvas AdTarget"}</span>
            </button>
            <button type="button" onClick={onOpenMonitoring} disabled={!onOpenMonitoring}>
              {lang === "en" ? "Go to Monitoring" : "Перейти к Monitoring"}
            </button>
          </div>
        </section>
      )}

      <details className="builder-history-panel">
        <summary>
          {lang === "en" ? "Dialog sessions" : "Диалоги Builder"}
          <span>{sessions.length}</span>
        </summary>
        {historyError && <p style={{ color: "var(--error)" }}>{historyError}</p>}
        {historyLoading && sessions.length === 0 ? (
          <p>{lang === "en" ? "Loading sessions…" : "Загружаем диалоги…"}</p>
        ) : sessions.length === 0 ? (
          <p>{lang === "en" ? "No backend sessions yet. Local messages are used as offline fallback." : "Пока нет backend-сессий. Локальные сообщения используются как offline fallback."}</p>
        ) : (
          <div className="builder-history-list">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => handleOpenSession(session.id)}
                title={session.title}
                className={session.id === currentSessionId ? "active" : undefined}
              >
                <strong>{session.title}</strong>
                <span>
                  Campaign {session.campaign_id ? `#${session.campaign_id}` : "—"} · {STATUS_LABELS[lang][session.status] ?? session.status} · {formatDate(session.updated_at, lang)}
                </span>
              </button>
            ))}
          </div>
        )}
      </details>

      {/* Message feed */}
      <div className="message-feed">
        {messages.length === 0 && !loading && (
          <div className="fw-empty-state">
            <div style={{ fontSize: 28, marginBottom: 8 }}>🤖</div>
            <strong style={{ color: "var(--text-primary)", fontSize: 14 }}>Campaign Builder</strong>
            <p style={{ margin: "6px 0 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {lang === "en"
                ? "Describe product, content and goal step-by-step — then ask the builder to assemble or refine a draft flow."
                : "Опишите продукт, контент и цель по шагам — затем попросите собрать или доработать draft flow."}
            </p>
            <div className="fw-suggestions-title">
              {lang === "en" ? "Multi-step examples" : "Примеры многошаговых команд"}
            </div>
            <div className="fw-suggestions-grid">
              {SUGGESTIONS[lang].map((s, i) => (
                <button
                  key={i}
                  className="fw-suggestion"
                  onClick={() => setInput(s)}
                  disabled={loading}
                  type="button"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message${msg.role === "user" ? " user" : ""}`}>
            {msg.role === "assistant" ? (
              <MarkdownText content={msg.content} />
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
        ))}

        {loading && (
          <div className="message">
            <div className="loading"><span /><span /><span /></div>
          </div>
        )}

        {error && (
          <div className="message" style={{ borderColor: "var(--error)", fontSize: 12, color: "var(--error)" }}>
            {error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Status bar */}
      {(lastResponse?.campaign_id || messages.length > 0 || hasPreferences(preferences)) && (
        <div className="fw-statusbar">
          {lastResponse?.campaign_id ? (
            <>
              <span>
                Campaign{" "}
                <code style={{ background: "#eef2ff", color: "#5257ff", padding: "1px 5px", borderRadius: 3, fontWeight: 700 }}>
                  #{lastResponse.campaign_id}
                </code>
              </span>
              <span style={{ color: STATUS_COLORS[lastResponse.status] ?? "inherit", fontWeight: 600, fontSize: 12 }}>
                {STATUS_LABELS[lang][lastResponse.status] ?? lastResponse.status}
              </span>
            </>
          ) : currentSessionId ? (
            <span>{lang === "en" ? "Backend dialog is loaded" : "Backend-диалог загружен"}</span>
          ) : (
            <span>{lang === "en" ? "Draft context is saved locally" : "Черновой контекст сохранён локально"}</span>
          )}
          <button className="fw-clear-btn" onClick={handleClear}>{lang === "en" ? "New chat" : "Новый чат"}</button>
          <button className="fw-clear-btn" onClick={handleClearAll}>{lang === "en" ? "Clear all" : "Очистить всё"}</button>
        </div>
      )}

      {selectedSegment && (
        <section
          className="builder-selected-segment-card"
          aria-label={lang === "en" ? "Selected segment for Builder" : "Выбранный сегмент для Builder"}
        >
          <div>
            <span>{lang === "en" ? "Segment from Audience Builder" : "Сегмент из Audience Builder"}</span>
            <strong>{selectedSegment.hypothesis.name}</strong>
            <small>{getSelectedSegmentMeta(selectedSegment, lang)}</small>
          </div>
          <button type="button" onClick={handleUseSelectedSegment} disabled={loading}>
            {lang === "en" ? "Build draft flow with this segment" : "Собрать draft flow с этим сегментом"}
          </button>
        </section>
      )}

      {/* Composer */}
      <div className="composer" style={{ borderTop: "1px solid var(--border)" }}>
        {variant === "demo" && (
          <button
            type="button"
            className="builder-quick-action"
            onClick={() => {
              const [preset] = demoPlaybook;
              if (preset) {
                handleApplyDemoPlaybook(preset);
              } else {
                handlePrepareBuilderCommand();
              }
            }}
            disabled={loading}
          >
            {demoPlaybook[0]?.label ?? (lang === "en" ? "Build draft flow" : "Собрать draft flow")}
          </button>
        )}
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={lang === "en" ? "Add context or ask to build/refine the draft flow…" : "Добавьте контекст или попросите собрать/доработать draft flow…"}
          rows={1}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>↑</button>
      </div>
    </div>
  );
}
