"use client";

/**
 * ChatWorkspace — full-page chat experience inspired by the Law Agent UI:
 *
 * Adapted for Vite + React Router (Stock Advisor uses these instead of
 * Next.js): "use client" removed, useRouter → useNavigate.
 *
 *   ┌─────────────┬───────────────────────────────────────┐
 *   │ Folders /   │     YOUR QUESTION Q1                  │
 *   │ Sessions    │     <user msg>                        │
 *   │ tree        │                                       │
 *   │             │     ASSISTANT · ANSWER  [Download ▼]  │
 *   │ + New chat  │     <markdown answer>                 │
 *   │ + New folder│                                       │
 *   ├─────────────┼───────────────────────────────────────┤
 *   │             │  [+] Ask a follow-up …    [LLM] [🎤] │
 *   └─────────────┴───────────────────────────────────────┘
 *
 * Persistence: sessions + folders live in localStorage under
 *   chat-workspace:<agentId>
 * so each agent has its own history.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { jsPDF } from "jspdf";
import { fetchWithRetry } from "../lib/fetchWithRetry";

// Bump on every user-facing chat-UI change — rendered as a tiny badge above the
// composer so a stale browser tab is diagnosable at a glance.
const UI_BUILD = "ui v08.24-10";

// ── Lightweight markdown renderer (no deps) ───────────────────────────────
// Renders GitHub-flavored tables, **bold**, `code`, bullet lists and line
// breaks so "make a table" actually shows a table (not raw pipes).
function inlineFmt(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Tokens: **bold**, `code`, [label](url) markdown links (http, chart:CODE, or
  // ask:QUESTION), and bare http(s) URLs. chart:CODE opens the TradingView proof
  // panel; ask:Q sends Q as the next chat message (proof-on-click evidence links).
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((?:https?:\/\/[^\s)]+|chart:\d{6}|evidence:\d{6}|ask:[^)]+)\)|https?:\/\/[^\s)]+)/g;
  const link = (href: string, label: string, k: number) => (
    <a key={k} href={href} target="_blank" rel="noopener noreferrer"
       className="text-blue-600 underline break-all hover:text-blue-700">{label}</a>
  );
  const chartLink = (code: string, label: string, k: number) => (
    <a key={k} href="#" role="button"
       onClick={(e) => { e.preventDefault(); try { window.dispatchEvent(new CustomEvent("vip-open-chart", { detail: code })); } catch {} }}
       title="차트 열기 / open chart"
       className="text-blue-600 font-semibold underline decoration-dotted hover:text-blue-800 cursor-pointer">📈{label}</a>
  );
  let last = 0, k = 0, m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={k++} className="px-1 py-0.5 bg-gray-100 rounded text-[13px] font-mono">{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[")) {
      const mc = /^\[([^\]]+)\]\(chart:(\d{6})\)$/.exec(tok);
      const me = /^\[([^\]]+)\]\(evidence:(\d{6})\)$/.exec(tok);
      const ma = /^\[([^\]]+)\]\(ask:([^)]+)\)$/.exec(tok);
      const mm = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(tok);
      if (mc) out.push(chartLink(mc[2], mc[1], k++));
      else if (me) out.push(
        <a key={k++} href="#" role="button"
           onClick={(e) => { e.preventDefault(); try { window.dispatchEvent(new CustomEvent("vip-evidence", { detail: me[2] })); } catch {} }}
           title="근거 데이터 + 차트 (오른쪽 패널) / evidence data + chart (right panel)"
           className="inline-block px-1.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 cursor-pointer text-[13px] no-underline">{me[1]}</a>);
      else if (ma) out.push(
        <a key={k++} href="#" role="button"
           onClick={(e) => { e.preventDefault(); try { window.dispatchEvent(new CustomEvent("vip-ask", { detail: ma[2] })); } catch {} }}
           title={ma[2]}
           className="inline-block px-1.5 rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 cursor-pointer text-[13px] no-underline">{ma[1]}</a>);
      else if (mm) out.push(link(mm[2], mm[1], k++));
      else out.push(tok);
    } else out.push(link(tok, tok, k++));  // bare URL
    last = m.index + tok.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export function MarkdownLite({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
  const isSep = (l: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(l) && l.includes("-");
  const cells = (l: string) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
  const blocks: ReactNode[] = [];
  let i = 0, key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const header = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      blocks.push(
        <div key={key++} className="overflow-x-auto my-2">
          <table className="w-full text-[13px] border-collapse">
            <thead><tr>{header.map((h, hi) => (
              <th key={hi} className="border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-left font-semibold text-gray-700">{inlineFmt(h)}</th>
            ))}</tr></thead>
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
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++; }
      blocks.push(<ul key={key++} className="list-disc ml-5 my-1 space-y-0.5">{items.map((it, ii) => <li key={ii}>{inlineFmt(it)}</li>)}</ul>);
      continue;
    }
    if (line.trim() === "") { blocks.push(<div key={key++} className="h-2" />); i++; continue; }
    blocks.push(<div key={key++} className="whitespace-pre-wrap">{inlineFmt(line)}</div>);
    i++;
  }
  return <div>{blocks}</div>;
}

// Inline so ChatWorkspace can be dropped into any of the 3 agent apps
// (VIP, Realty, Asset) without cross-importing AssistantCard.
export interface AssistantTurn {
  who: "user" | "assistant";
  text: string;
  ts: number;
  intent?: string;
  tool_used?: string;
  pendingAction?: { query: string; confirmText: string };
  attachmentNames?: string[];
  process?: AgentResponse["process"];   // checklist_reco checking-simulation data
}

interface Session {
  id: string;
  name: string;
  folderId: string;
  createdAt: number;
  updatedAt: number;
  turns: AssistantTurn[];
}

interface Folder {
  id: string;
  name: string;
}

interface WorkspaceStore {
  folders: Folder[];
  sessions: Session[];
  activeSessionId: string | null;
}

interface AvailableModel {
  id: string;
  provider: string;
  real_model: string;
  available: boolean;
  is_new?: boolean;
}

interface AgentResponse {
  reply?: string;
  intent?: string;
  tool_used?: string;
  action?: { type: string; to?: string; external?: boolean; command?: string };
  proposed_action?: { confirm_text?: string; tool?: string; args?: Record<string, unknown> };
  suggestions?: string[];
  // checklist_reco: every candidate's real scores → drives the live checking simulation
  process?: { market?: unknown[]; candidates?: { code: string; name: string; score: number;
              groups?: Record<string, number> }[]; picked?: string[]; n?: number };
}

interface Props {
  apiBase: string;
  agentId: string;
  agentLabel?: string;
}

const DEFAULT_FOLDER_ID = "inbox";
// Chat-history sidebar hidden per user request (2026-07-06). Sessions still persist in
// localStorage — flip to true to bring the rail back.
const SHOW_CHAT_HISTORY = false;

function uid(): string {
  return Math.random().toString(36).slice(2, 11);
}

function loadStore(agentId: string): WorkspaceStore {
  if (typeof window === "undefined") {
    return { folders: [{ id: DEFAULT_FOLDER_ID, name: "Inbox" }], sessions: [], activeSessionId: null };
  }
  try {
    const raw = localStorage.getItem(`chat-workspace:${agentId}`);
    if (raw) {
      const parsed = JSON.parse(raw) as WorkspaceStore;
      if (parsed?.folders && Array.isArray(parsed.folders) && parsed.folders.length > 0) {
        return parsed;
      }
    }
  } catch {}
  return { folders: [{ id: DEFAULT_FOLDER_ID, name: "Inbox" }], sessions: [], activeSessionId: null };
}

function saveStore(agentId: string, store: WorkspaceStore) {
  try {
    localStorage.setItem(`chat-workspace:${agentId}`, JSON.stringify(store));
  } catch {}
}

// ----------------------------------------------------------------------
//  Downloads — DOM-only, no document.write
// ----------------------------------------------------------------------

function escHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function turnsToHtml(turns: AssistantTurn[], title: string): string {
  const body = turns.map((t, i) => {
    const idx = Math.floor(i / 2) + 1;
    if (t.who === "user") {
      return `<div style="margin:20px 0;"><div style="font-size:11px;color:#6b7280;font-weight:bold;">YOUR QUESTION Q${idx}</div><div style="background:#3b82f6;color:#fff;padding:10px 14px;border-radius:12px;display:inline-block;margin-top:4px;max-width:75%;">${escHtml(t.text)}</div></div>`;
    }
    const safe = escHtml(t.text).replace(/\n/g, "<br>");
    return `<div style="margin:20px 0;"><div style="font-size:11px;color:#6b7280;font-weight:bold;">ASSISTANT · ANSWER</div><div style="background:#f3f4f6;color:#111;padding:12px 16px;border-radius:12px;margin-top:4px;line-height:1.6;">${safe}</div></div>`;
  }).join("\n");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escHtml(title)}</title></head><body style="font-family:Arial,sans-serif;max-width:780px;margin:40px auto;padding:0 20px;color:#111;"><h1 style="border-bottom:2px solid #e5e7eb;padding-bottom:8px;">${escHtml(title)}</h1>${body}</body></html>`;
}

function downloadAsWord(turns: AssistantTurn[], title: string) {
  const html = turnsToHtml(turns, title);
  const wrapped =
    "<html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>" +
    "<head><meta charset='utf-8'></head><body>" + html + "</body></html>";
  const blob = new Blob([wrapped], { type: "application/msword" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${title}.doc`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function downloadAsPdf(turns: AssistantTurn[], title: string) {
  // Build a REAL .pdf (selectable text) with jsPDF and OPEN it in a new tab
  // so the user immediately SEES the document — no print dialog. Falls back
  // to a direct file download if the browser blocks the popup.
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 48;
  const maxW = pageW - margin * 2;
  let y = margin;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  const titleLines = doc.splitTextToSize(title, maxW);
  if (y + titleLines.length * 22 + 12 > pageH - margin) { doc.addPage(); y = margin; }
  doc.text(titleLines, margin, y);
  y += titleLines.length * 22;
  doc.setDrawColor(229, 231, 235);
  doc.setLineWidth(1.5);
  doc.line(margin, y, pageW - margin, y);
  y += 22;

  turns.forEach((t, i) => {
    const idx = Math.floor(i / 2) + 1;
    const label = t.who === "user" ? `YOUR QUESTION Q${idx}` : "ASSISTANT · ANSWER";
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9);
    doc.setTextColor(107, 114, 128);
    if (y + 16 > pageH - margin) { doc.addPage(); y = margin; }
    doc.text(label, margin, y);
    y += 14;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);
    const lineH = 16;
    const lines = doc.splitTextToSize(t.text || "", maxW - 24);
    if (t.who === "user") { doc.setTextColor(255, 255, 255); doc.setFillColor(59, 130, 246); }
    else { doc.setTextColor(17, 17, 17); doc.setFillColor(243, 244, 246); }
    let li = 0;
    while (li < lines.length) {
      const remaining = pageH - margin - y;
      let fit = Math.max(1, Math.floor((remaining - 16) / lineH));
      if (fit <= 0) { doc.addPage(); y = margin; fit = Math.max(1, Math.floor((pageH - margin * 2 - 16) / lineH)); }
      const chunk = lines.slice(li, li + fit);
      const boxH = chunk.length * lineH + 14;
      doc.roundedRect(margin, y, maxW, boxH, 8, 8, "F");
      doc.text(chunk, margin + 12, y + 18);
      y += boxH;
      li += fit;
    }
    doc.setTextColor(17, 17, 17);
    y += 16;
  });

  const safeName = (title || "chat").replace(/[^a-z0-9\-_]+/gi, "_").slice(0, 80);
  // Open the generated PDF in a new tab so the user SEES it right away;
  // if the popup is blocked, fall back to a direct download.
  try {
    const blobUrl = doc.output("bloburl") as unknown as string;
    const win = window.open(blobUrl, "_blank");
    if (!win) doc.save(`${safeName}.pdf`);
  } catch {
    doc.save(`${safeName}.pdf`);
  }
}

// ----------------------------------------------------------------------
//  Browser-local offline answer — works with NO internet (no server call)
// ----------------------------------------------------------------------
// The orchestrator-side "No-LLM" mode still needs the network. When the
// connection is actually down we answer right here in the browser from:
//   1. basic small-talk (greetings / capabilities / thanks)
//   2. the page's own rendered menu links (for "open X" navigation)
//   3. a keyword scan of whatever is currently on screen
function localBasicAnswer(qlc: string, ko: boolean): string | null {
  const words = new Set(qlc.match(/[a-z0-9가-힣]+/g) || []);
  const w = (...xs: string[]) => xs.some(x => words.has(x));
  const sub = (...xs: string[]) => xs.some(x => qlc.includes(x));
  if (w("hi", "hello", "hey", "하이", "헬로", "안녕") || sub("안녕하", "good morning", "good afternoon", "good evening", "반가"))
    return ko
      ? "안녕하세요! 인터넷 연결이 없어 오프라인 모드예요. 메뉴 이동이나 현재 화면 내용을 도와드릴 수 있어요."
      : "Hi! I'm in offline mode (no internet). I can open menus and answer from what's on this screen.";
  if (w("thanks", "thank", "thx", "감사", "고마워", "고맙") || sub("thank you", "감사합니다"))
    return ko ? "천만에요!" : "You're welcome!";
  if (w("bye", "goodbye", "잘가") || sub("see you", "안녕히"))
    return ko ? "안녕히 가세요!" : "Goodbye!";
  if (w("help", "도와줘", "도와", "기능", "누구") || sub("what can you do", "who are you", "무엇을 도와", "뭐 할 수", "사용법"))
    return ko
      ? "오프라인에서는 메뉴 열기, 현재 화면 내용 안내, 기본 질문 답변을 할 수 있어요. 인터넷이 연결되면 지식베이스와 AI로 더 자세히 답변드려요."
      : "Offline I can open menus, read the current screen, and answer basic questions. Once you're back online I'll use your knowledge base and the AI.";
  return null;
}

function localOfflineAnswer(q: string, pageCtx: string): { reply: string; navTo?: string } {
  const qlc = q.toLowerCase().trim();
  const ko = /[가-힣]/.test(q);
  const basic = localBasicAnswer(qlc, ko);
  if (basic) return { reply: basic };
  // Navigate using the page's own rendered links (works with no network).
  const navVerb = /\b(open|go to|go|navigate|show)\b/.test(qlc) || /(열어|이동|보여|가줘|가자)/.test(q);
  if (navVerb && typeof document !== "undefined") {
    const links = Array.from(document.querySelectorAll("a[href]")) as HTMLAnchorElement[];
    for (const a of links) {
      const label = (a.textContent || "").trim().toLowerCase();
      const href = a.getAttribute("href") || "";
      if (label.length > 1 && href && qlc.includes(label))
        return { reply: ko ? `${label} 페이지로 이동합니다.` : `Opening ${label}.`, navTo: href };
    }
  }
  // Keyword scan of the current screen.
  if (pageCtx) {
    const terms = qlc.split(/\s+/).filter(t => t.length > 1).slice(0, 6);
    const scored = pageCtx.split("\n").map(l => l.trim()).filter(Boolean)
      .map(l => ({ l, h: terms.filter(t => l.toLowerCase().includes(t)).length }))
      .filter(x => x.h > 0).sort((a, b) => b.h - a.h);
    if (scored.length)
      return {
        reply: (ko ? "현재 화면에서 찾은 내용입니다 (오프라인):\n\n" : "From this page (offline):\n\n")
          + scored.slice(0, 4).map(x => x.l).join("\n").slice(0, 700),
      };
  }
  return {
    reply: ko
      ? "지금은 인터넷 연결이 없어 이 화면과 기본 정보만 사용할 수 있어요. 연결되면 더 자세히 답변드릴게요."
      : "I'm offline right now, so I can only use this page and basic info. I'll answer in full once you're back online.",
  };
}

// ----------------------------------------------------------------------
//  Thinking status + natural answer reveal (boss 2026-08-24)
// ----------------------------------------------------------------------

// While the server composes the full answer, rotate human-readable stages so the
// boss knows the bot is working and roughly what it's doing ("when it is thinking
// it should say something ... that we can understand and wait"). Client-side only —
// the backend still returns one complete reply.
function ThinkingStatus({ question }: { question: string }) {
  const ko = /[가-힣]/.test(question || "");
  const stages = ko
    ? ["🔍 질문을 읽고 있어요…", "📊 관련 데이터를 모으는 중…", "🤔 분석하며 생각하는 중…", "✍️ 답변을 정리하는 중…", "⏳ 거의 다 됐어요 — 잠시만요…"]
    : ["🔍 Reading your question…", "📊 Gathering the data…", "🤔 Thinking it through…", "✍️ Writing the answer…", "⏳ Almost there — one moment…"];
  const [i, setI] = useState(0);
  useEffect(() => {
    setI(0);
    const t = setInterval(() => setI(v => Math.min(v + 1, 4)), 3000);
    return () => clearInterval(t);
  }, [question]);
  return <span className="text-[12px] text-gray-500">{stages[Math.min(i, stages.length - 1)]}</span>;
}

// Fresh answers type themselves out (~2.5s) so they arrive naturally instead of
// slamming in as a wall of text. Old turns (reloads, session switches) render
// instantly — the Set remembers which timestamps already animated.
const _revealedTs = new Set<number>();
function RevealMarkdown({ text, ts }: { text: string; ts: number }) {
  const fresh = !_revealedTs.has(ts) && Date.now() - ts < 15000;
  const [n, setN] = useState(fresh ? 0 : (text || "").length);
  useEffect(() => {
    if (!fresh) { setN((text || "").length); return; }
    _revealedTs.add(ts);
    const total = (text || "").length;
    const step = Math.max(12, Math.ceil(total / 90));
    const timer = setInterval(() => {
      setN(v => {
        if (v + step >= total) { clearInterval(timer); return total; }
        return v + step;
      });
    }, 28);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ts]);
  const full = (text || "");
  return <MarkdownLite text={n >= full.length ? full : full.slice(0, n)} />;
}

// A KRX ticker mentioned in an answer, e.g. "삼성바이오로직스 (207940)" — powers the
// "verify on TradingView" proof button (boss 2026-08-24: clicking should open the
// chart inside the chat, left side, to prove the answer right or wrong).
function proofCodeIn(text: string): string | null {
  const m = /\((\d{6})\)/.exec(text || "");
  return m ? m[1] : null;
}

// LIVE CHECKING SIMULATION (boss 2026-08-24: "I wanna see like simulation process to
// proof that our agent is using checklist to decide, to selecting company"): animates
// through every candidate's REAL scores (from the backend's process payload), one by
// one, then collapses to the full scoreboard behind a toggle. Fresh answers animate;
// reloaded history shows the collapsed result instantly.
const _simDone = new Set<number>();
function ChecklistSimulation({ process, ts }: { process: NonNullable<AgentResponse["process"]>; ts: number }) {
  const cands = process.candidates || [];
  const picked = process.picked || [];
  const fresh = !_simDone.has(ts) && Date.now() - ts < 20000;
  const [i, setI] = useState(fresh ? 0 : cands.length);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!fresh) { setI(cands.length); return; }
    _simDone.add(ts);
    const timer = setInterval(() => setI(v => {
      if (v + 1 >= cands.length) { clearInterval(timer); return cands.length; }
      return v + 1;
    }), 140);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ts]);
  if (!cands.length) return null;
  const running = i < cands.length;
  const cur = cands[Math.min(i, cands.length - 1)];
  return (
    <div className="mb-2 rounded-xl border border-blue-200 bg-blue-50/60 px-3 py-2 text-[13px]">
      <div className="flex items-center gap-2 font-semibold text-blue-800">
        {running ? <span className="animate-pulse">🔎</span> : "✅"}
        {running
          ? <>100문항 체크리스트 점검 중… {i + 1}/{cands.length} 종목</>
          : <>100문항 점검 완료 — {cands.length}종목 전수 채점 → 상위 {picked.length} 선정</>}
        {!running && (
          <button onClick={() => setOpen(!open)}
            className="ml-auto text-[11.5px] text-blue-600 underline">{open ? "접기 ▲" : "전 종목 점수 보기 ▼"}</button>
        )}
      </div>
      {running && cur && (
        <div className="mt-1 font-mono text-[12.5px] text-gray-700">
          {cur.name} — 추세 {cur.groups?.trend ?? "-"} · 유동성 {cur.groups?.liquidity ?? "-"} ·
          지지저항 {cur.groups?.levels ?? "-"} · 모멘텀 {cur.groups?.momentum ?? "-"} ·
          수급 {cur.groups?.flows ?? "-"} → 종합 <b>{cur.score}</b>
        </div>
      )}
      {!running && open && (
        <div className="mt-1.5 max-h-56 overflow-y-auto font-mono text-[12px] text-gray-700">
          {cands.map((c2, ci) => (
            <div key={c2.code} className={picked.includes(c2.code) ? "text-emerald-700 font-bold" : ""}>
              {String(ci + 1).padStart(2, " ")}. {c2.name} — {c2.score}{picked.includes(c2.code) ? " ★ 선정" : ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
//  Workspace
// ----------------------------------------------------------------------

export default function ChatWorkspace({ apiBase, agentId, agentLabel }: Props) {
  const router = useRouter();
  const base = apiBase.replace(/\/$/, "");
  const [store, setStore] = useState<WorkspaceStore>(() => loadStore(agentId));
  const [model, setModel] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(`chatbot-${agentId}-model`) || "";
  });
  const [available, setAvailable] = useState<AvailableModel[]>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The question the thinking status narrates.
  const [lastQuestion, setLastQuestion] = useState("");
  // Clickable stock names ([이름](chart:005930) links) open the SAME bottom proof
  // sheet as evidence clicks (boss 2026-08-24: side panels were too small — one big
  // bottom panel with the chart large and the data in readable type).
  useEffect(() => {
    const h = (e: Event) => {
      const code = (e as CustomEvent).detail;
      if (code) setEvidenceCode(String(code));
    };
    window.addEventListener("vip-open-chart", h);
    return () => window.removeEventListener("vip-open-chart", h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Evidence links ([근거](ask:...) in answers) send their question as the next chat
  // message. A ref keeps the CURRENT send() (fresh state) reachable from the one-time
  // listener registration.
  const askSendRef = useRef<(q: string) => void>(() => {});
  askSendRef.current = (q: string) => { void send(q); };
  useEffect(() => {
    const h = (e: Event) => {
      const q = (e as CustomEvent).detail;
      if (q) askSendRef.current(String(q));
    };
    window.addEventListener("vip-ask", h);
    return () => window.removeEventListener("vip-ask", h);
  }, []);
  // Evidence PANEL ([근거](evidence:005930) links): opens on the RIGHT with the
  // TradingView chart + the checklist/daily/minute/volume/news data fetched by code.
  const [evidenceCode, setEvidenceCode] = useState<string | null>(null);
  const [evidenceMd, setEvidenceMd] = useState<string>("");
  const evidenceLangRef = useRef("ko");
  evidenceLangRef.current = /[가-힣]/.test(lastQuestion || "") ? "ko" : "en";
  useEffect(() => {
    const h = (e: Event) => {
      const code = (e as CustomEvent).detail;
      if (code) setEvidenceCode(String(code));
    };
    window.addEventListener("vip-evidence", h);
    return () => window.removeEventListener("vip-evidence", h);
  }, []);
  useEffect(() => {
    if (!evidenceCode) { setEvidenceMd(""); return; }
    let live = true;
    setEvidenceMd("");
    fetchWithRetry(`${base}/chat/reco-evidence/${evidenceCode}?lang=${evidenceLangRef.current}`)
      .then(r => r.json())
      .then(d => { if (live) setEvidenceMd(d?.reply || "데이터를 불러오지 못했습니다 / could not load"); })
      .catch(() => { if (live) setEvidenceMd("데이터를 불러오지 못했습니다 / could not load"); });
    return () => { live = false; };
  }, [evidenceCode, base]);
  // Live connectivity — drives the automatic switch to no-internet mode.
  const [online, setOnline] = useState<boolean>(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  useEffect(() => {
    if (typeof window === "undefined") return;
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [showDownload, setShowDownload] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Message typed/sent while a previous answer is still generating → queued, fired when done.
  const queuedRef = useRef<string | null>(null);

  // --- Voice state ---
  type VoiceState = "idle" | "listening" | "thinking" | "speaking";
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [continuousVoice, setContinuousVoice] = useState(false);
  const continuousVoiceRef = useRef(false);
  useEffect(() => { continuousVoiceRef.current = continuousVoice; }, [continuousVoice]);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadRafRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);

  // Auto-create a session if none exist
  useEffect(() => {
    if (store.sessions.length === 0) {
      const s: Session = {
        id: uid(),
        name: "New chat",
        folderId: store.folders[0]?.id || DEFAULT_FOLDER_ID,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        turns: [],
      };
      const next = { ...store, sessions: [s], activeSessionId: s.id };
      setStore(next);
      saveStore(agentId, next);
    } else if (!store.activeSessionId) {
      const next = { ...store, activeSessionId: store.sessions[0].id };
      setStore(next);
      saveStore(agentId, next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Models
  useEffect(() => {
    fetch(`${base}/api/twins/llm/models`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .catch(() => fetch(`${base}/twins/llm/models`).then(r => r.json()))
      .then((d: { models?: AvailableModel[] }) => {
        const ms = (d?.models || []).filter(m => m.available);
        setAvailable(ms);
      })
      .catch(() => {});
  }, [base]);

  useEffect(() => {
    if (!showModelPicker) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      if (!tgt.closest("[data-llm-picker]")) setShowModelPicker(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [showModelPicker]);

  useEffect(() => {
    if (!showDownload) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      if (!tgt.closest("[data-download-menu]")) setShowDownload(null);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [showDownload]);

  const activeSession = store.sessions.find(s => s.id === store.activeSessionId) || null;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [activeSession?.turns.length]);

  const update = useCallback((mut: (s: WorkspaceStore) => WorkspaceStore) => {
    setStore(prev => {
      const next = mut(prev);
      saveStore(agentId, next);
      return next;
    });
  }, [agentId]);

  function createSession(folderId?: string) {
    const s: Session = {
      id: uid(),
      name: "New chat",
      folderId: folderId || activeSession?.folderId || DEFAULT_FOLDER_ID,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      turns: [],
    };
    update(prev => ({ ...prev, sessions: [s, ...prev.sessions], activeSessionId: s.id }));
  }

  function deleteSession(id: string) {
    if (!window.confirm("Delete this chat?")) return;
    update(prev => {
      const rest = prev.sessions.filter(x => x.id !== id);
      return {
        ...prev,
        sessions: rest,
        activeSessionId: prev.activeSessionId === id ? (rest[0]?.id ?? null) : prev.activeSessionId,
      };
    });
  }

  function renameSession(id: string, name: string) {
    update(prev => ({
      ...prev,
      sessions: prev.sessions.map(s => s.id === id ? { ...s, name: name || s.name } : s),
    }));
  }

  function createFolder() {
    const name = window.prompt("Folder name?", "New folder");
    if (!name) return;
    const f: Folder = { id: uid(), name };
    update(prev => ({ ...prev, folders: [...prev.folders, f] }));
  }

  function deleteFolder(id: string) {
    if (id === DEFAULT_FOLDER_ID) {
      window.alert("Inbox cannot be deleted.");
      return;
    }
    if (!window.confirm("Delete folder and all its chats?")) return;
    update(prev => ({
      ...prev,
      folders: prev.folders.filter(f => f.id !== id),
      sessions: prev.sessions.filter(s => s.folderId !== id),
    }));
  }

  function renameFolder(id: string, name: string) {
    update(prev => ({ ...prev, folders: prev.folders.map(f => f.id === id ? { ...f, name: name || f.name } : f) }));
  }

  async function send(textOverride?: string) {
    const q = (textOverride ?? prompt).trim();
    if (!q || !activeSession) return;
    // Busy? Queue and send when the current answer finishes — don't block typing.
    if (thinking) {
      queuedRef.current = q;
      if (textOverride === undefined) setPrompt("");
      return;
    }
    setThinking(true);
    setError(null);
    setLastQuestion(q);
    if (textOverride === undefined) setPrompt("");

    const userTurn: AssistantTurn = { who: "user", text: q, ts: Date.now() };
    update(prev => ({
      ...prev,
      sessions: prev.sessions.map(s => s.id === activeSession.id
        ? { ...s, turns: [...s.turns, userTurn], updatedAt: Date.now(),
            name: s.turns.length === 0 && q ? q.slice(0, 40) : s.name }
        : s),
    }));

    try {
      // Capture page DOM. /chatbot itself doesn't carry useful page
      // data (it's the chat UI), so if our own capture is thin (<500
      // chars) we fall back to the most recent snapshot the
      // PageSnapshotter wrote to localStorage. That makes ChatWorkspace
      // and the floating AssistantCard answer from the SAME data — fixes
      // the "Dashboard says 55B but /chatbot says 1.4B" inconsistency.
      let pageCtx = "";
      try {
        if (typeof document !== "undefined") {
          const root = (document.querySelector("main") as HTMLElement | null) || document.body;
          const clone = root?.cloneNode(true) as HTMLElement | undefined;
          if (clone) {
            clone.querySelectorAll("[data-assistant-ui], [data-llm-picker], [data-download-menu]").forEach((n) => n.remove());
            clone.querySelectorAll("script, style, svg path, noscript").forEach((n) => n.remove());
            let text = (clone.innerText || clone.textContent || "").trim();
            text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n");
            pageCtx = text.length > 14000 ? text.slice(0, 14000) + "\n…[truncated]" : text;
          }
        }
      } catch {}
      // Fall back to PageSnapshotter cache when our own DOM is thin
      // (e.g. we're on /chatbot which has no useful page data). Only
      // use if recent (< 30 min) so we don't show stale numbers.
      if (pageCtx.length < 500) {
        try {
          if (typeof window !== "undefined") {
            const raw = window.localStorage.getItem(`page-ctx:${agentId}`);
            if (raw) {
              const cached = JSON.parse(raw);
              if (cached?.text && cached?.ts && (Date.now() - cached.ts) < 30 * 60 * 1000) {
                pageCtx = cached.text as string;
              }
            }
          }
        } catch {}
      }

      // No internet → answer locally in the browser (no server call). The
      // server-side "No-LLM" mode can't help here because it's unreachable.
      if (typeof navigator !== "undefined" && !navigator.onLine) {
        const ans = localOfflineAnswer(q, pageCtx);
        const offTurn: AssistantTurn = { who: "assistant", text: ans.reply, ts: Date.now(), intent: "offline-local" };
        update(prev => ({
          ...prev,
          sessions: prev.sessions.map(s => s.id === activeSession.id
            ? { ...s, turns: [...s.turns, offTurn], updatedAt: Date.now() } : s),
        }));
        if (ans.navTo) { try { router.push(ans.navTo); } catch {} }
        setThinking(false);
        return;
      }

      const r = await fetchWithRetry(`${base}/chat/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript: q,
          language: "auto",
          agentId,
          model: model || undefined,
          history: (activeSession.turns || []).slice(-6).map(t => ({
            role: t.who, text: t.text, intent: t.intent,
          })),
          current_path: "/chatbot",
          page_context: pageCtx || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AgentResponse = await r.json();
      const replyText = data.reply || "";
      const assistantTurn: AssistantTurn = {
        who: "assistant",
        text: replyText,
        ts: Date.now(),
        intent: data.intent,
        tool_used: data.tool_used,
        process: data.process,
      };
      update(prev => ({
        ...prev,
        sessions: prev.sessions.map(s => s.id === activeSession.id
          ? { ...s, turns: [...s.turns, assistantTurn], updatedAt: Date.now() }
          : s),
      }));
      const action = data.action;
      if (action?.type === "navigate" && action.to) {
        if (action.external) { try { window.open(action.to, "_blank", "noopener,noreferrer"); } catch {} }
        else { try { router.push(action.to); } catch { /* ignore nav errors */ } }
      }
      // In continuous voice mode, speak the reply, then go back to listening.
      // In single-mic mode (no continuous), do NOT auto-speak — user already
      // sees the text reply on screen.
      if (continuousVoiceRef.current && replyText) {
        speak(replyText, () => {
          if (continuousVoiceRef.current) {
            setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 400);
          }
        });
      }
    } catch (e) {
      // A fetch failure with no connection → fall back to the browser-local
      // offline answer instead of showing an error.
      if (typeof navigator !== "undefined" && !navigator.onLine) {
        const ans = localOfflineAnswer(q, "");
        const offTurn: AssistantTurn = { who: "assistant", text: `${ans.reply}\n\n(📴 offline)`, ts: Date.now(), intent: "offline-local" };
        update(prev => ({
          ...prev,
          sessions: prev.sessions.map(s => s.id === activeSession.id
            ? { ...s, turns: [...s.turns, offTurn], updatedAt: Date.now() } : s),
        }));
        if (ans.navTo) { try { router.push(ans.navTo); } catch {} }
      } else {
        setError(`Failed: ${(e as Error).message || e}`);
      }
    } finally {
      setThinking(false);
      const nextQ = queuedRef.current;
      if (nextQ) {
        queuedRef.current = null;
        setTimeout(() => void send(nextQ), 0);
      }
    }
  }

  // ---------------------------------------------------------------
  //  Voice: TTS
  // ---------------------------------------------------------------

  function speak(text: string, onDone?: () => void) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      onDone?.();
      return;
    }
    setVoiceState("speaking");
    try { speechSynthesis.cancel(); } catch {}
    const u = new SpeechSynthesisUtterance(text);
    const hasKo = /[가-힣]/.test(text);
    u.lang = hasKo ? "ko-KR" : "en-US";
    u.rate = 1.05;
    const v = speechSynthesis.getVoices().find(x => x.lang.startsWith(u.lang));
    if (v) u.voice = v;
    const finish = () => { setVoiceState("idle"); onDone?.(); };
    u.onend = finish;
    u.onerror = finish;
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    try { speechSynthesis.cancel(); } catch {}
    setVoiceState("idle");
  }

  // ---------------------------------------------------------------
  //  Voice: Activity Detection + Recording
  // ---------------------------------------------------------------

  const SILENCE_THRESHOLD = 0.012;
  const SILENCE_MS = 2500;
  const HARD_MAX_MS = 60000;
  const MIN_SPEECH_MS = 800;

  function cleanupVad() {
    if (vadRafRef.current) { cancelAnimationFrame(vadRafRef.current); vadRafRef.current = null; }
    try { audioCtxRef.current?.close(); } catch {}
    audioCtxRef.current = null;
    analyserRef.current = null;
    silenceStartRef.current = null;
  }

  function startVad(stream: MediaStream, onSilence: () => void) {
    try {
      const Ctx = (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext);
      const ctx = new Ctx();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      const buf = new Uint8Array(analyser.fftSize);
      const startedAt = performance.now();
      let everSpoke = false;
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const sample = buf[i];
          if (sample === undefined) continue;
          const v = (sample - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        const elapsed = now - startedAt;
        if (rms >= SILENCE_THRESHOLD) {
          everSpoke = true;
          silenceStartRef.current = null;
        } else if (everSpoke && elapsed > MIN_SPEECH_MS) {
          if (silenceStartRef.current == null) silenceStartRef.current = now;
          else if (now - silenceStartRef.current > SILENCE_MS) {
            onSilence();
            return;
          }
        }
        vadRafRef.current = requestAnimationFrame(tick);
      };
      vadRafRef.current = requestAnimationFrame(tick);
    } catch (e) {
      console.warn("VAD setup failed:", e);
    }
  }

  async function startListening() {
    setError(null);
    if (typeof navigator === "undefined" || !navigator.mediaDevices || !("MediaRecorder" in window)) {
      setError("Voice recording not supported in this browser.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setError("Microphone access denied.");
      return;
    }
    streamRef.current = stream;
    const mimeOpts = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", ""];
    const mime = mimeOpts.find(m => !m || MediaRecorder.isTypeSupported(m)) || "";
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRef.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    recorder.onstop = async () => {
      try { stream.getTracks().forEach(t => t.stop()); } catch {}
      streamRef.current = null;
      mediaRef.current = null;
      cleanupVad();
      if (stopTimerRef.current) { clearTimeout(stopTimerRef.current); stopTimerRef.current = null; }
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      if (blob.size < 1000) {
        if (continuousVoiceRef.current) {
          setVoiceState("idle");
          setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 300);
        } else {
          setError("Audio too short.");
          setVoiceState("idle");
        }
        return;
      }
      setVoiceState("thinking");
      try {
        const fd = new FormData();
        fd.append("file", blob, "voice.webm");
        const r = await fetch(`${base}/chatbot/transcribe`, { method: "POST", body: fd });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const transcript = (data.transcript || "").trim();
        if (!transcript) {
          if (continuousVoiceRef.current) {
            setVoiceState("idle");
            setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 300);
          } else {
            setError("I didn't catch that.");
            setVoiceState("idle");
          }
          return;
        }
        setVoiceState("idle");
        await send(transcript);
      } catch (e: unknown) {
        setError(`Transcription failed: ${(e as Error).message || e}`);
        setVoiceState("idle");
      }
    };
    setVoiceState("listening");
    recorder.start();
    startVad(stream, () => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    });
    stopTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    }, HARD_MAX_MS);
  }

  function stopListening() {
    try { mediaRef.current?.stop(); } catch {}
    cleanupVad();
    if (voiceState === "listening") setVoiceState("idle");
  }

  function startContinuousVoice() {
    setContinuousVoice(true);
    continuousVoiceRef.current = true;
    setTimeout(() => startListening(), 100);
  }

  function endContinuousVoice() {
    setContinuousVoice(false);
    continuousVoiceRef.current = false;
    stopListening();
    stopSpeaking();
  }

  function copyText(text: string) {
    try { navigator.clipboard?.writeText(text); } catch {}
  }

  // --- Self-improvement feedback (👍/👎) ---
  const [feedbackGiven, setFeedbackGiven] = useState<Record<number, "up" | "down">>({});
  async function sendFeedback(turnIdx: number, verdict: "up" | "down") {
    const t = activeSession?.turns[turnIdx];
    if (!t || t.who !== "assistant") return;
    const prev = turnIdx > 0 ? activeSession?.turns[turnIdx - 1] : undefined;
    const question = prev && prev.who === "user" ? prev.text : "";
    let correction: string | undefined;
    if (verdict === "down") {
      correction = window.prompt(
        "What should the answer have been? (helps the assistant learn — leave blank to just flag it)",
      ) || undefined;
    }
    setFeedbackGiven(prevState => ({ ...prevState, [t.ts]: verdict }));
    try {
      await fetch(`${base}/chat/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agentId, question, answer: t.text, verdict, correction }),
      });
    } catch { /* best-effort */ }
  }

  const sessionsByFolder: Record<string, Session[]> = {};
  for (const f of store.folders) sessionsByFolder[f.id] = [];
  for (const s of store.sessions) {
    const fid = sessionsByFolder[s.folderId] ? s.folderId : DEFAULT_FOLDER_ID;
    if (!sessionsByFolder[fid]) sessionsByFolder[fid] = [];
    sessionsByFolder[fid].push(s);
  }

  // Suggested starter prompts for the empty state — agent-specific so
  // the Stock workspace shows "Show me today's investor flow" while
  // VIP shows "Open the dashboard". Localized 2-language hints.
  const examplePrompts: string[] = (() => {
    const id = agentId.toLowerCase();
    if (id === "stock") return ["What moved the market today?", "오늘 외국인 순매수 상위 종목 알려줘", "Should I buy NVDA right now?", "내 거래일지 분석해줘"];
    if (id === "realty") return ["향남 에듀스퀘어 시세 알려줘", "Show me the market dashboard", "현금흐름 계산해줘", "Open evaluate page"];
    if (id === "asset") return ["내 총 자산 알려줘", "Whose lease expires this week?", "Show this month's cashflow", "Any overdue payments?"];
    if (id === "aiglass") return ["Show me today's listings", "고객 리드 상위 5개", "Open dashboard", "Compare properties"];
    return ["What can you do?", "Show me what's on this page", "Summarize my uploaded files", "Help me with my data"];
  })();

  return (
    <div
      data-assistant-ui="workspace"
      className="relative flex w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
      style={{ height: "100%", minHeight: 560 }}
    >
      {/* ========================================================== */}
      {/* === Sidebar: folder/session tree                       === */}
      {/* Chat-history rail hidden per user request (2026-07-06) — sessions still persist  */}
      {/* in localStorage; flip SHOW_CHAT_HISTORY to bring it back.                        */}
      {/* ========================================================== */}
      {SHOW_CHAT_HISTORY && (
      <aside className="hidden md:flex w-[280px] shrink-0 flex-col border-r border-gray-200 bg-gray-50">
        <div className="px-4 py-3.5 border-b border-gray-200 flex items-center gap-2 bg-white">
          <span className="w-8 h-8 rounded-lg bg-gray-900 text-white flex items-center justify-center text-[14px] shrink-0">💬</span>
          <div className="min-w-0">
            <div className="text-[13px] font-bold text-gray-900 truncate">{agentLabel || agentId}</div>
            <div className="text-[10px] text-gray-500">{store.sessions.length} chat{store.sessions.length === 1 ? "" : "s"}</div>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2.5 space-y-1">
          {store.folders.map(f => (
            <div key={f.id}>
              <div className="group flex items-center gap-1 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 hover:bg-gray-100 rounded">
                <span>📂</span>
                {editingFolderId === f.id ? (
                  <input
                    autoFocus
                    defaultValue={f.name}
                    onBlur={e => { renameFolder(f.id, e.target.value); setEditingFolderId(null); }}
                    onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="flex-1 bg-white border border-blue-400 rounded px-1 py-0 text-[11px] outline-none"
                  />
                ) : (
                  <span className="flex-1 truncate">{f.name}</span>
                )}
                <div className="opacity-0 group-hover:opacity-100 flex gap-0.5">
                  <button type="button" onClick={() => createSession(f.id)} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center" title="New chat in folder">📝</button>
                  <button type="button" onClick={() => setEditingFolderId(f.id)} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center" title="Rename folder">✏️</button>
                  {f.id !== DEFAULT_FOLDER_ID && (
                    <button type="button" onClick={() => deleteFolder(f.id)} className="hover:text-red-500 w-5 h-5 flex items-center justify-center" title="Delete folder">🗑️</button>
                  )}
                </div>
              </div>
              <div className="ml-1 mt-0.5 space-y-0.5">
                {(sessionsByFolder[f.id] || []).map(s => {
                  const active = s.id === store.activeSessionId;
                  const lastTurn = s.turns[s.turns.length - 1];
                  return (
                    <div
                      key={s.id}
                      className={`group flex flex-col gap-0.5 px-3 py-2.5 rounded-md cursor-pointer transition-all ${
                        active
                          ? "bg-white text-gray-950 shadow-sm ring-1 ring-gray-200"
                          : "text-gray-700 hover:bg-white hover:shadow-sm"
                      }`}
                      onClick={() => update(prev => ({ ...prev, activeSessionId: s.id }))}
                    >
                      <div className="flex items-center gap-1.5">
                        {editingSessionId === s.id ? (
                          <input
                            autoFocus
                            defaultValue={s.name}
                            onClick={e => e.stopPropagation()}
                            onBlur={e => { renameSession(s.id, e.target.value); setEditingSessionId(null); }}
                            onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                            className="flex-1 bg-white border border-blue-400 rounded px-1 py-0 text-[13px] outline-none"
                          />
                        ) : (
                          <span className={`flex-1 truncate text-[13px] ${active ? "font-semibold text-gray-900" : "text-gray-800"}`}>{s.name}</span>
                        )}
                        <div className="opacity-0 group-hover:opacity-100 flex gap-0.5 shrink-0">
                          <button type="button" onClick={e => { e.stopPropagation(); setEditingSessionId(s.id); }} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center text-[12px]" title="Rename">✏️</button>
                          <button type="button" onClick={e => { e.stopPropagation(); deleteSession(s.id); }} className="hover:text-red-500 w-5 h-5 flex items-center justify-center text-[12px]" title="Delete">🗑️</button>
                        </div>
                      </div>
                      {lastTurn && (
                        <div className="text-[11px] text-gray-500 truncate">
                          {lastTurn.who === "user" ? "You: " : ""}{lastTurn.text}
                        </div>
                      )}
                    </div>
                  );
                })}
                {(sessionsByFolder[f.id] || []).length === 0 && (
                  <div className="px-2.5 py-1.5 text-[11px] text-gray-400 italic">No chats yet</div>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="border-t border-gray-200 bg-white p-3">
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <button
              type="button"
              onClick={() => createSession()}
              className="min-h-9 rounded-md bg-gray-900 px-3 text-[12px] font-semibold text-white shadow-sm transition-colors hover:bg-gray-800"
              title="New chat"
            >+ New chat</button>
            <button
              type="button"
              onClick={createFolder}
              className="min-h-9 rounded-md border border-gray-300 bg-white px-3 text-[12px] font-semibold text-gray-700 transition-colors hover:bg-gray-50"
              title="New folder"
            >+ Folder</button>
          </div>
        </div>
      </aside>
      )}

      {/* ========================================================== */}
      {/* === Main: conversation flow + composer                === */}
      {/* ========================================================== */}
      <main className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Conversation header */}
        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between gap-3 bg-white">
          <div className="min-w-0 flex-1 flex items-center gap-3">
            <button
              onClick={() => createSession()}
              className="md:hidden w-9 h-9 rounded-lg bg-blue-600 text-white text-[16px] flex items-center justify-center shrink-0"
              title="New chat"
            >+</button>
            <div className="min-w-0">
              <div className="text-[15px] font-semibold text-gray-900 truncate">
                {activeSession?.name || "Select a chat"}
              </div>
              <div className="text-[11px] text-gray-500">
                {activeSession ? `${activeSession.turns.length} turn${activeSession.turns.length === 1 ? "" : "s"} · ${agentLabel || agentId}` : ""}
              </div>
            </div>
          </div>
          {activeSession && activeSession.turns.length > 0 && (
            <div className="flex items-center gap-2">
            {/* Clear chat — start fresh (history rail hidden; old session stays in localStorage) */}
            <button
              onClick={() => createSession()}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-[12px] font-medium text-gray-700 hover:bg-gray-50 flex items-center gap-1.5"
              title="Clear the conversation and start fresh"
            >
              🗑 Clear chat
            </button>
            <div className="relative" data-download-menu>
              <button
                onClick={() => setShowDownload(showDownload === activeSession.id ? null : activeSession.id)}
                className="px-3 py-1.5 rounded-lg border border-gray-300 text-[12px] font-medium text-gray-700 hover:bg-gray-50 flex items-center gap-1.5"
              >
                ⬇ Download ▾
              </button>
              {showDownload === activeSession.id && (
                <div className="absolute right-0 top-full mt-1 min-w-[220px] bg-white border border-gray-200 rounded-xl shadow-2xl z-50 py-1.5">
                  <button
                    onClick={() => { downloadAsWord(activeSession.turns, activeSession.name); setShowDownload(null); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50 text-[13px] text-gray-700 flex items-center gap-2"
                  >📄 Word (.doc)</button>
                  <button
                    onClick={() => { downloadAsPdf(activeSession.turns, activeSession.name); setShowDownload(null); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50 text-[13px] text-gray-700 flex items-center gap-2"
                  >📕 PDF</button>
                </div>
              )}
            </div>
            </div>
          )}
        </div>

        {/* Conversation scroll area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto bg-gray-50/50">
          {/* No session yet */}
          {!activeSession && (
            <div className="h-full flex items-center justify-center px-6">
              <div className="text-center max-w-md">
                <div className="text-5xl mb-3">💬</div>
                <h3 className="text-[16px] font-semibold text-gray-900 mb-1">Pick a chat</h3>
                <p className="text-[13px] text-gray-500">Choose a chat from the sidebar or start a new one.</p>
              </div>
            </div>
          )}
          {/* Empty session — welcome + example prompts */}
          {activeSession && activeSession.turns.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center px-6 py-10">
              <div className="w-full max-w-3xl text-center">
                <div className="w-16 h-16 mx-auto mb-4 rounded-xl bg-gray-900 text-white flex items-center justify-center text-[28px] shadow-lg">
                  🤖
                </div>
                <h2 className="text-[22px] font-bold text-gray-900 mb-2">
                  Ask {agentLabel || agentId} anything
                </h2>
                <p className="mx-auto mb-6 max-w-xl text-[14px] leading-6 text-gray-500">
                  I can read what&apos;s on your page, search your uploaded files, and reply in voice if you switch on the mic.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mb-2">
                  {examplePrompts.map((p, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => { setPrompt(p); setTimeout(() => { void send(p); }, 0); }}
                      className="min-h-11 rounded-full border border-gray-200 bg-white px-4 py-2 text-left text-[13px] text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {/* Conversation turns — centered column, ChatGPT-style */}
          {activeSession && activeSession.turns.length > 0 && (
            <div className="max-w-3xl mx-auto px-4 md:px-6 py-6 space-y-6">
              {activeSession.turns.map((t, i) => {
                const qIdx = Math.floor(i / 2) + 1;
                if (t.who === "user") {
                  return (
                    <div key={i} className="flex justify-end gap-3">
                      <div className="flex flex-col items-end gap-1.5 min-w-[120px] max-w-[85%]">
                        <div className="flex items-center gap-2 text-[10px] font-bold tracking-wide text-gray-400">
                          <span>YOU</span>
                          <span className="bg-gray-900 text-white px-1.5 py-0.5 rounded-md">Q{qIdx}</span>
                        </div>
                        <div className="bg-gradient-to-br from-blue-600 to-blue-700 text-white rounded-2xl rounded-tr-md px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap shadow-sm">
                          {t.text}
                        </div>
                        <button
                          onClick={() => copyText(t.text)}
                          className="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-1 px-1 py-0.5"
                          title="Copy"
                        >📋 Copy</button>
                      </div>
                      <div className="w-9 h-9 rounded-full bg-gray-200 text-gray-700 flex items-center justify-center text-[15px] shrink-0 mt-6">
                        🙂
                      </div>
                    </div>
                  );
                }
                // Assistant turn
                return (
                  <div key={i} className="flex justify-start gap-3">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-violet-500 text-white flex items-center justify-center text-[15px] shrink-0 mt-6 shadow-sm">
                      🤖
                    </div>
                    <div className="flex flex-col items-start gap-1.5 max-w-[85%] min-w-[120px] flex-1">
                      <div className="flex items-center gap-2 text-[10px] font-bold tracking-wide text-gray-400 uppercase">
                        <span>{(agentLabel || agentId)} · Answer</span>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-md px-4 py-3 text-[15px] leading-relaxed text-gray-900 shadow-sm w-full">
                        {t.process && <ChecklistSimulation process={t.process} ts={t.ts} />}
                        <RevealMarkdown text={t.text} ts={t.ts} />
                        {(t.intent || t.tool_used) && (
                          <div className="text-[10px] text-gray-400 mt-2 pt-2 border-t border-gray-100">
                            {t.intent}{t.tool_used ? ` · ${t.tool_used}` : ""}
                          </div>
                        )}
                      </div>
                      <div className="flex gap-1 text-[11px] flex-wrap">
                        <button
                          onClick={() => copyText(t.text)}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Copy"
                        >📋 Copy</button>
                        {proofCodeIn(t.text) && (
                          <button
                            onClick={() => setEvidenceCode(proofCodeIn(t.text))}
                            className={`px-2 py-1 rounded-md flex items-center gap-1 ${
                              evidenceCode === proofCodeIn(t.text)
                                ? "text-blue-600 bg-blue-50"
                                : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                            }`}
                            title="Open the big chart + evidence panel (bottom) to verify this answer"
                          >📈 {/[가-힣]/.test(t.text) ? "차트 검증" : "Verify chart"}</button>
                        )}
                        <button
                          onClick={() => {
                            const prev = i > 0 ? activeSession.turns[i - 1] : undefined;
                            const pair: AssistantTurn[] = prev && prev.who === "user" ? [prev, t] : [t];
                            downloadAsWord(pair, `${activeSession.name} - Q${qIdx}`);
                          }}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Download this Q&A as Word"
                        >📄 .doc</button>
                        <button
                          onClick={() => {
                            const prev = i > 0 ? activeSession.turns[i - 1] : undefined;
                            const pair: AssistantTurn[] = prev && prev.who === "user" ? [prev, t] : [t];
                            downloadAsPdf(pair, `${activeSession.name} - Q${qIdx}`);
                          }}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Open as PDF"
                        >📕 PDF</button>
                        <span className="w-px h-4 bg-gray-200 self-center mx-0.5" />
                        <button
                          onClick={() => sendFeedback(i, "up")}
                          disabled={!!feedbackGiven[t.ts]}
                          className={`px-2 py-1 rounded-md flex items-center gap-1 ${
                            feedbackGiven[t.ts] === "up" ? "text-green-600 bg-green-50" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                          }`}
                          title="Good answer — the assistant will remember this"
                        >👍</button>
                        <button
                          onClick={() => sendFeedback(i, "down")}
                          disabled={!!feedbackGiven[t.ts]}
                          className={`px-2 py-1 rounded-md flex items-center gap-1 ${
                            feedbackGiven[t.ts] === "down" ? "text-red-600 bg-red-50" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                          }`}
                          title="Wrong — tell it the correct answer so it learns"
                        >👎</button>
                        {feedbackGiven[t.ts] && (
                          <span className="self-center text-[10px] text-gray-400">Thanks — learning from this</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {thinking && (
                <div className="flex justify-start gap-3">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-violet-500 text-white flex items-center justify-center text-[15px] shrink-0 mt-6 shadow-sm animate-pulse">
                    🤖
                  </div>
                  <div className="bg-white border border-gray-200 px-4 py-3 rounded-2xl rounded-tl-md mt-6 shadow-sm">
                    <div className="flex items-center gap-2.5">
                      <div className="flex gap-1.5">
                        <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" />
                        <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                        <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                      </div>
                      <ThinkingStatus question={lastQuestion} />
                    </div>
                  </div>
                </div>
              )}
              {error && (
                <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
              )}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-gray-200 bg-white px-4 py-3 md:px-6">
          <div className="mx-auto max-w-3xl">
            {/* UI build badge — if a feature "doesn't click", check this first: an old
                number means the tab is running a stale bundle → Ctrl+F5. */}
            <div className="text-right text-[9px] text-gray-300 select-none -mb-1"
              title="chat UI build — hard-refresh (Ctrl+F5) if features look missing">{UI_BUILD}</div>
            {!online && (
              <div className="mb-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[12px] text-amber-800">
                <span>📴</span>
                <span>No internet — offline mode. I can open menus, read this page, and answer basic questions. Full answers resume when you&apos;re back online.</span>
              </div>
            )}
            <div className="flex min-h-[48px] items-center gap-2 rounded-xl border border-gray-200 bg-white px-2 py-1.5 shadow-sm transition-all hover:border-gray-300 focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-50">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[20px] text-gray-500 transition-colors hover:bg-gray-100"
                title="Attach a file"
              >+</button>
              <input ref={fileInputRef} type="file" multiple className="hidden" />
              <input
                type="text"
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") send(); }}
                placeholder={activeSession?.turns.length === 0
                  ? "Ask anything …"
                  : "Ask a follow-up — more detail, why, is this correct?"}
                className="min-w-0 flex-1 border-none bg-transparent px-2 py-2 text-[15px] text-gray-900 outline-none placeholder:text-gray-400"
                disabled={!activeSession}
              />
            <div className="relative shrink-0" data-llm-picker>
              <button
                type="button"
                onClick={() => setShowModelPicker(v => !v)}
                className="flex h-9 items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-[11px] font-semibold text-gray-700 transition-colors hover:bg-gray-200"
                title={model ? `Pinned to ${model}` : "Auto (Smart router)"}
              >
                {model === "none" ? "⚡ Offline" : "🧠 LLM"}
                {model && model !== "none" && (
                  <span className="text-[9px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-semibold truncate max-w-[80px]">
                    {model.replace(/^(claude-|gpt-|gemini-|groq-)/, "")}
                  </span>
                )}
              </button>
              {showModelPicker && (
                <div className="absolute bottom-full right-0 mb-2 min-w-[260px] max-h-[360px] overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-2xl py-1.5 z-[300]">
                  <button
                    onClick={() => { setModel(""); try { localStorage.setItem(`chatbot-${agentId}-model`, ""); } catch {}; setShowModelPicker(false); }}
                    className={`w-full text-left px-4 py-2 text-[13px] hover:bg-gray-50 ${!model ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
                  >
                    <div className="font-medium">Auto (Smart router)</div>
                    <div className="text-[10px] opacity-70">easy → DB only · normal → free LLM · hard → paid LLM</div>
                  </button>
                  <button
                    onClick={() => { setModel("none"); try { localStorage.setItem(`chatbot-${agentId}-model`, "none"); } catch {}; setShowModelPicker(false); }}
                    className={`w-full text-left px-4 py-2 text-[13px] hover:bg-gray-50 border-t border-gray-100 mt-1 ${model === "none" ? "bg-amber-50 text-amber-800 font-medium" : "text-gray-700"}`}
                  >
                    <div className="font-medium">⚡ No LLM (offline)</div>
                    <div className="text-[10px] opacity-70">knowledge base + this page only · no AI, no internet</div>
                  </button>
                  {["anthropic", "gemini", "openai", "groq", "ollama"].map(prov => {
                    const opts = available.filter(m => m.provider === prov);
                    if (opts.length === 0) return null;
                    return (
                      <div key={prov} className="border-t border-gray-100 mt-1 pt-1">
                        <div className="px-4 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                          {prov.charAt(0).toUpperCase() + prov.slice(1)}
                        </div>
                        {opts.map(m => (
                          <button
                            key={m.id}
                            onClick={() => { setModel(m.id); try { localStorage.setItem(`chatbot-${agentId}-model`, m.id); } catch {}; setShowModelPicker(false); }}
                            className={`w-full text-left px-4 py-1.5 text-[13px] hover:bg-gray-50 ${model === m.id ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
                          >{m.id}{m.is_new && <span className="ml-1.5 text-[9px] font-bold text-white bg-blue-600 rounded px-1.5 py-0.5 align-middle">NEW</span>}</button>
                        ))}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            {/* 🎤 single voice message: record → transcribe → send */}
            <button
              type="button"
              onClick={() => {
                if (voiceState === "listening") stopListening();
                else startListening();
              }}
              disabled={thinking || !activeSession || voiceState === "thinking" || continuousVoice}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[14px] transition-colors disabled:opacity-40 ${
                voiceState === "listening"
                  ? "bg-red-500 text-white animate-pulse"
                  : voiceState === "thinking"
                  ? "bg-amber-500 text-white"
                  : "bg-gray-100 hover:bg-gray-200 text-gray-700"
              }`}
              title={voiceState === "listening" ? "Stop recording" : "Record voice message"}
            >🎤</button>
            {/* ● continuous voice mode: full conversation by voice */}
            <button
              type="button"
              onClick={() => {
                if (continuousVoice) endContinuousVoice();
                else startContinuousVoice();
              }}
              disabled={thinking || !activeSession || voiceState === "thinking"}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[14px] transition-colors disabled:opacity-40 ${
                continuousVoice
                  ? "bg-green-500 text-white"
                  : "bg-gray-100 hover:bg-gray-200 text-gray-700"
              }`}
              title={continuousVoice ? "Stop continuous voice mode" : "Start continuous voice conversation"}
            >●</button>
            <button
              type="button"
              onClick={() => send()}
              disabled={!prompt.trim() || !activeSession}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-[16px] text-white shadow-sm transition-colors hover:bg-gray-800 disabled:opacity-40"
              title="Send"
            >↑</button>
            </div>
            <div className="mt-2 text-center text-[10px] text-gray-400">
              The Assistant can read this page, your uploaded files, and the conversation above.
            </div>
          </div>
        </div>
      </main>

      {/* Continuous voice mode — fullscreen overlay */}
      {continuousVoice && (
        <div className="fixed inset-0 z-[210] flex flex-col items-center justify-center bg-black/80 backdrop-blur-sm">
          <div className="text-white text-center">
            <div className="text-[16px] uppercase tracking-widest opacity-70 mb-3">
              Voice mode
            </div>
            <div className="text-[60px] mb-4">
              {voiceState === "listening" ? "🎙️"
                : voiceState === "thinking" ? "💭"
                : voiceState === "speaking" ? "🔊"
                : "🤖"}
            </div>
            <div className="text-[24px] font-medium mb-8">
              {voiceState === "listening" ? "Listening…"
                : voiceState === "thinking" ? "Thinking…"
                : voiceState === "speaking" ? "Speaking…"
                : "Ready"}
            </div>
            <div className="text-[12px] opacity-70 max-w-md px-4">
              Talk naturally — pause for ~2 seconds when you&apos;re done and the
              assistant will reply. The full conversation is also written into
              your chat history.
            </div>
          </div>
          <button
            type="button"
            onClick={endContinuousVoice}
            className="mt-10 px-6 py-2.5 rounded-full bg-white text-gray-900 text-[14px] font-medium hover:bg-gray-100"
          >End voice</button>
        </div>
      )}

      {/* ========================================================== */}
      {/* === PROOF SHEET (bottom, big): chart LARGE + evidence data readable === */}
      {/* Opens from stock-name clicks, 근거 🔍 pills, and 📈 차트 검증 buttons.   */}
      {/* (boss 2026-08-24: side panels were too small — "open in the downside   */}
      {/* full page which we can use easily with larger font")                   */}
      {/* ========================================================== */}
      {evidenceCode && (
        <div className="absolute inset-x-0 bottom-0 z-40 flex flex-col bg-white border-t-2 border-blue-400 shadow-2xl"
          style={{ height: "72%" }}>
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
            <span className="text-[15px] font-bold text-gray-800">
              📈 KRX:{evidenceCode} — 차트 + 근거 / chart + evidence
            </span>
            <button onClick={() => setEvidenceCode(null)}
              className="px-2.5 py-0.5 text-[15px] rounded-lg text-gray-500 hover:bg-gray-200" title="Close">✕ 닫기</button>
          </div>
          <div className="flex flex-1 min-h-0">
            <iframe
              key={evidenceCode}
              src={`https://s.tradingview.com/widgetembed/?symbol=KRX%3A${evidenceCode}&interval=D&theme=light&style=1&locale=kr&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=1`}
              className="border-0 h-full"
              style={{ width: "58%" }}
              title="TradingView chart"
            />
            <div className="flex-1 overflow-y-auto px-5 py-3 text-[14.5px] leading-relaxed text-gray-900 border-l border-gray-200">
              {evidenceMd
                ? <MarkdownLite text={evidenceMd} />
                : <div className="text-gray-400 text-[13px] py-4">근거 데이터 불러오는 중… / loading evidence…</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
