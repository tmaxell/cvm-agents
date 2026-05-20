import { useEffect, useMemo, useRef, useState } from "react";
import {
  useChatWorkspaceStore,
  type ChatEntry,
} from "../chat-workspace/store/chatWorkspaceStore";
import type { ChatAction, ChatTraceEvent, ChatSession } from "../api/chatApi";
import { MarkdownText } from "./MarkdownText";
import { Sources } from "./Sources";

type WidgetMode = "fab" | "panel" | "expanded";
type HistoryMode = "closed" | "open";

const SUGGESTIONS: { label: string; prompt: string }[] = [
  { label: "Кампании, требующие внимания", prompt: "Какие кампании сейчас требуют внимания?" },
  { label: "Собери сегмент", prompt: "Собери сегмент активных клиентов." },
  { label: "Создай кампанию", prompt: "Создай кампанию по продвижению нового тарифа для активной аудитории." },
  { label: "Доработать флоу", prompt: "Доработай текущий черновик кампании." },
  { label: "Вопрос по документации", prompt: "Как настроить контрольную группу в AdTarget?" },
];

const ACTION_LABELS: Record<string, string> = {
  save_campaign: "Сохранить кампанию",
  save_segment: "Сохранить сегмент",
  save_target_group: "Сохранить таргет-группу",
  apply_segment: "Применить сегмент",
  build_campaign_from_segment: "Создать кампанию из сегмента",
  refine_campaign: "Доработать",
  open_artifact: "Открыть артефакт",
  start_campaign: "Запустить кампанию",
  pause_campaign: "Поставить на паузу",
};

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

// ── SVG icons ────────────────────────────────────────────────────────────────

const ChatIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M4 6.5C4 5.119 5.119 4 6.5 4h11C18.881 4 20 5.119 20 6.5v8C20 15.881 18.881 17 17.5 17H10l-4 4v-4h-.5C4.119 17 3 15.881 3 14.5v-8z" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round"/>
    <circle cx="8" cy="10.5" r="1.1" fill="#fff"/>
    <circle cx="11.5" cy="10.5" r="1.1" fill="#fff"/>
    <circle cx="15" cy="10.5" r="1.1" fill="#fff"/>
  </svg>
);
const HistoryIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
  </svg>
);
const PlusIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
  </svg>
);
const MinimizeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M3 12h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
  </svg>
);
const CloseIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
  </svg>
);
const SendIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M2 8l12-5-5 12-2-5z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" fill="none"/>
  </svg>
);
const ExpandIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M3 7V3h4M13 9v4h-4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);
const CompressIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M7 3v4H3M9 13v-4h4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

// ── Plan / trace ─────────────────────────────────────────────────────────────

