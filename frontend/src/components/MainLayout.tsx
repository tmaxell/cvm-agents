import { useState } from "react";
import { ChatWorkspaceProvider, useChatWorkspaceStore } from "../chat-workspace/store/chatWorkspaceStore";
import { AdTargetMock } from "./AdTargetMock";
import { FloatingWidget } from "./FloatingWidget";
import { AppErrorBoundary } from "./AppErrorBoundary";
import type { CampaignRuntimeStatus } from "../types/api";

function MainLayoutInner() {
  const { draftFlow } = useChatWorkspaceStore();
  const [campaignStatus, setCampaignStatus] = useState<CampaignRuntimeStatus>("editing");
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleStart = async () => {
    setActionPending(true);
    setActionError(null);
    try {
      setCampaignStatus("active");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Не удалось запустить кампанию");
    } finally {
      setActionPending(false);
    }
  };

  const handlePause = async () => {
    setActionPending(true);
    setActionError(null);
    try {
      setCampaignStatus("paused");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Не удалось поставить на паузу");
    } finally {
      setActionPending(false);
    }
  };

  return (
    <>
      <AdTargetMock
        flow={draftFlow}
        campaignId={null}
        campaignStatus={campaignStatus}
        isActionPending={actionPending}
        actionError={actionError}
        canStartCampaign={Boolean(draftFlow)}
        onStartCampaign={handleStart}
        onPauseCampaign={handlePause}
      />
      <FloatingWidget />
    </>
  );
}

export function MainLayout() {
  return (
    <ChatWorkspaceProvider>
      <AppErrorBoundary title="Ошибка интерфейса">
        <MainLayoutInner />
      </AppErrorBoundary>
    </ChatWorkspaceProvider>
  );
}
