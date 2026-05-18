import { expect, test } from "@playwright/test";

const sessionId = "e2e-builder-session";
const now = "2026-05-18T12:00:00.000Z";

const campaignBrief = {
  product: "Тариф Family Max",
  goal: "Апсейл семейной аудитории",
  audience: {
    target_groups: ["Семейные клиенты Family Max"],
    description: "Семейные клиенты с высоким потенциалом апсейла",
    selected_segment: {
      hypothesis: { name: "Семейные пользователи Family Max" },
      selection_criteria: { product: "Family Max", exclude_recent_contact_days: 7 },
      matched_target_group: {
        id: 42,
        name: "Family Max upsell audience",
        clients_count: 12500,
        match_score: 0.94,
        match_reasons: ["family tariff", "upsell propensity"],
      },
      is_existing_target_group: true,
      risk_or_limitation: "Исключить opt-out и недавние контакты",
      recommendationOnly: false,
    },
  },
  channels: [{ name: "SMS" }, { name: "Push" }],
  constraints: {
    content: "Исключить opt-out и клиентов с контактом за последние 7 дней",
    offer_recommendations: "Семейный апсейл-пакет",
  },
};

const draftFlow = {
  activities: [
    {
      id: "start",
      type: "CommonActivity",
      name: "Family Max upsell",
      position: { left: 120, top: 140 },
      nextActivityId: "sms",
    },
    {
      id: "sms",
      type: "SendSmsActivity",
      name: "SMS offer",
      position: { left: 360, top: 140 },
      channelId: 1,
      contentType: "sms",
      nextActivityId: "push",
    },
    {
      id: "push",
      type: "SendPushActivity",
      name: "Push reminder",
      position: { left: 600, top: 140 },
      channelId: 2,
      contentType: "push",
      nextActivityId: null,
    },
  ],
  offers: [
    {
      id: "family-max-offer",
      activityId: "sms",
      channelId: 1,
      contentType: "sms",
      text: "Подключите Family Max для всей семьи",
    },
  ],
};

const reviewChecklist = {
  status: "blocked",
  items: [
    {
      category: "consent",
      label: "Consent",
      status: "blocker",
      message: "Mock readiness recommendation: verify opt-out exclusions before launch.",
    },
  ],
};

function builderResponse(overrides = {}) {
  return {
    message: "Mock Builder собрал draft flow для Family Max.",
    session_id: sessionId,
    campaign_id: null,
    draft_flow: draftFlow,
    draft_flow_version: 1,
    validation_errors: [],
    brief_completeness: {
      missing_fields: [],
      assumptions: [],
      safety_checks: ["Mock mode: backend side effects are disabled."],
    },
    review_checklist: reviewChecklist,
    review_status: reviewChecklist.status,
    review_checklist_acknowledged: false,
    status: "draft_ready",
    builder_preferences: {
      product: campaignBrief.product,
      goal: campaignBrief.goal,
      targetGroups: campaignBrief.audience.description,
      channels: "SMS, Push",
      content: campaignBrief.constraints.content,
      offerRecommendations: campaignBrief.constraints.offer_recommendations,
    },
    ...overrides,
  };
}

test("segments-to-builder smoke flow works without HTTP 500", async ({ page }) => {
  let createRequested = false;
  let latestBuilderResponse = builderResponse();
  let sessionStatus = "draft_ready";
  let campaignId: number | null = null;

  const sessionSummary = () => [
    {
      id: sessionId,
      campaign_id: campaignId,
      title: "Family Max mock campaign",
      created_at: now,
      updated_at: now,
      status: sessionStatus,
      campaign_brief: campaignBrief,
      draft_flow: draftFlow,
      draft_flow_version: 1,
      brief_completeness: latestBuilderResponse.brief_completeness,
      review_checklist: reviewChecklist,
      review_status: reviewChecklist.status,
      review_checklist_acknowledged: false,
    },
  ];

  const sessionDetail = () => ({
    ...sessionSummary()[0],
    messages: [
      {
        id: "m-user",
        session_id: sessionId,
        role: "user",
        content: "Собрать draft flow с выбранным сегментом",
        created_at: now,
      },
      {
        id: "m-assistant",
        session_id: sessionId,
        role: "assistant",
        content: latestBuilderResponse.message,
        created_at: now,
        metadata: latestBuilderResponse,
      },
    ],
  });

  await page.route("**/api/segments/suggest", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        summary: "Найдены mock-сегменты для Family Max.",
        warnings: [],
        recommendation_only: false,
        hypotheses: [
          {
            name: "Семейные пользователи Family Max",
            audience_description: "Клиенты с семейными тарифами и высоким потенциалом апсейла.",
            relevance_reason: "Соответствует продукту и цели кампании.",
            selection_criteria: { tariff: "Family Max", exclude_recent_contact_days: 7 },
            risk_or_limitation: "Исключить opt-out и клиентов с контактом за последние 7 дней",
            matched_target_group: {
              id: 42,
              name: "Family Max upsell audience",
              clients_count: 12500,
              match_score: 0.94,
              match_reasons: ["family tariff", "upsell propensity"],
            },
            is_existing_target_group: true,
            confidence: 0.94,
          },
        ],
      }),
    });
  });

  await page.route("**/api/builder", async (route) => {
    latestBuilderResponse = builderResponse();
    sessionStatus = "draft_ready";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(latestBuilderResponse),
    });
  });

  await page.route("**/api/builder/create", async (route) => {
    createRequested = true;
    campaignId = 9001;
    sessionStatus = "created_in_adtarget";
    latestBuilderResponse = builderResponse({
      message: "Mock mode: кампания создана в AdTarget, readiness-рекомендации остались предупреждением.",
      campaign_id: campaignId,
      status: "created_in_adtarget",
      review_status: "blocked",
      review_checklist: reviewChecklist,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(latestBuilderResponse),
    });
  });

  await page.route("**/api/sessions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(sessionSummary()),
    });
  });

  await page.route("**/api/sessions/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(sessionDetail()),
    });
  });

  await page.goto("/");
  await page.getByLabel("AI Assistant").click();
  await page.getByRole("button", { name: /Segments/ }).click();

  const segmentsPanel = page.locator(".fw-segments");
  await segmentsPanel.getByLabel("Продукт").fill("Тариф Family Max");
  await segmentsPanel.getByLabel("Цель кампании").fill("Апсейл семейной аудитории");
  await segmentsPanel
    .getByLabel("Ограничения аудитории")
    .fill("Исключить opt-out и клиентов с контактом за последние 7 дней");

  await segmentsPanel.getByRole("button", { name: "Подобрать сегменты" }).click();
  await expect(page.getByText("Семейные пользователи Family Max")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("HTTP 500");

  await page.getByRole("button", { name: "Use in Builder" }).click();
  await expect(page.getByText("Сегмент из Audience Builder")).toBeVisible();

  await page.getByText("Диалоги Builder").click();
  await expect(page.locator(".builder-history-panel")).not.toContainText("HTTP 500");
  await expect(page.getByRole("button", { name: /Family Max mock campaign/ })).toBeVisible();

  await page.getByRole("button", { name: "Собрать draft flow с этим сегментом" }).click();
  await page.locator(".composer button").click();
  await expect(page.getByText("Draft готов")).toBeVisible();
  await expect(page.getByText("Нужно доработать")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("HTTP 500");

  await page.getByText("Actions").click();
  await page.getByRole("button", { name: "Создать кампанию" }).click();
  await expect(page.getByText(/Создана в AdTarget|#9001/)).toBeVisible();
  expect(createRequested).toBe(true);
  await expect(page.locator("body")).not.toContainText("HTTP 500");
});