function PlanCard({ trace }: { trace: ChatTraceEvent[] | undefined }) {
  if (!trace || trace.length === 0) return null;
  const visible = trace.filter((t) => ["plan_created", "step_started", "tool_called", "step_completed", "run_completed", "run_failed"].includes(t.event));
  if (visible.length === 0) return null;
  return (
    <details className="fw-plan">
      <summary>🧭 План вызова агентов · {visible.length} шагов</summary>
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

// ── Action cards ─────────────────────────────────────────────────────────────

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
  const saveActions = actions.filter((a) => a.id === "save_campaign" || a.id === "save_segment" || a.id === "save_target_group");
  const otherActions = actions.filter((a) => !saveActions.includes(a));

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
              <button key={a.id} className="primary" disabled={pending} onClick={() => onAct(a)}>
                {ACTION_LABELS[a.id] ?? a.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {otherActions.length > 0 && (
        <div className="fw-quick-actions">
          {otherActions.map((a) => (
            <button key={`${a.id}-${a.label}`} disabled={pending} onClick={() => onAct(a)} className="fw-quick-action">
              {a.label || ACTION_LABELS[a.id] || a.id}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function MessageBubble({ msg, onAction, pending, isLast }: { msg: ChatEntry; onAction: (a: ChatAction) => void; pending: boolean; isLast: boolean; }) {
  return (
    <>
      <div className={`fw-msg ${msg.role}`}>
        {msg.role === "user" ? msg.content : <MarkdownText content={msg.content} />}
        <div className="fw-msg-time">{formatTime(msg.createdAt)}</div>
      </div>
      {msg.role === "assistant" && msg.citations && msg.citations.length > 0 && (
        <Sources citations={msg.citations} />
      )}
      {msg.role === "assistant" && <PlanCard trace={msg.trace} />}
      {msg.role === "assistant" && msg.actions && isLast && (
        <ActionCards actions={msg.actions} onAct={onAction} pending={pending} />
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
        <button className="fw-icon-btn" title="Закрыть историю" onClick={onClose}><CloseIcon /></button>
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

// ── Auto-grow textarea ───────────────────────────────────────────────────────

function AutoGrowTextarea({
  value,
  onChange,
  onSubmit,
  disabled,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          onSubmit();
        }
      }}
      placeholder={placeholder}
      disabled={disabled}
      rows={1}
    />
  );
}

// ── Main component ───────────────────────────────────────────────────────────

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
    const label = ACTION_LABELS[action.id] ?? action.label;
    void sendMessage(label, action);
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
          aria-label="Открыть CVM Copilot"
          title="CVM Copilot"
        >
          <ChatIcon />
        </button>
      </div>
    );
  }

  const isExpanded = mode === "expanded";
  const showSideHistory = isExpanded;          // в расширенном режиме история — постоянная боковая колонка
  const showOverlayHistory = !isExpanded && history === "open";

  return (
    <div className={`fw-root ${isExpanded ? "fw-root-expanded" : ""}`}>
      <div className={`fw-panel ${isExpanded ? "fw-panel-expanded" : ""}`}>
        {showSideHistory && (
          <aside className="fw-side-history">
            <div className="fw-side-history-header">
              <span>История</span>
              <button className="fw-btn-primary" onClick={() => void handleNewChat()}>+ Новый</button>
            </div>
            <SideHistoryList
              sessions={sessions}
              activeId={activeSessionId}
              loading={loadingSessions}
              onSelect={(id) => void handleSelectSession(id)}
            />
          </aside>
        )}

        <div className="fw-main">
          <header className="fw-header">
            {!isExpanded && (
              <button
                className={`fw-icon-btn ${history === "open" ? "active" : ""}`}
                title="История диалогов"
                onClick={() => setHistory(history === "open" ? "closed" : "open")}
              >
                <HistoryIcon />
              </button>
            )}
            <div className="fw-header-title">CVM Copilot</div>
            <button className="fw-icon-btn" title="Новый диалог" onClick={() => void handleNewChat()}>
              <PlusIcon />
            </button>
            <button
              className="fw-icon-btn"
              title={isExpanded ? "Свернуть до компактного режима" : "Развернуть"}
              onClick={() => setMode(isExpanded ? "panel" : "expanded")}
            >
              {isExpanded ? <CompressIcon /> : <ExpandIcon />}
            </button>
            <button className="fw-icon-btn" title="Свернуть" onClick={() => setMode("fab")}>
              <MinimizeIcon />
            </button>
          </header>

          <div className="fw-thread" ref={threadRef}>
            {loadingMessages && messages.length === 0 ? (
              <div className="fw-thread-empty">Загрузка…</div>
            ) : messages.length === 0 ? (
              <ThreadEmpty onPick={handlePickSuggestion} />
            ) : (
              messages.map((m, i) => (
                <MessageBubble
                  key={m.id}
                  msg={m}
                  onAction={handleAction}
                  pending={sending}
                  isLast={i === messages.length - 1}
                />
              ))
            )}
            {sending && <div className="fw-typing">Ассистент думает…</div>}
          </div>

          {error && <div className="fw-error">{error}</div>}

          <div className="fw-composer">
            <AutoGrowTextarea
              value={input}
              onChange={setInput}
              onSubmit={() => void handleSend()}
              disabled={false}
              placeholder="Спросите про кампании, сегменты или документацию…"
            />
            <button onClick={() => void handleSend()} disabled={sending || !input.trim()} title="Отправить">
              <SendIcon />
            </button>
          </div>
        </div>

        {showOverlayHistory && (
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

// ── Side history list (для расширенного режима) ──────────────────────────────

function SideHistoryList({
  sessions,
  activeId,
  loading,
  onSelect,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => `${s.title} ${s.lastMessagePreview ?? ""}`.toLowerCase().includes(q));
  }, [sessions, query]);
  const groups = groupSessions(filtered);

  return (
    <div className="fw-side-history-body">
      <input
        className="fw-side-history-search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Поиск по диалогам"
      />
      <div className="fw-side-history-list">
        {loading && sessions.length === 0 && <div className="fw-history-empty">Загрузка…</div>}
        {!loading && groups.length === 0 && <div className="fw-history-empty">Диалогов нет</div>}
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
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
