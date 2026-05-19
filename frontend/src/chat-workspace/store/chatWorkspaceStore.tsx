import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getChat, listChats, sendAction as postAction, sendMessage as postMessage, type ChatActionRequestPayload, type ChatActionResponse, type ChatArtifact, type ChatMessage, type ChatSession, type ChatSessionContext } from "../../api/chatApi";

const PAGE_SIZE = 50;

export interface SessionItem extends ChatSession { optimistic?: boolean }
export interface ChatEntry extends ChatMessage { optimistic?: boolean; correlationId?: string }
export interface ArtifactItem extends ChatArtifact {}
type NetworkState = "idle" | "initial_loading" | "refreshing" | "hard_error";

interface ChatWorkspaceState {
  sessions: SessionItem[]; activeSessionId: string | null; messages: ChatEntry[]; artifacts: ArtifactItem[];
  loadingSessions: boolean; loadingMessages: boolean; sending: boolean; error: string | null;
  sessionsState: NetworkState; chatState: NetworkState; contextBySession: Record<string, ChatSessionContext>;
  setActiveSessionId: (sessionId: string | null) => void; setSessionContext: (sessionId: string, context: ChatSessionContext) => void;
  selectSession: (sessionId: string) => Promise<void>; refreshSessions: (background?: boolean) => Promise<void>;
  createNewChat: () => Promise<string>; sendMessage: (content: string) => Promise<void>;
  sendAction: (params: { message: string; action: ChatActionRequestPayload; artifactId?: string }) => Promise<ChatActionResponse>;
  loadOlderMessages: () => Promise<void>; hasMoreMessages: boolean; loadingOlderMessages: boolean;
  isOffline: boolean; retryFailedRequests: () => Promise<void>;
}

const ChatWorkspaceContext = createContext<ChatWorkspaceState | null>(null);
const chatMessagesKey = (sessionId: string) => ["chatMessages", sessionId] as const;

function dedupeMessages(messages: ChatEntry[]): ChatEntry[] {
  const byId = new Map<string, ChatEntry>();
  for (const m of messages) byId.set(m.id, m);
  return Array.from(byId.values()).sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""));
}

function mergeOptimisticMessages(current: ChatEntry[], server: ChatEntry[]): ChatEntry[] {
  const serverByCorrelation = new Map(server.filter((m) => m.correlationId).map((m) => [m.correlationId as string, m]));
  const unresolvedOptimistic = current.filter((m) => m.optimistic && !m.id.startsWith("tmp-") ? false : !m.correlationId || !serverByCorrelation.has(m.correlationId));
  return dedupeMessages([...server, ...unresolvedOptimistic]);
}

