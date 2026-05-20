import type { ReactNode } from "react";

/**
 * Минимальный markdown-парсер для сообщений чата.
 * Поддерживает: ### h3, ## h2, # h1, **bold**, `code`, списки (- / *), нумерованные списки (1.),
 * пустые строки как разделители абзацев.
 */
export function MarkdownText({ content }: { content: string }) {
  const blocks = parseBlocks(content);
  return (
    <div className="markdown-body">
      {blocks.map((block, idx) => renderBlock(block, idx))}
    </div>
  );
}

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "code"; text: string };

function parseBlocks(content: string): Block[] {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let current: Block | null = null;
  let inCode = false;
  let codeLines: string[] = [];

  const flush = () => {
    if (current) {
      blocks.push(current);
      current = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine;
    // code fences
    if (/^\s*```/.test(line)) {
      if (inCode) {
        blocks.push({ kind: "code", text: codeLines.join("\n") });
        codeLines = [];
        inCode = false;
      } else {
        flush();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (line.trim() === "") {
      flush();
      continue;
    }

    const headingMatch = /^(#{1,3})\s+(.+)$/.exec(line);
    if (headingMatch) {
      flush();
      blocks.push({ kind: "heading", level: headingMatch[1].length as 1 | 2 | 3, text: headingMatch[2].trim() });
      continue;
    }

    const ulMatch = /^[\s]*[-*]\s+(.+)$/.exec(line);
    if (ulMatch) {
      if (!current || current.kind !== "ul") {
        flush();
        current = { kind: "ul", items: [] };
      }
      current.items.push(ulMatch[1].trim());
      continue;
    }

    const olMatch = /^[\s]*\d+\.\s+(.+)$/.exec(line);
    if (olMatch) {
      if (!current || current.kind !== "ol") {
        flush();
        current = { kind: "ol", items: [] };
      }
      current.items.push(olMatch[1].trim());
      continue;
    }

    if (!current || current.kind !== "paragraph") {
      flush();
      current = { kind: "paragraph", lines: [] };
    }
    current.lines.push(line.trim());
  }

  if (inCode && codeLines.length > 0) {
    blocks.push({ kind: "code", text: codeLines.join("\n") });
  }
  flush();
  return blocks;
}

function renderBlock(block: Block, key: number): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${block.level}` as "h1" | "h2" | "h3";
      return <Tag key={key}>{renderInline(block.text)}</Tag>;
    }
    case "ul":
      return (
        <ul key={key}>
          {block.items.map((item, i) => <li key={i}>{renderInline(item)}</li>)}
        </ul>
      );
    case "ol":
      return (
        <ol key={key}>
          {block.items.map((item, i) => <li key={i}>{renderInline(item)}</li>)}
        </ol>
      );
    case "code":
      return <pre key={key}><code>{block.text}</code></pre>;
    case "paragraph": {
      return (
        <p key={key}>
          {block.lines.map((line, i) => (
            <span key={i}>
              {i > 0 && <br />}
              {renderInline(line)}
            </span>
          ))}
        </p>
      );
    }
  }
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
    return <span key={index}>{part}</span>;
  });
}
