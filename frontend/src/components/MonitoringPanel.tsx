/**
 * MonitoringPanel — вкладка «Мониторинг» в FloatingWidget.
 *
 * Показывает метрики кампании и AI-рекомендации по улучшению.
 * Загружает данные с /api/monitor автоматически при появлении campaign_id.
 */

import { useState, useEffect, useCallback } from "react";
import type { CampaignRuntimeStatus, ChannelDeliveryMetric, MonitorMetrics, MonitorResponse } from "../types/api";

interface Props {
  campaignId: number | null;
  draftFlowJson: string | null;
  campaignStatus: CampaignRuntimeStatus;
  onCampaignStatusChange: (status: CampaignRuntimeStatus) => void;
  lang?: "ru" | "en";
}

export function MonitoringPanel({
  campaignId,
  draftFlowJson,
  campaignStatus,
  onCampaignStatusChange,
  lang = "ru",
}: Props) {
  const [data, setData] = useState<MonitorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seed, setSeed] = useState(0);

  const fetchMonitor = useCallback(async (currentSeed: number, statusOverride: CampaignRuntimeStatus = campaignStatus) => {
    if (!campaignId || !draftFlowJson) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/monitor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign_id: campaignId,
          draft_flow_json: draftFlowJson,
          refresh_seed: currentSeed,
          campaign_status: statusOverride,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json = await r.json();
      setData(json as MonitorResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, [campaignId, draftFlowJson, campaignStatus]);

  // Auto-fetch when campaign changes
  useEffect(() => {
    if (campaignId && draftFlowJson) {
      setSeed(0);
      fetchMonitor(0);
    } else {
      setData(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, draftFlowJson, campaignStatus]);

  const handleRefresh = () => {
    const nextSeed = seed + 1;
    setSeed(nextSeed);
    fetchMonitor(nextSeed);
  };

  const handleStart = () => {
    onCampaignStatusChange("active");
    const nextSeed = seed + 1;
    setSeed(nextSeed);
    fetchMonitor(nextSeed);
  };

  const handlePause = () => {
    onCampaignStatusChange("paused");
  };

  if (!campaignId) {
    return (
      <div className="fw-monitor-empty">
        <div style={{ fontSize: 32, marginBottom: 10, opacity: 0.3 }}>📊</div>
        <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          {lang === "en" ? "No active campaign" : "Нет активной кампании"}
        </p>
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
          {lang === "en"
            ? <>Build a campaign in the<br />&ldquo;Campaign Builder&rdquo; tab — data will appear here</>
            : <>Создайте кампанию во вкладке<br />«Campaign Builder» — данные появятся здесь</>}
        </p>
      </div>
    );
  }

  const structureRecommendations = data?.structure_recommendations?.length
    ? data.structure_recommendations
    : data?.recommendations ?? [];
  const launchRecommendations = data?.launch_recommendations ?? [];
  const similarActions = data?.similar_campaign_actions ?? [];
  const hasLaunched = campaignStatus === "active" || campaignStatus === "paused";

  return (
    <div className="fw-monitor">
      {/* Header */}
      <div className="fw-monitor-header">
        <div>
          <span className="fw-monitor-title">{lang === "en" ? "Campaign" : "Кампания"}</span>
          <code className="fw-monitor-campaign-id">#{campaignId}</code>
          <span className={`fw-monitor-status ${campaignStatus}`}>
            {campaignStatus === "editing"
              ? (lang === "en" ? "Editing" : "Editing")
              : campaignStatus === "active"
              ? (lang === "en" ? "Active" : "Active")
              : (lang === "en" ? "Paused" : "Paused")}
          </span>
        </div>
        <div className="fw-monitor-actions">
          <button
            className="fw-monitor-run"
            onClick={handleStart}
            disabled={loading || campaignStatus === "active"}
            title={lang === "en" ? "Start campaign" : "Запустить кампанию"}
          >
            ▶ {lang === "en" ? "Start" : "Запуск"}
          </button>
          <button
            className="fw-monitor-pause"
            onClick={handlePause}
            disabled={campaignStatus !== "active"}
            title={lang === "en" ? "Pause campaign" : "Поставить на паузу"}
          >
            ⏸ {lang === "en" ? "Pause" : "Пауза"}
          </button>
          <button
            className="fw-monitor-refresh"
            onClick={handleRefresh}
            disabled={loading}
            title={lang === "en" ? "Refresh data" : "Обновить данные"}
          >
            {loading ? "…" : "↻"}
          </button>
        </div>
      </div>

      {loading && !data && (
        <div className="fw-monitor-loading">
          <div className="loading"><span /><span /><span /></div>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {lang === "en" ? "Analysing campaign…" : "Анализирую кампанию…"}
          </span>
        </div>
      )}

      {error && (
        <div className="fw-monitor-error">{error}</div>
      )}

      {data && (
        <>
          {/* Score + Summary */}
          <div className="fw-monitor-score-row">
            <ScoreBadge score={data.overall_score} lang={lang} />
            <p className="fw-monitor-summary">{data.summary}</p>
          </div>

          {!hasLaunched && (
            <div className="fw-monitor-prelaunch-note">
              <strong>{lang === "en" ? "Pre-launch mode" : "До запуска"}</strong>
              <span>{lang === "en"
                ? "Metrics will appear after Start is pressed. For now, use these recommendations to improve the flow."
                : "Статистика появится после нажатия «Запуск». Пока здесь — советы по доработке flow."}</span>
            </div>
          )}

          {hasLaunched && (
            <>
              {/* KPI counts */}
              <div className="fw-monitor-kpis">
                <KpiCard
                  label={lang === "en" ? "Activations" : "Активации"}
                  value={data.metrics.activation_count ?? 0}
                  accent="#8b5cf6"
                />
                <KpiCard
                  label={lang === "en" ? "Delivered" : "Доставлено"}
                  value={data.metrics.delivered_count ?? 0}
                  subValue={data.metrics.sent_count ? `${formatNumber(data.metrics.sent_count)} ${lang === "en" ? "sent" : "отправлено"}` : undefined}
                  accent="#22c55e"
                />
              </div>

              {/* Metrics */}
              <div className="fw-monitor-metrics">
                <MetricCard label={lang === "en" ? "Delivery" : "Доставка"} value={data.metrics.delivery_rate} color="#22c55e" benchmark={92} lang={lang} />
                <MetricCard label={lang === "en" ? "Open rate" : "Прочтения"} value={data.metrics.open_rate} color="#3b82f6" benchmark={55} lang={lang} />
                <MetricCard label={lang === "en" ? "Conversion" : "Конверсия"} value={data.metrics.conversion_rate} color="#8b5cf6" benchmark={15} lang={lang} />
                <MetricCard label={lang === "en" ? "Clicks" : "Переходы"} value={data.metrics.click_rate} color="#f59e0b" benchmark={10} lang={lang} />
              </div>

              <Funnel metrics={data.metrics} lang={lang} />

              <ChannelDeliveryList channels={data.metrics.channel_deliveries ?? []} lang={lang} />

              {data.metrics.control_group && (
                <ControlGroupCard comparison={data.metrics.control_group} lang={lang} />
              )}
            </>
          )}

          <RecommendationSection
            icon="🧩"
            title={lang === "en" ? "Flow improvements" : "Доработка flow"}
            recommendations={structureRecommendations}
          />
          <RecommendationSection
            icon="🕘"
            title={lang === "en" ? "Similar past campaigns" : "Похожие прошлые кампании"}
            recommendations={similarActions}
          />
          {hasLaunched && (
            <RecommendationSection
              icon="🚀"
              title={lang === "en" ? "After launch" : "После запуска"}
              recommendations={launchRecommendations}
            />
          )}
        </>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ScoreBadge({ score, lang = "ru" }: { score: number; lang?: "ru" | "en" }) {
  const color = score >= 75 ? "#16a34a" : score >= 55 ? "#d97706" : "#dc2626";
  const label = lang === "en"
    ? (score >= 75 ? "Excellent" : score >= 55 ? "Good" : "Needs improvement")
    : (score >= 75 ? "Отличная" : score >= 55 ? "Хорошая" : "Требует доработки");
  return (
    <div className="fw-monitor-score" style={{ borderColor: color + "40", background: color + "12" }}>
      <span className="fw-monitor-score-num" style={{ color }}>{score}</span>
      <span className="fw-monitor-score-label" style={{ color }}>{label}</span>
    </div>
  );
}

function KpiCard({ label, value, subValue, accent }: {
  label: string; value: number; subValue?: string; accent: string;
}) {
  return (
    <div className="fw-monitor-kpi" style={{ borderLeftColor: accent }}>
      <span className="fw-monitor-kpi-label">{label}</span>
      <strong className="fw-monitor-kpi-value" style={{ color: accent }}>{formatNumber(value)}</strong>
      {subValue && <span className="fw-monitor-kpi-sub">{subValue}</span>}
    </div>
  );
}

function MetricCard({ label, value, color, benchmark, lang = "ru" }: {
  label: string; value: number; color: string; benchmark: number; lang?: "ru" | "en";
}) {
  const pct = Math.min(100, Math.round(value));
  const aboveBenchmark = value >= benchmark;
  return (
    <div className="fw-monitor-metric">
      <div className="fw-monitor-metric-top">
        <span className="fw-monitor-metric-label">{label}</span>
        <span className="fw-monitor-metric-value" style={{ color }}>{value}%</span>
      </div>
      <div className="fw-monitor-bar-track">
        <div
          className="fw-monitor-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
        {/* benchmark line */}
        <div
          className="fw-monitor-bar-benchmark"
          style={{ left: `${benchmark}%` }}
          title={`${lang === "en" ? "Benchmark" : "Бенчмарк"}: ${benchmark}%`}
        />
      </div>
      <div className="fw-monitor-metric-bench">
        <span style={{ color: aboveBenchmark ? "#16a34a" : "#9ca3af", fontSize: 10 }}>
          {aboveBenchmark ? "▲" : "▼"} {lang === "en" ? "bench" : "бенч"}: {benchmark}%
        </span>
      </div>
    </div>
  );
}


function Funnel({ metrics, lang }: { metrics: MonitorMetrics; lang: "ru" | "en" }) {
  const sent = metrics.sent_count ?? 0;
  const delivered = metrics.delivered_count ?? 0;
  const opened = Math.round(delivered * (metrics.open_rate ?? 0) / 100);
  const clicked = Math.round(opened * (metrics.click_rate ?? 0) / 100);
  const activated = metrics.activation_count ?? 0;
  const max = Math.max(sent, delivered, opened, clicked, activated, 1);
  const steps = [
    { label: lang === "en" ? "Sent" : "Отправлено", value: sent, color: "#64748b" },
    { label: lang === "en" ? "Delivered" : "Доставлено", value: delivered, color: "#22c55e" },
    { label: lang === "en" ? "Read/opened" : "Прочитано", value: opened, color: "#3b82f6" },
    { label: lang === "en" ? "Clicked" : "Переходы", value: clicked, color: "#f59e0b" },
    { label: lang === "en" ? "Activated" : "Активации", value: activated, color: "#8b5cf6" },
  ];

  return (
    <section className="fw-monitor-section">
      <div className="fw-monitor-section-header">
        <span>🪄 {lang === "en" ? "Campaign funnel" : "Воронка кампании"}</span>
      </div>
      <div className="fw-monitor-funnel">
        {steps.map((step) => (
          <div className="fw-monitor-funnel-step" key={step.label}>
            <div className="fw-monitor-funnel-main">
              <span>{step.label}</span>
              <strong>{formatNumber(step.value)}</strong>
            </div>
            <div className="fw-monitor-funnel-bar">
              <div
                style={{
                  width: `${Math.max(4, Math.round(step.value / max * 100))}%`,
                  background: step.color,
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ChannelDeliveryList({ channels, lang }: { channels: ChannelDeliveryMetric[]; lang: "ru" | "en" }) {
  if (channels.length === 0) return null;
  return (
    <section className="fw-monitor-section">
      <div className="fw-monitor-section-header">
        <span>📨 {lang === "en" ? "Deliveries by channel" : "Доставки по каналам"}</span>
        <span className="fw-monitor-recs-count">{channels.length}</span>
      </div>
      <div className="fw-monitor-channel-list">
        {channels.map((ch, i) => (
          <div className="fw-monitor-channel" key={`${ch.content_type}-${ch.channel_id ?? i}`}>
            <div className="fw-monitor-channel-main">
              <strong>{ch.channel_name}</strong>
              <span>{formatNumber(ch.delivered_count)} / {formatNumber(ch.sent_count)}</span>
            </div>
            <div className="fw-monitor-channel-bar">
              <div style={{ width: `${Math.min(100, ch.delivery_rate)}%` }} />
            </div>
            <span className="fw-monitor-channel-rate">{ch.delivery_rate}%</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function ControlGroupCard({ comparison, lang }: {
  comparison: NonNullable<MonitorResponse["metrics"]["control_group"]>;
  lang: "ru" | "en";
}) {
  const positive = comparison.uplift_pp >= 0;
  return (
    <section className="fw-monitor-control">
      <div className="fw-monitor-section-header">
        <span>🧪 {lang === "en" ? "Test vs control" : "Тест vs контроль"}</span>
        <span className={positive ? "fw-monitor-positive" : "fw-monitor-negative"}>
          {positive ? "+" : ""}{comparison.uplift_pp} п.п.
        </span>
      </div>
      <div className="fw-monitor-control-grid">
        <ControlCell label={lang === "en" ? "Test group" : "Тестовая"} value={`${comparison.test_conversion_rate}%`} hint={`${formatNumber(comparison.test_activations)} ${lang === "en" ? "activations" : "активаций"}`} />
        <ControlCell label={lang === "en" ? "Control" : "Контроль"} value={`${comparison.control_conversion_rate}%`} hint={`${formatNumber(comparison.control_activations)} ${lang === "en" ? "activations" : "активаций"}`} />
        <ControlCell label="Uplift" value={`${comparison.uplift_percent}%`} hint={lang === "en" ? "incremental effect" : "инкрементальный эффект"} />
      </div>
      <p className="fw-monitor-control-note">
        {lang === "en"
          ? `${formatNumber(comparison.test_group_size)} clients in test, ${formatNumber(comparison.control_group_size)} in control.`
          : `${formatNumber(comparison.test_group_size)} клиентов в тесте, ${formatNumber(comparison.control_group_size)} в контроле.`}
      </p>
    </section>
  );
}

function ControlCell({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="fw-monitor-control-cell">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  );
}

function RecommendationSection({ icon, title, recommendations }: {
  icon: string; title: string; recommendations: string[];
}) {
  if (!recommendations.length) return null;
  return (
    <section className="fw-monitor-section">
      <div className="fw-monitor-section-header">
        <span>{icon} {title}</span>
        <span className="fw-monitor-recs-count">{recommendations.length}</span>
      </div>
      <ul className="fw-monitor-recs">
        {recommendations.map((rec, i) => (
          <li key={i} className="fw-monitor-rec-item">
            <span className="fw-monitor-rec-num">{i + 1}</span>
            <span>{rec}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}
