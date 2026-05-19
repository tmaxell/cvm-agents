/**
 * App — корневой компонент.
 *
 * Архитектура:
 *   1. AdTargetMock (фон, на весь экран) — статичный макет AdTarget
 *      При наличии campaign flow — рендерит его в холсте AdTarget
 *   2. FloatingWidget (поверх, position:fixed, правый нижний угол)
 *      Круглая кнопка + панель (CVM Copilot / Campaign Builder / Monitoring)
 */

import { useState, useCallback, useEffect } from "react";
import { AdTargetMock } from "./components/AdTargetMock";
import { FloatingWidget } from "./components/FloatingWidget";
import { ChatWorkspacePage } from "./pages/chat-workspace/ChatWorkspacePage";
import type {
  BuilderResponse,
  CampaignActionResponse,
  CampaignFlow,
  CampaignRuntimeStatus,
} from "./types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const CHAT_WORKSPACE_ENABLED = (import.meta.env.VITE_CHAT_WORKSPACE_ENABLED ?? "true") !== "false";

function isLegacyMode() {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  return params.get("legacy") === "1" || window.location.pathname.startsWith("/legacy");
}

function isChatWorkspaceRoute() {
  if (typeof window === "undefined") return false;
  return window.location.pathname === "/chat-workspace" || window.location.pathname === "/";
}

export function App() {
  const [legacyMode] = useState(isLegacyMode());

  useEffect(() => {
    if (!CHAT_WORKSPACE_ENABLED || legacyMode || typeof window === "undefined") return;
    if (window.location.pathname === "/") {
      window.history.replaceState({}, "", "/chat-workspace");
    }
  }, [legacyMode]);

  if (CHAT_WORKSPACE_ENABLED && !legacyMode && isChatWorkspaceRoute()) {
    return <ChatWorkspacePage />;
  }

  const [currentFlow, setCurrentFlow] = useState<CampaignFlow | null>(null);
  const [currentResponse, setCurrentResponse] = useState<BuilderResponse | null>(null);
  const [hasErrors, setHasErrors] = useState(false);
  const [campaignStatus, setCampaignStatus] = useState<CampaignRuntimeStatus>("editing");
  const [campaignActionPending, setCampaignActionPending] = useState(false);
  const [campaignActionError, setCampaignActionError] = useState<string | null>(null);

  const handleCampaignAction = useCallback(async (action: "start" | "pause") => {
    const campaignId = currentResponse?.campaign_id;
    if (!campaignId) {
      return;
    }

    setCampaignActionPending(true);
    setCampaignActionError(null);
    try {
      const response = await fetch(`${API_BASE}/api/campaigns/${campaignId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign_id: campaignId,
          review_status: currentResponse?.review_status ?? "blocked",
          review_checklist_acknowledged: Boolean(currentResponse?.review_checklist_acknowledged),
        }),
      });
      if (!response.ok) {
        throw new Error(await extractCampaignActionError(response, action));
      }
      const data = await response.json() as CampaignActionResponse;
      setCampaignStatus(data.status);
    } catch (error) {
      console.error(`Failed to ${action} campaign`, error);
      setCampaignActionError(error instanceof Error ? error.message : getDefaultActionError(action));
    } finally {
      setCampaignActionPending(false);
    }
  }, [currentResponse?.campaign_id]);

  const handleFlowUpdate = useCallback((response: BuilderResponse | null) => {
    if (!response) {
      setCurrentFlow(null);
      setCurrentResponse(null);
      setHasErrors(false);
      setCampaignStatus("editing");
      setCampaignActionError(null);
      return;
    }
    if (currentResponse?.campaign_id !== response.campaign_id) {
      setCampaignStatus("editing");
      setCampaignActionError(null);
    }
    setCurrentResponse(response);
    if (response.draft_flow) {
      setCurrentFlow(response.draft_flow);
      const anyErrors = response.draft_flow.activities?.some(
        a => Array.isArray(a.errors) && a.errors.length > 0
      ) ?? false;
      setHasErrors(anyErrors || response.status === "error");
    }
  }, [currentResponse?.campaign_id]);

  return (
    <>
      {/* AdTarget mock background */}
      <AdTargetMock
        flow={currentFlow}
        campaignId={currentResponse?.campaign_id ?? null}
        campaignStatus={campaignStatus}
        isActionPending={campaignActionPending}
        actionError={campaignActionError}
        canStartCampaign={currentResponse?.review_status === "green" || Boolean(currentResponse?.review_checklist_acknowledged)}
        onStartCampaign={() => handleCampaignAction("start")}
        onPauseCampaign={() => handleCampaignAction("pause")}
      />

      {/* Floating AI widget */}
      <FloatingWidget
        onFlowUpdate={handleFlowUpdate}
        hasErrors={hasErrors}
        builderResponse={currentResponse}
        campaignStatus={campaignStatus}
      />
    </>
  );
}

async function extractCampaignActionError(response: Response, action: "start" | "pause"): Promise<string> {
  const fallback = getDefaultActionError(action);
  const text = await response.text();
  if (!text) {
    return fallback;
  }

  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    return formatErrorDetail(payload.detail) || text;
  } catch {
    return text;
  }
}

function formatErrorDetail(detail: unknown): string | null {
  if (!detail) {
    return null;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (typeof detail !== "object") {
    return String(detail);
  }

  const value = detail as { message?: unknown; errors?: unknown };
  const message = typeof value.message === "string" ? value.message : null;
  const errors = Array.isArray(value.errors)
    ? value.errors
        .flatMap(item => {
          if (item && typeof item === "object" && Array.isArray((item as { errors?: unknown }).errors)) {
            return (item as { errors: unknown[] }).errors.map(String);
          }
          return [String(item)];
        })
        .filter(Boolean)
    : [];

  if (message && errors.length > 0) {
    return `${message}: ${errors.join("; ")}`;
  }
  return message ?? (errors.length > 0 ? errors.join("; ") : JSON.stringify(detail));
}

function getDefaultActionError(action: "start" | "pause"): string {
  return action === "start"
    ? "Не удалось запустить кампанию"
    : "Не удалось поставить кампанию на паузу";
}

export default App;
