"use client";
/* 🧠 HOW THE PRICE IS BEING CHOSEN — BOTH SIDES AT ONCE, LIVE.

   Boss 2026-09-10: "make a simulation; like thinking, started to finding 5
   different buying position, analyze market and find 5 efficient price and
   price choosen, so it should automatically changing. In the alongside we
   should like for selling also like thinking, then finding highest volume then
   under this fixing selling price. So please implement this in the menu 3 in
   both semi auto and auto."

   Menu 1 already narrated this, but only for ONE side at a time — whichever the
   BUY/SELL switch was on — so the half he was not looking at was invisible, and
   Menu 3 had no narration at all. Here the two run side by side: buying is
   answered by the stock's own habit (five efficient prices, never the market
   price), selling by the book right now (the highest-volume price, one tick
   under it). The pricing itself is NOT re-implemented — buyPlan/sellPlan are the
   same functions Menu 1 uses, so the two menus can never drift apart.

   The book is re-read every 3s and the 3-month habit every 30s, which is what
   makes the numbers move on their own. */
import { useEffect, useRef, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "@/components/api";
import { buyPlan, sellPlan, type Book, type Plan } from "@/components/PricePlan";

type Bar = { t: string; o: number; h: number; l: number; c: number; v?: number };
const won = (n: number) => "₩" + Math.round(n).toLocaleString();
const pct = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";

/** One narration line. `val` is a measured number, never a caption — and it
    flashes only when it actually changed, so a still line reads as "unchanged",
    not "stuck". */
function Line({ on, ko, en, val }: { on: boolean; ko: string; en: string; val: string }) {
  const { t } = useLanguage();
  const prev = useRef(val);
  const [hot, setHot] = useState(false);
  useEffect(() => {
    if (prev.current !== val && on) {
      prev.current = val;
      setHot(true);
      const id = setTimeout(() => setHot(false), 900);
      return () => clearTimeout(id);
    }
    prev.current = val;
  }, [val, on]);
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 8, padding: "3px 6px",
      borderRadius: 6, opacity: on ? 1 : 0.28,
      background: hot ? "rgba(46,125,50,0.16)" : "transparent",
      transition: "background 500ms ease",
    }}>
      <span style={{ fontSize: 11.5, opacity: 0.85 }}>{on ? "✓" : "·"}</span>
      <span style={{ fontSize: 12, flex: 1 }}>{t(ko, en)}</span>
      <span style={{ fontSize: 12, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
        {on ? val : "…"}
      </span>
    </div>
  );
}

