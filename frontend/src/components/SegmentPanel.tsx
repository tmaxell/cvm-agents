import { useMemo, useState } from "react";
import type {
  SegmentHypothesis,
  SegmentSuggestRequest,
  SegmentSuggestResponse,
  SelectedSegmentForBuilder,
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

interface SegmentPanelProps {
  lang?: "ru" | "en";
  onUseInBuilder?: () => void;
  onSegmentSelected?: (segment: SelectedSegmentForBuilder) => void;
}

function constraintsToPayload(value: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Plain text constraints are a supported, user-friendly input mode.
  }
  return { note: trimmed };
}

function stringifyCriteria(criteria: Record<string, unknown>): string[] {
  return Object.entries(criteria).map(([key, value]) => {
    if (Array.isArray(value)) return `${key}: ${value.join(", ")}`;
    if (value && typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
    return `${key}: ${String(value)}`;
  });
}

function confidencePercent(value?: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

function targetGroupLabel(hypothesis: SegmentHypothesis, lang: "ru" | "en"): string {
  const match = hypothesis.matched_target_group;
  if (!match || !hypothesis.is_existing_target_group) {
    return lang === "en" ? "recommendation only" : "только рекомендация";
  }
  const matchedId = match.id ?? match.target_group_id;
  const id = matchedId != null && matchedId !== "" ? `#${matchedId} · ` : "";
  const size = match.clients_count != null ? ` · ${match.clients_count.toLocaleString()} clients` : "";
  return `${id}${match.name}${size}`;
}


export function SegmentPanel({ lang = "ru", onUseInBuilder, onSegmentSelected }: SegmentPanelProps) {
  const [product, setProduct] = useState("");
  const [campaignGoal, setCampaignGoal] = useState("");
  const [audienceConstraints, setAudienceConstraints] = useState("");
  const [response, setResponse] = useState<SegmentSuggestResponse | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = useMemo(() => product.trim() && campaignGoal.trim() && !loading, [product, campaignGoal, loading]);

  const handleSuggest = async () => {
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    setSelectedName(null);
    try {
      const payload: SegmentSuggestRequest = {
        product: product.trim(),
        campaign_goal: campaignGoal.trim(),
        audience_constraints: constraintsToPayload(audienceConstraints),
      };
      const result = await fetch(`${API_BASE}/api/segments/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!result.ok) {
        const detail = await result.text();
        throw new Error(`HTTP ${result.status}: ${detail.slice(0, 180)}`);
      }
      setResponse(await result.json() as SegmentSuggestResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : (lang === "en" ? "Unknown error" : "Неизвестная ошибка"));
    } finally {
      setLoading(false);
    }
  };

  const handleUseInBuilder = (hypothesis: SegmentHypothesis) => {
    onSegmentSelected?.({
      product: product.trim(),
      goal: campaignGoal.trim(),
      hypothesis,
      recommendationOnly: response?.recommendation_only,
    });
    setSelectedName(hypothesis.name);
    onUseInBuilder?.();
  };

  return (
    <div className="fw-segments">
      <div className="fw-segments-form">
        <div>
          <h2>{lang === "en" ? "Segment suggestions" : "Подбор сегментов"}</h2>
          <p>
            {lang === "en"
              ? "Describe a product and goal to get 2–3 audience hypotheses mapped to existing Target Groups when possible."
              : "Опишите продукт и цель, чтобы получить 2–3 гипотезы аудитории с привязкой к Target Groups, если есть совпадение."}
          </p>
        </div>
        <label>
          {lang === "en" ? "Product" : "Продукт"}
          <input
            value={product}
            onChange={(e) => setProduct(e.target.value)}
            placeholder={lang === "en" ? "Family Max tariff" : "Тариф Family Max"}
          />
        </label>
        <label>
          {lang === "en" ? "Campaign goal" : "Цель кампании"}
          <input
            value={campaignGoal}
            onChange={(e) => setCampaignGoal(e.target.value)}
            placeholder={lang === "en" ? "upsell, retention, activation…" : "апсейл, удержание, активация…"}
          />
        </label>
        <label>
          {lang === "en" ? "Audience constraints" : "Ограничения аудитории"}
          <textarea
            value={audienceConstraints}
            onChange={(e) => setAudienceConstraints(e.target.value)}
            rows={3}
            placeholder={lang === "en"
              ? "Exclude recent contacts, opt-out users, age 18+…"
              : "Исключить недавние контакты, opt-out, возраст 18+…"}
          />
        </label>
        <button type="button" className="fw-segments-submit" onClick={handleSuggest} disabled={!canSubmit}>
          {loading ? (lang === "en" ? "Searching…" : "Ищем…") : (lang === "en" ? "Suggest segments" : "Подобрать сегменты")}
        </button>
      </div>

      {error && <div className="fw-segments-error">{error}</div>}

      {response && (
        <div className="fw-segments-results">
          <div className="fw-segments-summary">
            <strong>{response.summary}</strong>
            {response.warnings.map((warning) => <span key={warning}>{warning}</span>)}
          </div>
          {response.hypotheses.map((hypothesis) => {
            const criteria = stringifyCriteria(hypothesis.selection_criteria);
            return (
              <article className="fw-segment-card" key={hypothesis.name}>
                <div className="fw-segment-card-head">
                  <h3>{hypothesis.name}</h3>
                  <span>{confidencePercent(hypothesis.confidence)}</span>
                </div>
                <p>{hypothesis.audience_description}</p>
                <dl>
                  <div>
                    <dt>{lang === "en" ? "Relevance" : "Релевантность"}</dt>
                    <dd>{hypothesis.relevance_reason || "—"}</dd>
                  </div>
                  <div>
                    <dt>{lang === "en" ? "Selection criteria" : "Критерии отбора"}</dt>
                    <dd>{criteria.length ? criteria.join("; ") : "—"}</dd>
                  </div>
                  <div>
                    <dt>{lang === "en" ? "Risk / limitation" : "Риск / ограничение"}</dt>
                    <dd>{hypothesis.risk_or_limitation || "—"}</dd>
                  </div>
                  <div>
                    <dt>Target Group</dt>
                    <dd>{targetGroupLabel(hypothesis, lang)}</dd>
                  </div>
                </dl>
                <button type="button" className="fw-segment-use" onClick={() => handleUseInBuilder(hypothesis)}>
                  {selectedName === hypothesis.name ? "✓ " : ""}
                  {lang === "en" ? "Use in Builder" : "Использовать в Builder"}
                </button>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
