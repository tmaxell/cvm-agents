import { useEffect, useMemo, useRef, useState } from "react";
import {
  useChatWorkspaceStore,
  type ChatEntry,
} from "../chat-workspace/store/chatWorkspaceStore";
import type { ChatAction, ChatTraceEvent, ChatSession } from "../api/chatApi";
import { MarkdownText } from "./MarkdownText";

type WidgetMode = "fab" | "panel";
type HistoryMode = "closed" | "open";

const SUGGESTIONS: { label: string; prompt: string }[] = [
  { label: "Кампании, требующие внимания", prompt: "Какие кампании сейчас требуют внимания? Дай рекомендации." },
  { label: "Собери сегмент", prompt: "Помоги собрать сегмент для нового продукта." },
  { label: "Создать кампанию", prompt: "Создай кампанию по описанию: продвижение нового тарифа для активной аудитории." },
  { label: "Доработать флоу", prompt: "Проанализируй текущий черновик флоу и предложи доработки." },
  { label: "Вопрос по документации", prompt: "Как настроить контрольную группу для пилотной кампании?" },
];

function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isSameDay(left: Date, right: Date): boolean {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function groupSessions(sessions: ChatSession[]): { label: string; items: ChatSession[] }[] {
  const now = new Date();
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  const groups: { [key: string]: ChatSession[] } = { "Сегодня": [], "Вчера": [], "Ранее": [] };
  for (const s of sessions) {
    const d = s.updatedAt ? new Date(s.updatedAt) : null;
    if (!d || Number.isNaN(d.getTime())) groups["Ранее"].push(s);
    else if (isSameDay(d, now)) groups["Сегодня"].push(s);
    else if (isSameDay(d, yesterday)) groups["Вчера"].push(s);
    else groups["Ранее"].push(s);
  }
  return Object.entries(groups).filter(([, v]) => v.length > 0).map(([label, items]) => ({ label, items }));
}

function PlanCard({ trace }: { trace: ChatTraceEvent[] }) {
  if (!trace || trace.length === 0) return null;
  const visible = trace.filter((t) => ["plan_created", "step_started", "tool_called", "step_completed", "run_completed", "run_failed"].includes(t.event));
  if (visible.length === 0) return null;
  return (
    <details className="fw-plan">
      <summary>План вызова агентов · {visible.length} шагов</summary>
      <div className="fw-plan-steps">
        {visible.map((e, i) => (
          <div key={i} className={`fw-plan-step ${e.status}`}>
            <span>
              <strong>{e.event}</strong>
              {e.detail ? <> · {e.detail}</> : null}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

const ACTION_TITLES: Record<string, string> = {
  save_campaign: "Сохранить кампанию",
  save_segment: "Сохранить сегмент",
  save_target_group: "Сохранить таргет-группу",
  apply_segment: "Применить сегмент",
  open_artifact: "Открыть артефакт",
  builder: "Открыть Builder",
};

function ActionCards({
  actions,
  onAct,
  pending,
}: {
  actions: ChatAction[];
  onAct: (action: ChatAction) => void;
  pending: boolean;
}) {
  if (!actions || actions.length === 0) return null;
  const saveActions = actions.filter((a) => a.id.startsWith("save_") || a.id === "apply_segment");
  const navActions = actions.filter((a) => !saveActions.includes(a));

  return (
    <>
      {saveActions.length > 0 && (
        <div className="fw-action-card">
          <div className="fw-action-card-title">💾 Предлагаемые сохранения</div>
          <div className="fw-action-card-body">
            Агент подготовил артефакт. Сохраните его, чтобы переиспользовать в следующих шагах.
          </div>
          <div className="fw-action-card-buttons">
            {saveActions.map((a) => (
              <button
                key={a.id}
                className="primary"
                disabled={pending}
                onClick={() => onAct(a)}
              >
                {ACTION_TITLES[a.id] ?? a.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {navActions.length > 0 && (
        <div className="fw-action-card-buttons" style={{ alignSelf: "flex-start", marginTop: 4 }}>
          {navActions.map((a) => (
            <button key={a.id} disabled={pending} onClick={() => onAct(a)}>
              {ACTION_TITLES[a.id] ?? a.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function MessageBubble({ msg, onAction, pending }: { msg: ChatEntry; onAction: (a: ChatAction) => void; pending: boolean }) {
  return (
    <>
      <div className={`fw-msg ${msg.role}`}>
        {msg.role === "user" ? msg.content : <MarkdownText content={msg.content} />}
        <div className="fw-msg-time">{formatTime(msg.createdAt)}</div>
      </div>
      {msg.role === "assistant" && msg.trace && <PlanCard trace={msg.trace} />}
      {msg.role === "assistant" && msg.actions_available && (
        <ActionCards actions={msg.actions_available} onAct={onAction} pending={pending} />
      )}
    </>
  );
}

function ThreadEmpty({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="fw-thread-empty">
      <div style={{ fontSize: 16, color: "#1e293b", fontWeight: 700 }}>CVM Assistant</div>
      <div>Спросите про кампании, сегменты, документацию.</div>
      <div className="fw-suggestion-grid">
        {SUGGESTIONS.map((s) => (
          <button key={s.label} className="fw-suggestion" onClick={() => onPick(s.prompt)}>
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function HistoryPanel({
  sessions,
  activeId,
  loading,
  onSelect,
  onNew,
  onClose,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => `${s.title} ${s.lastMessagePreview ?? ""}`.toLowerCase().includes(q));
  }, [sessions, query]);
  const groups = groupSessions(filtered);

  return (
    <div className="fw-history">
      <div className="fw-history-toolbar">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по диалогам"
        />
        <button className="fw-btn-primary" onClick={onNew}>+ Новый</button>
        <button className="fw-icon-btn" title="Закрыть историю" onClick={onClose}>✕</button>
      </div>
      <div className="fw-history-list">
        {loading && sessions.length === 0 && (
          <div className="fw-history-empty">Загрузка…</div>
        )}
        {!loading && groups.length === 0 && (
          <div className="fw-history-empty">История диалогов пуста</div>
        )}
        {groups.map((g) => (
          <div key={g.label}>
            <div className="fw-history-group-title">{g.label}</div>
            {g.items.map((s) => (
              <button
                key={s.id}
                className={`fw-history-item ${s.id === activeId ? "active" : ""}`}
                onClick={() => onSelect(s.id)}
              >
                <div className="fw-history-item-title">{s.title || "Без названия"}</div>
                {s.lastMessagePreview && (
                  <div className="fw-history-item-preview">{s.lastMessagePreview}</div>
                )}
                <div className="fw-history-item-meta">
                  <span>{formatTime(s.updatedAt)}</span>
                  {s.status && s.status !== "active" && <span>· {s.status}</span>}
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function FloatingWidget() {
  const {
    sessions,
    activeSessionId,
    messages,
    loadingSessions,
    loadingMessages,
    sending,
    error,
    selectSession,
    createNewChat,
    sendMessage,
  } = useChatWorkspaceStore();

  const [mode, setMode] = useState<WidgetMode>("fab");
  const [history, setHistory] = useState<HistoryMode>("closed");
  const [input, setInput] = useState("");
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages.length, sending]);

  const handlePickSuggestion = (prompt: string) => {
    void sendMessage(prompt);
  };

  const handleAction = (action: ChatAction) => {
    void sendMessage(ACTION_TITLES[action.id] ?? action.label, action);
  };

  const handleSelectSession = async (id: string) => {
    await selectSession(id);
    setHistory("closed");
  };

  const handleNewChat = async () => {
    await createNewChat();
    setHistory("closed");
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    await sendMessage(text);
  };

  if (mode === "fab") {
    return (
      <div className="fw-root">
        <button
          className="fw-fab"
          onClick={() => setMode("panel")}
          aria-label="Открыть AI-ассистент"
          title="AI Assistant"
          style={{ position: "relative" }}
        >
          <span className="fw-fab-icon">💬</span>
        </button>
      </div>
    );
  }

  return (
    <div className="fw-root">
      <div className="fw-panel">
        <header className="fw-header">
          <button
            className={`fw-icon-btn ${history === "open" ? "active" : ""}`}
            title="История диалогов"
            onClick={() => setHistory(history === "open" ? "closed" : "open")}
          >
            ☰
          </button>
          <div className="fw-header-title">
            <span className="fw-header-dot" />
            AI Assistant
          </div>
          <button className="fw-icon-btn" title="Новый диалог" onClick={() => void handleNewChat()}>＋</button>
          <button className="fw-icon-btn" title="Свернуть" onClick={() => setMode("fab")}>＿</button>
        </header>

        <div className="fw-thread" ref={threadRef}>
          {loadingMessages && messages.length === 0 ? (
            <div className="fw-thread-empty">Загрузка…</div>
          ) : messages.length === 0 ? (
            <ThreadEmpty onPick={handlePickSuggestion} />
          ) : (
            messages.map((m) => (
              <MessageBubble key={m.id} msg={m} onAction={handleAction} pending={sending} />
            ))
          )}
          {sending && <div className="fw-typing">Ассистент думает…</div>}
        </div>

        {error && <div className="fw-error">{error}</div>}

        <div className="fw-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
            placeholder="Спросите про кампании, сегменты или документацию…"
            rows={1}
          />
          <button onClick={() => void handleSend()} disabled={sending || !input.trim()} title="Отправить">
            ➤
          </button>
        </div>

        {history === "open" && (
          <HistoryPanel
            sessions={sessions}
            activeId={activeSessionId}
            loading={loadingSessions}
            onSelect={(id) => void handleSelectSession(id)}
            onNew={() => void handleNewChat()}
            onClose={() => setHistory("closed")}
          />
        )}
      </div>
    </div>
  );
}
