import type { CampaignFlow, FlowActivity } from "../../types/api";

/**
 * Нормализация upsell-кампании, прочитанной из истории.
 *
 * Старые артефакты (до коммита 481f51c) сохраняли upsell-флоу как линейную
 * цепочку 9 активностей через nextActivityId, без проставленных
 * defaultSuccessActivityId / cases / timeOutNextActivityId и без subNodes
 * (Filter-карточек). Рендерер при таком виде корректно рисует одну колонку —
 * данные действительно линейны, — но визуально это не соответствует
 * фактической структуре кампании (две ветки откликов через OrJoin).
 *
 * Эта функция распознаёт характерную сигнатуру по типам активностей
 * (CommonActivity, TargetGroupActivity, 2× PushCommunicationActivity,
 * 2× ResponseActivity, OrJoinActivity, BusinessTransactionActivity и
 * опционально ExcludeFromCampaignActivity) и пересобирает связи в
 * настоящий DAG:
 *  - SMS offer → Response#1 (через defaultSuccessActivityId)
 *  - Response#1.cases["1"] → OrJoin, timeOutNextActivityId → SMS reminder
 *  - SMS reminder → Response#2, Response#2.cases["1"] → OrJoin
 *  - OrJoin → BT (терминальная нода)
 *  - ExcludeFromCampaignActivity отбрасывается (визуально не нужен).
 *
 * Дополнительно добавляются subNodes (ActivityFilter «Equals [Ок]»),
 * чтобы Filter-карточки появились между Response и OrJoin.
 *
 * Если сигнатура не совпадает — флоу возвращается без изменений.
 */
export function normalizeUpsellFlow(flow: CampaignFlow): CampaignFlow {
  if (!flow || !Array.isArray(flow.activities)) return flow;

  const byType = new Map<string, FlowActivity[]>();
  for (const a of flow.activities) {
    let arr = byType.get(a.type);
    if (!arr) { arr = []; byType.set(a.type, arr); }
    arr.push(a);
  }

  const common = byType.get("CommonActivity") ?? [];
  const tg = byType.get("TargetGroupActivity") ?? [];
  const sms = byType.get("PushCommunicationActivity") ?? [];
  const resp = byType.get("ResponseActivity") ?? [];
  const orJoin = byType.get("OrJoinActivity") ?? [];
  const bt = byType.get("BusinessTransactionActivity") ?? [];

  // Сигнатура upsell-флоу. Используем >= 1/2, а не ===, чтобы покрыть
  // артефакты с лишними нодами (например, второй Exclude или Wait), —
  // главное, чтобы базовая структура была.
  const isUpsell =
    common.length >= 1 &&
    tg.length >= 1 &&
    sms.length >= 2 &&
    resp.length >= 2 &&
    orJoin.length >= 1 &&
    bt.length >= 1;
  if (!isUpsell) return flow;

  // Если связи уже корректно проставлены (новый артефакт) — не трогаем,
  // только подчищаем Exclude и добавляем subNodes если их нет.
  const resp1 = resp[0];
  const resp2 = resp[1];
  const hasProperBranching =
    !!(resp1.cases && Object.keys(resp1.cases).length > 0 && resp1.timeOutNextActivityId)
    && !!(resp2.cases && Object.keys(resp2.cases).length > 0);

  const smsOffer = sms[0];
  const smsReminder = sms[1];
  const or = orJoin[0];
  const btAct = bt[0];

  if (hasProperBranching) {
    const subNodes = flow.subNodes && flow.subNodes.length >= 2 ? flow.subNodes : [
      { id: `${resp1.id}__1`, type: "ActivityFilter" },
      { id: `${resp2.id}__1`, type: "ActivityFilter" },
    ];
    // eslint-disable-next-line no-console
    console.info("[normalizeUpsellFlow] proper-branching: drop Exclude, ensure subNodes");
    return {
      ...flow,
      // Отбрасываем ExcludeFromCampaignActivity, если он остался в активностях
      // (старый артефакт мог его содержать).
      activities: flow.activities.filter(a => a.type !== "ExcludeFromCampaignActivity"),
      subNodes,
    };
  }
  // eslint-disable-next-line no-console
  console.info("[normalizeUpsellFlow] linear legacy → rebuild DAG, drop Exclude");

  // Старый линейный артефакт — пересобираем DAG.
  const defaultFilter = [{
    type: "CalculatedResponseFilter",
    function: "Equals",
    arguments: ["Ок"],
    index: 1,
  }];

  const sanitize = (a: FlowActivity, patch: Partial<FlowActivity>): FlowActivity => {
    const next: FlowActivity = {
      ...a,
      nextActivityId: null,
      defaultSuccessActivityId: null,
      defaultFailActivityId: null,
      cases: undefined,
      timeOutNextActivityId: null,
      ...patch,
    };
    return next;
  };

  const newActivities: FlowActivity[] = [
    sanitize(common[0], { nextActivityId: tg[0].id }),
    sanitize(tg[0], { nextActivityId: smsOffer.id }),
    sanitize(smsOffer, { defaultSuccessActivityId: resp1.id }),
    sanitize(resp1, {
      cases: { "1": or.id },
      timeOutNextActivityId: smsReminder.id,
      filters: resp1.filters ?? defaultFilter,
      linkedCommunicationActivities: [smsOffer.id],
    }),
    sanitize(smsReminder, {
      defaultSuccessActivityId: resp2.id,
      isNotification: true,
    }),
    sanitize(resp2, {
      cases: { "1": or.id },
      filters: resp2.filters ?? defaultFilter,
      linkedCommunicationActivities: [smsReminder.id],
    }),
    sanitize(or, { nextActivityId: btAct.id }),
    sanitize(btAct, {}),
    // ExcludeFromCampaignActivity отбрасывается сознательно.
  ];

  return {
    ...flow,
    activities: newActivities,
    subNodes: [
      { id: `${resp1.id}__1`, type: "ActivityFilter" },
      { id: `${resp2.id}__1`, type: "ActivityFilter" },
    ],
  };
}
