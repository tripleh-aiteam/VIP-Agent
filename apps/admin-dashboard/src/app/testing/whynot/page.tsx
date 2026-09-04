"use client";
/* 🚧 WHY NOT BUYING YET — the proof menu (boss 2026-09-04 13:0x: "we have
   gates so we have few chances — during this time we need PROOF why no popup
   is coming out. Create this menu; if we click SK hynix it should explain
   each gate with actual numbers and weights").

   One card per watched stock. Click = the gate cascade in the boss's order:
   ① 갭상승 ② bottom check ③ volume ④ bad news ⑤ 100-checklist score with
   its item weights (gap/volume/news excluded so nothing counts twice).
   The FIRST failing gate is the reason there is no popup. */
import { Fragment, useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "../../../components/api";

type Gate = { n: number; key: string; passed: boolean; ko: string; en: string;
              link?: string | null };
type Item = { k: string; en?: string; v: string; ven?: string; s?: number | null };
type Row = { code: string; name: string; name_en?: string;
             held?: boolean; pending?: boolean;
             yc?: number | null; op?: number | null; px?: number | null;
             gap_pct?: number | null; now_vs_yc?: number | null;
             gates: Gate[]; stopped_at?: number | null;
             score?: number | null; rank?: number | null; tot?: number;
             items?: Item[]; verdict_ko: string; verdict_en: string };
type Payload = { ok: boolean; market_open: boolean; rows: Row[];
                 remembered?: boolean; reconstructed?: boolean;
                 as_of?: string; day?: string };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

export default function WhyNotPage() {
  const { t } = useLanguage();
  const ko = t("k", "e") === "k";
  const [data, setData] = useState<Payload | null>(null);
  const [open, setOpen] = useState<string | null>(null);      // stable: the code
  const [itemsOpen, setItemsOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    const pull = async () => {
      try {
        const r = await fetch(`${API}/approval/whynot`, { cache: "no-store" });
        const j = await r.json();
        if (!dead && j?.ok) { setData(j); setErr(null); }
      } catch { if (!dead) setErr("서버 연결 중…"); }
    };
    pull();
    const iv = setInterval(pull, 15000);
    return () => { dead = true; clearInterval(iv); };
  }, []);

  const rows = data?.rows || [];
  const blocked = rows.filter((r) => !r.held && !r.pending && r.stopped_at);
  const ready = rows.filter((r) => !r.held && !r.pending && !r.stopped_at);

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "18px 14px 60px" }}>
      <div style={{ display: "flex", gap: 12, fontSize: 12, marginBottom: 8, flexWrap: "wrap" }}>
        <a href="/testing" style={{ color: "inherit" }}>{t("← 모의투자 메뉴", "← Paper Trading menu")}</a>
        <a href="/testing/approve" style={{ color: "#2e7d32" }}>{t("🖥 메뉴3 실시간 모니터링", "🖥 Menu 3 Real Time Monitoring")}</a>
      </div>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: "2px 0 2px" }}>
        🚧 {t("아직 왜 안 사나 — 관문 증명", "Why Not Buying Yet — proof of the gates")}
      </h1>
      <p style={{ fontSize: 12.5, opacity: 0.7, margin: "0 0 12px", lineHeight: 1.5 }}>
        {t("관문이 있으니 기회는 적습니다 — 팝업이 안 오는 시간 동안, 종목마다 어느 관문에서 왜 멈춰 있는지 실제 숫자로 증명합니다. 종목을 클릭하세요.",
           "The gates make chances few — while no popup comes, this page proves with real numbers which gate each stock is stopped at. Click a stock.")}
      </p>

      {!data && !err && <div style={{ fontSize: 13, opacity: 0.6 }}>{t("불러오는 중…", "loading…")}</div>}
      {err && <div style={{ fontSize: 13, color: "#c62828" }}>{err}</div>}
      {data && data.market_open === false && (
        <div style={{ fontSize: 13, fontWeight: 800, color: "#c62828",
                      border: "1px solid rgba(198,40,40,0.4)", borderRadius: 8,
                      padding: "8px 12px", marginBottom: 10 }}>
          ⛔ {data.remembered
            ? t(`장 마감 — 아래는 에이전트가 기억하는 오늘(${data.day || ""})의 판정입니다 (${data.as_of || ""} 기준${data.reconstructed ? ", 오늘 아침부터 규칙을 돌렸다면의 재구성" : ""}). 각 종목이 오늘 왜 안 샀는지 그대로 남아 있습니다.`,
                `MARKET CLOSED — below is the agent's MEMORY of today (${data.day || ""}), as of ${data.as_of || ""}${data.reconstructed ? ", reconstructed as if the rule had run from this morning" : ""}. Why each stock was not bought today stays on record.`)
            : t("장 마감 — 관문 판정은 다음 장에서 다시 시작합니다.",
                "MARKET CLOSED — the gate verdicts resume next session.")}
        </div>)}

      {data && (
        <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 10 }}>
          {t(`감시 ${rows.length}종목 — 관문에 막힘 ${blocked.length} · 전 관문 통과(신호 대기) ${ready.length} · 보유/제안 중 ${rows.length - blocked.length - ready.length}`,
             `${rows.length} watched — blocked at a gate ${blocked.length} · all gates passed (waiting for signal) ${ready.length} · holding/proposal out ${rows.length - blocked.length - ready.length}`)}
        </div>)}

      {rows.map((r) => {
        const isOpen = open === r.code;
        const nm = ko ? r.name : (r.name_en || r.name);
        const badge = r.held
          ? { txt: t("보유 중", "HOLDING"), color: "#2e7d32" }
          : r.pending
            ? { txt: t("제안 중", "PROPOSAL OUT"), color: "#e65100" }
            : r.stopped_at
              ? { txt: t(`${r.stopped_at}관문 멈춤`, `stopped at gate ${r.stopped_at}`), color: "#c62828" }
              : { txt: t("전 관문 통과 — 신호 대기", "all gates passed — waiting"), color: "#1565c0" };
        return (
          <Fragment key={r.code}>
          <div onClick={() => setOpen(isOpen ? null : r.code)}
               style={{ border: "1px solid rgba(128,128,128,0.3)", borderRadius: 10,
                        padding: "9px 12px", marginBottom: 6, cursor: "pointer",
                        borderLeft: `4px solid ${badge.color}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <b style={{ fontSize: 14 }}>{nm}</b>
              <span style={{ fontSize: 11.5, fontWeight: 800, color: badge.color }}>{badge.txt}</span>
              <span style={{ fontSize: 11.5, opacity: 0.7 }}>
                {W(r.px)}{r.now_vs_yc != null && <> ({r.now_vs_yc >= 0 ? "+" : ""}{r.now_vs_yc}% {t("vs 어제", "vs yesterday")})</>}
                {r.score != null && <> · {r.score}{t("점", " pts")}{r.rank != null && ` · ${r.rank}/${r.tot}`}</>}
              </span>
              <span style={{ marginLeft: "auto", fontSize: 11, opacity: 0.5 }}>{isOpen ? "▲" : "▼"}</span>
            </div>
            <div style={{ fontSize: 12, marginTop: 3, opacity: 0.85 }}>
              {ko ? r.verdict_ko : r.verdict_en}
            </div>

            {isOpen && (
              <div onClick={(e) => e.stopPropagation()}
                   style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.6, cursor: "default" }}>
                {r.gates.map((g) => (
                  <div key={g.n}
                       style={{ padding: "6px 9px", borderRadius: 7, marginBottom: 5,
                                background: g.passed ? "rgba(46,125,50,0.06)" : "rgba(198,40,40,0.07)",
                                borderLeft: `3px solid ${g.passed ? "#2e7d32" : "#c62828"}`,
                                fontWeight: !g.passed && g.n === r.stopped_at ? 700 : 400 }}>
                    {g.passed ? "✅" : "⛔"} <b>{g.n}. {t(
                      ({ gap: "갭상승 관문", bottom: "바닥 확인 관문", volume: "거래량 관문",
                         news: "나쁜 뉴스 관문", score: "100 체크리스트 관문" } as Record<string, string>)[g.key] || g.key,
                      ({ gap: "Gap-up gate", bottom: "Bottom-check gate", volume: "Volume gate",
                         news: "Bad-news gate", score: "100-checklist gate" } as Record<string, string>)[g.key] || g.key)}</b>
                    {" — "}{ko ? g.ko : g.en}
                    {g.link && <> <a href={g.link} target="_blank" rel="noreferrer"
                                     style={{ fontWeight: 800, color: "#1565c0" }}>
                      📎 {t("기사 읽기", "read the article")}</a></>}
                  </div>))}

                {/* ⑤ the item-by-item weights, gap/volume/news excluded */}
                {(r.items?.length || 0) > 0 && (
                  <div style={{ marginTop: 4 }}>
                    <span onClick={() => setItemsOpen(itemsOpen === r.code ? null : r.code)}
                          style={{ fontSize: 12, fontWeight: 800, color: "#1565c0",
                                   cursor: "pointer", textDecoration: "underline dotted",
                                   textUnderlineOffset: 3 }}>
                      📋 {t(`체크리스트 항목별 점수·가중치 보기 (${r.items!.length}개 — 갭·거래량·뉴스 제외)`,
                            `see the item-by-item scores & weights (${r.items!.length} items — gap/volume/news excluded)`)} {itemsOpen === r.code ? "▲" : "▼"}
                    </span>
                    {itemsOpen === r.code && (
                      <div style={{ overflowX: "auto", marginTop: 5 }}>
                        <table style={{ fontSize: 11.5, borderCollapse: "collapse", width: "100%" }}>
                          <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                            <th style={{ padding: "3px 8px 3px 0" }}>{t("항목", "item")}</th>
                            <th style={{ padding: "3px 8px 3px 0" }}>{t("측정값", "measured")}</th>
                            <th style={{ padding: "3px 0" }}>{t("점수(가중치)", "score (weight)")}</th>
                          </tr></thead>
                          <tbody>
                            {[...r.items!].sort((a, b) => (a.s ?? 50) - (b.s ?? 50)).map((it, k) => (
                              <tr key={k} style={{ borderTop: "1px solid rgba(128,128,128,0.15)" }}>
                                <td style={{ padding: "3px 8px 3px 0" }}>{ko ? it.k : (it.en || it.k)}</td>
                                <td style={{ padding: "3px 8px 3px 0" }}>{ko ? it.v : (it.ven || it.v)}</td>
                                <td style={{ padding: "3px 0", fontWeight: 700,
                                             color: (it.s ?? 50) < 40 ? "#c62828"
                                               : (it.s ?? 50) >= 70 ? "#2e7d32" : "inherit" }}>
                                  {it.s ?? "-"}</td>
                              </tr>))}
                          </tbody>
                        </table>
                      </div>)}
                  </div>)}
              </div>)}
          </div>
          </Fragment>);
      })}
    </div>
  );
}
