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
import type { BuilderResponse, CampaignFlow } from "./types/api";

export function App() {
  const [currentFlow, setCurrentFlow] = useState<CampaignFlow | null>(null);
  const [currentResponse, setCurrentResponse] = useState<BuilderResponse | null>(null);
  const [hasErrors, setHasErrors] = useState(false);

  const handleFlowUpdate = useCallback((response: BuilderResponse | null) => {
    if (!response) {
      setCurrentFlow(null);
      setCurrentResponse(null);
      setHasErrors(false);
      return;
    }
    setCurrentResponse(response);
    if (response.draft_flow) {
      setCurrentFlow(response.draft_flow);
      const anyErrors = response.draft_flow.activities?.some(
        a => Array.isArray(a.errors) && a.errors.length > 0
      ) ?? false;
      setHasErrors(anyErrors || response.status === "error");
    }
  }, []);

  return (
    <>
      {/* AdTarget mock background */}
      <AdTargetMock
        flow={currentFlow}
        campaignId={currentResponse?.campaign_id ?? null}
      />

      {/* Floating AI widget */}
      <FloatingWidget
        onFlowUpdate={handleFlowUpdate}
        hasErrors={hasErrors}
        builderResponse={currentResponse}
      />
    </>
  );
}

export default App;
