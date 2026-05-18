/**
 * CampaignBuilderChat — чат Campaign Builder без встроенного FlowCanvas.
 *
 * FlowCanvas теперь рендерится в фоновом AdTarget-макете через колбэк onResponse.
 * Это даёт эффект «AI собирает кампанию прямо в интерфейсе AdTarget».
 */

import { useState, useRef, useEffect, useCallback } from "react";
import type {
  AgentContext,
  BuilderPreferences,
  BuilderResponse,
  CampaignBrief,
  BuilderSession,
  BuilderSessionDetail,
  ChatMessage,
  SelectedSegmentForBuilder,
} from "../types/api";
import { useChat } from "../hooks/useChat";
import { MarkdownText } from "./MarkdownText";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const DEFAULT_CONTEXT: AgentContext = {
  screen: "campaign_wizard",
  user_role: "analyst",
};

const BUILDER_MESSAGES_KEY = "cvm.builder.messages.v1";
const BUILDER_RESPONSE_KEY = "cvm.builder.lastResponse.v1";
const BUILDER_BRIEF_KEY = "cvm.builder.brief.v1";
const BUILDER_PREFS_KEY = "cvm.builder.preferences.v1";
const BUILDER_SESSION_KEY = "cvm.builder.sessionId.v1";

const SUGGESTIONS: Record<"ru" | "en", string[]> = {
  ru: [
    "Запомни: продукт — тариф Family Max, цель — апсейл на семейную аудиторию",
    "Собери draft flow из введённых параметров",
    "Доработай текст: сделай тон более премиальным",
    "Добавь бизнес-транзакцию для активации оффера",
  ],
  en: [
    "Remember: product is Family Max, goal is family upsell",
    "Build draft flow from the parameters",
    "Refine the copy: make the tone more premium",
    "Add a business transaction for offer activation",
  ],
};

const STATUS_LABELS: Record<"ru" | "en", Record<string, string>> = {
  ru: {
    in_progress: "⏳ В процессе",
    created: "✅ Создана",
    started: "🚀 Запущена",
    error: "❌ Ошибка",
  },
  en: {
    in_progress: "⏳ In progress",
    created: "✅ Created",
    started: "🚀 Started",
    error: "❌ Error",
  },
};

const STATUS_COLORS: Record<string, string> = {
  in_progress: "#b7791f",
  created: "#5257ff",
  started: "#16a34a",
  error: "#dc2626",
};

interface BuilderDemoPlaybookItem {
  label: string;
  description?: string;
  prompt?: string;
}

interface ResultPanelItem {
  label: string;
  value: string;
}

type BriefInlineField = "goal" | "product" | "audience" | "constraints";

interface Props {
  onResponse: (response: BuilderResponse | null) => void;
  onOpenMonitoring?: () => void;
  lang?: "ru" | "en";
  selectedSegment?: SelectedSegmentForBuilder | null;
  variant?: "classic" | "demo";
  demoPlaybook?: BuilderDemoPlaybookItem[];
}

function readStoredJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : fallback;
  } catch {
    return fallback;
  }
}

function readStoredString(key: string): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(key);
}

const EMPTY_CAMPAIGN_BRIEF: CampaignBrief = {
  product: null,
  goal: null,
  audience: { target_groups: [], description: null },
  channels: [],
  constraints: { content: null, offer_recommendations: null },
};

