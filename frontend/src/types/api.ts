// Типы для API cvm-agents backend

export interface AgentContext {
  screen?: string;
  campaign_id?: number | null;
  segment_id?: number | null;
  mode?: "general_analysis" | "builder" | "monitoring";
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
export interface BuilderPreferences {
  product?: string;
  goal?: string;
  content?: string;
  channels?: string;
  targetGroups?: string;
  offerRecommendations?: string;
}

export interface CampaignAudienceSelectedSegment {
  hypothesis: {
    name: string;
  };
  selection_criteria: Record<string, unknown>;
  matched_target_group?: MatchedTargetGroup | null;
  is_existing_target_group: boolean;
  risk_or_limitation?: string | null;
  recommendationOnly: boolean;
}

export interface CampaignAudienceRef {
  target_groups: string[];
  description?: string | null;
  selected_segment?: CampaignAudienceSelectedSegment | null;
}

export interface CampaignChannel {
  name: string;
  channel_id?: number | null;
  content_type?: string | null;
}

export interface CampaignConstraints {
  content?: string | null;
  offer_recommendations?: string | null;
}

export interface CampaignBrief {
  product?: string | null;
  goal?: string | null;
  audience: CampaignAudienceRef;
  channels: CampaignChannel[];
  constraints: CampaignConstraints;
}

export type ReviewChecklistCategory = "audience" | "consent" | "contact_policy" | "offer" | "content" | "validation";
export type ReviewChecklistItemStatus = "green" | "warning" | "blocker";
export type ReviewStatus = "green" | "warnings" | "blocked";

export interface ReviewChecklistItem {
  category: ReviewChecklistCategory;
  label: string;
  status: ReviewChecklistItemStatus;
  message: string;
}

export interface ReviewChecklist {
  items: ReviewChecklistItem[];
  status: ReviewStatus;
}

export interface BuilderRequest {
  goal: string;
  session_id?: string | null;
  context?: AgentContext;
  history?: { role: "user" | "assistant"; content: string }[];
  session_campaign_id?: number | null;
  session_flow_json?: string | null;
  draft_flow_version?: number | null;
  campaign_brief?: CampaignBrief;
  builder_preferences?: BuilderPreferences;
  review_checklist_acknowledged?: boolean;
}

export interface BuilderOptimizeRequest {
  session_id: string;
  draft_flow: CampaignFlow;
  campaign_brief?: CampaignBrief;
  draft_flow_version: number;
  validation_errors?: unknown[];
  review_checklist_acknowledged?: boolean;
}

export type BuilderOptimizeResponse = BuilderResponse;


export type BuilderStatus =
  | "collect_brief"
  | "draft_ready"
  | "needs_review"
  | "created_in_adtarget"
  | "running"
  | "error";

export interface BuilderSessionMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  metadata?: Record<string, unknown> | null;
}

export interface BuilderSession {
  id: string;
  campaign_id?: number | null;
  title: string;
  created_at: string;
  updated_at: string;
  status: BuilderStatus | string;
  campaign_brief?: CampaignBrief | null;
  draft_flow?: CampaignFlow | null;
  draft_flow_version?: number | null;
  brief_completeness?: CampaignBriefCompleteness | null;
  review_checklist?: ReviewChecklist | null;
  review_status?: ReviewStatus;
  review_checklist_acknowledged?: boolean;
}

export interface BuilderSessionDetail extends BuilderSession {
  messages: BuilderSessionMessage[];
}

export interface FlowContentParameter {
  name: string;
  value?: string | number | boolean | null;
  valueExpression?: string | null;
}

export interface CampaignOffer {
  id: string;
  activityId?: string;
  channelId?: number;
  contentType?: string;
  text?: string;
  sender?: string;
  offerTemplateId?: number;
  businessOperationId?: string;
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
  content?: {
    type?: string;
    parameters?: FlowContentParameter[];
  };
  eventCode?: string;
  clientSourceId?: number;
  channelId?: number;
  offerTemplateId?: number;
  businessOperation?: { id?: string; parameters?: unknown[] };
  // Branching support
  cases?: Record<string, string>;
  defaultSuccessActivityId?: string | null;
  defaultFailActivityId?: string | null;
  // Response timeout: куда идти, если за timeoutParameters.interval секунд
  // не пришёл отклик (например, на reminder-ветку).
  timeOutNextActivityId?: string | null;
  timeoutParameters?: { interval?: number | null } | null;
  // Какие communication-ноды эта Response слушает (нужно для ActivityFilter).
  linkedCommunicationActivities?: string[];
  // Response.cases ссылается на ActivityFilter по индексу 1..N — описание
  // фильтра по которому матчится отклик (Equals «Ок» и т.п.).
  filters?: Array<{ type?: string; function?: string; arguments?: unknown[]; index?: number }>;
  // Wait
  waitingPeriod?: { type: string; count: number };
  // ExcludeFromCampaign / TransferToCampaign
  removeFromCurrentCampaign?: boolean;
  // Notification flag — отрисовываем reminder отдельным значком.
  isNotification?: boolean;
}

