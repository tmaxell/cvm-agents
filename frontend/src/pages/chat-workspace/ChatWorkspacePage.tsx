import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate, useParams } from "react-router-dom";
import { ChatWorkspaceProvider, useChatWorkspaceStore, type SessionItem } from "../../chat-workspace/store/chatWorkspaceStore";
import { ApiError, type ChatMessage } from "../../api/chatApi";
import { MarkdownText } from "../../components/MarkdownText";
import type { ChatSessionContext } from "../../api/chatApi";
import { AppErrorBoundary } from "../../components/AppErrorBoundary";

function isSameDay(left: Date, right: Date): boolean {
  return left.getFullYear() === right.getFullYear() && left.getMonth() === right.getMonth() && left.getDate() === right.getDate();
}

function groupByUpdatedAt(sessions: SessionItem[]) {
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);

  const grouped = {
    "Сегодня": [] as SessionItem[],
    "Вчера": [] as SessionItem[],
    "Ранее": [] as SessionItem[],
  };

  sessions.forEach((session) => {
    const date = session.updatedAt ? new Date(session.updatedAt) : null;
    if (!date || Number.isNaN(date.getTime())) {
      grouped["Ранее"].push(session);
      return;
    }
    if (isSameDay(date, now)) grouped["Сегодня"].push(session);
    else if (isSameDay(date, yesterday)) grouped["Вчера"].push(session);
    else grouped["Ранее"].push(session);
  });

  return grouped;
}

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function parseStructured(content: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(content);
    return typeof parsed === "object" && parsed !== null ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

type ActionStatus = "idle" | "pending" | "success" | "error";

function ChatActionCard({
  id,
  title,
  explanation,
  onExecute,
}: {
  id: string;
  title: string;
  explanation: string;
  onExecute: (actionId: "save_campaign" | "save_segment") => Promise<{ artifactId: string | null; nextActions: string[] }>;
}) {
  const [status, setStatus] = useState<ActionStatus>("idle");
  const [result, setResult] = useState<{ artifactId: string | null; nextActions: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isSupported = id === "save_campaign" || id === "save_segment";

  const handleClick = async () => {
    if (!isSupported || status === "pending") return;
    setStatus("pending");
    setError(null);
    try {
      const next = await onExecute(id);
      setResult(next);
      setStatus("success");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : "Не удалось выполнить действие");
    }
  };

  return <section className="action-card chat-action-card">
    <strong>{title}</strong>
    <MarkdownText content={explanation} />
    {isSupported && <div className="chat-action-controls">
      <button disabled={status === "pending"} onClick={() => void handleClick()}>
        {status === "pending" ? "Сохранение…" : "Сохранить"}
      </button>
      {status === "pending" && <span>Выполняется…</span>}
      {status === "error" && <span className="chat-action-error">{error}</span>}
    </div>}
    {status === "success" && result && <div className="chat-action-success">
      <div>✅ Сохранено. artifact_id: <code>{result.artifactId ?? "—"}</code></div>
      {result.nextActions.length > 0 && <div>Доступные next actions: {result.nextActions.join(", ")}</div>}
    </div>}
  </section>;
}

function MessageThread({ messages, onExecuteAction, onLoadOlder, hasMore, loadingOlder }: { messages: ChatMessage[]; onExecuteAction: (actionId: "save_campaign" | "save_segment", messageId: string) => Promise<{ artifactId: string | null; nextActions: string[] }>; onLoadOlder: () => Promise<void>; hasMore: boolean; loadingOlder: boolean; }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({ count: messages.length, getScrollElement: () => containerRef.current, estimateSize: () => 180, overscan: 6 });

  return (<div className="chat-messages" ref={containerRef}>
    {hasMore && <button disabled={loadingOlder} onClick={() => void onLoadOlder()}>{loadingOlder ? "Загрузка…" : "Загрузить предыдущие сообщения"}</button>}
    <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, width: "100%", position: "relative" }}>
      {rowVirtualizer.getVirtualItems().map((virtualItem) => {
        const m = messages[virtualItem.index];
        const structured = parseStructured(m.content);
        const messageType = typeof structured?.type === "string" ? structured.type : null;
        const explanation = typeof structured?.explanation === "string" ? structured.explanation : m.content;
        return <article key={m.id} className={`bubble ${m.role}`} style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${virtualItem.start}px)` }}>
          <header className="bubble-meta">{m.role} · {formatDateTime(m.createdAt)}</header>
          {messageType === "action_card" ? <ChatActionCard id={typeof structured?.action_id === "string" ? structured.action_id : ""} title={typeof structured?.title === "string" ? structured.title : "Action"} explanation={explanation} onExecute={(actionId) => onExecuteAction(actionId, m.id)} /> : <MarkdownText content={explanation} />}
        </article>;
      })}
    </div>
  </div>);
}

function ChatListSidebar() {
  const { sessions, activeSessionId, createNewChat, loadingSessions, sessionsState } = useChatWorkspaceStore();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...sessions].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));
    if (!q) return sorted;
    return sorted.filter((session) => `${session.title} ${session.lastMessagePreview ?? ""}`.toLowerCase().includes(q));
  }, [query, sessions]);

  const grouped = useMemo(() => groupByUpdatedAt(filtered), [filtered]);

  return (
    <aside className="chat-left-panel">
      <div className="chat-left-panel-header">
        <h3>Чаты</h3>
        <button onClick={async () => navigate(`/chat/${await createNewChat()}`)}>New chat</button>
      </div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по title и last message" />
      {loadingSessions && <div>{sessionsState === "refreshing" ? "Обновление списка…" : "Загрузка списка…"}</div>}
      {loadingSessions && sessions.length === 0 && Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="chat-skeleton-row">Загрузка…</div>
      ))}
      {!loadingSessions && sessions.length === 0 && <div className="chat-empty-group">История чатов пуста</div>}
      {Object.entries(grouped).map(([label, items]) => (
        <section key={label}>
          <h4>{label}</h4>
          {items.length === 0 ? <div className="chat-empty-group">Нет чатов</div> : items.map((s) => (
            <button key={s.id} className={s.id === activeSessionId ? "active" : ""} onClick={() => navigate(`/chat/${s.id}`)}>
              <div>{s.title}</div>
              <div>{s.lastMessagePreview || "(нет сообщений)"}</div>
              <div>status: {s.status ?? "—"}</div>
              <div>updated: {formatDateTime(s.updatedAt)}</div>
            </button>
          ))}
        </section>
      ))}
    </aside>
  );
}

function WorkspaceBody() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const [sessionMissing, setSessionMissing] = useState(false);
  const { sessions, activeSessionId, setActiveSessionId, messages, artifacts, sendMessage, sendAction, sending, error, loadingMessages, refreshSessions, selectSession, chatState, contextBySession, setSessionContext, createNewChat, loadOlderMessages, hasMoreMessages, loadingOlderMessages, isOffline, retryFailedRequests } = useChatWorkspaceStore();
  const defaultContext: ChatSessionContext = { mode: "general_analysis", campaign_id: null, segment_id: null };
  const activeContext: ChatSessionContext = activeSessionId ? (contextBySession[activeSessionId] ?? defaultContext) : defaultContext;
  const [contextWarning, setContextWarning] = useState<string | null>(null);
  useEffect(() => {
    if (!sessionId) {
      setActiveSessionId(null);
      setSessionMissing(false);
      return;
    }
    setActiveSessionId(sessionId);
    window.localStorage.setItem("last-active-chat-session-id", sessionId);
    if (sessionId.startsWith("tmp-")) {
      setSessionMissing(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        await selectSession(sessionId);
        if (!cancelled) setSessionMissing(false);
      } catch (e) {
        if (!cancelled) setSessionMissing(e instanceof ApiError && e.status === 404);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, selectSession, setActiveSessionId]);

  useEffect(() => {
    if (!sessionId && sessions.length > 0) {
      const last = window.localStorage.getItem("last-active-chat-session-id");
      const target = (last && sessions.some((s) => s.id === last)) ? last : sessions[0]?.id;
      if (target) navigate(`/chat/${target}`, { replace: true });
    }
  }, [navigate, sessionId, sessions]);

  useEffect(() => {
    if (!activeSessionId) return;
    const invalidCampaignId = activeContext.campaign_id !== null && activeContext.campaign_id !== undefined && activeContext.campaign_id <= 0;
    if (invalidCampaignId) {
      setContextWarning("Контекст кампании сброшен: выбранная кампания недоступна.");
      setSessionContext(activeSessionId, { ...activeContext, campaign_id: null });
    }
  }, [activeContext, activeSessionId, setSessionContext]);

  const executeAction = async (actionId: "save_campaign" | "save_segment", messageId: string) => {
    const response = await sendAction({
      message: `Execute action ${actionId} from message ${messageId}`,
      action: { id: actionId, label: actionId === "save_campaign" ? "Сохранить кампанию" : "Сохранить сегмент", kind: "default", payload: {} },
    });
    const nextActions = (response.actions_available ?? []).map((item) => item.label).filter((x): x is string => Boolean(x));
    return { artifactId: response.artifacts?.[0]?.id ?? null, nextActions };
  };

  const retry = async () => {
    await refreshSessions();
    if (activeSessionId) await selectSession(activeSessionId);
  };

  if (sessionMissing) {
    return <div className="chat-workspace-layout"><main className="chat-center-panel">
      <h2>Not Found</h2>
      <p>Чат с id <code>{sessionId}</code> не найден.</p>
      <button onClick={async () => navigate(`/chat/${await createNewChat()}`)}>Создать новый чат</button>
    </main></div>;
  }

  return (
    <div className="chat-workspace-layout">
      <ChatListSidebar />
      <main className="chat-center-panel">
        {isOffline && <div className="chat-error">Проблемы с соединением. <button onClick={() => void retryFailedRequests()}>Повторить</button></div>}
        <div className="chat-context-header">
          <strong>Контекст:</strong> кампания {activeContext.campaign_id ?? "—"} / сегмент {activeContext.segment_id ?? "—"} / режим {activeContext.mode ?? "general_analysis"}
        </div>
        <div className="chat-context-switcher" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select
            value={activeContext.mode ?? "general_analysis"}
            onChange={(e) => activeSessionId && setSessionContext(activeSessionId, { ...activeContext, mode: e.target.value as "general_analysis" | "builder" | "monitoring" })}
          >
            <option value="general_analysis">общий анализ</option>
            <option value="builder">builder</option>
            <option value="monitoring">monitoring</option>
          </select>
          <input
            placeholder="campaign_id"
            value={activeContext.campaign_id ?? ""}
            onChange={(e) => activeSessionId && setSessionContext(activeSessionId, { ...activeContext, campaign_id: e.target.value === "" ? null : Number(e.target.value) })}
          />
          <input
            placeholder="segment_id"
            value={activeContext.segment_id ?? ""}
            onChange={(e) => activeSessionId && setSessionContext(activeSessionId, { ...activeContext, segment_id: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </div>
        {contextWarning && <div className="chat-error">{contextWarning}</div>}
        {loadingMessages ? <div className="chat-messages">{chatState === "refreshing" ? "Фоновое обновление…" : "Загрузка…"}</div> : messages.length === 0 ? <div className="chat-empty-group">Сообщений пока нет</div> : <MessageThread messages={messages} onExecuteAction={executeAction} onLoadOlder={loadOlderMessages} hasMore={hasMoreMessages} loadingOlder={loadingOlderMessages} />}
        {error && <div className="chat-error">{error} <button onClick={() => void retry()}>Повторить</button></div>}
        <div className="chat-composer">
          <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Введите сообщение" />
          <button disabled={sending || !input.trim() || !activeSessionId} onClick={async () => { await sendMessage(input); setInput(""); }}>Отправить</button>
        </div>
      </main>
      <aside className={`chat-right-panel ${collapsed ? "collapsed" : ""}`}>
        <button onClick={() => setCollapsed((v) => !v)}>{collapsed ? "Развернуть" : "Свернуть"}</button>
        {!collapsed && (
          <div>
            <h4>Артефакты</h4>
            {artifacts.length === 0 ? <div>Нет артефактов</div> : artifacts.map((a) => <pre key={a.id}>{a.type}: {JSON.stringify(a.content ?? {}, null, 2)}</pre>)}
          </div>
        )}
      </aside>
    </div>
  );
}

export function ChatWorkspacePage() {
  return (
    <ChatWorkspaceProvider>
      <AppErrorBoundary title="Ошибка workspace">
        <WorkspaceBody />
      </AppErrorBoundary>
    </ChatWorkspaceProvider>
  );
}
