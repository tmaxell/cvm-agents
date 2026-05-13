/**
 * CampaignBuilderChat — чат Campaign Builder без встроенного FlowCanvas.
 *
 * FlowCanvas теперь рендерится в фоновом AdTarget-макете через колбэк onResponse.
 * Это даёт эффект «AI собирает кампанию прямо в интерфейсе AdTarget».
 */

import { useState, useRef, useEffect } from "react";
import type { AgentContext, BuilderPreferences, BuilderResponse, ChatMessage } from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const BUILDER_DIALOGS_KEY = "cvm.builder.dialogs.v2";
const BUILDER_LEGACY_MESSAGES_KEY = "cvm.builder.messages.v1";
const BUILDER_LEGACY_RESPONSE_KEY = "cvm.builder.lastResponse.v1";
const BUILDER_LEGACY_PREFS_KEY = "cvm.builder.preferences.v1";

interface BuilderDialog {
  id: string;
  title: string;
  updatedAt: string;
  messages: ChatMessage[];
  lastResponse: BuilderResponse | null;
  preferences: BuilderPreferences;
}

const SUGGESTIONS: Record<"ru" | "en", string[]> = {
  ru: [
    "Создай SMS-кампанию по утилизации пакета данных",
    "Хочу Email-кампанию для абонентов с низким ARPU",
    "Нужна событийная Push-кампания на день рождения абонента",
    "Создай промо-кампанию с активацией скидочного пакета",
    "Добавь бизнес-транзакцию в конце текущего flow",
  ],
  en: [
    "Create an SMS campaign for data pack utilization",
    "Email campaign for low-ARPU subscribers",
    "Birthday event-triggered push campaign",
    "Promo campaign with discount package activation",
    "Add a business transaction at the end of the current flow",
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

interface Props {
  onResponse: (response: BuilderResponse | null) => void;
  lang?: "ru" | "en";
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

function newDialog(): BuilderDialog {
  const now = new Date().toISOString();
  return {
    id: `builder-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    title: "Новая сборка кампании",
    updatedAt: now,
    messages: [],
    lastResponse: null,
    preferences: {},
  };
}

function titleFromMessages(messages: ChatMessage[], fallback: string): string {
  const firstUserMessage = messages.find((message) => message.role === "user")?.content.trim();
  if (!firstUserMessage) return fallback;
  return firstUserMessage.length > 48 ? `${firstUserMessage.slice(0, 45)}…` : firstUserMessage;
}

function readStoredDialogs(): BuilderDialog[] {
  const dialogs = readStoredJson<BuilderDialog[]>(BUILDER_DIALOGS_KEY, []);
  if (dialogs.length > 0) return dialogs;

  const legacyMessages = readStoredJson<ChatMessage[]>(BUILDER_LEGACY_MESSAGES_KEY, []);
  const legacyResponse = readStoredJson<BuilderResponse | null>(BUILDER_LEGACY_RESPONSE_KEY, null);
  const legacyPreferences = readStoredJson<BuilderPreferences>(BUILDER_LEGACY_PREFS_KEY, {});
  if (legacyMessages.length > 0 || legacyResponse || hasPreferences(legacyPreferences)) {
    const migrated = newDialog();
    migrated.messages = legacyMessages;
    migrated.lastResponse = legacyResponse;
    migrated.preferences = legacyPreferences;
    migrated.title = titleFromMessages(legacyMessages, "Восстановленная сборка");
    return [migrated];
  }

  return [newDialog()];
}

function hasPreferences(preferences: BuilderPreferences): boolean {
  return Object.values(preferences).some((value) => Boolean(value?.trim()));
}

function formatDialogDate(value: string, lang: "ru" | "en"): string {
  try {
    return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function CampaignBuilderChat({ onResponse, lang = "ru" }: Props) {
  const [dialogs, setDialogs] = useState<BuilderDialog[]>(() => readStoredDialogs());
  const [activeDialogId, setActiveDialogId] = useState(() => dialogs[0]?.id ?? newDialog().id);
  const activeDialog = dialogs.find((dialog) => dialog.id === activeDialogId) ?? dialogs[0];

  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(activeDialog?.lastResponse ?? null);
  const [preferences, setPreferences] = useState<BuilderPreferences>(activeDialog?.preferences ?? {});

  const storageKey = activeDialog ? `cvm.builder.dialog.${activeDialog.id}.messages` : undefined;
  const { messages, loading, error, send, clear, replaceMessages } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    storageKey,
    extraPayload: () => ({
      session_campaign_id: lastResponse?.campaign_id ?? null,
      session_flow_json: lastResponse?.draft_flow
        ? JSON.stringify(lastResponse.draft_flow)
        : null,
      builder_preferences: preferences,
    }),
  });

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!activeDialog) return;
    setLastResponse(activeDialog.lastResponse ?? null);
    setPreferences(activeDialog.preferences ?? {});
    if (typeof window !== "undefined") {
      window.localStorage.setItem(storageKey ?? "", JSON.stringify(activeDialog.messages ?? []));
    }
    replaceMessages(activeDialog.messages ?? []);
    onResponse(activeDialog.lastResponse ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDialogId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!activeDialog) return;
    setDialogs((current) => current.map((dialog) => {
      if (dialog.id !== activeDialog.id) return dialog;
      return {
        ...dialog,
        messages,
        lastResponse,
        preferences,
        title: titleFromMessages(messages, dialog.title),
        updatedAt: new Date().toISOString(),
      };
    }));
    onResponse(lastResponse);
  }, [messages, lastResponse, preferences, activeDialog?.id, onResponse]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(BUILDER_DIALOGS_KEY, JSON.stringify(dialogs));
  }, [dialogs]);

  const handlePreferenceChange = (key: keyof BuilderPreferences, value: string) => {
    setPreferences((current) => ({ ...current, [key]: value }));
  };

  const handleNewDialog = () => {
    const dialog = newDialog();
    setDialogs((current) => [dialog, ...current]);
    setActiveDialogId(dialog.id);
    setInput("");
  };

  const handleSelectDialog = (dialog: BuilderDialog) => {
    if (dialog.id === activeDialogId) return;
    setActiveDialogId(dialog.id);
    setInput("");
  };

  const handleDeleteDialog = (dialogId: string) => {
    setDialogs((current) => {
      const next = current.filter((dialog) => dialog.id !== dialogId);
      const safeNext = next.length > 0 ? next : [newDialog()];
      if (dialogId === activeDialogId) {
        setActiveDialogId(safeNext[0].id);
      }
      return safeNext;
    });
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(`cvm.builder.dialog.${dialogId}.messages`);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const data = await send(text);
    if (data) {
      setLastResponse(data as BuilderResponse);
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
    setLastResponse(null);
  };

  const handleClearAll = () => {
    handleClear();
    setPreferences({});
  };

  return (
    <div className="fw-builder-chat">
      <details className="builder-history-panel">
        <summary>
          {lang === "en" ? "Build history" : "История сборок"}
          <span>{dialogs.length}</span>
        </summary>
        <div className="builder-history-actions">
          <button type="button" onClick={handleNewDialog}>
            {lang === "en" ? "+ New campaign" : "+ Новая сборка"}
          </button>
        </div>
        <div className="builder-history-list">
          {dialogs.map((dialog) => (
            <div
              key={dialog.id}
              className={`builder-history-item${dialog.id === activeDialogId ? " active" : ""}`}
            >
              <button
                type="button"
                onClick={() => handleSelectDialog(dialog)}
                title={dialog.title}
              >
                <strong>{dialog.lastResponse?.campaign_id ? `#${dialog.lastResponse.campaign_id}` : "Draft"}</strong>
                <span>{dialog.title}</span>
                <small>{formatDialogDate(dialog.updatedAt, lang)}</small>
              </button>
              <button
                className="builder-history-delete"
                type="button"
                onClick={() => handleDeleteDialog(dialog.id)}
                title={lang === "en" ? "Delete dialog" : "Удалить диалог"}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </details>

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
            {lang === "en" ? "Preferred channels" : "Желаемые каналы"}
            <input
              value={preferences.channels ?? ""}
              onChange={(e) => handlePreferenceChange("channels", e.target.value)}
              placeholder={lang === "en" ? "SMS, Email, Push" : "SMS, Email, Push"}
            />
          </label>
          <label>
            {lang === "en" ? "Target groups" : "Таргет-группы"}
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

      {/* Message feed */}
      <div className="message-feed">
        {messages.length === 0 && !loading && (
          <div className="fw-empty-state">
            <div style={{ fontSize: 28, marginBottom: 8 }}>🤖</div>
            <strong style={{ color: "var(--text-primary)", fontSize: 14 }}>Campaign Builder</strong>
            <p style={{ margin: "6px 0 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {lang === "en"
                ? "Choose a ready campaign or describe product, content and goal step-by-step — then refine the flow."
                : "Выберите готовый сценарий или опишите продукт, контент и цель по шагам — затем дорабатывайте flow."}
            </p>
            <div className="fw-suggestions-title">
              {lang === "en" ? "Ready campaigns and edits" : "Готовые кампании и доработки"}
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
          ) : (
            <span>{lang === "en" ? "Draft context is saved" : "Черновой контекст сохранён"}</span>
          )}
          <button className="fw-clear-btn" onClick={handleClear}>{lang === "en" ? "Clear chat" : "Очистить чат"}</button>
          <button className="fw-clear-btn" onClick={handleClearAll}>{lang === "en" ? "Clear all" : "Очистить всё"}</button>
        </div>
      )}

      {/* Composer */}
      <div className="composer" style={{ borderTop: "1px solid var(--border)" }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={lang === "en" ? "Add context or ask to build/refine the campaign…" : "Добавьте контекст или попросите собрать/доработать кампанию…"}
          rows={1}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>↑</button>
      </div>
    </div>
  );
}
