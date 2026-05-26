/**
 * Tiny zero-dependency Markdown renderer for chat bubbles.
 *
 * Why not react-markdown? It pulls in ~200 KB (remark, mdast, micromark) which
 * is wasteful for the handful of patterns LLM replies actually use. This
 * covers the 95% case:
 *
 *   **bold**   *italic*   `inline code`
 *   ```fenced code blocks```
 *   [link text](https://url)
 *   - bullet lists / 1. ordered lists
 *   | tables | with | pipes |
 *   # heading 1 / ## h2 / ### h3
 *   paragraphs separated by blank lines
 *   \n single line break → <br/>
 *
 * Anything not matched is rendered verbatim — never throws on weird input.
 */

import React from "react";

interface MarkdownProps {
  text: string;
  className?: string;
}

export function Markdown({ text, className }: MarkdownProps) {
  if (!text) return null;
  return (
    <div className={className}>
      {renderBlocks(text)}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Block-level pass — split on blank lines, dispatch per block kind.
 * ------------------------------------------------------------------------- */
function renderBlocks(src: string): React.ReactNode[] {
  // Normalize line endings, peel off a code-fence at a time to preserve content.
  const out: React.ReactNode[] = [];
  let buf = src;
  let key = 0;
  while (buf.length > 0) {
    // Fenced code block ```...```
    const fence = buf.match(/^```(\w*)\n([\s\S]*?)```\s*/);
    if (fence) {
      out.push(
        <pre
          key={key++}
          className="my-2 rounded-md bg-gray-900 text-gray-100 text-[12px] p-2 overflow-x-auto font-mono"
        >
          <code>{fence[2]}</code>
        </pre>,
      );
      buf = buf.slice(fence[0].length);
      continue;
    }
    // Heading
    const heading = buf.match(/^(#{1,3})\s+(.+?)(?:\n|$)/);
    if (heading) {
      const level = heading[1].length;
      const cls = level === 1 ? "text-[15px] font-semibold mt-2 mb-1"
                 : level === 2 ? "text-[14px] font-semibold mt-2 mb-1"
                 : "text-[13px] font-semibold mt-1.5 mb-0.5";
      out.push(<div key={key++} className={cls}>{renderInline(heading[2])}</div>);
      buf = buf.slice(heading[0].length);
      continue;
    }
    // Table
    const table = matchTable(buf);
    if (table) {
      out.push(renderTable(table.lines, key++));
      buf = buf.slice(table.length);
      continue;
    }
    // List
    const list = matchList(buf);
    if (list) {
      out.push(renderList(list.items, list.ordered, key++));
      buf = buf.slice(list.length);
      continue;
    }
    // Paragraph (until blank line or block boundary)
    const para = buf.match(/^([\s\S]+?)(?:\n\s*\n|$)/);
    if (para) {
      const para_text = para[1].trim();
      if (para_text) {
        out.push(
          <p key={key++} className="my-1 whitespace-pre-wrap">
            {renderInline(para_text)}
          </p>,
        );
      }
      buf = buf.slice(para[0].length);
      continue;
    }
    // Safety — shouldn't reach here, but bail to avoid infinite loop
    out.push(<span key={key++}>{buf}</span>);
    break;
  }
  return out;
}

/* ---------------------------------------------------------------------------
 * Lists (- / * / 1.) — accept indent up to 4 spaces, single-level only
 * ------------------------------------------------------------------------- */
function matchList(src: string): { items: string[]; ordered: boolean; length: number } | null {
  const m = src.match(/^((?: {0,4}(?:[-*+]|\d+\.) +.+\n?)+)/);
  if (!m) return null;
  const ordered = /^\s*\d+\./.test(m[0]);
  const items = m[0]
    .split(/\n/)
    .map(l => l.replace(/^ {0,4}(?:[-*+]|\d+\.) +/, ""))
    .filter(Boolean);
  return { items, ordered, length: m[0].length };
}

function renderList(items: string[], ordered: boolean, key: number): React.ReactNode {
  const cls = ordered ? "list-decimal" : "list-disc";
  return (
    <ul key={key} className={`${cls} pl-5 my-1 space-y-0.5`}>
      {items.map((it, i) => (
        <li key={i}>{renderInline(it)}</li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------------------------
 * Tables — `| col | col |` with separator row of dashes
 * ------------------------------------------------------------------------- */
function matchTable(src: string): { lines: string[]; length: number } | null {
  const m = src.match(/^(\|[^\n]+\|\n\|[\s-:|]+\|\n(?:\|[^\n]+\|\n?)+)/);
  if (!m) return null;
  return { lines: m[0].split("\n").filter(Boolean), length: m[0].length };
}

function renderTable(lines: string[], key: number): React.ReactNode {
  const cells = (row: string) =>
    row.split("|").slice(1, -1).map(c => c.trim());
  const headers = cells(lines[0]);
  const rows = lines.slice(2).map(cells);
  return (
    <div key={key} className="my-2 overflow-x-auto">
      <table className="text-[12px] border-collapse">
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i} className="border border-gray-300 px-2 py-1 bg-gray-50 text-left font-semibold">
                {renderInline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}>
              {r.map((c, ci) => (
                <td key={ci} className="border border-gray-300 px-2 py-1 align-top">
                  {renderInline(c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Inline — bold, italic, code, links. Processes in one pass so nested cases
 * (e.g. **a `b` c**) emerge correctly.
 * ------------------------------------------------------------------------- */
function renderInline(text: string): React.ReactNode[] {
  // Split on the union of all inline patterns; capture the delimiters so we
  // can rebuild styled spans afterwards.
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\([^)]+\))/g;
  const parts = text.split(pattern);
  const out: React.ReactNode[] = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (!p) continue;
    if (p.startsWith("`") && p.endsWith("`")) {
      out.push(
        <code key={i} className="px-1 py-0.5 rounded bg-gray-200 text-[12px] font-mono text-gray-800">
          {p.slice(1, -1)}
        </code>,
      );
    } else if (p.startsWith("**") && p.endsWith("**")) {
      out.push(<strong key={i}>{p.slice(2, -2)}</strong>);
    } else if ((p.startsWith("*") && p.endsWith("*")) || (p.startsWith("_") && p.endsWith("_"))) {
      out.push(<em key={i}>{p.slice(1, -1)}</em>);
    } else if (p.startsWith("[") && p.includes("](")) {
      const m = p.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (m) {
        out.push(
          <a key={i} href={m[2]} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">
            {m[1]}
          </a>,
        );
      } else {
        out.push(p);
      }
    } else {
      // Plain text — convert single \n to <br/> so manual line breaks survive
      const lines = p.split("\n");
      lines.forEach((line, li) => {
        if (li > 0) out.push(<br key={`${i}-br${li}`} />);
        out.push(line);
      });
    }
  }
  return out;
}
