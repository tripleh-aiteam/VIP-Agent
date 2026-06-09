"use client";

import { type ReactNode } from "react";

// Dependency-free markdown renderer — GitHub-flavored tables, **bold**, `code`,
// bullet lists, headings and line breaks. Enough to render agent reports.
function inlineFmt(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, k = 0, m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>);
    else out.push(<code key={k++} className="px-1 py-0.5 bg-gray-100 rounded text-[13px] font-mono">{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export default function MarkdownLite({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
  const isSep = (l: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(l) && l.includes("-");
  const cells = (l: string) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
  const blocks: ReactNode[] = [];
  let i = 0, key = 0;
  while (i < lines.length) {
    const line = lines[i];
    // table
    if (isRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const header = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      const hasHeader = header.some(h => h);
      blocks.push(
        <div key={key++} className="overflow-x-auto my-3">
          <table className="w-full text-[13px] border-collapse">
            {hasHeader && (
              <thead><tr>{header.map((h, hi) => (
                <th key={hi} className="border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-left font-semibold text-gray-700">{inlineFmt(h)}</th>
              ))}</tr></thead>
            )}
            <tbody>{rows.map((r, ri) => (
              <tr key={ri} className={ri % 2 ? "bg-gray-50/50" : ""}>{r.map((c, ci) => (
                <td key={ci} className="border border-gray-200 px-2.5 py-1.5">{inlineFmt(c)}</td>
              ))}</tr>
            ))}</tbody>
          </table>
        </div>
      );
      continue;
    }
    // headings
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      const cls = lvl <= 1 ? "text-[20px] font-bold mt-4 mb-2 text-gray-900"
        : lvl === 2 ? "text-[16px] font-bold mt-4 mb-2 text-gray-900"
        : "text-[14px] font-semibold mt-3 mb-1.5 text-gray-800";
      blocks.push(<div key={key++} className={cls}>{inlineFmt(h[2])}</div>);
      i++; continue;
    }
    // bullet list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++; }
      blocks.push(<ul key={key++} className="list-disc ml-5 my-1.5 space-y-1">{items.map((it, ii) => <li key={ii}>{inlineFmt(it)}</li>)}</ul>);
      continue;
    }
    if (line.trim() === "") { blocks.push(<div key={key++} className="h-2.5" />); i++; continue; }
    blocks.push(<div key={key++} className="leading-relaxed mb-1">{inlineFmt(line)}</div>);
    i++;
  }
  return <div className="text-[14px] text-gray-700">{blocks}</div>;
}
