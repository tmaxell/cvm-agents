import type { ReactNode } from "react";

export function MarkdownText({ content }: { content: string }) {
  const blocks = content
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  return (
    <div className="markdown-body">
      {blocks.map((block, index) => {
        const lines = block
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);

        if (lines.every((line) => /^[-*]\s+/.test(line))) {
          return (
            <ul key={index}>
              {lines.map((line, i) => (
                <li key={i}>{renderInline(line.replace(/^[-*]\s+/, ""))}</li>
              ))}
            </ul>
          );
        }

        if (lines.every((line) => /^\d+\.\s+/.test(line))) {
          return (
            <ol key={index}>
              {lines.map((line, i) => (
                <li key={i}>{renderInline(line.replace(/^\d+\.\s+/, ""))}</li>
              ))}
            </ol>
          );
        }

        if (/^#{1,3}\s+/.test(block)) {
          const level = Math.min(block.match(/^#+/)?.[0].length ?? 2, 3);
          const text = block.replace(/^#{1,3}\s+/, "");
          const Tag = `h${level}` as "h1" | "h2" | "h3";
          return <Tag key={index}>{renderInline(text)}</Tag>;
        }

        if (/^\*\*.+\*\*$/.test(block) && block.length < 120) {
          return <h3 key={index}>{renderInline(block.replace(/^\*\*|\*\*$/g, ""))}</h3>;
        }

        return (
          <p key={index}>
            {lines.map((line, i) => (
              <span key={i}>
                {i > 0 && <br />}
                {renderInline(line)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}
