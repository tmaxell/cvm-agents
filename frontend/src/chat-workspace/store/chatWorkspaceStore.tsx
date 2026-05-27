import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChatApiError,
  createChat,
  getChat,
  listChats,
  sendChat,
  type ChatAction,
  type ChatArtifact,
  type ChatMessage,
  type ChatSession,
} from "../../api/chatApi";
import type { CampaignFlow } from "../../types/api";
import { normalizeUpsellFlow } from "../../components/flow/normalizeUpsellFlow";

export interface ChatEntry extends ChatMessage {
  optimistic?: boolean;
}

interface ChatWorkspaceState {
  sessions: ChatSession[];
  activeSessionId: string | null;
  messages: ChatEntry[];
  artifacts: ChatArtifact[];
  draftFlow: CampaignFlow | null;
  loadingSessions: boolean;
  loadingMessages: boolean;
  sending: boolean;
  error: string | null;
  selectSession: (id: string) => Promise<void>;
  createNewChat: () => Promise<string>;
  sendMessage: (content: string, action?: ChatAction) => Promise<void>;
  refreshSessions: () => Promise<void>;
}

const ChatWorkspaceContext = createContext<ChatWorkspaceState | null>(null);

function extractDraftFlow(artifacts: ChatArtifact[]): CampaignFlow | null {
  for (let i = artifacts.length - 1; i >= 0; i -= 1) {
    const a = artifacts[i];
    if ((a.type === "draft_flow" || a.type === "campaign_draft") && a.content && Array.isArray((a.content as { activities?: unknown }).activities)) {
      // Старые upsell-артефакты (до коммита 481f51c) лежат в БД линейной
      // цепочкой через nextActivityId — без cases/timeOutNext/subNodes.
      // Нормализатор распознаёт сигнатуру по типам активностей и
      // пересобирает их как DAG, чтобы рендерер корректно показал две ветки.
      const flow = normalizeUpsellFlow(a.content as unknown as CampaignFlow);
      // Безусловно выбрасываем ExcludeFromCampaignActivity из любого
      // draft_flow — на платформе это «удалить клиента из текущей кампании»
      // и для визуализации не нужен (включая случаи, когда сигнатура
      // upsell не совпала и normalizer вернул flow как есть).
      const activities = (flow.activities ?? []).filter(
        a => a.type !== "ExcludeFromCampaignActivity",
      );
      // Если предыдущая нода ссылалась на Exclude через nextActivityId —
      // переподключаем её на то, на что ссылался Exclude (или null).
      const removedIds = new Set(
        (flow.activities ?? [])
          .filter(a => a.type === "ExcludeFromCampaignActivity")
          .map(a => a.id),
      );
      if (removedIds.size > 0) {
        const nextOfRemoved = new Map<string, string | null>();
        for (const a of flow.activities ?? []) {
          if (removedIds.has(a.id)) nextOfRemoved.set(a.id, a.nextActivityId ?? null);
        }
        for (const a of activities) {
          if (a.nextActivityId && removedIds.has(a.nextActivityId)) {
            a.nextActivityId = nextOfRemoved.get(a.nextActivityId) ?? null;
          }
        }
      }
      return { ...flow, activities };
    }
  }
  return null;
}

