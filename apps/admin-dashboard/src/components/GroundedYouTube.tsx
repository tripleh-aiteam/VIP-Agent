"use client";

import { useEffect, useState } from "react";
import { API } from "@/components/api";
import { useLanguage } from "@/components/i18n";

/**
 * GroundedYouTube — renders the latest GROUNDED YouTube report (gpu_youtube row)
 * from GET /reports/youtube/latest. Read-only pass-through of the colleague's GPU
 * pipeline output: per-stock table with deep-links, BUY/SELL recommendations,
 * KO/EN executive summary, the 4 downloadable files, and source videos.
 * It is a fixed MORNING SNAPSHOT (we show the 'as of' time), not a live feed.
 */
export default function GroundedYouTube() {
  const { lang } = useLanguage();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let on = true;
    setLoading(true);
    fetch(`${API}/reports/youtube/latest`)
      .then((r) => r.json())
      .then((d) => { if (on) { setData(d); setLoading(false); } })
      .catch(() => { if (on) setLoading(false); });
    return () => { on = false; };
  }, []);

  if (loading)
    return <div className="text-[12px] text-[var(--text-muted)] p-4">불러오는 중…</div>;
  if (!data?.available)
    return (
      <div className="text-[12px] text-[var(--text-muted)] p-4 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)]">
        아직 유튜브 그라운드 리포트가 없습니다. (매일 아침 ~06:50 자동 생성)
      </div>
    );

  const rep = data.report || {};
  const rows: any[] = rep.rows || [];
  const recs: any[] = rep.recommendations || [];
  const sources: any[] = rep.sources || [];
  const files = data.files || {};
  const asOf = String(data.generated_at_kst || "").replace("T", " ").slice(0, 16);

  const actionCls = (a: string) =>
    ({
      BUY: "text-green-600 bg-green-50 border-green-200",
      SELL: "text-red-600 bg-red-50 border-red-200",
      HOLD: "text-amber-600 bg-amber-50 border-amber-200",
      WATCH: "text-blue-600 bg-blue-50 border-blue-200",
    } as any)[a] || "text-gray-600 bg-gray-50 border-gray-200";
  const won = (v: any) =>
    v == null ? "—" : Number(v).toLocaleString() + "원";
  const pct = (v: any) =>
    v == null ? "—" : (v >= 0 ? "▲ " : "▼ ") + Math.abs(Number(v)).toFixed(2) + "%";

  const fileBtn = (url: string, label: string) =>
    url ? (
      <a key={label} href={url} target="_blank" rel="noreferrer"
        className="px-2.5 py-1 text-[11px] rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] text-[var(--text-primary)] font-medium">
        {label}
      </a>
    ) : null;

  return (
    <div className="space-y-4 mb-5 border border-[var(--border-default)] rounded-xl bg-[var(--bg-card)] p-5">
      {/* Header + downloads */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">📺 유튜브 그라운드 리포트</h3>
          <p className="text-[11px] text-[var(--text-muted)] mt-0.5">
            모닝 스냅샷 · 기준 {asOf} KST · 한국 금융 유튜브 분석 (Whisper 전사 + 근거 인용/타임스탬프)
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          {fileBtn(files.docx_ko_url, "KO .docx")}
          {fileBtn(files.pdf_ko_url, "KO .pdf")}
          {fileBtn(files.docx_en_url, "EN .docx")}
          {fileBtn(files.pdf_en_url, "EN .pdf")}
        </div>
      </div>

      {/* Executive summary (KO/EN follows the dashboard language) */}
      {(rep.summary_ko || rep.summary_en) && (
        <p className="text-[13px] text-[var(--text-secondary)] leading-relaxed border-l-2 border-rose-300 pl-3">
          {lang === "ko" ? rep.summary_ko || rep.summary_en : rep.summary_en || rep.summary_ko}
        </p>
      )}

      {/* Per-stock grounded table */}
      <div className="overflow-x-auto border border-[var(--border-default)] rounded-lg">
        <table className="w-full text-[12px] border-collapse">
          <thead>
            <tr className="bg-[var(--bg-elevated)] text-[var(--text-muted)] text-left">
              <th className="px-3 py-2 font-medium">종목</th>
              <th className="px-3 py-2 font-medium">현재가</th>
              <th className="px-3 py-2 font-medium">등락률</th>
              <th className="px-3 py-2 font-medium">액션</th>
              <th className="px-3 py-2 font-medium">근거</th>
              <th className="px-3 py-2 font-medium">영상</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-[var(--border-default)] align-top">
                <td className="px-3 py-2 whitespace-nowrap text-[var(--text-primary)]">
                  {lang === "ko" ? r.ko : r.en || r.ko}{" "}
                  <span className="text-[var(--text-muted)] text-[10px]">{r.t}</span>
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{won(r.close)}</td>
                <td className={`px-3 py-2 whitespace-nowrap ${Number(r.change_pct) >= 0 ? "text-green-600" : "text-red-600"}`}>
                  {pct(r.change_pct)}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${actionCls(r.action)}`}>{r.action}</span>
                </td>
                <td className="px-3 py-2 max-w-[360px] text-[var(--text-secondary)]">{r.grounded_summary}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {r.deeplink && (
                    <a href={r.deeplink} target="_blank" rel="noreferrer" className="text-rose-500 hover:underline" title={r.top_quote || "영상 보기"}>▶ 보기</a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Recommendations */}
      {recs.length > 0 && (
        <div>
          <h4 className="text-[13px] font-semibold text-[var(--text-primary)] mb-1.5">투자의견 (BUY / SELL / HOLD)</h4>
          <div className="space-y-1.5">
            {recs.map((rc, i) => (
              <div key={i} className="text-[12px] flex gap-2 items-start">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold shrink-0 ${actionCls(rc.action)}`}>{rc.action}</span>
                <span className="font-medium text-[var(--text-primary)] shrink-0">{rc.stock}</span>
                <span className="text-[var(--text-secondary)] flex-1">{rc.reason}</span>
                {rc.deeplink && (
                  <a href={rc.deeplink} target="_blank" rel="noreferrer" className="text-rose-500 hover:underline shrink-0">▶</a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Source videos */}
      {sources.length > 0 && (
        <div>
          <h4 className="text-[13px] font-semibold text-[var(--text-primary)] mb-1.5">출처 영상</h4>
          <div className="space-y-0.5">
            {sources.map((s, i) => (
              <div key={i} className="text-[11px] text-[var(--text-muted)]">
                <a href={s.url} target="_blank" rel="noreferrer" className="text-rose-500 hover:underline">{s.channel}</a>
                {" — "}{s.title}
                {s.n_insights ? <span className="ml-1 text-[var(--text-muted)]">({s.n_insights} insights)</span> : null}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
