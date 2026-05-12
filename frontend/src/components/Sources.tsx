import type { SourceCitation } from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export function Sources({ citations }: { citations: SourceCitation[] }) {
  if (!citations || citations.length === 0) return null;

  return (
    <details className="sources">
      <summary>Источники: {citations.length}</summary>
      <div>
        {citations.map((citation) => {
          // source looks like "source-docs/PLATFORM_API.md" or "cvmCopilot-docs/baseinfo.txt"
          // Both map directly to /source-docs/... and /cvmCopilot-docs/... on the backend
          const href = `${API_BASE}/${citation.source}`;
          const headingLabel =
            citation.heading_path.filter(Boolean).join(" / ") || "";

          return (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              key={citation.id}
            >
              <strong>{citation.title}</strong>
              <span>{headingLabel || citation.source}</span>
              <small>score {citation.score.toFixed(2)}</small>
            </a>
          );
        })}
      </div>
    </details>
  );
}