function splitListValue(value?: string | null): string[] {
  return (value ?? "")
    .replace(/;/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function cleanBriefValue(value?: string | null): string | null {
  const trimmed = value?.trim();
  return trimmed || null;
}

function preferencesToBrief(preferences: BuilderPreferences): CampaignBrief {
  const targetGroupsText = cleanBriefValue(preferences.targetGroups);
  return {
    product: cleanBriefValue(preferences.product),
    goal: cleanBriefValue(preferences.goal),
    audience: {
      target_groups: splitListValue(targetGroupsText),
      description: targetGroupsText,
    },
    channels: splitListValue(preferences.channels).map((name) => ({ name })),
    constraints: {
      content: cleanBriefValue(preferences.content),
      offer_recommendations: cleanBriefValue(preferences.offerRecommendations),
    },
  };
}

function briefToPreferences(brief: CampaignBrief): BuilderPreferences {
  return {
    product: brief.product ?? undefined,
    goal: brief.goal ?? undefined,
    targetGroups: (brief.audience.description ?? brief.audience.target_groups.join(", ")) || undefined,
    channels: brief.channels.map((channel) => channel.name).filter(Boolean).join(", ") || undefined,
    content: brief.constraints.content ?? undefined,
    offerRecommendations: brief.constraints.offer_recommendations ?? undefined,
  };
}

function hasBrief(brief: CampaignBrief): boolean {
  return hasPreferences(briefToPreferences(brief));
}

function hasPreferences(preferences: BuilderPreferences): boolean {
  return Object.values(preferences).some((value) => Boolean(value?.trim()));
}

function preserveStructuredAudience(current: CampaignBrief, next: CampaignBrief): CampaignBrief {
  if (!next.audience.selected_segment && current.audience.selected_segment) {
    return {
      ...next,
      audience: {
        ...next.audience,
        selected_segment: current.audience.selected_segment,
      },
    };
  }
  return next;
}

function mergeResponseBrief(
  current: CampaignBrief,
  response: BuilderResponse,
): CampaignBrief | null {
  if (response.builder_preferences) {
    return preserveStructuredAudience(current, preferencesToBrief(response.builder_preferences));
  }
  if (response.preference_patch) {
    return preserveStructuredAudience(
      current,
      preferencesToBrief({ ...briefToPreferences(current), ...response.preference_patch }),
    );
  }
  return null;
}

function getMatchedTargetGroupId(match: SelectedSegmentForBuilder["hypothesis"]["matched_target_group"]): string | null {
  if (!match) return null;
  const id = match.id ?? match.target_group_id;
  return id == null || id === "" ? null : String(id);
}

function formatSelectedSegmentTargetGroups(
  selectedSegment: SelectedSegmentForBuilder,
  lang: "ru" | "en",
): string {
  const { hypothesis } = selectedSegment;
  const match = hypothesis.matched_target_group;
  const matchId = getMatchedTargetGroupId(match);
  const hasExistingTargetGroup = Boolean(match && hypothesis.is_existing_target_group);
  const isRecommendationOnly = Boolean(selectedSegment.recommendationOnly) || !hasExistingTargetGroup;

  const targetGroupLine = isRecommendationOnly
    ? (lang === "en"
      ? "Recommendation-only segment; Target Group is not attached yet"
      : "Сегмент-рекомендация; Target Group пока не привязана")
    : `Target Group: ${matchId ? `#${matchId} · ` : ""}${match?.name ?? hypothesis.name}`;

  const segmentLabel = lang === "en" ? "Segment" : "Сегмент";
  return [targetGroupLine, `${segmentLabel}: ${hypothesis.name}`].filter(Boolean).join(" · ");
}

function audienceFromSelectedSegment(
  selectedSegment: SelectedSegmentForBuilder,
  lang: "ru" | "en",
): CampaignBrief["audience"] {
  const { hypothesis } = selectedSegment;
  const match = hypothesis.matched_target_group ?? null;
  const matchId = getMatchedTargetGroupId(match);
  const hasExistingTargetGroup = Boolean(match && hypothesis.is_existing_target_group);
  const recommendationOnly = Boolean(selectedSegment.recommendationOnly) || !hasExistingTargetGroup;
  const summary = formatSelectedSegmentTargetGroups(selectedSegment, lang);

  return {
    target_groups: matchId && !recommendationOnly ? [matchId] : [],
    description: summary,
    selected_segment: {
      hypothesis: { name: hypothesis.name },
      selection_criteria: hypothesis.selection_criteria,
      matched_target_group: match,
      is_existing_target_group: hasExistingTargetGroup,
      risk_or_limitation: hypothesis.risk_or_limitation,
      recommendationOnly,
    },
  };
}

function preferencesFromSelectedSegment(
  selectedSegment: SelectedSegmentForBuilder,
  lang: "ru" | "en",
): Partial<BuilderPreferences> {
  return {
    ...(selectedSegment.product ? { product: selectedSegment.product } : {}),
    ...(selectedSegment.goal ? { goal: selectedSegment.goal } : {}),
    targetGroups: formatSelectedSegmentTargetGroups(selectedSegment, lang),
  };
}

function getSelectedSegmentMeta(selectedSegment: SelectedSegmentForBuilder, lang: "ru" | "en"): string {
  const product = getPlanValue(selectedSegment.product);
  const goal = getPlanValue(selectedSegment.goal);
  return lang === "en" ? `Product: ${product} · Goal: ${goal}` : `Продукт: ${product} · Цель: ${goal}`;
}


function getPlanValue(value?: string | null): string {
  const trimmed = value?.trim();
  return trimmed || "—";
}

function stringifyBriefCriteria(criteria?: Record<string, unknown> | null): string[] {
  if (!criteria) return [];
  return Object.entries(criteria).map(([key, value]) => {
    if (Array.isArray(value)) return `${key}: ${value.join(", ")}`;
    if (value && typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
    return `${key}: ${String(value)}`;
  });
}

function formatCriteriaToken(key: string, value: unknown, lang: "ru" | "en"): string | null {
  const normalizedKey = key.replace(/_/g, " ").trim();
  const keyLower = normalizedKey.toLowerCase();
  const stringify = (item: unknown) => String(item).replace(/_/g, " ").trim();

  if (Array.isArray(value)) {
    const joined = value.map(stringify).filter(Boolean).join(", ");
    if (!joined) return null;
    if (keyLower.includes("exclude") || keyLower.includes("opt")) return `${joined} excluded`;
    return joined;
  }

  if (value && typeof value === "object") {
    const nested = Object.entries(value as Record<string, unknown>)
      .map(([nestedKey, nestedValue]) => formatCriteriaToken(nestedKey, nestedValue, lang))
      .filter(Boolean);
    return nested[0] ?? null;
  }

  const rawValue = stringify(value);
  if (!rawValue || rawValue === "true") {
    if (keyLower.includes("travel")) return lang === "en" ? "travelers" : "путешествующие";
    if (keyLower.includes("opt")) return "opt-out excluded";
    return normalizedKey;
  }

  const valueLower = rawValue.toLowerCase();
  if (keyLower.includes("arpu") || valueLower.includes("arpu")) {
    if (valueLower.includes("low") || valueLower.includes("низ")) return lang === "en" ? "Low ARPU" : "Низкий ARPU";
    return rawValue.includes("ARPU") ? rawValue : `${rawValue} ARPU`;
  }
  if (keyLower.includes("travel") || valueLower.includes("travel")) {
    return lang === "en" ? "travelers" : "путешествующие";
  }
  if (keyLower.includes("exclude") || keyLower.includes("opt") || valueLower.includes("opt-out")) {
    return rawValue.includes("excluded") ? rawValue : `${rawValue} excluded`;
  }
  return rawValue;
}

function getAudienceSummary(brief: CampaignBrief, lang: "ru" | "en"): string {
  const selected = brief.audience.selected_segment;
  if (!selected) return brief.audience.description ?? (brief.audience.target_groups.join(", ") || "");

  const criteriaTokens = Object.entries(selected.selection_criteria ?? {})
    .map(([key, value]) => formatCriteriaToken(key, value, lang))
    .filter((value): value is string => Boolean(value));
  const riskToken = selected.risk_or_limitation?.toLowerCase().includes("opt") ? "opt-out excluded" : null;
  const tokens = [...criteriaTokens, riskToken].filter((value): value is string => Boolean(value));
  const uniqueTokens = [...new Set(tokens)];

  if (uniqueTokens.length > 0) return uniqueTokens.slice(0, 3).join(" · ");
  return selected.hypothesis.name || brief.audience.description || "";
}

function getAudienceFullCriteria(brief: CampaignBrief, lang: "ru" | "en"): string[] {
  const selected = brief.audience.selected_segment;
  if (!selected) return [];
  const criteria = stringifyBriefCriteria(selected.selection_criteria);
  return [
    `${lang === "en" ? "Segment" : "Сегмент"}: ${selected.hypothesis.name}`,
    ...criteria,
    selected.risk_or_limitation ? `${lang === "en" ? "Risk / limitation" : "Риск / ограничение"}: ${selected.risk_or_limitation}` : null,
  ].filter((value): value is string => Boolean(value));
}

function getConstraintSummary(brief: CampaignBrief): string {
  return [brief.constraints.content, brief.constraints.offer_recommendations]
    .map((value) => value?.trim())
    .filter(Boolean)
    .join(" · ");
}

function buildBuilderPrompt(brief: CampaignBrief, lang: "ru" | "en"): string {
  const preferences = briefToPreferences(brief);
  const fields = lang === "en"
    ? [
      ["Campaign goal", getPlanValue(preferences.goal)],
      ["Product", getPlanValue(preferences.product)],
      ["Audience", getPlanValue(preferences.targetGroups)],
      ["Channels", getPlanValue(preferences.channels)],
      ["Content constraints", getPlanValue(preferences.content)],
      ["Offer recommendations", getPlanValue(preferences.offerRecommendations)],
    ]
    : [
      ["Цель кампании", getPlanValue(preferences.goal)],
      ["Продукт", getPlanValue(preferences.product)],
      ["Аудитория", getPlanValue(preferences.targetGroups)],
      ["Каналы", getPlanValue(preferences.channels)],
      ["Контентные ограничения", getPlanValue(preferences.content)],
      ["Рекомендации по офферам", getPlanValue(preferences.offerRecommendations)],
    ];

  const intro = lang === "en"
    ? "Build a draft Campaign Builder flow using the plan below. Use existing Target Group details when provided and return a ready-to-review draft flow."
    : "Собери draft flow в Campaign Builder по плану ниже. Используй данные существующей Target Group, если они указаны, и верни готовый к проверке draft flow.";

  return [intro, "", ...fields.map(([label, value]) => `- ${label}: ${value}`)].join("\n");
}


function pluralizeActivities(count: number, lang: "ru" | "en"): string {
  if (lang === "en") return `${count} ${count === 1 ? "activity" : "activities"}`;
  const mod10 = count % 10;
  const mod100 = count % 100;
  const suffix = mod10 === 1 && mod100 !== 11
    ? "активность"
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
      ? "активности"
      : "активностей";
  return `${count} ${suffix}`;
}

function getFlowSummary(response: BuilderResponse, lang: "ru" | "en"): string {
  const activities = response.draft_flow?.activities ?? [];
  if (activities.length === 0) {
    return lang === "en" ? "No flow yet" : "Flow не собран";
  }

  const activityNames = activities
    .slice(0, 3)
    .map((activity) => activity.name || activity.type)
    .filter(Boolean);
  const suffix = activities.length > activityNames.length ? "…" : "";
  const summary = activityNames.length > 0 ? ` · ${activityNames.join(" → ")}${suffix}` : "";
  return `${pluralizeActivities(activities.length, lang)}${summary}`;
}

function getValidationSummary(response: BuilderResponse, lang: "ru" | "en"): string {
  const validationErrorsCount = response.validation_errors?.length ?? 0;
  const activityIssuesCount = response.draft_flow?.activities.reduce((count, activity) => {
    const errors = Array.isArray(activity.errors) ? activity.errors.length : 0;
    const warnings = Array.isArray(activity.warnings) ? activity.warnings.length : 0;
    return count + errors + warnings;
  }, 0) ?? 0;
  const totalIssues = validationErrorsCount + activityIssuesCount;

  if (response.status === "error") {
    return totalIssues > 0
      ? (lang === "en" ? `${totalIssues} issue(s) to review` : `${totalIssues} замечаний к проверке`)
      : (lang === "en" ? "Needs review" : "Нужна проверка");
  }
  if (totalIssues > 0) {
    return lang === "en" ? `${totalIssues} checklist issue(s)` : `${totalIssues} замечаний checklist`;
  }
  if (response.draft_flow?.activities?.length || response.campaign_id) {
    return lang === "en" ? "Checklist passed" : "Checklist пройден";
  }
  return lang === "en" ? "Waiting for flow" : "Ожидаем flow";
}

function getResultPanelState(response: BuilderResponse): "success" | "warning" | "pending" {
  const hasValidationErrors = (response.validation_errors?.length ?? 0) > 0;
  const hasActivityIssues = response.draft_flow?.activities.some((activity) =>
    (Array.isArray(activity.errors) && activity.errors.length > 0) ||
    (Array.isArray(activity.warnings) && activity.warnings.length > 0)
  ) ?? false;

  if (response.status === "error" || hasValidationErrors || hasActivityIssues) return "warning";
  if (response.campaign_id || response.draft_flow?.activities?.length) return "success";
  return "pending";
}

function getResultPanelItems(response: BuilderResponse, lang: "ru" | "en"): ResultPanelItem[] {
  const items: ResultPanelItem[] = [
    {
      label: lang === "en" ? "Status" : "Статус",
      value: STATUS_LABELS[lang][response.status] ?? response.status,
    },
    {
      label: lang === "en" ? "Flow summary" : "Сводка flow",
      value: getFlowSummary(response, lang),
    },
  ];

  if (response.campaign_id) {
    items.push({
      label: lang === "en" ? "Campaign" : "Кампания",
      value: `#${response.campaign_id}`,
    });
  } else {
    items.push({
      label: lang === "en" ? "Checklist" : "Checklist",
      value: getValidationSummary(response, lang),
    });
  }

  const missingFields = response.brief_completeness?.missing_fields ?? [];
  items.push({
    label: lang === "en" ? "Brief completeness" : "Полнота brief",
    value: missingFields.length === 0
      ? (lang === "en" ? "Complete" : "Заполнен")
      : (lang === "en" ? `Missing: ${missingFields.join(", ")}` : `Не хватает: ${missingFields.join(", ")}`),
  });

  const assumptions = response.brief_completeness?.assumptions ?? [];
  if (assumptions.length > 0) {
    items.push({
      label: lang === "en" ? "Assumptions" : "Допущения",
      value: assumptions.join("; "),
    });
  }

  return items;
}

function formatDate(value: string, lang: "ru" | "en"): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function responseFromSession(session: BuilderSessionDetail): BuilderResponse | null {
  const lastAssistant = [...session.messages].reverse().find((message) => message.role === "assistant");
  const metadata = lastAssistant?.metadata ?? {};
  return {
    message: lastAssistant?.content ?? "",
    session_id: session.id,
    campaign_id: typeof metadata.campaign_id === "number" ? metadata.campaign_id : session.campaign_id ?? null,
    builder_preferences: metadata.builder_preferences as BuilderResponse["builder_preferences"] ?? null,
    preference_patch: metadata.preference_patch as BuilderResponse["preference_patch"] ?? null,
    draft_flow: metadata.draft_flow as BuilderResponse["draft_flow"] ?? null,
    validation_errors: Array.isArray(metadata.validation_errors) ? metadata.validation_errors : [],
    brief_completeness: metadata.brief_completeness as BuilderResponse["brief_completeness"] ?? null,
    status: session.status as BuilderResponse["status"],
  };
}

export function CampaignBuilderChat({
  onResponse,
  onOpenMonitoring,
  lang = "ru",
  selectedSegment = null,
  variant = "classic",
  demoPlaybook = [],
}: Props) {
  const [lastResponse, setLastResponse] = useState<BuilderResponse | null>(() =>
    readStoredJson<BuilderResponse | null>(BUILDER_RESPONSE_KEY, null),
  );
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(() => readStoredString(BUILDER_SESSION_KEY));
  const [sessions, setSessions] = useState<BuilderSession[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [campaignBrief, setCampaignBrief] = useState<CampaignBrief>(() =>
    readStoredJson<CampaignBrief | null>(BUILDER_BRIEF_KEY, null)
      ?? preferencesToBrief(readStoredJson<BuilderPreferences>(BUILDER_PREFS_KEY, {})),
  );
  const [targetGroupsSource, setTargetGroupsSource] = useState<"audience-builder" | "manual" | null>(null);

  const { messages, loading, error, send, clear, replaceMessages } = useChat({
    endpoint: "/api/builder",
    messageKey: "goal",
    context: DEFAULT_CONTEXT,
    storageKey: BUILDER_MESSAGES_KEY,
    extraPayload: () => ({
      session_id: currentSessionId,
      session_campaign_id: lastResponse?.campaign_id ?? null,
      session_flow_json: lastResponse?.draft_flow
        ? JSON.stringify(lastResponse.draft_flow)
        : null,
      campaign_brief: campaignBrief,
      builder_preferences: briefToPreferences(campaignBrief),
    }),
  });

  const [input, setInput] = useState("");
  const [editingBriefField, setEditingBriefField] = useState<BriefInlineField | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const response = await fetch(`${API_BASE}/api/sessions`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setSessions(await response.json() as BuilderSession[]);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (lastResponse) {
      window.localStorage.setItem(BUILDER_RESPONSE_KEY, JSON.stringify(lastResponse));
    } else {
      window.localStorage.removeItem(BUILDER_RESPONSE_KEY);
    }
    onResponse(lastResponse);
  }, [lastResponse, onResponse]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (currentSessionId) {
      window.localStorage.setItem(BUILDER_SESSION_KEY, currentSessionId);
    } else {
      window.localStorage.removeItem(BUILDER_SESSION_KEY);
    }
  }, [currentSessionId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(BUILDER_BRIEF_KEY, JSON.stringify(campaignBrief));
    window.localStorage.setItem(BUILDER_PREFS_KEY, JSON.stringify(briefToPreferences(campaignBrief)));
  }, [campaignBrief]);

  useEffect(() => {
    if (!selectedSegment) return;
    setCampaignBrief((current) => ({
      ...preferencesToBrief({
        ...briefToPreferences(current),
        ...preferencesFromSelectedSegment(selectedSegment, lang),
      }),
      audience: audienceFromSelectedSegment(selectedSegment, lang),
    }));
    setTargetGroupsSource("audience-builder");
  }, [selectedSegment, lang]);

  const handlePreferenceChange = (key: keyof BuilderPreferences, value: string) => {
    if (key === "targetGroups") {
      setTargetGroupsSource(value.trim() ? "manual" : null);
    }
    setCampaignBrief((current) => {
      const nextBrief = preferencesToBrief({ ...briefToPreferences(current), [key]: value });
      if (key !== "targetGroups") {
        nextBrief.audience = current.audience;
      }
      return nextBrief;
    });
  };

  const toggleChannel = (channelName: string) => {
    const currentChannels = campaignBrief.channels.map((channel) => channel.name).filter(Boolean);
    const channelExists = currentChannels.some((channel) => channel.toLowerCase() === channelName.toLowerCase());
    const nextChannels = channelExists
      ? currentChannels.filter((channel) => channel.toLowerCase() !== channelName.toLowerCase())
      : [...currentChannels, channelName];
    handlePreferenceChange("channels", nextChannels.join(", "));
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const data = await send(text);
    if (data) {
      const builderResponse = data as BuilderResponse;
      setLastResponse(builderResponse);
      setCampaignBrief((current) => mergeResponseBrief(current, builderResponse) ?? current);
      setCurrentSessionId(builderResponse.session_id ?? currentSessionId);
      refreshSessions();
    }
  };

  const handleOpenSession = async (sessionId: string) => {
    setHistoryError(null);
    try {
      const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const session = await response.json() as BuilderSessionDetail;
      const loadedMessages: ChatMessage[] = session.messages
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) => ({ role: message.role as "user" | "assistant", content: message.content }));
      replaceMessages(loadedMessages);
      setCurrentSessionId(session.id);
      const loadedResponse = responseFromSession(session);
      setLastResponse(loadedResponse);
      if (loadedResponse) {
        setCampaignBrief((current) => mergeResponseBrief(current, loadedResponse) ?? current);
      }
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Failed to load session");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    clear();
    setCurrentSessionId(null);
    setLastResponse(null);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_RESPONSE_KEY);
      window.localStorage.removeItem(BUILDER_SESSION_KEY);
    }
  };

  const handleClearAll = () => {
    handleClear();
    setCampaignBrief(EMPTY_CAMPAIGN_BRIEF);
    setTargetGroupsSource(null);
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(BUILDER_BRIEF_KEY);
      window.localStorage.removeItem(BUILDER_PREFS_KEY);
    }
  };

  const handlePrepareBuilderCommand = () => {
    setInput(buildBuilderPrompt(campaignBrief, lang));
  };

  const handleUseSelectedSegment = () => {
    if (!selectedSegment) return;
    const nextPreferences = {
      ...briefToPreferences(campaignBrief),
      ...preferencesFromSelectedSegment(selectedSegment, lang),
    };
    const nextBrief = {
      ...preferencesToBrief(nextPreferences),
      audience: audienceFromSelectedSegment(selectedSegment, lang),
    };
    setCampaignBrief(nextBrief);
    setTargetGroupsSource("audience-builder");
    setInput(buildBuilderPrompt(nextBrief, lang));
  };

  const handleApplyDemoPlaybook = (item: BuilderDemoPlaybookItem) => {
    if (item.prompt) setInput(item.prompt);
  };

  const targetGroupsStatusLabel = variant === "demo" && targetGroupsSource
    ? targetGroupsSource === "audience-builder"
      ? "Applied from Audience Builder"
      : "Edited manually"
    : null;

  const examplesCount = SUGGESTIONS[lang].length + (variant === "demo" ? demoPlaybook.length : 0) + 1;
  const resultPanelState = lastResponse ? getResultPanelState(lastResponse) : "pending";
  const resultPanelItems = lastResponse ? getResultPanelItems(lastResponse, lang) : [];
  const selectedChannelNames = campaignBrief.channels.map((channel) => channel.name).filter(Boolean);
  const selectedChannelNamesLower = selectedChannelNames.map((channel) => channel.toLowerCase());
  const hasExplicitChannels = selectedChannelNames.length > 0;
  const displayedChannelNames = hasExplicitChannels ? selectedChannelNames : ["SMS", "Push"];
  const audienceSummary = getAudienceSummary(campaignBrief, lang);
  const audienceFullCriteria = getAudienceFullCriteria(campaignBrief, lang);
  const constraintsSummary = getConstraintSummary(campaignBrief);

  return (
    <div className="fw-builder-chat">
      <section className="builder-brief-summary" aria-label={lang === "en" ? "Builder summary" : "Краткое описание Builder"}>
        <strong>Campaign Builder</strong>
        <p>
          {lang === "en"
            ? "Describe product, audience, content and goal step-by-step, then ask the builder to assemble or refine a draft flow."
            : "Опишите продукт, аудиторию, контент и цель по шагам, затем попросите собрать или доработать draft flow."}
        </p>
      </section>

      <section className="builder-brief-card" aria-label={lang === "en" ? "Campaign brief" : "Brief кампании"}>
        <div className="builder-brief-card-header">
          <div>
            <span>{lang === "en" ? "Compact brief" : "Компактный brief"}</span>
            <strong>{lang === "en" ? "Campaign inputs" : "Вводные кампании"}</strong>
          </div>
          {hasBrief(campaignBrief) && <em>{lang === "en" ? "filled" : "заполнено"}</em>}
        </div>

        <div className="builder-brief-lines">
          <div className="builder-brief-line">
            <span className="builder-brief-label">{lang === "en" ? "Goal" : "Цель"}</span>
            {editingBriefField === "goal" ? (
              <input
                autoFocus
                value={campaignBrief.goal ?? ""}
                onChange={(e) => handlePreferenceChange("goal", e.target.value)}
                onBlur={() => setEditingBriefField(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setEditingBriefField(null);
                  if (e.key === "Escape") setEditingBriefField(null);
                }}
                placeholder={lang === "en" ? "upsell, retention, activation…" : "апсейл, удержание, активация…"}
              />
            ) : (
              <button type="button" className="builder-brief-value" onClick={() => setEditingBriefField("goal")}>
                {campaignBrief.goal || (lang === "en" ? "Add goal" : "Укажите цель")}
              </button>
            )}
          </div>

          <div className="builder-brief-line">
            <span className="builder-brief-label">{lang === "en" ? "Product / offer" : "Продукт / оффер"}</span>
            {editingBriefField === "product" ? (
              <input
                autoFocus
                value={campaignBrief.product ?? ""}
                onChange={(e) => handlePreferenceChange("product", e.target.value)}
                onBlur={() => setEditingBriefField(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setEditingBriefField(null);
                  if (e.key === "Escape") setEditingBriefField(null);
                }}
                placeholder={lang === "en" ? "e.g. Family Max tariff" : "Напр. тариф Family Max"}
              />
            ) : (
              <button type="button" className="builder-brief-value" onClick={() => setEditingBriefField("product")}>
                {campaignBrief.product || (lang === "en" ? "Add product or offer" : "Укажите продукт или оффер")}
              </button>
            )}
          </div>

          <div className="builder-brief-line audience">
            <span className="builder-brief-label">{lang === "en" ? "Audience" : "Аудитория"}</span>
            {editingBriefField === "audience" ? (
              <input
                autoFocus
                value={campaignBrief.audience.description ?? campaignBrief.audience.target_groups.join(", ")}
                onChange={(e) => handlePreferenceChange("targetGroups", e.target.value)}
                onBlur={() => setEditingBriefField(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setEditingBriefField(null);
                  if (e.key === "Escape") setEditingBriefField(null);
                }}
                placeholder={lang === "en" ? "low ARPU, data users…" : "низкий ARPU, пользователи data…"}
              />
            ) : (
              <div className="builder-brief-audience-value">
                <button type="button" className="builder-brief-value" onClick={() => setEditingBriefField("audience")}>
                  {audienceSummary || (lang === "en" ? "Add audience" : "Укажите аудиторию")}
                </button>
                {audienceFullCriteria.length > 0 && (
                  <details className="builder-audience-criteria">
                    <summary>{lang === "en" ? "Full criteria" : "Полный критерий"}</summary>
                    <ul>
                      {audienceFullCriteria.map((criterion) => (
                        <li key={criterion}>{criterion}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </div>

          <div className="builder-brief-line channels">
            <span className="builder-brief-label">{lang === "en" ? "Channels" : "Каналы"}</span>
            <div className="builder-channel-chips" aria-label={lang === "en" ? "Channel selection" : "Выбор каналов"}>
              {["SMS", "Push", "Email"].map((channel) => {
                const selected = hasExplicitChannels
                  ? selectedChannelNamesLower.includes(channel.toLowerCase())
                  : channel === "SMS" || channel === "Push";
                return (
                  <button
                    key={channel}
                    type="button"
                    className={selected ? "selected" : undefined}
                    onClick={() => toggleChannel(channel)}
                  >
                    {channel}
                  </button>
                );
              })}
              {!hasExplicitChannels && (
                <span className="builder-assumption-chip">
                  {lang === "en" ? "assumption" : "допущение"}: {displayedChannelNames.join(" + ")}
                </span>
              )}
            </div>
          </div>

          <div className="builder-brief-line constraints">
            <span className="builder-brief-label">{lang === "en" ? "Constraints" : "Ограничения"}</span>
            {editingBriefField === "constraints" ? (
              <textarea
                autoFocus
                value={campaignBrief.constraints.content ?? ""}
                onChange={(e) => handlePreferenceChange("content", e.target.value)}
                onBlur={() => setEditingBriefField(null)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setEditingBriefField(null);
                }}
                rows={2}
                placeholder={lang === "en" ? "message, tone, mandatory wording" : "текст, тональность, обязательные формулировки"}
              />
            ) : (
              <button type="button" className="builder-brief-value" onClick={() => setEditingBriefField("constraints")}>
                {constraintsSummary || (lang === "en" ? "Add content or offer limits" : "Укажите ограничения")}
              </button>
            )}
          </div>
        </div>
      </section>

      <details className="builder-params-panel builder-params-panel-advanced">
        <summary>
          {lang === "en" ? "Advanced parameters" : "Расширенные параметры"}
          <span>{lang === "en" ? "legacy" : "legacy"}</span>
        </summary>
        <div className="builder-params-grid">
          <label>
            {lang === "en" ? "Product / tariff" : "Продукт / тариф"}
            <input
              value={campaignBrief.product ?? ""}
              onChange={(e) => handlePreferenceChange("product", e.target.value)}
              placeholder={lang === "en" ? "e.g. Family Max tariff" : "Напр. тариф Family Max"}
            />
          </label>
          <label>
            {lang === "en" ? "Campaign goal" : "Цель кампании"}
            <input
              value={campaignBrief.goal ?? ""}
              onChange={(e) => handlePreferenceChange("goal", e.target.value)}
              placeholder={lang === "en" ? "upsell, retention, activation…" : "апсейл, удержание, активация…"}
            />
          </label>
          <label>
            {lang === "en" ? "Channels" : "Каналы"}
            <input
              value={briefToPreferences(campaignBrief).channels ?? ""}
              onChange={(e) => handlePreferenceChange("channels", e.target.value)}
              placeholder={lang === "en" ? "SMS, Push, Email…" : "SMS, Push, Email…"}
            />
          </label>
          <label>
            {variant === "demo" ? (
              <span className="builder-field-label">
                {lang === "en" ? "Target groups" : "Целевые группы"}
                {targetGroupsStatusLabel && (
                  <em className={targetGroupsSource === "manual" ? "manual" : undefined}>
                    {targetGroupsStatusLabel}
                  </em>
                )}
              </span>
            ) : (
              lang === "en" ? "Target groups" : "Целевые группы"
            )}
            <input
              value={campaignBrief.audience.description ?? campaignBrief.audience.target_groups.join(", ")}
              onChange={(e) => handlePreferenceChange("targetGroups", e.target.value)}
              placeholder={lang === "en" ? "low ARPU, data users…" : "низкий ARPU, пользователи data…"}
            />
          </label>
          <label className="builder-params-wide">
            {lang === "en" ? "Content notes" : "Контент / тональность"}
            <textarea
              value={campaignBrief.constraints.content ?? ""}
              onChange={(e) => handlePreferenceChange("content", e.target.value)}
              rows={2}
              placeholder={lang === "en" ? "message, tone, mandatory wording" : "текст, тональность, обязательные формулировки"}
            />
          </label>
          <label className="builder-params-wide">
            {lang === "en" ? "Offer recommendations" : "Рекомендации по офферам"}
            <textarea
              value={campaignBrief.constraints.offer_recommendations ?? ""}
              onChange={(e) => handlePreferenceChange("offerRecommendations", e.target.value)}
              rows={2}
              placeholder={lang === "en" ? "discount, bundle, activation transaction…" : "скидка, пакет, транзакция активации…"}
            />
          </label>
        </div>
      </details>

      {variant === "demo" && lastResponse && (
        <section
          className={`builder-result-panel ${resultPanelState}`}
          aria-label={lang === "en" ? "Builder result" : "Результат Builder"}
        >
          <div className="builder-result-panel-header">
            <div>
              <span>{lang === "en" ? "Last response" : "Последний ответ"}</span>
              <h3>{lang === "en" ? "Campaign assembly result" : "Результат сборки кампании"}</h3>
            </div>
            <strong>
              {resultPanelState === "success"
                ? (lang === "en" ? "Ready" : "Готово")
                : resultPanelState === "warning"
                  ? (lang === "en" ? "Review" : "Проверка")
                  : (lang === "en" ? "Context" : "Контекст")}
            </strong>
          </div>
          {lastResponse.draft_flow && (
            <div className="builder-canvas-hint" role="status">
              <span aria-hidden="true">✓</span>
              {lang === "en" ? "Canvas updated" : "Canvas обновлён"}
            </div>
          )}
          <dl className="builder-result-panel-grid">
            {resultPanelItems.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          {onOpenMonitoring && (
            <div className="builder-result-panel-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => onResponse(lastResponse)}
              >
                {lang === "en" ? "Refresh canvas" : "Обновить canvas"}
                <span>{lang === "en" ? "Uses the current flow" : "Использует текущий flow"}</span>
              </button>
              <button
                type="button"
                onClick={onOpenMonitoring}
                disabled={!lastResponse.draft_flow && !lastResponse.campaign_id}
              >
                {lang === "en" ? "Go to Monitoring" : "Перейти к Monitoring"}
              </button>
            </div>
          )}
        </section>
      )}


      <details className="builder-history-panel">
        <summary>
          {lang === "en" ? "Dialog sessions" : "Диалоги Builder"}
          <span>{sessions.length}</span>
        </summary>
        {historyError && <p style={{ color: "var(--error)" }}>{historyError}</p>}
        {historyLoading && sessions.length === 0 ? (
          <p>{lang === "en" ? "Loading sessions…" : "Загружаем диалоги…"}</p>
        ) : sessions.length === 0 ? (
          <p>{lang === "en" ? "No backend sessions yet. Local messages are used as offline fallback." : "Пока нет backend-сессий. Локальные сообщения используются как offline fallback."}</p>
        ) : (
          <div className="builder-history-list">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => handleOpenSession(session.id)}
                title={session.title}
                className={session.id === currentSessionId ? "active" : undefined}
              >
                <strong>{session.title}</strong>
                <span>
                  Campaign {session.campaign_id ? `#${session.campaign_id}` : "—"} · {STATUS_LABELS[lang][session.status] ?? session.status} · {formatDate(session.updated_at, lang)}
                </span>
              </button>
            ))}
          </div>
        )}
      </details>

      <details className="builder-examples-panel">
        <summary>
          {lang === "en" ? "Examples" : "Примеры"}
          <span>{examplesCount}</span>
        </summary>
        <div className="builder-examples-body">
          <div className="fw-suggestions-title">
            {lang === "en" ? "Useful prompts" : "Полезные команды"}
          </div>
          <div className="fw-suggestions-grid">
            <button
              className="fw-suggestion"
              onClick={handlePrepareBuilderCommand}
              disabled={loading}
              type="button"
            >
              {lang === "en" ? "Build draft flow from current campaign parameters" : "Собрать draft flow из текущих параметров кампании"}
            </button>
            {SUGGESTIONS[lang].map((suggestion) => (
              <button
                key={suggestion}
                className="fw-suggestion"
                onClick={() => setInput(suggestion)}
                disabled={loading}
                type="button"
              >
                {suggestion}
              </button>
            ))}
          </div>
          {variant === "demo" && demoPlaybook.length > 0 && (
            <>
              <div className="fw-suggestions-title">
                {lang === "en" ? "Demo playbook" : "Demo playbook"}
              </div>
              <div className="fw-suggestions-grid">
                {demoPlaybook.map((item) => (
                  <button
                    key={`${item.label}-${item.prompt ?? "demo"}`}
                    className="fw-suggestion"
                    onClick={() => handleApplyDemoPlaybook(item)}
                    disabled={loading || !item.prompt}
                    type="button"
                  >
                    <strong>{item.label}</strong>
                    {item.description && <span>{item.description}</span>}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </details>

      {/* Message feed */}
      <div className="message-feed">
        {messages.length === 0 && !loading && (
          <div className="fw-empty-state">
            <p>
              {lang === "en"
                ? "Start with one message, or open Examples for optional prompts."
                : "Начните с одного сообщения или откройте «Примеры» для подсказок."}
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message${msg.role === "user" ? " user" : ""}`}>
            {msg.role === "assistant" ? (
              <MarkdownText content={msg.content} />
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
        ))}

        {loading && (
          <div className="message">
            <div className="loading"><span /><span /><span /></div>
          </div>
        )}

        {error && (
          <div className="message" style={{ borderColor: "var(--error)", fontSize: 12, color: "var(--error)" }}>
            {error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Status bar */}
      {(lastResponse?.campaign_id || messages.length > 0 || hasBrief(campaignBrief)) && (
        <div className="fw-statusbar">
          {lastResponse?.campaign_id ? (
            <>
              <span>
                Campaign{" "}
                <code style={{ background: "#eef2ff", color: "#5257ff", padding: "1px 5px", borderRadius: 3, fontWeight: 700 }}>
                  #{lastResponse.campaign_id}
                </code>
              </span>
              <span style={{ color: STATUS_COLORS[lastResponse.status] ?? "inherit", fontWeight: 600, fontSize: 12 }}>
                {STATUS_LABELS[lang][lastResponse.status] ?? lastResponse.status}
              </span>
            </>
          ) : currentSessionId ? (
            <span>{lang === "en" ? "Backend dialog is loaded" : "Backend-диалог загружен"}</span>
          ) : (
            <span>{lang === "en" ? "Draft context is saved locally" : "Черновой контекст сохранён локально"}</span>
          )}
          {lastResponse && onOpenMonitoring && (
            <button className="fw-clear-btn" onClick={onOpenMonitoring}>
              {lang === "en" ? "Monitoring" : "Monitoring"}
            </button>
          )}
          <button className="fw-clear-btn" onClick={handleClear}>{lang === "en" ? "New chat" : "Новый чат"}</button>
          <button className="fw-clear-btn" onClick={handleClearAll}>{lang === "en" ? "Clear all" : "Очистить всё"}</button>
        </div>
      )}

      {selectedSegment && (
        <section
          className="builder-selected-segment-card"
          aria-label={lang === "en" ? "Selected segment for Builder" : "Выбранный сегмент для Builder"}
        >
          <div>
            <span>{lang === "en" ? "Segment from Audience Builder" : "Сегмент из Audience Builder"}</span>
            <strong>{selectedSegment.hypothesis.name}</strong>
            <small>{getSelectedSegmentMeta(selectedSegment, lang)}</small>
          </div>
          <button type="button" onClick={handleUseSelectedSegment} disabled={loading}>
            {lang === "en" ? "Build draft flow with this segment" : "Собрать draft flow с этим сегментом"}
          </button>
        </section>
      )}

      {/* Composer */}
      <div className="composer" style={{ borderTop: "1px solid var(--border)" }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={lang === "en" ? "Add context or ask to build/refine the draft flow…" : "Добавьте контекст или попросите собрать/доработать draft flow…"}
          rows={1}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>↑</button>
      </div>
    </div>
  );
}
