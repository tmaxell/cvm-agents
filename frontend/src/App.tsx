/**
 * App — корневой компонент.
 *
 * Архитектура:
 *   1. AdTargetMock (фон, на весь экран) — статичный макет AdTarget
 *      При наличии campaign flow — рендерит его в холсте AdTarget
 *   2. FloatingWidget (поверх, position:fixed, правый нижний угол)
 *      Круглая кнопка + панель (CVM Copilot / Campaign Builder / Monitoring)
 */

import { useState, useCallback } from "react";
import { AdTargetMock } from "./components/AdTargetMock";
import { FloatingWidget } from "./components/FloatingWidget";
import type {
  BuilderResponse,
  CampaignActionResponse,
  CampaignFlow,
  CampaignRuntimeStatus,
} from "./types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export function App() {
  const [currentFlow, setCurrentFlow] = useState<CampaignFlow | null>(null);
  const [currentResponse, setCurrentResponse] = useState<BuilderResponse | null>(null);
  const [hasErrors, setHasErrors] = useState(false);
  const [campaignStatus, setCampaignStatus] = useState<CampaignRuntimeStatus>("editing");
  const [campaignActionPending, setCampaignActionPending] = useState(false);

  const handleCampaignAction = useCallback(async (action: "start" | "pause") => {
    const campaignId = currentResponse?.campaign_id;
    if (!campaignId) {
      return;
    }

    setCampaignActionPending(true);
    try {
      const response = await fetch(`${API_BASE}/api/campaigns/${campaignId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ campaign_id: campaignId }),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Campaign ${action} failed`);
      }
      const data = await response.json() as CampaignActionResponse;
      setCampaignStatus(data.status);
    } catch (error) {
      console.error(`Failed to ${action} campaign`, error);
      window.alert(
        action === "start"
          ? "Не удалось запустить кампанию"
          : "Не удалось поставить кампанию на паузу"
      );
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
      return;
    }
    if (currentResponse?.campaign_id !== response.campaign_id) {
      setCampaignStatus("editing");
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

export default App;
