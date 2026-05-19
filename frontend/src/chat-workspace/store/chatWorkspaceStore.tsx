import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export interface SessionItem {
  id: string;
  title: string;
  status?: string;
  updated_at?: string;
  last_message_preview?: string;
  optimistic?: boolean;
}

export interface ChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
}

export interface ArtifactItem {
  id: string;
  type: string;
  title?: string | null;
  content?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
}

interface SessionDetailResponse {
  id: string;
  messages?: ChatEntry[];
  draft_flow?: Record<string, unknown> | null;
  campaign_brief?: Record<string, unknown> | null;
}

interface ChatWorkspaceState {
  sessions: SessionItem[];
  activeSessionId: string | null;
  messages: ChatEntry[];
  artifacts: ArtifactItem[];
  loadingSessions: boolean;
  loadingMessages: boolean;
  sending: boolean;
  error: string | null;
  selectSession: (sessionId: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  createNewChat: () => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
}

const ChatWorkspaceContext = createContext<ChatWorkspaceState | null>(null);

function makeLastPreview(messages: ChatEntry[] | undefined): string {
  if (!messages || messages.length === 0) return "";
  return messages[messages.length - 1].content.slice(0, 120);
}

export function ChatWorkspaceProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
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
      const nextSessions = (Array.isArray(data) ? data : (data.sessions ?? [])).map((item) => ({
        ...item,
        last_message_preview: item.last_message_preview ?? "",
      }));
      setSessions((prev) => {
        const optimistic = prev.filter((item) => item.optimistic && !nextSessions.some((x) => x.id === item.id));
        return [...optimistic, ...nextSessions];
      });
      setActiveSessionId((prev) => prev ?? nextSessions[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить список сессий");
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const selectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setLoadingMessages(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as SessionDetailResponse;
      const nextMessages = data.messages ?? [];
      setMessages(nextMessages);
      const nextArtifacts: ArtifactItem[] = [];
      if (data.draft_flow) nextArtifacts.push({ id: `${sessionId}-draft-flow`, type: "draft_flow", content: data.draft_flow });
      if (data.campaign_brief) nextArtifacts.push({ id: `${sessionId}-campaign-brief`, type: "campaign_brief", content: data.campaign_brief });
      setArtifacts(nextArtifacts);

      setSessions((prev) => prev.map((item) => (
        item.id === sessionId ? { ...item, last_message_preview: makeLastPreview(nextMessages), updated_at: nextMessages[nextMessages.length - 1]?.created_at ?? item.updated_at } : item
      )));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить сообщения");
      setMessages([]);
      setArtifacts([]);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  const createNewChat = useCallback(async () => {
    const optimisticId = `tmp-${Date.now()}`;
    const now = new Date().toISOString();
    const optimistic: SessionItem = { id: optimisticId, title: "New chat", updated_at: now, status: "collect_brief", last_message_preview: "", optimistic: true };
    setSessions((prev) => [optimistic, ...prev]);
    setActiveSessionId(optimisticId);
    setMessages([]);
    setArtifacts([]);

    try {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New chat" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const created = (await res.json()) as SessionItem;
      setSessions((prev) => prev.map((item) => (item.id === optimisticId ? { ...created, last_message_preview: "" } : item)));
      setActiveSessionId(created.id);
      await refreshSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать чат");
      setSessions((prev) => prev.filter((item) => item.id !== optimisticId));
      setActiveSessionId((prev) => (prev === optimisticId ? null : prev));
    }
  }, [refreshSessions]);

  const sendMessage = useCallback(async (content: string) => {
    if (!activeSessionId || !content.trim() || activeSessionId.startsWith("tmp-")) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: activeSessionId, message: content }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await selectSession(activeSessionId);
      await refreshSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отправить сообщение");
    } finally {
      setSending(false);
    }
  }, [activeSessionId, refreshSessions, selectSession]);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    if (activeSessionId && !activeSessionId.startsWith("tmp-")) {
      void selectSession(activeSessionId);
    }
  }, [activeSessionId, selectSession]);

  const value = useMemo(() => ({
    sessions,
    activeSessionId,
    messages,
    artifacts,
    loadingSessions,
    loadingMessages,
    sending,
    error,
    selectSession,
    refreshSessions,
    createNewChat,
    sendMessage,
  }), [sessions, activeSessionId, messages, artifacts, loadingSessions, loadingMessages, sending, error, selectSession, refreshSessions, createNewChat, sendMessage]);

  return <ChatWorkspaceContext.Provider value={value}>{children}</ChatWorkspaceContext.Provider>;
}

export function useChatWorkspaceStore() {
  const ctx = useContext(ChatWorkspaceContext);
  if (!ctx) {
    throw new Error("useChatWorkspaceStore must be used inside ChatWorkspaceProvider");
  }
  return ctx;
}
