export interface ChatSession {
  id: string;
  title: string;
  status: string;
  updatedAt: string | null;
  lastMessagePreview: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string | null;
}

export interface ChatArtifact {
  id: string;
  type: string;
  title: string | null;
  content: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export interface ChatSessionDetail {
  session: ChatSession;
  messages: ChatMessage[];
  artifacts: ChatArtifact[];
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const MAX_RETRIES = 2;

const ERRORS = {
  chats: "Не удалось загрузить историю чатов",
  chat: "Не удалось загрузить чат",
  messages: "Не удалось загрузить сообщения",
  artifacts: "Не удалось загрузить артефакты",
  send: "Не удалось отправить сообщение",
};

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const asString = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const asNullableString = (v: unknown): string | null => (typeof v === "string" ? v : null);

function normalizeSession(raw: unknown): ChatSession {
  const o = isObject(raw) ? raw : {};
  return {
    id: asString(o.id, `unknown-${Math.random().toString(36).slice(2, 9)}`),
    title: asString(o.title, "Без названия"),
    status: asString(o.status, "unknown"),
    updatedAt: asNullableString(o.updated_at),
    lastMessagePreview: asString(o.last_message_preview),
  };
}

function normalizeMessage(raw: unknown, index: number): ChatMessage {
  const o = isObject(raw) ? raw : {};
  const role = o.role === "assistant" || o.role === "system" ? o.role : "user";
  return {
    id: asString(o.id, `m-${index}`),
    role,
    content: asString(o.content),
    createdAt: asNullableString(o.created_at),
  };
}

async function fetchWithRetry(path: string, init: RequestInit, userError: string): Promise<Response> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    try {
      const res = await fetch(`${API_BASE}${path}`, init);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(userError);
      if (attempt < MAX_RETRIES) {
        await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 250));
        continue;
      }
    }
  }
  throw new Error(userError + (lastError ? ` (${lastError.message})` : ""));
}

export async function listChats(): Promise<ChatSession[]> {
  const res = await fetchWithRetry("/api/sessions", {}, ERRORS.chats);
  const data = await res.json();
  const list = Array.isArray(data) ? data : (isObject(data) && Array.isArray(data.sessions) ? data.sessions : []);
  return list.map(normalizeSession);
}

export async function getChat(sessionId: string): Promise<ChatSessionDetail> {
  const res = await fetchWithRetry(`/api/sessions/${encodeURIComponent(sessionId)}`, {}, ERRORS.chat);
  const data = await res.json();
  const session = normalizeSession({ ...(isObject(data) ? data : {}), id: sessionId });
  const messages = listMessagesFromPayload(data);
  const artifacts = listArtifactsFromPayload(sessionId, data);
  return { session, messages, artifacts };
}

function listMessagesFromPayload(data: unknown): ChatMessage[] {
  if (!isObject(data) || !Array.isArray(data.messages)) return [];
  return data.messages.map((m, i) => normalizeMessage(m, i));
}

function listArtifactsFromPayload(sessionId: string, data: unknown): ChatArtifact[] {
  if (!isObject(data)) return [];
  const items: ChatArtifact[] = [];
  if (isObject(data.draft_flow)) items.push({ id: `${sessionId}-draft-flow`, type: "draft_flow", title: null, content: data.draft_flow, metadata: {} });
  if (isObject(data.campaign_brief)) items.push({ id: `${sessionId}-campaign-brief`, type: "campaign_brief", title: null, content: data.campaign_brief, metadata: {} });
  return items;
}

export async function listMessages(sessionId: string): Promise<ChatMessage[]> {
  const detail = await getChat(sessionId);
  return detail.messages;
}

export async function listArtifacts(sessionId: string): Promise<ChatArtifact[]> {
  const detail = await getChat(sessionId);
  return detail.artifacts;
}

export async function sendMessage(sessionId: string, content: string): Promise<void> {
  if (!content.trim()) return;
  await fetchWithRetry("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: content }),
  }, ERRORS.send);
}
