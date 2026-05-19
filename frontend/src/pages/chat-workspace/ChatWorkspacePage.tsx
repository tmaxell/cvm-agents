import { useState } from "react";
import { ChatWorkspaceProvider, useChatWorkspaceStore } from "../../chat-workspace/store/chatWorkspaceStore";

function WorkspaceBody() {
  const [collapsed, setCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const { sessions, activeSessionId, messages, selectSession, sendMessage, sending, error, loadingMessages } = useChatWorkspaceStore();

  return (
    <div className="chat-workspace-layout">
      <aside className="chat-left-panel">
        <h3>Чаты</h3>
        {sessions.map((s) => (
          <button key={s.id} className={s.id === activeSessionId ? "active" : ""} onClick={() => void selectSession(s.id)}>{s.title}</button>
        ))}
      </aside>
      <main className="chat-center-panel">
        <div className="chat-messages">
          {loadingMessages ? <div>Загрузка…</div> : messages.map((m) => <div key={m.id} className={`bubble ${m.role}`}>{m.content}</div>)}
        </div>
        {error && <div className="chat-error">{error}</div>}
        <div className="chat-composer">
          <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Введите сообщение" />
          <button disabled={sending || !input.trim()} onClick={async () => { await sendMessage(input); setInput(""); }}>Отправить</button>
        </div>
      </main>
      <aside className={`chat-right-panel ${collapsed ? "collapsed" : ""}`}>
        <button onClick={() => setCollapsed((v) => !v)}>{collapsed ? "Развернуть" : "Свернуть"}</button>
        {!collapsed && <div>Контекст и артефакты (placeholder)</div>}
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
