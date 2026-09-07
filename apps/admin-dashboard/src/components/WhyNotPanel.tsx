"use client";
/* 🚧 WHY NOT BUYING YET — the gate-proof panel (boss 2026-09-04 13:0x: "we
   have gates so we have few chances — during this time we need PROOF why no
   popup is coming out"; 2026-09-07: "the menu should be INSIDE Menu 3 —
   please do not make separate"). So this is a PANEL, not a page: Menu 3
   embeds it, and the old /testing/whynot route just redirects home.

   One card per watched stock. Click = the gate cascade in the boss's order:
   ① 갭상승 ② weekly position ③ volume ④ bad news ⑤ 100-checklist score with
   its true item weights. Explanations stop at the first blocked gate. */
import { Fragment, useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "./api";

type Gate = { n: number; key: string; passed: boolean; ko: string; en: string;
              link?: string | null };
type Item = { k: string; en?: string; v: string; ven?: string; s?: number | null;
              w?: number | null; ctr?: number | null };
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

export default function WhyNotPanel() {
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

  // HIS SIX COME FIRST, IN HIS ORDER (boss 2026-09-07: "please reorder these:
  // 1. SK하이닉스 2. 삼성전자 3. 한화오션 4. 두산에너빌리티 5. SK텔레콤
  // 6. NAVER, then the others"). These are his standing six, and he reads the
  // board in that order every morning; everything else keeps the order the
  // desk sent, which is by score.
  const SIX_ORDER = ["000660", "005930", "042660", "034020", "017670", "035420"];
  const rows = [...(data?.rows || [])].sort((a, b) => {
    const ia = SIX_ORDER.indexOf(a.code), ib = SIX_ORDER.indexOf(b.code);
    if (ia !== -1 && ib !== -1) return ia - ib;      // both his: his order
    if (ia !== -1) return -1;                        // his six always above
    if (ib !== -1) return 1;
    return 0;                                        // the rest: unchanged
  });
  const blocked = rows.filter((r) => !r.held && !r.pending && r.stopped_at);
  const ready = rows.filter((r) => !r.held && !r.pending && !r.stopped_at);

  return (
    <div>
      <p style={{ fontSize: 12.5, opacity: 0.7, margin: "0 0 10px", lineHeight: 1.5 }}>
        {t("관문이 있으니 기회는 적습니다 — 팝업이 안 오는 시간 동안, 종목마다 어느 관문에서 왜 멈춰 있는지 실제 숫자로 증명합니다. 종목을 클릭하세요.",
           "The gates make chances few — while no popup comes, this proves with real numbers which gate each stock is stopped at. Click a stock.")}
      </p>

      {!data && !err && <div style={{ fontSize: 13, opacity: 0.6 }}>{t("불러오는 중…", "loading…")}</div>}
      {err && <div style={{ fontSize: 13, color: "#c62828" }}>{err}</div>}
      {data && data.market_open === false && (
        <div style={{ fontSize: 13, fontWeight: 800, color: "#c62828",
                      border: "1px solid rgba(198,40,40,0.4)", borderRadius: 8,
                      padding: "8px 12px", marginBottom: 10 }}>
          ⛔ {data.remembered
            ? t(`장 마감 — 아래는 에이전트가 기억하는 그날(${data.day || ""})의 판정입니다 (${data.as_of || ""} 기준${data.reconstructed ? ", 아침부터 규칙을 돌렸다면의 재구성" : ""}). 각 종목이 왜 안 샀는지 그대로 남아 있습니다.`,
                `MARKET CLOSED — below is the agent's MEMORY of that day (${data.day || ""}), as of ${data.as_of || ""}${data.reconstructed ? ", reconstructed as if the rule had run from the morning" : ""}. Why each stock was not bought stays on record.`)
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
                                whiteSpace: "pre-line",  // the position story is line-by-line
                                fontWeight: !g.passed && g.n === r.stopped_at ? 700 : 400 }}>
                    {g.passed ? "✅" : "⛔"} <b>{g.n}. {t(
                      ({ gap: "갭상승 관문", position: "위치 관문 (주·월·3개월·6개월)", bottom: "바닥 확인 관문",
                         volume: "거래량 관문", news: "나쁜 뉴스 관문",
                         score: "100 체크리스트 관문" } as Record<string, string>)[g.key] || g.key,
                      ({ gap: "Gap-up gate", position: "Position gate (week·month·3m·6m)", bottom: "Bottom-check gate",
                         volume: "Volume gate", news: "Bad-news gate",
                         score: "100-checklist gate" } as Record<string, string>)[g.key] || g.key)}</b>
                    {" — "}{ko ? g.ko : g.en}
                    {g.link && <> <a href={g.link} target="_blank" rel="noreferrer"
                                     style={{ fontWeight: 800, color: "#1565c0" }}>
                      📎 {t("기사 읽기", "read the article")}</a></>}
                  </div>))}
                {/* the cascade stops at the first blocked gate (boss 2026-09-04
                    17:4x: "no need to add other explanations") */}
                {r.stopped_at != null && r.gates.length < 5 && (
                  <div style={{ fontSize: 11.5, opacity: 0.55, padding: "2px 4px" }}>
                    {t(`나머지 ${5 - r.gates.length}개 관문은 이 관문을 통과한 뒤에 검사합니다.`,
                       `The remaining ${5 - r.gates.length} gate(s) are checked only after this one is passed.`)}
                  </div>)}

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
                    {itemsOpen === r.code && (() => {
                      // HIGHEST FIRST + TRUE WEIGHTS (boss 2026-09-04 18:0x):
                      // sorted by real contribution; weight = the item's
                      // %-share of the total score, contribution = s×w/100.
                      const its = [...r.items!].sort((a, b) =>
                        ((b.ctr ?? b.s ?? 0) as number) - ((a.ctr ?? a.s ?? 0) as number));
                      const hasW = its.some((x) => x.w != null);
                      const totC = its.reduce((a, x) => a + (x.ctr ?? 0), 0);
                      return (
                      <div style={{ overflowX: "auto", marginTop: 5 }}>
                        <table style={{ fontSize: 11.5, borderCollapse: "collapse", width: "100%" }}>
                          <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                            <th style={{ padding: "3px 8px 3px 0" }}>{t("항목", "item")}</th>
                            <th style={{ padding: "3px 8px 3px 0" }}>{t("측정값", "measured")}</th>
                            <th style={{ padding: "3px 8px 3px 0" }}>{t("점수(0-100)", "score (0-100)")}</th>
                            {hasW && <th style={{ padding: "3px 8px 3px 0" }}>{t("가중치", "weight")}</th>}
                            {hasW && <th style={{ padding: "3px 0" }}>{t("기여 점수", "contributed")}</th>}
                          </tr></thead>
                          <tbody>
                            {its.map((it, k) => (
                              <tr key={k} style={{ borderTop: "1px solid rgba(128,128,128,0.15)",
                                                   opacity: it.w === 0 ? 0.5 : 1 }}>
                                <td style={{ padding: "3px 8px 3px 0" }}>{ko ? it.k : (it.en || it.k)}</td>
                                <td style={{ padding: "3px 8px 3px 0" }}>{ko ? it.v : (it.ven || it.v)}</td>
                                <td style={{ padding: "3px 8px 3px 0", fontWeight: 700,
                                             color: (it.s ?? 50) < 40 ? "#c62828"
                                               : (it.s ?? 50) >= 70 ? "#2e7d32" : "inherit" }}>
                                  {it.s ?? "-"}</td>
                                {hasW && <td style={{ padding: "3px 8px 3px 0" }}>
                                  {it.w != null ? `${it.w}%` : "-"}</td>}
                                {hasW && <td style={{ padding: "3px 0", fontWeight: 700 }}>
                                  {it.ctr != null ? `${it.ctr}` : "-"}{t("점", " pts")}</td>}
                              </tr>))}
                          </tbody>
                        </table>
                        {hasW && (
                          <div style={{ fontSize: 11, opacity: 0.6, marginTop: 4 }}>
                            {t(`가중치 0% 항목은 점수가 높아도 총점에 들어가지 않습니다 (회색). 표의 기여 합계 ≈ ${totC.toFixed(1)}점 — 여기에 갭·거래량·뉴스 관문 몫이 더해져 총점이 됩니다.`,
                               `A 0%-weight item adds nothing to the total even at score 100 (greyed). Contributions here sum to ≈ ${totC.toFixed(1)} pts — the gap/volume/news gates carry the rest of the total.`)}
                          </div>)}
                      </div>);
                    })()}
                  </div>)}
              </div>)}
          </div>
          </Fragment>);
      })}
    </div>
  );
}
