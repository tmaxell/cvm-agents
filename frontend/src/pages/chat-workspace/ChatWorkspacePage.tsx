import { useEffect, useMemo, useRef, useState } from "react";
import { ChatWorkspaceProvider, useChatWorkspaceStore, type SessionItem } from "../../chat-workspace/store/chatWorkspaceStore";
import type { ChatMessage } from "../../api/chatApi";
import { MarkdownText } from "../../components/MarkdownText";

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

type TraceStepStatus = "selected" | "running" | "done";
interface TraceStep {
  title: string;
  status: TraceStepStatus;
}

function parseStructured(content: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(content);
    return typeof parsed === "object" && parsed !== null ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function MessageThread({ messages }: { messages: ChatMessage[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const wasNearBottomRef = useRef(true);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    if (wasNearBottomRef.current) node.scrollTop = node.scrollHeight;
  }, [messages]);

  return (
    <div
      className="chat-messages"
      ref={containerRef}
      onScroll={(e) => {
        const node = e.currentTarget;
        const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
        wasNearBottomRef.current = distanceFromBottom < 64;
      }}
    >
      {messages.length === 0 ? <div>Пока нет сообщений</div> : messages.map((m) => {
        const structured = parseStructured(m.content);
        const messageType = typeof structured?.type === "string" ? structured.type : null;
        const explanation = typeof structured?.explanation === "string" ? structured.explanation : m.content;
        const actionCardTitle = typeof structured?.title === "string" ? structured.title : "Action";
        const traceSummary = typeof structured?.summary === "string" ? structured.summary : null;
        const traceRaw = Array.isArray(structured?.trace) ? structured.trace : [];
        const steps: TraceStep[] = traceRaw
          .map((item) => {
            if (!item || typeof item !== "object") return null;
            const row = item as Record<string, unknown>;
            const title = typeof row.step === "string" ? row.step : typeof row.title === "string" ? row.title : "";
            const statusRaw = typeof row.status === "string" ? row.status : "";
            const status: TraceStepStatus =
              statusRaw === "done" ? "done" : statusRaw === "running" ? "running" : "selected";
            return title ? { title, status } : null;
          })
          .filter((item): item is TraceStep => Boolean(item));
        const activitiesRaw = Array.isArray(structured?.activities) ? structured.activities : [];
        const activities = activitiesRaw.filter((item): item is string => typeof item === "string");

        return (
          <article key={m.id} className={`bubble ${m.role}`}>
            <header className="bubble-meta">{m.role} · {formatDateTime(m.createdAt)}</header>
            {messageType === "action_card" && (
              <section className="action-card">
                <strong>{actionCardTitle}</strong>
                <MarkdownText content={explanation} />
              </section>
            )}
            {(m.role === "system" || messageType === "trace-summary") && (
              <section className="trace-summary">
                <strong>План выполнения</strong>
                {traceSummary && <p>{traceSummary}</p>}
                {steps.length > 0 && (
                  <ul className="trace-steps">
                    {steps.map((step, idx) => <li key={`${step.title}-${idx}`} className={step.status}>{step.title}</li>)}
                  </ul>
                )}
              </section>
            )}
            {activities.length > 0 && (
              <details className="agent-activity">
                <summary>Что сделал агент</summary>
                <ul>{activities.map((activity, idx) => <li key={`${activity}-${idx}`}>{activity}</li>)}</ul>
              </details>
            )}
            {messageType !== "action_card" && !(m.role === "system" || messageType === "trace-summary") && <MarkdownText content={explanation} />}
          </article>
        );
      })}
    </div>
  );
}

function ChatListSidebar() {
  const { sessions, activeSessionId, selectSession, createNewChat, loadingSessions, sessionsState } = useChatWorkspaceStore();
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
        <button onClick={() => void createNewChat()}>New chat</button>
      </div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по title и last message" />
      {loadingSessions && <div>{sessionsState === "refreshing" ? "Обновление списка…" : "Загрузка списка…"}</div>}
      {Object.entries(grouped).map(([label, items]) => (
        <section key={label}>
          <h4>{label}</h4>
          {items.length === 0 ? <div className="chat-empty-group">Нет чатов</div> : items.map((s) => (
            <button key={s.id} className={s.id === activeSessionId ? "active" : ""} onClick={() => void selectSession(s.id)}>
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
  const [collapsed, setCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const { activeSessionId, messages, artifacts, sendMessage, sending, error, loadingMessages, refreshSessions, selectSession, chatState } = useChatWorkspaceStore();

  const retry = async () => {
    await refreshSessions();
    if (activeSessionId) await selectSession(activeSessionId);
  };

  return (
    <div className="chat-workspace-layout">
      <ChatListSidebar />
      <main className="chat-center-panel">
        {loadingMessages ? <div className="chat-messages">{chatState === "refreshing" ? "Фоновое обновление…" : "Загрузка…"}</div> : <MessageThread messages={messages} />}
        {error && <div className="chat-error">{error} <button onClick={() => void retry()}>Retry</button></div>}
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
      <WorkspaceBody />
    </ChatWorkspaceProvider>
  );
}
