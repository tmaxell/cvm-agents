/**
 * CampaignBuilderChat — чат Campaign Builder без встроенного FlowCanvas.
 *
 * FlowCanvas теперь рендерится в фоновом AdTarget-макете через колбэк onResponse.
 * Это даёт эффект «AI собирает кампанию прямо в интерфейсе AdTarget».
 */

import { useState, useRef, useEffect } from "react";
import type { BuilderResponse, AgentContext } from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const SUGGESTIONS: Record<"ru" | "en", string[]> = {
  ru: [
    "SMS по утилизации пакета данных",
    "Email для абонентов с низким ARPU",
    "Push ко дню рождения абонента",
    "Промо с активацией скидочного пакета",
  ],
  en: [
    "SMS for data pack utilization",
    "Email for low-ARPU subscribers",
    "Birthday push campaign",
    "Promo with discount package activation",
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

export function CampaignBuilderChat({ onResponse, lang = "ru" }: Props) {
  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(null);

  const { messages, loading, error, send, clear } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    extraPayload: () => ({
      session_campaign_id: lastResponse?.campaign_id ?? null,
      session_flow_json: lastResponse?.draft_flow
        ? JSON.stringify(lastResponse.draft_flow)
        : null,
    }),
  });

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const data = await send(text);
    if (data) {
      const resp = data as BuilderResponse;
      setLastResponse(resp);
      onResponse(resp);
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
    onResponse(null);
  };

  return (
    <div className="fw-builder-chat">
      {/* Message feed */}
      <div className="message-feed">
        {messages.length === 0 && !loading && (
          <div className="fw-empty-state">
            <div style={{ fontSize: 28, marginBottom: 8 }}>🤖</div>
            <strong style={{ color: "var(--text-primary)", fontSize: 14 }}>Campaign Builder</strong>
            <p style={{ margin: "6px 0 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {lang === "en"
                ? "Describe your goal — the agent will build the campaign in AdTarget"
                : "Опишите цель — агент соберёт кампанию в AdTarget"}
            </p>
            <div className="fw-suggestions-title">
              {lang === "en" ? "Quick starts" : "Быстрые сценарии"}
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
      {lastResponse?.campaign_id && (
        <div className="fw-statusbar">
          <span>
            Campaign{" "}
            <code style={{ background: "#eef2ff", color: "#5257ff", padding: "1px 5px", borderRadius: 3, fontWeight: 700 }}>
              #{lastResponse.campaign_id}
            </code>
          </span>
          <span style={{ color: STATUS_COLORS[lastResponse.status] ?? "inherit", fontWeight: 600, fontSize: 12 }}>
            {STATUS_LABELS[lang][lastResponse.status] ?? lastResponse.status}
          </span>
          <button className="fw-clear-btn" onClick={handleClear}>{lang === "en" ? "Clear" : "Очистить"}</button>
        </div>
      )}

      {/* Composer */}
      <div className="composer" style={{ borderTop: "1px solid var(--border)" }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={lang === "en" ? "Create an SMS campaign for data pack utilization…" : "Создай SMS-кампанию по утилизации пакета данных…"}
          rows={1}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>↑</button>
      </div>
    </div>
  );
}
