/**
 * CampaignBuilder — F2 Campaign Builder agent UI.
 *
 * Двухколоночный layout:
 *   - Слева: чат с агентом
 *   - Справа: FlowCanvas (визуализация текущего campaign flow)
 */

import { useState, useRef, useEffect } from "react";
import type { BuilderResponse, AgentContext, CampaignFlow } from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";
import { FlowCanvas } from "./FlowCanvas";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const SUGGESTIONS = [
  "Создай SMS-кампанию по утилизации пакета данных",
  "Хочу Email-кампанию для абонентов с низким ARPU",
  "Нужна событийная Push-кампания на день рождения абонента",
  "Создай промо-кампанию с активацией скидочного пакета",
];

const STATUS_LABELS: Record<string, string> = {
  collect_brief: "📝 Сбор brief",
  draft_ready: "✅ Draft готов",
  needs_review: "⚠️ Нужен review",
  created_in_adtarget: "📌 Создана в AdTarget",
  running: "🚀 Запущена",
  error: "❌ Ошибка",
  in_progress: "В процессе",
  created: "✅ Создана",
  started: "🚀 Запущена",
};

const STATUS_COLORS: Record<string, string> = {
  collect_brief: "#b7791f",
  draft_ready: "var(--accent)",
  needs_review: "#d97706",
  created_in_adtarget: "var(--accent)",
  running: "var(--success)",
  error: "var(--error)",
  in_progress: "#b7791f",
  created: "var(--accent)",
  started: "var(--success)",
};

export function CampaignBuilder() {
  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(null);

  const { messages, loading, error, send, clear } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    // Pass current session state so follow-up messages know about the active campaign
    extraPayload: () => ({
      session_campaign_id: lastResponse?.campaign_id ?? null,
      session_flow_json: lastResponse?.draft_flow
        ? JSON.stringify(lastResponse.draft_flow)
        : null,
      draft_flow_version: lastResponse?.draft_flow_version ?? null,
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
    if (data) setLastResponse(data as BuilderResponse);
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

  const currentFlow: CampaignFlow | null = lastResponse?.draft_flow ?? null;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "420px 1fr", gap: 0, height: "100%", minHeight: 0 }}>

      {/* ── Left: Chat Panel ── */}
      <div
        style={{
          display: "grid",
          gridTemplateRows: "1fr auto auto",
          minHeight: 0,
          borderRight: "1px solid var(--border)",
          background: "var(--surface-primary)",
        }}
      >
        {/* Message feed */}
        <div className="message-feed">
          {messages.length === 0 && !loading && (
            <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 14, marginTop: 32 }}>
              <div style={{ fontSize: 32, marginBottom: 10 }}>🤖</div>
              <strong style={{ color: "var(--text-primary)", fontSize: 15 }}>Campaign Builder</strong>
              <p style={{ margin: "8px 0 20px", fontSize: 13, lineHeight: 1.5 }}>
                Опишите цель кампании — агент соберёт её автоматически
              </p>
              <div className="suggestions-strip" style={{ justifyContent: "center" }}>
                {SUGGESTIONS.map((s, i) => (
                  <button key={i} onClick={() => setInput(s)} disabled={loading}>
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
            <div className="message" style={{ borderColor: "var(--error)", color: "var(--error)", fontSize: 13 }}>
              {error}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Clear button */}
        {messages.length > 0 && (
          <div style={{ padding: "6px 12px", borderTop: "1px solid var(--border)", background: "var(--surface-secondary)", display: "flex", alignItems: "center", gap: 10 }}>
            {lastResponse?.campaign_id && (
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                Campaign{" "}
                <code style={{ background: "#eef2ff", color: "var(--accent)", padding: "1px 6px", borderRadius: 4, fontWeight: 700 }}>
                  #{lastResponse.campaign_id}
                </code>{" "}
                <span style={{ color: STATUS_COLORS[lastResponse.status] ?? "inherit", fontWeight: 700 }}>
                  {STATUS_LABELS[lastResponse.status] ?? lastResponse.status}
                </span>
              </span>
            )}
            <button
              onClick={handleClear}
              style={{
                marginLeft: "auto", height: 26, padding: "0 10px",
                border: "1px solid var(--border)", borderRadius: 6,
                background: "var(--surface-primary)", color: "var(--text-secondary)",
                fontSize: 11,
              }}
            >
              Очистить
            </button>
          </div>
        )}

        {/* Composer */}
        <div className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Создай SMS-кампанию по утилизации пакета данных…"
            rows={1}
          />
          <button onClick={handleSend} disabled={loading || !input.trim()}>↑</button>
        </div>
      </div>

      {/* ── Right: Flow Canvas ── */}
      <div className="workspace-panel" style={{ gridTemplateRows: "42px 1fr" }}>
        {/* Canvas header */}
        <div className="workspace-tabs">
          <button className="active">
            📊 Flow
            {currentFlow && (
              <span className="workspace-tab-count">
                {currentFlow.activities?.length ?? 0}
              </span>
            )}
          </button>
          {lastResponse?.campaign_id && (
            <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)", padding: "0 8px" }}>
              ID: <strong style={{ color: "var(--accent)" }}>#{lastResponse.campaign_id}</strong>
            </span>
          )}
        </div>

        {/* Canvas body */}
        <div style={{ minHeight: 0, overflow: "hidden", position: "relative" }}>
          <FlowCanvas flow={currentFlow} />
        </div>
      </div>

    </div>
  );
}
