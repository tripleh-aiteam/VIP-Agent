"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "./i18n";
import MarkdownLite from "./MarkdownLite";

type NewsItem = { title: string; snippet: string; url: string; source: string; type: string; direction: string };
type NewsResp = { ticker: string; name: string; count: number; items: NewsItem[]; configured?: boolean; days?: number };

const dirColor = (d: string) => (d === "▲" ? "#e53935" : d === "▼" ? "#1e88e5" : "#8b95a1");

export default function StockNewsPanel() {
  const { t } = useLanguage();
  const [stocks, setStocks] = useState<{ code: string; name: string }[]>([]);
  const [code, setCode] = useState("000660");
  const [data, setData] = useState<NewsResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [sumLoading, setSumLoading] = useState(false);

  useEffect(() => {
    api<{ stocks: { code: string; name: string }[] }>("/predictions/news-universe")
      .then((d) => setStocks(d.stocks || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!code) return;
    let alive = true;
    setLoading(true); setData(null); setSummary(null);   // reset summary on stock change
    api<NewsResp>(`/predictions/stock-news/${code}?days=7&limit=20`)
      .then((d) => { if (alive) setData(d); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [code]);

  const loadSummary = () => {
    setSumLoading(true); setSummary(null);
    api<{ summary_ko: string }>(`/predictions/stock-news/${code}/summary?days=7&limit=15`)
      .then((d) => setSummary(d.summary_ko || t("요약을 생성하지 못했습니다.", "Could not generate a summary.")))
      .catch(() => setSummary(t("요약 생성 실패.", "Summary failed.")))
      .finally(() => setSumLoading(false));
  };

  const stockName = stocks.find((s) => s.code === code)?.name || data?.name || code;

  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden mb-4">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]/50 flex-wrap">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">
          📰 {t("종목별 뉴스", "Stock News")}
        </span>
        <select
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="text-[13px] px-2.5 py-1.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] text-[var(--text-primary)] cursor-pointer"
        >
          {stocks.map((s) => (
            <option key={s.code} value={s.code}>{s.name} ({s.code})</option>
          ))}
        </select>
        <button
          onClick={loadSummary}
          disabled={loading || sumLoading || !(data && data.count > 0)}
          className="ml-auto inline-flex items-center gap-1.5 text-[12.5px] font-semibold px-3 py-1.5 rounded-lg bg-[var(--brand-blue)] text-white shadow-sm hover:opacity-90 disabled:opacity-50 transition"
        >
          {sumLoading
            ? <span className="w-3 h-3 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            : "🤖"}
          {t("AI 요약 보기", "AI summary")}
        </button>
      </div>

      <div className="p-4">
        {loading && (
          <div className="py-8 text-center text-[13px] text-[var(--text-muted)]">
            {t("뉴스 불러오는 중…", "Loading news…")}
          </div>
        )}

        {/* On-demand AI summary (only after the button is pressed) */}
        {summary && (
          <div className="rounded-lg bg-[var(--badge-blue-bg)]/25 border border-[var(--border-default)]/50 p-3 mb-3 text-[13px] leading-relaxed">
            <div className="text-[11px] font-bold text-[var(--text-muted)] mb-1">🤖 {t("AI 요약", "AI summary")} — {stockName}</div>
            <MarkdownLite text={summary} />
          </div>
        )}

        {data && (
          <>
            <ul className="flex flex-col divide-y divide-[var(--border-default)]/40">
              {data.items.map((it, i) => (
                <li key={i} className="py-2.5 flex items-start gap-2">
                  <span className="text-[15px] font-extrabold leading-tight mt-[1px]" style={{ color: dirColor(it.direction) }}>
                    {it.direction}
                  </span>
                  <div className="min-w-0 flex-1">
                    <a href={it.url} target="_blank" rel="noreferrer"
                       className="text-[13.5px] font-medium text-[var(--text-primary)] hover:text-[var(--brand-blue)] hover:underline">
                      {it.title}
                    </a>
                    {it.snippet ? (
                      <div className="text-[12px] text-[var(--text-secondary)] mt-0.5 line-clamp-2">{it.snippet}</div>
                    ) : null}
                    <div className="text-[10.5px] text-[var(--text-muted)] mt-0.5">
                      {it.source}{it.type && it.type !== "일반" ? ` · ${it.type}` : ""}
                    </div>
                  </div>
                </li>
              ))}
            </ul>

            {data.count === 0 && !loading && (
              <div className="py-6 text-center text-[13px] text-[var(--text-muted)]">
                {data.configured === false
                  ? t("뉴스 검색 API가 설정되지 않았습니다 (NAVER/SERPER 키 필요).", "News search API not configured (NAVER/SERPER key needed).")
                  : t("최근 뉴스가 없습니다.", "No recent news.")}
              </div>
            )}
          </>
        )}
      </div>

      <div className="px-4 py-2 text-[10.5px] text-[var(--text-muted)] border-t border-[var(--border-default)]/40">
        {t("네이버 뉴스 + 구글 뉴스 · ▲호재 ▼악재 · 제목 클릭 시 원문 · 요약은 버튼으로",
          "Naver + Google News · ▲positive ▼negative · click a title for the source · summary on demand")}
      </div>
    </div>
  );
}
