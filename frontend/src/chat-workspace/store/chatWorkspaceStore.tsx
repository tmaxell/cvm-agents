import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getChat, listChats, sendMessage as postMessage, type ChatArtifact, type ChatMessage, type ChatSession } from "../../api/chatApi";

export interface SessionItem extends ChatSession {
  optimistic?: boolean;
}

export interface ChatEntry extends ChatMessage {}
export interface ArtifactItem extends ChatArtifact {}

type NetworkState = "idle" | "initial_loading" | "refreshing" | "hard_error";

interface ChatWorkspaceState {
  sessions: SessionItem[];
  activeSessionId: string | null;
  messages: ChatEntry[];
  artifacts: ArtifactItem[];
  loadingSessions: boolean;
  loadingMessages: boolean;
  sending: boolean;
  error: string | null;
  sessionsState: NetworkState;
  chatState: NetworkState;
  selectSession: (sessionId: string) => Promise<void>;
  refreshSessions: (background?: boolean) => Promise<void>;
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
  const [sessionsState, setSessionsState] = useState<NetworkState>("idle");
  const [chatState, setChatState] = useState<NetworkState>("idle");

  const refreshSessions = useCallback(async (background = false) => {
    setLoadingSessions(true);
    setError(null);
    setSessionsState((prev) => (background || prev !== "idle" ? "refreshing" : "initial_loading"));
    try {
      const nextSessions = await listChats();
      setSessions((prev) => {
        const optimistic = prev.filter((item) => item.optimistic && !nextSessions.some((x) => x.id === item.id));
        return [...optimistic, ...nextSessions];
      });
      setActiveSessionId((prev) => prev ?? nextSessions[0]?.id ?? null);
      setSessionsState("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить историю чатов");
      setSessionsState("hard_error");
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const selectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setLoadingMessages(true);
    setError(null);
    setChatState((prev) => (prev === "idle" ? "initial_loading" : "refreshing"));
    try {
      const detail = await getChat(sessionId);
      const nextMessages = detail.messages;
      setMessages(nextMessages);
      setArtifacts(detail.artifacts);

      setSessions((prev) => prev.map((item) => (
        item.id === sessionId ? { ...item, lastMessagePreview: makeLastPreview(nextMessages), updatedAt: nextMessages[nextMessages.length - 1]?.createdAt ?? item.updatedAt } : item
      )));
      setChatState("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить чат");
      setMessages([]);
      setArtifacts([]);
      setChatState("hard_error");
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  const createNewChat = useCallback(async () => {
    const optimisticId = `tmp-${Date.now()}`;
    const now = new Date().toISOString();
    const optimistic: SessionItem = { id: optimisticId, title: "New chat", updatedAt: now, status: "collect_brief", lastMessagePreview: "", optimistic: true };
    setSessions((prev) => [optimistic, ...prev]);
    setActiveSessionId(optimisticId);
    setMessages([]);
    setArtifacts([]);
  }, []);

  const sendMessage = useCallback(async (content: string) => {
    if (!activeSessionId || !content.trim() || activeSessionId.startsWith("tmp-")) return;
    setSending(true);
    setError(null);
    try {
      await postMessage(activeSessionId, content);
      await selectSession(activeSessionId);
      await refreshSessions(true);
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
    sessionsState,
    chatState,
    selectSession,
    refreshSessions,
    createNewChat,
    sendMessage,
  }), [sessions, activeSessionId, messages, artifacts, loadingSessions, loadingMessages, sending, error, sessionsState, chatState, selectSession, refreshSessions, createNewChat, sendMessage]);

  return <ChatWorkspaceContext.Provider value={value}>{children}</ChatWorkspaceContext.Provider>;
}

export function useChatWorkspaceStore() {
  const ctx = useContext(ChatWorkspaceContext);
  if (!ctx) {
    throw new Error("useChatWorkspaceStore must be used inside ChatWorkspaceProvider");
  }
  return ctx;
}
