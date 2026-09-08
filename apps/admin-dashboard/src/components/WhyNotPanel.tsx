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
import LiveBookTape from "@/components/LiveBookTape";
import { API } from "./api";

type DistWin = { k: string; ko: string; en: string; n: number; below: number; pct: number };
type Dist = { closes: number[]; px: number; windows: DistWin[]; rank_avg: number;
              plain?: number; weighted?: number; near?: number; near_pct?: number;
              rank5?: number | null };
type Gate = { n: number; key: string; passed: boolean; ko: string; en: string;
              link?: string | null; dist?: Dist | null };
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

/* 📊 EVERY DAY, DRAWN — the proof that the gate does not live on 3 numbers
   (boss 2026-09-07: "show that we are not caring only 3 numbers"). One tick
   per trading day, placed by its closing price; blue = a day cheaper than
   today, grey = dearer; the red line is today. Count the blue ones and you
   have the percentage the text states. */
function DistStrip({ d, ko, t }: { d: Dist; ko: boolean;
                                   t: (a: string, b: string) => string }) {
  const rows = d.windows;
  const Wd = 560, rowH = 34, padL = 62, padR = 8;
  // the WHOLE read leads: one row holding every day, recent days brighter —
  // one analysis, not four chunks (boss 2026-09-07: "it should be analysed
  // based, not chunk based"). The window cuts follow underneath as detail.
  const all = d.closes;
  const aLo = Math.min(...all, d.px), aHi = Math.max(...all, d.px);
  const aX = (v: number) => padL + ((v - aLo) / Math.max(1e-9, aHi - aLo)) * (Wd - padL - padR);
  const H = rows.length * rowH + 16;
  return (
    <div style={{ overflowX: "auto", marginTop: 6 }}>
      <svg width={Wd} height={54} style={{ display: "block" }}>
        <text x={0} y={14} fontSize={10.5} fill="currentColor" opacity={0.85} fontWeight={700}>
          {t(`전체 ${all.length}일`, `all ${all.length} days`)}</text>
        <line x1={padL} x2={Wd - padR} y1={30} y2={30}
              stroke="currentColor" strokeOpacity={0.18} strokeWidth={1} />
        {all.map((c, k) => (
          <line key={k} x1={aX(c)} x2={aX(c)} y1={30 - 11} y2={30 + 11}
                stroke={c < d.px ? "#1e88e5" : "#9e9e9e"}
                strokeOpacity={Math.max(0.16, Math.pow(0.5, k / 20) * 0.9)}
                strokeWidth={1.6}>
            <title>{`${k === 0 ? "yesterday" : k + " sessions ago"} · ₩${Math.round(c).toLocaleString()}`}</title>
          </line>))}
        <line x1={aX(d.px)} x2={aX(d.px)} y1={30 - 16} y2={30 + 16}
              stroke="#e53935" strokeWidth={2.4} />
        <text x={aX(d.px)} y={12} fontSize={10} fill="#e53935" textAnchor="middle" fontWeight={800}>
          {d.plain != null ? `${d.plain}%` : ""}</text>
        <text x={padL} y={49} fontSize={9.5} fill="currentColor" opacity={0.55}>
          ₩{Math.round(aLo).toLocaleString()}</text>
        <text x={Wd - padR} y={49} fontSize={9.5} fill="currentColor" opacity={0.55}
              textAnchor="end">₩{Math.round(aHi).toLocaleString()}</text>
      </svg>
      <div style={{ fontSize: 11, opacity: 0.72, margin: "1px 0 6px" }}>
        {t(`↑ 눈금 하나가 하루 종가입니다 — 최근일수록 진하게. 파란 눈금이 오늘보다 쌌던 날 (${d.plain ?? "-"}%), 최근 가중 ${d.weighted ?? "-"}%${d.near != null ? ` · 이 가격대에서 보낸 날 ${d.near}일` : ""}.`,
           `↑ one tick = one day's close, brighter = more recent. Blue ticks are days cheaper than today (${d.plain ?? "-"}%), recency-weighted ${d.weighted ?? "-"}%${d.near != null ? ` · days spent at this price ${d.near}` : ""}.`)}
      </div>
      <div style={{ fontSize: 10.5, opacity: 0.6, marginBottom: 2 }}>
        {t("같은 자료를 기간별로 잘라 본 모습:", "the same data, cut into windows:")}
      </div>
      <svg width={Wd} height={H} style={{ display: "block" }}>
        {rows.map((w, i) => {
          const cl = d.closes.slice(0, w.n);
          const lo = Math.min(...cl, d.px), hi = Math.max(...cl, d.px);
          const y = i * rowH + 20;
          const x = (v: number) => padL + ((v - lo) / Math.max(1e-9, hi - lo)) * (Wd - padL - padR);
          return (
            <g key={w.k}>
              <text x={0} y={y + 4} fontSize={10.5} fill="currentColor" opacity={0.8}>
                {ko ? w.ko : w.en}</text>
              <line x1={padL} x2={Wd - padR} y1={y} y2={y}
                    stroke="currentColor" strokeOpacity={0.18} strokeWidth={1} />
              {cl.map((c, k) => (
                <line key={k} x1={x(c)} x2={x(c)} y1={y - 7} y2={y + 7}
                      stroke={c < d.px ? "#1e88e5" : "#9e9e9e"}
                      strokeOpacity={c < d.px ? 0.75 : 0.45} strokeWidth={1.5}>
                  <title>{`₩${Math.round(c).toLocaleString()}`}</title>
                </line>))}
              <line x1={x(d.px)} x2={x(d.px)} y1={y - 12} y2={y + 12}
                    stroke="#e53935" strokeWidth={2.2} />
              <text x={padL} y={y + 21} fontSize={9.5} fill="currentColor" opacity={0.55}>
                ₩{Math.round(lo).toLocaleString()}</text>
              <text x={Wd - padR} y={y + 21} fontSize={9.5} fill="currentColor"
                    opacity={0.55} textAnchor="end">₩{Math.round(hi).toLocaleString()}</text>
              <text x={x(d.px)} y={y - 15} fontSize={9.5} fill="#e53935"
                    textAnchor="middle" fontWeight={700}>
                {w.below}/{w.n}</text>
            </g>);
        })}
      </svg>
      <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
        {t(`🔵 파란 눈금 = 오늘보다 쌌던 날 · ⚪ 회색 = 더 비쌌던 날 · 🔴 빨간 선 = 지금 가격 ₩${Math.round(d.px).toLocaleString()}. 눈금 하나가 하루입니다 — 직접 세어보실 수 있습니다 (모든 날 평균 ${d.rank_avg}%).`,
           `🔵 blue tick = a day cheaper than today · ⚪ grey = dearer · 🔴 red line = now ₩${Math.round(d.px).toLocaleString()}. Every tick is one trading day — you can count them yourself (all-days average ${d.rank_avg}%).`)}
      </div>
    </div>);
}

