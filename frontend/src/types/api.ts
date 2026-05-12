// Типы для API cvm-agents backend

export interface AgentContext {
  screen?: string;
  campaign_id?: number | null;
  strategy_id?: number | null;
  user_role?: string;
  platform_url?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: SourceCitation[];
}

export interface SourceCitation {
  id: string;
  title: string;
  source: string;
  heading_path: string[];
  score: number;
}

// F1 Copilot
export interface CopilotRequest {
  question: string;
  context?: AgentContext;
  history?: { role: "user" | "assistant"; content: string }[];
}

export interface CopilotResponse {
  answer: string;
  citations?: SourceCitation[];
}

// F2 Campaign Builder
export interface BuilderRequest {
  goal: string;
  context?: AgentContext;
  history?: { role: "user" | "assistant"; content: string }[];
  session_campaign_id?: number | null;
  session_flow_json?: string | null;
}

export interface FlowActivity {
  id: string;
  type: string;
  name: string;
  position?: { left: number; top: number };
  nextActivityId?: string | null;
  errors?: unknown[];
  warnings?: unknown[];
  contentType?: string;
  eventCode?: string;
  clientSourceId?: number;
  channelId?: number;
  offerTemplateId?: number;
  // Branching support
  cases?: Record<string, string>;
  defaultSuccessActivityId?: string | null;
  defaultFailActivityId?: string | null;
  // Wait
  waitingPeriod?: { type: string; count: number };
}

export interface CampaignFlow {
  activities: FlowActivity[];
}

export interface BuilderResponse {
  message: string;
  campaign_id?: number | null;
  draft_flow?: CampaignFlow | null;
  validation_errors?: unknown[];
  status: "in_progress" | "created" | "started" | "error";
}

// F3 Campaign Monitor
export interface MonitorRequest {
  campaign_id: number;
  draft_flow_json: string;
  refresh_seed?: number;
}

export interface MonitorMetrics {
  delivery_rate: number;
  open_rate: number;
  conversion_rate: number;
  click_rate: number;
}

export interface MonitorResponse {
  metrics: MonitorMetrics;
  recommendations: string[];
  overall_score: number;
  summary: string;
}
