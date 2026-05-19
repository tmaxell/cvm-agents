import { useMemo, useState } from "react";
import { ChatWorkspaceProvider, useChatWorkspaceStore, type SessionItem } from "../../chat-workspace/store/chatWorkspaceStore";

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
    const date = session.updated_at ? new Date(session.updated_at) : null;
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

function formatDateTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function ChatListSidebar() {
  const { sessions, activeSessionId, selectSession, createNewChat, loadingSessions } = useChatWorkspaceStore();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...sessions].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    if (!q) return sorted;
    return sorted.filter((session) => `${session.title} ${session.last_message_preview ?? ""}`.toLowerCase().includes(q));
  }, [query, sessions]);

  const grouped = useMemo(() => groupByUpdatedAt(filtered), [filtered]);

  return (
    <aside className="chat-left-panel">
      <div className="chat-left-panel-header">
        <h3>Чаты</h3>
        <button onClick={() => void createNewChat()}>New chat</button>
      </div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по title и last message" />
      {loadingSessions && <div>Загрузка списка…</div>}
      {Object.entries(grouped).map(([label, items]) => (
        <section key={label}>
          <h4>{label}</h4>
          {items.length === 0 ? <div className="chat-empty-group">Нет чатов</div> : items.map((s) => (
            <button key={s.id} className={s.id === activeSessionId ? "active" : ""} onClick={() => void selectSession(s.id)}>
              <div>{s.title}</div>
              <div>{s.last_message_preview || "(нет сообщений)"}</div>
              <div>status: {s.status ?? "—"}</div>
              <div>updated: {formatDateTime(s.updated_at)}</div>
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
  const { activeSessionId, messages, artifacts, sendMessage, sending, error, loadingMessages, refreshSessions, selectSession } = useChatWorkspaceStore();

  const retry = async () => {
    await refreshSessions();
    if (activeSessionId) await selectSession(activeSessionId);
  };

  return (
    <div className="chat-workspace-layout">
      <ChatListSidebar />
      <main className="chat-center-panel">
        <div className="chat-messages">
          {loadingMessages ? <div>Загрузка…</div> : messages.length === 0 ? <div>Пока нет сообщений</div> : messages.map((m) => <div key={m.id} className={`bubble ${m.role}`}>{m.content}</div>)}
        </div>
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