export default function WhyNotPanel() {
  const { t } = useLanguage();
  const ko = t("k", "e") === "k";
  const [data, setData] = useState<Payload | null>(null);
  const [open, setOpen] = useState<string | null>(null);      // stable: the code
  const [itemsOpen, setItemsOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 📗 THE LIVE WINDOWS, OVER THIS BOARD (boss 2026-09-08: "기존 설명은 있는
  // 그대로 띄우고, 실시간 호가 창과 실시간 체결 창은 팝업 형태로"). The gate
  // explanation below is untouched; this only adds the live pair on top of it.
  // ONE stock at a time on purpose - every poll is a real Kiwoom REST call on a
  // shared session, and LiveBookTape stops polling the moment this closes.
  const [popCode, setPopCode] = useState<string | null>(null);

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

  useEffect(() => {
    if (!popCode) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setPopCode(null); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [popCode]);

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
        {t("관문이 있으니 기회는 적습니다 — 팝업이 안 오는 시간 동안, 종목마다 어느 관문에서 왜 멈춰 있는지 실제 숫자로 증명합니다. 종목을 클릭하면 관문 판정이 펼쳐지고, 📗 버튼을 누르면 그 종목의 실시간 호가·체결 창이 이 화면 위에 뜹니다.",
           "The gates make chances few — while no popup comes, this proves with real numbers which gate each stock is stopped at. Click a stock for its gate verdicts; press 📗 for its live order book and execution tape, over this page.")}
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
              {/* the live pair for THIS stock — never toggles the card */}
              <button onClick={(e) => { e.stopPropagation(); setPopCode(r.code); }}
                      title={t("실시간 호가·체결 창 열기", "open the live order book and execution tape")}
                      style={{ marginLeft: "auto", fontSize: 11, fontWeight: 800,
                               padding: "3px 9px", borderRadius: 999, cursor: "pointer",
                               border: "1.5px solid #00838f", background: "transparent",
                               color: "#00838f" }}>
                📗 {t("실시간 호가·체결", "live book & tape")}
              </button>
              <span style={{ fontSize: 11, opacity: 0.5 }}>{isOpen ? "▲" : "▼"}</span>
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
                    {g.dist && <DistStrip d={g.dist} ko={ko} t={t} />}
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

      {/* 📗📼 the live pair, over the board — the exact section from the live
          desk (/testing/live), rendered from the one shared component so the
          two screens can never drift apart */}
      {popCode && (() => {
        const r = rows.find((x) => x.code === popCode);
        const nm = r ? (ko ? r.name : (r.name_en || r.name)) : popCode;
        return (
          <div onClick={() => setPopCode(null)}
               style={{ position: "fixed", inset: 0, zIndex: 9000, overflowY: "auto",
                        background: "rgba(0,0,0,0.55)", display: "flex",
                        alignItems: "flex-start", justifyContent: "center",
                        padding: "3vh 2vw" }}>
            <div onClick={(e) => e.stopPropagation()}
                 style={{ width: "min(1180px, 96vw)", borderRadius: 14, cursor: "default",
                          background: "var(--bg-card, #fff)", color: "var(--text-primary)",
                          border: "2px solid #00838f", padding: "12px 14px 14px",
                          boxShadow: "0 18px 60px rgba(0,0,0,0.45)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10,
                            flexWrap: "wrap", marginBottom: 10 }}>
                <b style={{ fontSize: 15, color: "#00838f" }}>
                  📗 {nm} ({popCode}) — {t("실시간 호가 · 실시간 체결", "live order book · live executions")}
                </b>
                {r && (
                  <span style={{ fontSize: 11.5, opacity: 0.75 }}>
                    {W(r.px)}{r.now_vs_yc != null && <> ({r.now_vs_yc >= 0 ? "+" : ""}{r.now_vs_yc}% {t("vs 어제", "vs yesterday")})</>}
                    {" · "}{ko ? r.verdict_ko : r.verdict_en}
                  </span>)}
                <button onClick={() => setPopCode(null)}
                        style={{ marginLeft: "auto", fontSize: 12, fontWeight: 800,
                                 padding: "4px 12px", borderRadius: 8, cursor: "pointer",
                                 border: "1px solid rgba(128,128,128,0.45)",
                                 background: "transparent", color: "inherit" }}>
                  {t("닫기 ✕ (ESC)", "close ✕ (ESC)")}
                </button>
              </div>
              <LiveBookTape code={popCode} />
              <div style={{ fontSize: 10.5, opacity: 0.6, marginTop: 8 }}>
                {t("3초마다 새로 받아옵니다. 창을 닫으면 조회도 멈춥니다 — 한 번에 한 종목만 봅니다.",
                   "refreshed every 3 seconds; closing this stops the polling — one stock at a time.")}
              </div>
            </div>
          </div>);
      })()}
    </div>
  );
}
