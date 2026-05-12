import { useState, useCallback } from "react";
import type { ChatMessage, AgentContext, SourceCitation } from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

interface UseChatOptions {
  endpoint: "/api/copilot" | "/api/builder";
  messageKey: "question" | "goal";
  context?: AgentContext;
  /** Extra fields merged into each API request body (e.g. session_campaign_id) */
  extraPayload?: () => Record<string, unknown>;
}

export function useChat({ endpoint, messageKey, context, extraPayload }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    async (userInput: string) => {
      if (!userInput.trim()) return;

      const userMessage: ChatMessage = { role: "user", content: userInput };
      setMessages((prev) => [...prev, userMessage]);
      setLoading(true);
      setError(null);

      try {
        // История для API — только role+content, без citations
        const historyForApi = messages.map(({ role, content }) => ({ role, content }));

        const response = await fetch(`${API_BASE}${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            [messageKey]: userInput,
            context: context ?? {},
            history: historyForApi,
            ...(extraPayload ? extraPayload() : {}),
          }),
        });

        if (!response.ok) {
          const errText = await response.text();
          // Try to parse JSON error detail
          try {
            const errJson = JSON.parse(errText);
            throw new Error(errJson.detail ?? `HTTP ${response.status}`);
          } catch {
            throw new Error(`HTTP ${response.status}: ${errText.slice(0, 200)}`);
          }
        }

        const data = await response.json();
        const assistantText: string = data.answer ?? data.message ?? "";
        const citations: SourceCitation[] = data.citations ?? [];

        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: assistantText, citations },
        ]);
        return data;
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Неизвестная ошибка";
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [endpoint, messageKey, context, extraPayload, messages]
  );

  const clear = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return { messages, loading, error, send, clear };
}
