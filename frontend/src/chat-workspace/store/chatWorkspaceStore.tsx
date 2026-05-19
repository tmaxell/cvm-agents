import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export interface SessionItem {
  id: string;
  title: string;
  updated_at?: string;
}

export interface ChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
}

interface ChatWorkspaceState {
  sessions: SessionItem[];
  activeSessionId: string | null;
  messages: ChatEntry[];
  loadingSessions: boolean;
  loadingMessages: boolean;
  sending: boolean;
  error: string | null;
  selectSession: (sessionId: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
}

const ChatWorkspaceContext = createContext<ChatWorkspaceState | null>(null);

export function ChatWorkspaceProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    setLoadingSessions(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/sessions`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { sessions?: SessionItem[] } | SessionItem[];
      const nextSessions = Array.isArray(data) ? data : (data.sessions ?? []);
      setSessions(nextSessions);
      if (!activeSessionId && nextSessions.length > 0) {
        setActiveSessionId(nextSessions[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить список сессий");
    } finally {
      setLoadingSessions(false);
    }
  }, [activeSessionId]);

  const selectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setLoadingMessages(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/chat?session_id=${encodeURIComponent(sessionId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { messages?: ChatEntry[] };
      setMessages(data.messages ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить сообщения");
      setMessages([]);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  const sendMessage = useCallback(async (content: string) => {
    if (!activeSessionId || !content.trim()) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: activeSessionId, message: content }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { messages?: ChatEntry[]; answer?: string };
      if (Array.isArray(data.messages)) {
        setMessages(data.messages);
      } else {
        setMessages((prev) => [
          ...prev,
          { id: `u-${Date.now()}`, role: "user", content },
          { id: `a-${Date.now()}`, role: "assistant", content: data.answer ?? "" },
        ]);
      }
      await refreshSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отправить сообщение");
    } finally {
      setSending(false);
    }
  }, [activeSessionId, refreshSessions]);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    if (activeSessionId) {
      void selectSession(activeSessionId);
    }
  }, [activeSessionId, selectSession]);

  const value = useMemo(() => ({
    sessions,
    activeSessionId,
    messages,
    loadingSessions,
    loadingMessages,
    sending,
    error,
    selectSession,
    refreshSessions,
    sendMessage,
  }), [sessions, activeSessionId, messages, loadingSessions, loadingMessages, sending, error, selectSession, refreshSessions, sendMessage]);

  return <ChatWorkspaceContext.Provider value={value}>{children}</ChatWorkspaceContext.Provider>;
}

export function useChatWorkspaceStore() {
  const ctx = useContext(ChatWorkspaceContext);
  if (!ctx) {
    throw new Error("useChatWorkspaceStore must be used inside ChatWorkspaceProvider");
  }
  return ctx;
}
