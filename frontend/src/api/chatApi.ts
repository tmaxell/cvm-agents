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

export interface ChatActionRequestPayload {
  id: string;
  label: string;
  kind?: string;
  payload?: Record<string, unknown>;
}

export interface ChatActionResponse {
  assistant_message: string;
  artifacts: ChatArtifact[];
  actions_available: ChatActionRequestPayload[];
}

export type BackendChatMode = "general_analysis" | "builder" | "monitoring";

export interface ChatSessionContext {
  campaign_id?: number | null;
  segment_id?: number | null;
  mode?: BackendChatMode;
}

const DEFAULT_CHAT_MODE: BackendChatMode = "general_analysis";

function withDefaultContextMode(context?: ChatSessionContext): ChatSessionContext {
  return { ...(context ?? {}), mode: context?.mode ?? DEFAULT_CHAT_MODE };
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const MAX_RETRIES = 2;
const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type ApiErrorKind = "network" | "timeout" | "http" | "validation" | "unknown";

export class ChatApiError extends Error {
  kind: ApiErrorKind;
  status: number | null;
  retryable: boolean;

  constructor(message: string, kind: ApiErrorKind, status: number | null, retryable: boolean) {
    super(message);
    this.name = "ChatApiError";
    this.kind = kind;
    this.status = status;
    this.retryable = retryable;
  }
}

const ERRORS = {
  chats: "Не удалось загрузить историю чатов",
  chat: "Не удалось загрузить чат",
  messages: "Не удалось загрузить сообщения",
  artifacts: "Не удалось загрузить артефакты",
  send: "Не удалось отправить сообщение",
};

function telemetryApiError(sessionId: string | null, endpoint: string, statusCode: number | null): void {
  console.error("telemetry.api_error", {
    session_id: sessionId,
    endpoint,
    status_code: statusCode,
  });
}

function classifyApiError(err: unknown, fallback: string): ChatApiError {
  if (err instanceof ChatApiError) return err;
  if (err instanceof ApiError) {
    const message = err.status >= 500
      ? "Сервис временно недоступен (5xx). Попробуйте повторить запрос."
      : err.status === 422
        ? "Ошибка валидации данных запроса (422). Проверьте параметры и повторите."
        : "Запрос отклонён (4xx). Проверьте данные и попробуйте снова.";
    return new ChatApiError(message, err.status === 422 ? "validation" : "http", err.status, err.status >= 500);
  }
  if (err instanceof DOMException && err.name === "AbortError") {
    return new ChatApiError("Превышено время ожидания ответа. Проверьте соединение и повторите.", "timeout", null, true);
  }
  if (err instanceof TypeError) {
    return new ChatApiError("Проблемы с соединением. Проверьте сеть и повторите.", "network", null, true);
  }
  return new ChatApiError(fallback, "unknown", null, false);
}

function extractSessionIdFromBody(body: RequestInit["body"]): string | null {
  if (typeof body !== "string") return null;
  try {
    const parsed = JSON.parse(body);
    return isObject(parsed) && typeof parsed.session_id === "string" ? parsed.session_id : null;
  } catch {
    return null;
  }
}

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
  let lastError: ChatApiError | null = null;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    try {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      const res = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal });
      window.clearTimeout(timeoutId);
      if (!res.ok) {
        const sessionId = extractSessionIdFromBody(init.body);
        telemetryApiError(sessionId, path, res.status);
        throw new ApiError(`HTTP ${res.status}`, res.status);
      }
      return res;
    } catch (err) {
      lastError = classifyApiError(err, userError);
      if (attempt < MAX_RETRIES && lastError.retryable) {
        await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 250));
        continue;
      }
    }
  }
  throw (lastError ?? new ChatApiError(userError, "unknown", null, false));
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

export async function sendMessage(sessionId: string, content: string, context?: ChatSessionContext): Promise<void> {
  if (!content.trim()) return;
  await fetchWithRetry("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: content, context: withDefaultContextMode(context) }),
  }, ERRORS.send);
}

export async function sendAction(sessionId: string, message: string, action: ChatActionRequestPayload, artifactId?: string, context?: ChatSessionContext): Promise<ChatActionResponse> {
  const res = await fetchWithRetry("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      context: withDefaultContextMode(context),
      action,
      artifact_id: artifactId,
    }),
  }, ERRORS.send);
  return await res.json() as ChatActionResponse;
}
