/**
 * CampaignBuilderChat — чат Campaign Builder без встроенного FlowCanvas.
 *
 * FlowCanvas теперь рендерится в фоновом AdTarget-макете через колбэк onResponse.
 * Это даёт эффект «AI собирает кампанию прямо в интерфейсе AdTarget».
 */

import { useState, useRef, useEffect } from "react";
import type { BuilderPreferences, BuilderResponse, AgentContext } from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const BUILDER_MESSAGES_KEY = "cvm.builder.messages.v1";
const BUILDER_RESPONSE_KEY = "cvm.builder.lastResponse.v1";
const BUILDER_PREFS_KEY = "cvm.builder.preferences.v1";

const SUGGESTIONS: Record<"ru" | "en", string[]> = {
  ru: [
    "Запомни: продукт — тариф Family Max, цель — апсейл на семейную аудиторию",
    "Собери кампанию из введённых параметров",
    "Доработай текст: сделай тон более премиальным",
    "Добавь бизнес-транзакцию для активации оффера",
  ],
  en: [
    "Remember: product is Family Max, goal is family upsell",
    "Build a campaign from the parameters",
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

function hasPreferences(preferences: BuilderPreferences): boolean {
  return Object.values(preferences).some((value) => Boolean(value?.trim()));
}

export function CampaignBuilderChat({ onResponse, lang = "ru" }: Props) {
  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(() =>
    readStoredJson<BuilderResponse | null>(BUILDER_RESPONSE_KEY, null),
  );
  const [preferences, setPreferences] = useState<BuilderPreferences>(() =>
    readStoredJson<BuilderPreferences>(BUILDER_PREFS_KEY, {}),
  );

  const { messages, loading, error, send, clear } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    storageKey: BUILDER_MESSAGES_KEY,
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
    window.localStorage.setItem(BUILDER_PREFS_KEY, JSON.stringify(preferences));
  }, [preferences]);

  const handlePreferenceChange = (key: keyof BuilderPreferences, value: string) => {
    setPreferences((current) => ({ ...current, [key]: value }));
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
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_RESPONSE_KEY);
    }
  };

  const handleClearAll = () => {
    handleClear();
    setPreferences({});
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_PREFS_KEY);
    }
  };

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

      <details className="builder-history-panel">
        <summary>
          {lang === "en" ? "Dialog history" : "История диалога"}
          <span>{messages.length}</span>
        </summary>
        {messages.length === 0 ? (
          <p>{lang === "en" ? "No saved messages yet." : "Пока нет сохранённых сообщений."}</p>
        ) : (
          <div className="builder-history-list">
            {messages.map((message, index) => (
              <button
                key={`${message.role}-${index}`}
                type="button"
                onClick={() => setInput(message.content)}
                title={message.content}
              >
                <strong>{message.role === "user" ? (lang === "en" ? "You" : "Вы") : "AI"}</strong>
                <span>{message.content}</span>
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
                ? "Describe product, content and goal step-by-step — then ask the builder to assemble or refine the campaign."
                : "Опишите продукт, контент и цель по шагам — затем попросите собрать или доработать кампанию."}
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
