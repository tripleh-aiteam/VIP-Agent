"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "./i18n";
import MarkdownLite from "./MarkdownLite";

type NewsItem = { title: string; snippet: string; url: string; source: string; type: string; direction: string };
type NewsResp = {
  ticker: string; name: string; count: number; items: NewsItem[];
  summary_ko: string; provider?: string; configured?: boolean; days?: number;
};

const dirColor = (d: string) => (d === "▲" ? "#e53935" : d === "▼" ? "#1e88e5" : "#8b95a1");

export default function StockNewsPanel() {
  const { t } = useLanguage();
  const [stocks, setStocks] = useState<{ code: string; name: string }[]>([]);
  const [code, setCode] = useState("000660");
  const [data, setData] = useState<NewsResp | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<{ stocks: { code: string; name: string }[] }>("/predictions/news-universe")
      .then((d) => setStocks(d.stocks || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!code) return;
    let alive = true;
    setLoading(true); setData(null);
    api<NewsResp>(`/predictions/stock-news/${code}?days=7&limit=15`)
      .then((d) => { if (alive) setData(d); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [code]);

  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden mb-4">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]/50 flex-wrap">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">
          📰 {t("종목별 뉴스 + AI 요약", "Stock News + AI Summary")}
        </span>
        <select
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="ml-auto text-[13px] px-2.5 py-1.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-card)] text-[var(--text-primary)] cursor-pointer"
        >
          {stocks.map((s) => (
            <option key={s.code} value={s.code}>{s.name} ({s.code})</option>
          ))}
        </select>
      </div>

      <div className="p-4">
        {loading && (
          <div className="py-8 text-center text-[13px] text-[var(--text-muted)]">
            {t("뉴스 불러오는 중…", "Loading news…")}
          </div>
        )}

        {data && (
          <>
            {data.summary_ko ? (
              <div className="rounded-lg bg-[var(--badge-blue-bg)]/25 border border-[var(--border-default)]/50 p-3 mb-3 text-[13px] leading-relaxed">
                <div className="text-[11px] font-bold text-[var(--text-muted)] mb-1">
                  🤖 {t("AI 요약", "AI summary")} — {data.name}
                </div>
                <MarkdownLite text={data.summary_ko} />
              </div>
            ) : null}

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

            {data.count === 0 && (
              <div className="py-6 text-center text-[13px] text-[var(--text-muted)]">
                {data.configured === false
                  ? t("검색 API가 설정되지 않았습니다 (SERPER_API_KEY 필요).", "Search API not configured (SERPER_API_KEY needed).")
                  : t("최근 7일 뉴스가 없습니다.", "No news in the last 7 days.")}
              </div>
            )}
          </>
        )}
      </div>

      <div className="px-4 py-2 text-[10.5px] text-[var(--text-muted)] border-t border-[var(--border-default)]/40">
        {t("최근 7일 한국 뉴스 · 실시간 검색 · ▲호재 ▼악재 · 제목 클릭 시 원문",
          "Last 7 days · live search · ▲positive ▼negative · click a title for the source")}
      </div>
    </div>
  );
}