function toError(err: unknown): string {
  if (err instanceof ChatApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Неизвестная ошибка";
}

export function ChatWorkspaceProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [artifacts, setArtifacts] = useState<ChatArtifact[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const userActedRef = useRef(false);

  const refreshSessions = useCallback(async (silent = false) => {
    setLoadingSessions(true);
    try {
      const list = await listChats();
      setSessions(list.sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
      setError(null);
    } catch (e) {
      // Не показываем "сервис недоступен" пока пользователь не начал действовать —
      // backend часто стартует чуть позже фронта.
      if (!silent && userActedRef.current) {
        setError(toError(e));
      }
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  // Initial load: silent retry до 5 раз с шагом 1.5с.
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tryLoad = async () => {
      attempts += 1;
      try {
        const list = await listChats();
        if (!cancelled) {
          setSessions(list.sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")));
          setError(null);
        }
      } catch (e) {
        if (cancelled) return;
        if (attempts < 5) {
          setTimeout(tryLoad, 1500);
        }
      }
    };
    void tryLoad();
    return () => { cancelled = true; };
  }, []);

  const didAutoSelectRef = useRef(false);

  const selectSession = useCallback(async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setLoadingMessages(true);
    setError(null);
    try {
      const detail = await getChat(sessionId);
      setMessages(detail.messages.map((m) => ({ ...m })));
      setArtifacts(detail.artifacts);
    } catch (e) {
      setError(toError(e));
      setMessages([]);
      setArtifacts([]);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  // Авто-выбор самой свежей сессии на старте. Раньше история подтягивалась
  // только после первого отправленного сообщения — при перезаходе на страницу
  // холст и переписка были пустыми до клика. Делаем один раз через ref,
  // чтобы автоселект не «прыгал» обратно после createNewChat / refreshSessions.
  useEffect(() => {
    if (didAutoSelectRef.current) return;
    if (sessions.length === 0) return;
    if (activeSessionId) { didAutoSelectRef.current = true; return; }
    didAutoSelectRef.current = true;
    void selectSession(sessions[0].id);
  }, [sessions, activeSessionId, selectSession]);

  const createNewChat = useCallback(async () => {
    setError(null);
    try {
      const session = await createChat();
      setSessions((prev) => [session, ...prev.filter((s) => s.id !== session.id)]);
      setActiveSessionId(session.id);
      setMessages([]);
      setArtifacts([]);
      return session.id;
    } catch (e) {
      setError(toError(e));
      throw e;
    }
  }, []);

  const sendMessage = useCallback(
    async (content: string, action?: ChatAction) => {
      if (!content.trim() && !action) return;
      let sessionId = activeSessionId;
      if (!sessionId) {
        sessionId = await createNewChat();
      }
      setError(null);
      setSending(true);
      userActedRef.current = true;

      const userTmpId = `tmp-${Date.now()}`;
      const userMsg: ChatEntry = {
        id: userTmpId,
        role: "user",
        content,
        createdAt: new Date().toISOString(),
        optimistic: true,
      };
      setMessages((prev) => [...prev, userMsg]);

      try {
        const response = await sendChat(sessionId, content, action);
        const assistantMsg: ChatEntry = {
          id: `srv-${Date.now()}`,
          role: "assistant",
          content: response.assistant_message,
          createdAt: new Date().toISOString(),
          trace: response.trace,
          actions: response.actions_available,
        };
        setMessages((prev) => [...prev.filter((m) => m.id !== userTmpId), { ...userMsg, optimistic: false }, assistantMsg]);
        if (response.artifacts.length > 0) {
          setArtifacts((prev) => {
            const map = new Map<string, ChatArtifact>();
            for (const a of prev) map.set(a.id, a);
            for (const a of response.artifacts) map.set(a.id, a);
            return Array.from(map.values());
          });
        }
        // Подтягиваем canonical state с сервера — citations/trace в metadata теперь там.
        try {
          const detail = await getChat(sessionId);
          setMessages(detail.messages.map((m) => ({ ...m })));
          setArtifacts(detail.artifacts);
        } catch {
          // если перезагрузка не удалась — оставляем оптимистичный state
        }
        void refreshSessions(true);
      } catch (e) {
        setError(toError(e));
        setMessages((prev) => prev.filter((m) => m.id !== userTmpId));
      } finally {
        setSending(false);
      }
    },
    [activeSessionId, createNewChat, refreshSessions],
  );

  const draftFlow = useMemo(() => extractDraftFlow(artifacts), [artifacts]);

  const value = useMemo<ChatWorkspaceState>(
    () => ({
      sessions,
      activeSessionId,
      messages,
      artifacts,
      draftFlow,
      loadingSessions,
      loadingMessages,
      sending,
      error,
      selectSession,
      createNewChat,
      sendMessage,
      refreshSessions,
    }),
    [
      sessions,
      activeSessionId,
      messages,
      artifacts,
      draftFlow,
      loadingSessions,
      loadingMessages,
      sending,
      error,
      selectSession,
      createNewChat,
      sendMessage,
      refreshSessions,
    ],
  );

  return <ChatWorkspaceContext.Provider value={value}>{children}</ChatWorkspaceContext.Provider>;
}

export function useChatWorkspaceStore() {
  const ctx = useContext(ChatWorkspaceContext);
  if (!ctx) throw new Error("useChatWorkspaceStore must be used inside ChatWorkspaceProvider");
  return ctx;
}