export function ChatWorkspaceProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionsState, setSessionsState] = useState<NetworkState>("idle");
  const [chatState, setChatState] = useState<NetworkState>("idle");
  const [contextBySession, setContextBySession] = useState<Record<string, ChatSessionContext>>({});
  const [isOffline, setIsOffline] = useState<boolean>(typeof navigator !== "undefined" ? !navigator.onLine : false);

  useEffect(() => {
    const raw = window.localStorage.getItem("chat-context-by-session");
    if (raw) try { const parsed = JSON.parse(raw); if (parsed && typeof parsed === "object") setContextBySession(parsed); } catch {}
  }, []);
  useEffect(() => { window.localStorage.setItem("chat-context-by-session", JSON.stringify(contextBySession)); }, [contextBySession]);
  const setSessionContext = useCallback((sessionId: string, context: ChatSessionContext) => setContextBySession((prev) => ({ ...prev, [sessionId]: context })), []);

  const sessionsQuery = useQuery({ queryKey: ["chatSessions"], queryFn: listChats, refetchInterval: 30_000, refetchIntervalInBackground: true, staleTime: 10_000 });

  const messagesQuery = useInfiniteQuery({
    queryKey: activeSessionId ? chatMessagesKey(activeSessionId) : ["chatMessages", "empty"],
    enabled: Boolean(activeSessionId && !activeSessionId.startsWith("tmp-")),
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const sid = activeSessionId as string;
      const detail = await getChat(sid);
      const all = dedupeMessages(detail.messages as ChatEntry[]);
      const start = Math.max(0, all.length - (pageParam + 1) * PAGE_SIZE);
      const end = all.length - pageParam * PAGE_SIZE;
      return { page: all.slice(start, end), all, artifacts: detail.artifacts, hasMore: start > 0 };
    },
    getNextPageParam: (lastPage, allPages) => (lastPage.hasMore ? allPages.length : undefined),
  });

  const messages = useMemo(() => dedupeMessages((messagesQuery.data?.pages ?? []).flatMap((p) => p.page)), [messagesQuery.data]);
  const artifacts = useMemo(() => messagesQuery.data?.pages[0]?.artifacts ?? [], [messagesQuery.data]);

  const refreshSessions = useCallback(async (background = false) => {
    setSessionsState(background ? "refreshing" : "initial_loading");
    try { await sessionsQuery.refetch(); setSessionsState("idle"); } catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить историю чатов"); setSessionsState("hard_error"); }
  }, [sessionsQuery]);

  const selectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId); setChatState("initial_loading");
    try { await queryClient.invalidateQueries({ queryKey: chatMessagesKey(sessionId) }); setChatState("idle"); } catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить чат"); setChatState("hard_error"); throw e; }
  }, [queryClient]);

  const sendMessageMutation = useMutation({ mutationFn: async (content: string) => {
    if (!activeSessionId) return; const correlationId = `corr-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const optimistic: ChatEntry = { id: `tmp-${correlationId}`, role: "user", content, createdAt: new Date().toISOString(), optimistic: true, correlationId };
    queryClient.setQueryData(chatMessagesKey(activeSessionId), (old: any) => {
      const first = old?.pages?.[0];
      if (!first) return { pages: [{ page: [optimistic], all: [optimistic], artifacts: [], hasMore: false }], pageParams: [0] };
      return { ...old, pages: [{ ...first, page: dedupeMessages([...first.page, optimistic]), all: dedupeMessages([...(first.all ?? []), optimistic]) }, ...old.pages.slice(1)] };
    });
    await postMessage(activeSessionId, content, contextBySession[activeSessionId]);
    const detail = await getChat(activeSessionId);
    queryClient.setQueryData(chatMessagesKey(activeSessionId), (old: any) => ({ ...old, pages: [{ page: mergeOptimisticMessages(old?.pages?.[0]?.page ?? [], detail.messages as ChatEntry[]), all: detail.messages, artifacts: detail.artifacts, hasMore: false }], pageParams: [0] }));
  }});

  const sendAction = useCallback(async ({ message, action, artifactId }: { message: string; action: ChatActionRequestPayload; artifactId?: string }) => {
    if (!activeSessionId || activeSessionId.startsWith("tmp-")) throw new Error("Сначала выберите сохранённую сессию");
    const response = await postAction(activeSessionId, message, action, artifactId, contextBySession[activeSessionId]);
    await queryClient.invalidateQueries({ queryKey: chatMessagesKey(activeSessionId) });
    await sessionsQuery.refetch();
    return response;
  }, [activeSessionId, contextBySession, queryClient, sessionsQuery]);

  useEffect(() => { void refreshSessions(true); }, [refreshSessions]);
  useEffect(() => {
    const onOnline = () => setIsOffline(false);
    const onOffline = () => setIsOffline(true);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  const value = useMemo(() => ({
    sessions: sessionsQuery.data ?? [], activeSessionId, messages, artifacts, error,
    loadingSessions: sessionsQuery.isLoading || sessionsQuery.isFetching, loadingMessages: messagesQuery.isLoading,
    sending: sendMessageMutation.isPending, sessionsState, chatState, contextBySession, setActiveSessionId, setSessionContext,
    selectSession, refreshSessions, createNewChat: async () => { const id = `tmp-${Date.now()}`; setActiveSessionId(id); return id; },
    sendMessage: async (content: string) => { if (!content.trim() || !activeSessionId || activeSessionId.startsWith("tmp-")) return; setError(null); await sendMessageMutation.mutateAsync(content); await sessionsQuery.refetch(); },
    sendAction,
    loadOlderMessages: async () => { await messagesQuery.fetchNextPage(); },
    hasMoreMessages: Boolean(messagesQuery.hasNextPage), loadingOlderMessages: messagesQuery.isFetchingNextPage,
    isOffline,
    retryFailedRequests: async () => {
      await refreshSessions();
      if (activeSessionId) await selectSession(activeSessionId);
    },
  }), [sessionsQuery.data, activeSessionId, messages, artifacts, error, sessionsQuery.isLoading, sessionsQuery.isFetching, messagesQuery.isLoading, sendMessageMutation.isPending, sessionsState, chatState, contextBySession, setSessionContext, selectSession, refreshSessions, sendAction, messagesQuery, isOffline]);

  return <ChatWorkspaceContext.Provider value={value}>{children}</ChatWorkspaceContext.Provider>;
}

export function useChatWorkspaceStore() { const ctx = useContext(ChatWorkspaceContext); if (!ctx) throw new Error("useChatWorkspaceStore must be used inside ChatWorkspaceProvider"); return ctx; }