export default function PriceThinking({ code, name, qty = 100 }:
  { code: string; name?: string; qty?: number }) {
  const { t } = useLanguage();
  const [book, setBook] = useState<Book | null>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [step, setStep] = useState(0);

  // the book drives everything on the sell side — 3s, same clock as Menu 1
  useEffect(() => {
    if (!code) return;
    let dead = false;
    setBook(null);
    const pull = () => fetch(`${API}/paper-desk/live/book?code=${code}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((b) => { if (!dead && b?.asks) setBook(b); })
      .catch(() => {});
    pull();
    const iv = setInterval(pull, 3000);
    return () => { dead = true; clearInterval(iv); };
  }, [code]);

  // the 3-month habit drives the buy side — 30s; a daily bar cannot move faster
  useEffect(() => {
    if (!code) return;
    let dead = false;
    setBars(null);
    const pull = () => fetch(`${API}/approval/chart/${code}?mode=month3`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => { if (!dead) setBars(Array.isArray(d?.bars) ? d.bars : []); })
      .catch(() => { if (!dead) setBars([]); });
    pull();
    const iv = setInterval(pull, 30000);
    return () => { dead = true; clearInterval(iv); };
  }, [code]);

  // reveal the lines one at a time whenever the stock changes, then let the
  // numbers keep moving on their own
  useEffect(() => {
    setStep(0);
    const id = setInterval(() => setStep((v) => (v >= 5 ? v : v + 1)), 450);
    return () => clearInterval(id);
  }, [code]);

  const bPlan: Plan | null = book && bars && bars.length >= 20 ? buyPlan(code, qty, book, bars) : null;
  const sPlan: Plan | null = book ? sellPlan(code, qty, book) : null;
  const asksL = (book?.asks || []).filter(([p, q]) => p > 0 && q > 0);
  const wall = asksL.length ? asksL.reduce((a, b) => (b[1] > a[1] ? b : a)) : null;
  const mkt = book?.best_ask || book?.last || 0;
  const dipMid = (() => {
    const d = (bars || []).filter((b) => b.o > 0).map((b) => (b.o - b.l) / b.o).sort((a, b) => a - b);
    return d.length ? d[Math.floor(d.length / 2)] : null;
  })();

  const col: React.CSSProperties = {
    flex: "1 1 320px", minWidth: 280, border: "1px solid var(--border-default)",
    borderRadius: 10, padding: "8px 10px",
  };

  return (
    <div style={{ marginTop: 14, border: "1px solid rgba(46,125,50,0.45)",
                  borderRadius: 10, padding: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13.5 }}>
          🧠 {t("가격을 고르는 과정 — 지금 이 순간", "How the price is being chosen — right now")}
        </b>
        <span style={{ fontSize: 11, opacity: 0.65 }}>
          {name || code} · {t("호가 3초 · 습관 30초마다 갱신", "book every 3s · habit every 30s")}
        </span>
      </div>

      <div style={{ display: "flex", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
        {/* ── BUYING: answered by this stock's own habit ── */}
        <div style={col}>
          <b style={{ fontSize: 12.5, color: "#c62828" }}>
            🛒 {t("매수 — 5개 효율가", "BUY — five efficient prices")}
          </b>
          <div style={{ marginTop: 4 }}>
            <Line on={step >= 0} ko="생각합니다 — 이 종목을 어떻게 살지"
                  en="thinking — how to buy this one"
                  val={code ? (name || code) : "…"} />
            <Line on={step >= 1} ko="5개 매수 자리를 찾기 시작합니다"
                  en="starting to find 5 buying positions"
                  val={mkt ? `${t("시장가 ", "market ")}${won(mkt)}` : "…"} />
            <Line on={step >= 2} ko="시장과 과거 데이터를 분석합니다"
                  en="analysing the market and our historical data"
                  val={bars === null ? "…" : `${bars.length}${t("일", " sessions")}`} />
            <Line on={step >= 3} ko="실제로 닿는 깊이만 고릅니다"
                  en="keeping only the depths it truly reaches"
                  val={dipMid === null ? "…" : `${t("중앙값 ", "median ")}${pct(-dipMid * 100)}`} />
            <Line on={step >= 4} ko="효율가 5개를 정합니다"
                  en="settling on the 5 efficient prices"
                  val={bPlan ? `${bPlan.slices.length}${t("개 가격", " prices")}` : "…"} />
          </div>
          {bPlan && step >= 5 && (
            <div style={{ marginTop: 6, borderTop: "1px dashed var(--border-default)", paddingTop: 6 }}>
              {bPlan.slices.map((s, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between",
                                      fontSize: 11.5, padding: "1.5px 0" }}>
                  <span style={{ opacity: 0.7 }}>{i + 1}.</span>
                  <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{won(s.px)}</span>
                  <span style={{ opacity: 0.75, fontVariantNumeric: "tabular-nums" }}>
                    {s.qty.toLocaleString()}{t("주", " sh")}
                  </span>
                </div>
              ))}
              <div style={{ fontSize: 10.5, opacity: 0.6, marginTop: 3 }}>
                {t("시장가는 쓰지 않습니다 — 안 사면 손해가 없고, 비싸게 사면 매번 손해입니다.",
                   "never the market price — an unfilled buy costs nothing, an expensive one costs every time.")}
              </div>
            </div>
          )}
        </div>

        {/* ── SELLING: answered by the book, right now ── */}
        <div style={col}>
          <b style={{ fontSize: 12.5, color: "#1565c0" }}>
            🏷 {t("매도 — 거래 최다 가격의 한 호가 아래", "SELL — one tick under the highest-volume price")}
          </b>
          <div style={{ marginTop: 4 }}>
            <Line on={step >= 0} ko="생각합니다 — 이 종목을 어떻게 팔지"
                  en="thinking — how to sell this one"
                  val={code ? (name || code) : "…"} />
            <Line on={step >= 1} ko="호가창을 읽습니다" en="reading the order book"
                  val={book ? `${t("매도 ", "asks ")}${asksL.length}` : "…"} />
            <Line on={step >= 2} ko="거래가 가장 많이 몰린 가격을 찾습니다"
                  en="finding the highest-volume price"
                  val={wall ? `${won(wall[0])} · ${wall[1].toLocaleString()}${t("주", " sh")}` : "…"} />
            <Line on={step >= 3} ko="그 아래 한 호가로 매도가를 정합니다"
                  en="fixing the sell price one tick under it"
                  val={sPlan?.slices[0] ? won(sPlan.slices[0].px) : "…"} />
            <Line on={step >= 4} ko="지금 바로 팔리는 자리인지 봅니다"
                  en="checking whether it deals now"
                  val={!sPlan ? "…" : sPlan.slices[0]?.now ? t("바로 체결", "deals now") : t("기다립니다", "it waits")} />
          </div>
          {sPlan && step >= 5 && (
            <div style={{ marginTop: 6, borderTop: "1px dashed var(--border-default)", paddingTop: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
                <span style={{ opacity: 0.7 }}>{t("확정", "settled")}</span>
                <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {won(sPlan.slices[0].px)}
                </span>
                <span style={{ opacity: 0.75, fontVariantNumeric: "tabular-nums" }}>
                  {sPlan.slices[0].qty.toLocaleString()}{t("주", " sh")}
                </span>
              </div>
              <div style={{ fontSize: 10.5, opacity: 0.6, marginTop: 3 }}>
                {t("매도는 한 가격입니다 — 나눠 걸면 일부가 벽 위에 남아 못 팔립니다.",
                   "a sell is ONE price — splitting it strands part of the position above the wall.")}
              </div>
            </div>
          )}
        </div>
      </div>
      {!book && (
        <div style={{ fontSize: 11.5, opacity: 0.6, marginTop: 6 }}>
          {t("호가창을 기다리는 중입니다 — 장중에만 움직입니다.",
             "waiting for the order book — it only moves during market hours.")}
        </div>
      )}
    </div>
  );
}