// SubNode — небольшие карточки-фильтры, которые AdTarget рисует между
// Response и его case-таргетом (Filter 1 → Equals [Ок]).
export interface FlowSubNode {
  id: string;       // обычно "<responseId>__<caseIndex>"
  type: string;     // "ActivityFilter"
  position?: { left: number; top: number };
}

export interface CampaignFlow {
  activities: FlowActivity[];
  offers?: CampaignOffer[];
  subNodes?: FlowSubNode[];
}

export type CampaignRuntimeStatus = "editing" | "active" | "paused";

export interface CampaignBriefCompleteness {
  missing_fields: string[];
  assumptions: string[];
  safety_checks: string[];
}

export interface BuilderResponse {
  message: string;
  builder_preferences?: BuilderPreferences | null;
  preference_patch?: Partial<BuilderPreferences> | null;
  session_id?: string | null;
  campaign_id?: number | null;
  draft_flow?: CampaignFlow | null;
  draft_flow_version?: number | null;
  validation_errors?: unknown[];
  brief_completeness?: CampaignBriefCompleteness | null;
  review_checklist?: ReviewChecklist | null;
  review_status: ReviewStatus;
  review_checklist_acknowledged?: boolean;
  status: BuilderStatus;
}

export interface CampaignActionRequest {
  campaign_id: number;
  review_status?: ReviewStatus;
  review_checklist_acknowledged?: boolean;
}

export interface CampaignActionResponse {
  campaign_id: number;
  status: CampaignRuntimeStatus;
  result: unknown;
}


// F4 Segment Suggestions
export interface SegmentSuggestRequest {
  product: string;
  campaign_goal: string;
  audience_constraints?: Record<string, unknown>;
  current_campaign_context?: Record<string, unknown> | null;
}

export interface MatchedTargetGroup {
  id?: number | string | null;
  target_group_id?: number | null;
  name: string;
  clients_count?: number | null;
  match_score: number;
  match_reasons: string[];
}

export interface SegmentHypothesis {
  name: string;
  audience_description: string;
  relevance_reason: string;
  selection_criteria: Record<string, unknown>;
  risk_or_limitation: string;
  matched_target_group?: MatchedTargetGroup | null;
  is_existing_target_group: boolean;
  confidence: number;
  title?: string;
  description?: string;
  rationale?: string;
  product_fit?: string;
  expected_effect?: string;
  audience_filters?: Record<string, unknown>;
  matched_target_groups?: MatchedTargetGroup[];
  exclusions?: string[];
  priority?: number;
}

export interface SelectedSegmentForBuilder {
  product?: string;
  goal?: string;
  hypothesis: SegmentHypothesis;
  recommendationOnly?: boolean;
}

export interface SegmentSuggestResponse {
  summary: string;
  hypotheses: SegmentHypothesis[];
  warnings: string[];
  recommendation_only: boolean;
}

// F3 Campaign Monitor
export interface MonitorRequest {
  campaign_id: number;
  draft_flow_json: string;
  refresh_seed?: number;
  campaign_status?: CampaignRuntimeStatus;
}

export interface ChannelDeliveryMetric {
  channel_id?: number | null;
  channel_name: string;
  content_type: string;
  sent_count: number;
  delivered_count: number;
  delivery_rate: number;
}

export interface ControlGroupComparison {
  test_group_size: number;
  control_group_size: number;
  test_conversion_rate: number;
  control_conversion_rate: number;
  uplift_pp: number;
  uplift_percent: number;
  test_activations: number;
  control_activations: number;
}

export interface MonitorMetrics {
  delivery_rate: number;
  open_rate: number;
  conversion_rate: number;
  click_rate: number;
  sent_count?: number;
  delivered_count?: number;
  opened_count?: number;
  clicked_count?: number;
  activation_count?: number;
  channel_deliveries?: ChannelDeliveryMetric[];
  control_group?: ControlGroupComparison | null;
}

export type OptimizationRecommendationCategory =
  | "channel"
  | "time"
  | "contact_time"
  | "offer"
  | "control_group"
  | "text"
  | "content"
  | "flow"
  | (string & {});

export type OptimizationRecommendationPhase =
  | "pre_launch"
  | "post_launch"
  | "before_launch"
  | "after_launch"
  | (string & {});

export interface OptimizationRecommendation {
  category: OptimizationRecommendationCategory;
  phase: OptimizationRecommendationPhase;
  change: string;
  reason: string;
  expected_effect: string;
  confidence: number | string;
}

export interface MonitorResponse {
  metrics: MonitorMetrics;
  recommendations: string[];
  structure_recommendations?: string[];
  launch_recommendations?: string[];
  similar_campaign_actions?: string[];
  optimization_recommendations?: OptimizationRecommendation[];
  overall_score: number;
  summary: string;
}
