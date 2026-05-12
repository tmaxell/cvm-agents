/**
 * MonitoringPanel — вкладка «Мониторинг» в FloatingWidget.
 *
 * Показывает метрики кампании и AI-рекомендации по улучшению.
 * Загружает данные с /api/monitor автоматически при появлении campaign_id.
 */

import { useState, useEffect, useCallback } from "react";
import type { MonitorResponse } from "../types/api";

interface Props {
  campaignId: number | null;
  draftFlowJson: string | null;
  lang?: "ru" | "en";
}

export function MonitoringPanel({ campaignId, draftFlowJson, lang = "ru" }: Props) {
  const [data, setData] = useState<MonitorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seed, setSeed] = useState(0);

  const fetchMonitor = useCallback(async (currentSeed: number) => {
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
  }, [campaignId, draftFlowJson]);

  // Auto-fetch when campaign changes
  useEffect(() => {
    if (campaignId && draftFlowJson) {
      setSeed(0);
      fetchMonitor(0);
    } else {
      setData(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId]);

  const handleRefresh = () => {
    const nextSeed = seed + 1;
    setSeed(nextSeed);
    fetchMonitor(nextSeed);
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

  return (
    <div className="fw-monitor">
      {/* Header */}
      <div className="fw-monitor-header">
        <div>
          <span className="fw-monitor-title">{lang === "en" ? "Campaign" : "Кампания"}</span>
          <code className="fw-monitor-campaign-id">#{campaignId}</code>
        </div>
        <button
          className="fw-monitor-refresh"
          onClick={handleRefresh}
          disabled={loading}
          title={lang === "en" ? "Refresh data" : "Обновить данные"}
        >
          {loading ? "…" : lang === "en" ? "↻ Refresh" : "↻ Обновить"}
        </button>
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

          {/* Metrics */}
          <div className="fw-monitor-metrics">
            <MetricCard label={lang === "en" ? "Delivery" : "Доставка"} value={data.metrics.delivery_rate} color="#22c55e" benchmark={92} lang={lang} />
            <MetricCard label={lang === "en" ? "Open rate" : "Прочтения"} value={data.metrics.open_rate} color="#3b82f6" benchmark={55} lang={lang} />
            <MetricCard label={lang === "en" ? "Conversion" : "Конверсия"} value={data.metrics.conversion_rate} color="#8b5cf6" benchmark={15} lang={lang} />
            <MetricCard label={lang === "en" ? "Clicks" : "Переходы"} value={data.metrics.click_rate} color="#f59e0b" benchmark={10} lang={lang} />
          </div>

          {/* Recommendations */}
          <div className="fw-monitor-recs-header">
            <span>💡 {lang === "en" ? "Recommendations" : "Рекомендации"}</span>
            <span className="fw-monitor-recs-count">{data.recommendations.length}</span>
          </div>
          <ul className="fw-monitor-recs">
            {data.recommendations.map((rec, i) => (
              <li key={i} className="fw-monitor-rec-item">
                <span className="fw-monitor-rec-num">{i + 1}</span>
                <span>{rec}</span>
              </li>
            ))}
          </ul>
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
