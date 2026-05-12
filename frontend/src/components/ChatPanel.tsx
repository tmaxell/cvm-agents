/**
 * ChatPanel — универсальная панель чата для F1 и F2 агентов.
 *
 * Props:
 *   title      — заголовок панели
 *   endpoint   — "/api/copilot" или "/api/builder"
 *   messageKey — "question" или "goal"
 *   context    — AgentContext (screen, campaign_id, …)
 *   placeholder — подсказка в поле ввода
 *   suggestions — быстрые подсказки-кнопки
 */

import { useState, useRef, useEffect } from "react";
import type { AgentContext } from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";
import { Sources } from "./Sources";

interface ChatPanelProps {
  title: string;
  endpoint: "/api/copilot" | "/api/builder";
  messageKey: "question" | "goal";
  context?: AgentContext;
  placeholder?: string;
  suggestions?: string[];
}

export function ChatPanel({
  title,
  endpoint,
  messageKey,
  context,
  placeholder = "Введите сообщение…",
  suggestions = [],
}: ChatPanelProps) {
  const { messages, loading, error, send } = useChat({
    endpoint,
    messageKey,
    context,
  });
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    await send(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSuggestion = async (text: string) => {
    if (loading) return;
    await send(text);
  };

  const showSuggestions = suggestions.length > 0 && messages.length === 0;

  return (
    <div className="chat-panel-main" style={{ height: "100%", display: "grid", gridTemplateRows: "1fr auto auto" }}>
      {/* Message feed */}
      <div className="message-feed">
        {messages.length === 0 && !loading && (
          <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 14, marginTop: 40 }}>
            <div style={{ fontSize: 28, marginBottom: 12 }}>💬</div>
            <strong style={{ color: "var(--text-primary)" }}>{title}</strong>
            <p style={{ margin: "8px 0 0", fontSize: 13 }}>Начните диалог ниже</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`message${msg.role === "user" ? " user" : ""}`}
          >
            {msg.role === "assistant" ? (
              <>
                <MarkdownText content={msg.content} />
                {msg.citations && msg.citations.length > 0 && (
                  <Sources citations={msg.citations} />
                )}
              </>
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
        ))}

        {loading && (
          <div className="message">
            <div className="loading">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}

        {error && (
          <div className="message" style={{ borderColor: "var(--error)", color: "var(--error)", fontSize: 13 }}>
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Suggestions strip */}
      {showSuggestions && (
        <div style={{ padding: "0 16px 8px" }}>
          <div className="suggestions-strip">
            {suggestions.map((s, i) => (
              <button key={i} onClick={() => handleSuggestion(s)} disabled={loading}>
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Composer */}
      <div className="composer">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>
          ↑
        </button>
      </div>
    </div>
  );
}
