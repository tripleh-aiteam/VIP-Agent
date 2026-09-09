"use client";
/* 🎯 REAL-TIME PRICE CHOOSING — where each slice of an order actually stands.

   Boss 2026-09-09: "in the Why not buying part we have live order book. So I
   wanna show in the real time price choosing. For example in the selling it
   should be one down top highest volume price and we have to show in the demo
   and for buying case it should buy separately different efficient price by
   analyzing historical prices. For example if we have a 100 stock like 30 from
   this price another 20 another price like this."

   TWO SIDES, TWO DIFFERENT QUESTIONS — which is the whole point:

   SELLING is answered by the BOOK, right now. His standing law (08-11) is to
   stand one tick in front of the biggest wall, so the anchor is ONE TICK BELOW
   the largest ask — we clear before the crowd queued there. The rest of the
   order rests higher, where it only fills if the price comes to us.

   BUYING is answered by HISTORY, because there is no hurry: a buy that does not
   fill costs nothing, while a buy at a bad price costs money every time. The
   levels come from this stock's OWN habit — three months of "how far did it
   fall from its own open today", sorted, so each level is a depth it genuinely
   reaches on 75 / 55 / 35 / 15% of days — and the shares at each are weighted
   by that likelihood times how many sessions actually traded there.

   The split is deliberately UNEVEN. Equal fifths pretend every price is equally
   likely; these weights say what we actually believe. */
import { useEffect, useRef, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "@/components/api";

export type Book = { asks: [number, number][]; bids: [number, number][];
                     best_ask?: number; best_bid?: number; last?: number };
export type Slice = { px: number; qty: number; now: boolean; ko: string; en: string };
export type Plan = { slices: Slice[]; headKo: string; headEn: string; basis: string };

type Bar = { t: string; o: number; h: number; l: number; c: number; v?: number };

// KRX's own price ladder — the same table ml/krx_tick.py enforces server-side.
// A price off this grid cannot be placed at all, so every number below is
// snapped to it before it is shown.
const KOSDAQ = new Set(["042700", "247540"]);
export function tickSize(price: number, code = ""): number {
  if (price < 1_000) return 1;
  if (price < 5_000) return 5;
  if (price < 10_000) return 10;
  if (price < 50_000) return 50;
  if (price < 100_000) return 100;
  if (KOSDAQ.has(code)) return 100;
  if (price < 500_000) return 500;
  return 1_000;
}
const snap = (p: number, code: string) => {
  const t = tickSize(p, code);
  return Math.max(t, Math.round(p / t) * t);
};
const won = (n: number) => "₩" + Math.round(n).toLocaleString();
const pct = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";

/** SELL - ONE order at ONE price: one tick under the biggest ask wall.
    Boss 2026-09-09: "in case of the selling just put price one tick below
    highest volume". A sell that must leave has one job - clear ahead of the
    thickest queue - and splitting it only leaves part of the position sitting
    above the wall it was meant to get in front of. */
function sellPlan(code: string, qty: number, book: Book): Plan | null {
  const asks = (book.asks || []).filter(([p, q]) => p > 0 && q > 0);
  if (!asks.length) return null;
  const [wallPx, wallQty] = asks.reduce((a, b) => (b[1] > a[1] ? b : a));
  const tk = tickSize(wallPx, code);
  const anchor = snap(wallPx - tk, code);
  const dealsNow = !!book.best_bid && anchor <= book.best_bid;
  return {
    slices: [{
      px: anchor, qty, now: dealsNow,
      ko: `가장 두꺼운 매도벽 ${won(wallPx)}(${wallQty.toLocaleString()}주) 바로 한 호가 아래 — `
        + `그 줄보다 먼저 팔립니다. 전량 ${qty.toLocaleString()}주를 한 가격에 냅니다`
        + `${dealsNow ? " (지금 바로 체결)" : ""}.`,
      en: `one tick under the biggest ask wall ${won(wallPx)} (${wallQty.toLocaleString()} sh) - we clear `
        + `ahead of that queue. The whole ${qty.toLocaleString()} sh goes at this one price`
        + `${dealsNow ? " (deals now)" : ""}.`,
    }],
    basis: "book",
    headKo: `매도는 한 가격입니다 — 가장 두꺼운 매도벽 ${won(wallPx)}(${wallQty.toLocaleString()}주)의 한 호가 아래 ${won(anchor)}. `
      + `나가야 할 물량을 조각으로 나누면 일부가 벽 위에 남아 못 팔립니다.`,
    headEn: `A sell is ONE price - one tick under the biggest ask wall ${won(wallPx)} (${wallQty.toLocaleString()} sh), i.e. ${won(anchor)}. `
      + `Splitting stock that has to leave only strands part of it above the wall it was meant to beat.`,
  };
}

/** BUY — priced from where this stock has actually traded for three months. */
function buyPlan(code: string, qty: number, book: Book, bars: Bar[]): Plan | null {
  const now = book.best_ask || book.last || 0;
  if (!now || bars.length < 20) return null;

  // THE LEVELS ARE THIS STOCK'S OWN DAILY DIPS, so every one of them is a price
  // it actually reaches. Sorting three months of "how far did it fall from its
  // own open today" and taking the quarter / middle / two-thirds / far marks
  // gives four depths the stock hits on roughly 75 / 55 / 35 / 15% of days.
  //
  // Picking instead the levels where the MOST days traded looked cleverer and
  // was worse: measured on 09-09 it put 70% of an SK하이닉스 order 7-14% below
  // the market, at levels reached on 2-11% of days. That is not an efficient
  // price, it is money parked. Reachability first, then support as the weight.
  const dips = bars.map((b) => (b.o > 0 ? (b.o - b.l) / b.o : 0)).sort((a, b) => a - b);
  const q = (p: number) => dips[Math.min(dips.length - 1, Math.max(0, Math.round((dips.length - 1) * p)))];
  // TIME AT PRICE — of the last N sessions, how many actually traded through
  // this level. A level many days touched is a price the market kept agreeing
  // on; one nobody visited is a guess.
  const support = (px: number) => bars.filter((b) => b.l <= px && px <= b.h).length;
  // FIVE EFFICIENT PRICES - AND NOT ONE OF THEM THE MARKET PRICE (boss
  // 2026-09-09: "in case of the buying you just put market price; we should buy
  // efficient price, so you have to choose 5 different efficient prices").
  //
  // The first slice used to be the cheapest ask, 30% at whatever the screen
  // said - which is the one price in the plan that was never chosen, only
  // accepted. It is gone. All five now stand BELOW the market at depths this
  // stock genuinely reaches, taken from five marks of its own three-month
  // habit: 85 / 70 / 50 / 30 / 15% of days. A buy that does not fill costs
  // nothing; a buy at a price nobody chose costs money every time.
  //
  // When two marks snap onto the SAME KRX tick - which happens on a quiet
  // stock, and used to silently leave the plan with three prices instead of
  // five - the collision steps one tick lower so five real, distinct,
  // placeable prices always stand.
  const tkb = tickSize(now, code);
  const picked: { px: number; reach: number; days: number }[] = [];
  [0.15, 0.30, 0.50, 0.70, 0.85].forEach((p) => {
    let px = snap(now * (1 - q(p)), code);
    // bounded: snap() rounds to the KRX grid, and near a tick boundary it can
    // round straight back up - an unbounded while would then never terminate
    for (let g = 0; g < 40 && (px >= now || picked.some((x) => x.px === px)); g++) {
      const nx = snap(px - tkb, code);
      px = nx < px ? nx : px - tkb;
    }
    if (px > 0 && px < now && !picked.some((x) => x.px === px)) {
      picked.push({ px, reach: 1 - p, days: support(px) });
    }
  });
  if (picked.length < 5) return null;
  picked.sort((a, b) => b.px - a.px);                     // dearest first

  // size by (how often we actually get there) x (how many days really traded
  // through it) - the likeliest, best-supported level carries the most
  const wsum = picked.reduce((s, p) => s + p.reach * Math.max(p.days, 1), 0) || 1;
  const out: Slice[] = [];
  let left = qty;
  picked.forEach((p, i) => {
    const w = (p.reach * Math.max(p.days, 1)) / wsum;
    const n = i === picked.length - 1 ? left : Math.min(left, Math.max(1, Math.round(qty * w)));
    if (n <= 0) return;
    left -= n;
    out.push({
      px: p.px, qty: n, now: false,
      ko: `오늘 안에 여기까지 내려올 확률 ${Math.round(p.reach * 100)}% — 최근 ${bars.length}일 중 그만큼의 날이 시가에서 이 깊이(${pct(((p.px - now) / now) * 100)})까지 밀렸습니다. 이 가격대에서 실제로 거래된 날은 ${p.days}일. 지금 값 ${won(now)}보다 ${won(now - p.px)} 싸게 삽니다.`,
      en: `${Math.round(p.reach * 100)}% chance it reaches here today — that share of the last ${bars.length} sessions fell this far (${pct(((p.px - now) / now) * 100)}) from their own open. It actually traded at this level on ${p.days} of them, and it is ${won(now - p.px)} cheaper than the ${won(now)} on screen.`,
    });
  });
  return { slices: out.filter((s) => s.qty > 0), basis: "history",
    headKo: `매수는 시장가로 사지 않습니다 — 고른 5개 가격에만 겁니다. 안 사면 손해가 없고, 비싸게 사면 매번 손해입니다. 그래서 자리는 호가창이 아니라 이 종목 자신의 습관에서 고릅니다: 최근 ${bars.length}일 동안 시가에서 하루에 얼마나 밀렸는지를 줄 세워, 실제로 자주 닿는 깊이만 씁니다. 수량은 닿을 확률이 높을수록 많이 겁니다.`,
    headEn: `A buy never takes the market price - it rests at FIVE chosen prices, and is never in a hurry — an unfilled buy costs nothing, an expensive one costs every time. So the levels come from this stock's own habit rather than from the book: three months of "how far did it fall from its own open today", sorted, using only the depths it genuinely reaches. The likelier a level, the more shares stand there.` };
}

/** 🧠 THE WORK, AS ITS OWN BLOCK (boss 2026-09-09: "please move 'what it is
    doing right now' under the live executions - it is hiding other text"). It
    was standing beside the plan table and squeezing it; it belongs at the foot
    of the page, under the tape, where it has the full width to itself. */
export function ProcessSteps({ steps, step }:
  { steps: { ko: string; en: string; val: string }[]; step: number }) {
  const { t } = useLanguage();
  // IT MUST BE SEEN TO BE LIVE (boss 2026-09-09: "make sure this one should be
  // real time and must change automatically"). The book behind it has always
  // polled on a 3s clock, so these numbers were already recomputing - but a
  // screen where nothing moves cannot be told from a screen that is frozen.
  // A value that changes now flashes for a moment, the header carries a
  // pulsing LIVE mark, and the age of the last change counts up every second,
  // so the panel proves its own freshness instead of asking to be believed.
  const seen = useRef<string[]>([]);
  const [hot, setHot] = useState<Record<number, number>>({});
  const [changedAt, setChangedAt] = useState<number>(0);
  const [beats, setBeats] = useState(0);
  const [, setNowTick] = useState(0);
  const vals = steps.map((x) => x.val).join("|");
  useEffect(() => {
    const cur = steps.map((x) => x.val);
    const fresh: Record<number, number> = {};
    cur.forEach((v, i) => {
      if (seen.current.length && seen.current[i] !== undefined && seen.current[i] !== v) {
        fresh[i] = Date.now();
      }
    });
    const had = seen.current.length > 0;
    seen.current = cur;
    if (Object.keys(fresh).length) {
      setHot((h) => ({ ...h, ...fresh }));
      setChangedAt(Date.now());
      setBeats((b) => b + 1);
    } else if (!had) {
      setChangedAt(Date.now());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vals]);
  useEffect(() => {
    const id = setInterval(() => setNowTick((v) => v + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const ago = changedAt ? Math.max(0, Math.round((Date.now() - changedAt) / 1000)) : null;
  if (!steps.length) return null;
  return (
    <div className="rounded-xl border overflow-hidden"
         style={{ borderColor: "rgba(106,27,154,0.45)" }}>
      <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]"
           style={{ borderColor: "var(--border-default)" }}>
        <div className="flex items-center gap-2 flex-wrap">
          <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
            🧠 {t("지금 하고 있는 일 — 가격을 고르는 과정",
                  "what it is doing right now - how the price is chosen")}</b>
          <span className="flex items-center gap-1 text-[9.5px] font-extrabold px-1.5 py-[1px] rounded-full"
                style={{ background: "rgba(46,125,50,0.14)", color: "#2e7d32" }}>
            <span className="inline-block rounded-full animate-pulse"
                  style={{ width: 6, height: 6, background: "#2e7d32" }} />
            LIVE · 3s
          </span>
          <span className="text-[9.5px] tabular-nums" style={{ color: "var(--text-muted)" }}>
            {ago === null ? "" : t(`마지막 변화 ${ago}초 전 · 갱신 ${beats}회`,
                                   `last change ${ago}s ago · ${beats} updates`)}
          </span>
        </div>
        <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
          {t("아래 숫자는 캡션이 아니라 실제로 측정한 값이며, 호가가 3초마다 들어올 때마다 다시 계산됩니다. 값이 바뀌면 그 줄이 잠깐 켜집니다.",
             "every number below is a measured value, not a caption, recomputed each time the book arrives (every 3s). A value that changes lights up for a moment.")}
        </div>
      </div>
      <div className="px-4 py-2">
        {steps.map((sp, i) => (
          <div key={i} className="flex gap-2 items-baseline text-[11.5px] py-[3px]"
               style={{ opacity: i <= step ? 1 : 0.32, transition: "opacity .25s ease",
                        borderTop: i ? "1px solid rgba(106,27,154,0.13)" : undefined }}>
            <span className="shrink-0 tabular-nums" style={{ width: 14, color: "#6a1b9a" }}>
              {i < step ? "✓" : i === step ? "◍" : "·"}</span>
            <span className="flex-1 leading-[1.4]" style={{ color: "var(--text-secondary)" }}>
              {t(sp.ko, sp.en)}</span>
            <span className="shrink-0 font-bold tabular-nums text-right px-1 rounded"
                  style={{ color: "#6a1b9a",
                           background: hot[i] && Date.now() - hot[i] < 1600
                             ? "rgba(46,125,50,0.22)" : "transparent",
                           transition: "background .5s ease" }}>
              {i <= step ? sp.val : ""}</span>
          </div>))}
      </div>
    </div>);
}

export default function PricePlan({ code, book, onPlan, onSteps }:
  { code: string; book: Book | null; onPlan?: (p: Plan | null, side: "BUY" | "SELL") => void;
    onSteps?: (s: { ko: string; en: string; val: string }[], step: number) => void }) {
  const { t } = useLanguage();
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState(100);
  const [bars, setBars] = useState<Bar[] | null>(null);

  useEffect(() => {
    let dead = false;
    setBars(null);
    fetch(`${API}/approval/chart/${code}?mode=month3`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => { if (!dead) setBars(Array.isArray(d?.bars) ? d.bars : []); })
      .catch(() => { if (!dead) setBars([]); });
    return () => { dead = true; };
  }, [code]);

  const plan = !book ? null
    : side === "SELL" ? sellPlan(code, qty, book)
    : (bars ? buyPlan(code, qty, book, bars) : null);

  useEffect(() => { onPlan?.(plan, side); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(plan?.slices || null), side]);

  const filled = (plan?.slices || []).reduce((s, x) => s + x.qty, 0);

  // THE WORK, SHOWN AS IT HAPPENS (boss 2026-09-09: "on the right side there is
  // a space, so please show the process like thinking, checking, choosing
  // prices"). Every line below reports a REAL measured value, not a caption -
  // the wall it found, the days it read, the depths it kept. The steps reveal
  // one at a time when the stock, the side or the size changes; after that the
  // numbers keep updating live with the book, which is the point of the panel.
  const [step, setStep] = useState(0);
  useEffect(() => {
    setStep(0);
    const id = setInterval(() => setStep((v) => (v >= 6 ? v : v + 1)), 400);
    return () => clearInterval(id);
  }, [code, side, qty]);

  const asksL = (book?.asks || []).filter(([p, q]) => p > 0 && q > 0);
  const bidsL = (book?.bids || []).filter(([p, q]) => p > 0 && q > 0);
  const wall = asksL.length ? asksL.reduce((a, b) => (b[1] > a[1] ? b : a)) : null;
  const dipMid = (() => {
    const d = (bars || []).filter((b) => b.o > 0).map((b) => (b.o - b.l) / b.o).sort((a, b) => a - b);
    return d.length ? d[Math.floor(d.length / 2)] : null;
  })();
  const steps: { ko: string; en: string; val: string }[] = side === "SELL"
    ? [
        { ko: "호가창을 읽습니다", en: "reading the order book",
          val: book ? `${t("매도 ", "asks ")}${asksL.length} · ${t("매수 ", "bids ")}${bidsL.length}` : "…" },
        { ko: "가장 두꺼운 매도벽을 찾습니다", en: "finding the thickest ask wall",
          val: wall ? `${won(wall[0])} · ${wall[1].toLocaleString()}${t("주", " sh")}` : "…" },
        { ko: "그 벽보다 한 호가 아래에 섭니다", en: "standing one tick in front of it",
          val: plan?.slices[0] ? won(plan.slices[0].px) : "…" },
        { ko: "지금 바로 팔리는 자리인지 봅니다", en: "checking whether it deals now",
          val: !plan ? "…" : plan.slices[0]?.now ? t("바로 체결", "deals now") : t("기다립니다", "it waits") },
        { ko: "한 가격으로 확정합니다", en: "settling on ONE price",
          val: plan ? `${filled.toLocaleString()}${t("주", " sh")}` : "…" },
      ]
    : [
        { ko: "지금 시장 값이 얼마인지만 봅니다", en: "noting what the market costs right now",
          val: book ? `${t("최우선 매도 ", "best ask ")}${won(book.best_ask || book.last || 0)}` : "…" },
        { ko: "이 종목의 3개월 습관을 읽습니다", en: "reading this stock's own 3-month habit",
          val: bars === null ? "…" : `${bars.length}${t("일", " sessions")}` },
        { ko: "하루에 얼마나 밀리는지 줄 세웁니다", en: "sorting how far it falls each day",
          val: dipMid === null ? "…" : `${t("중앙값 ", "median ")}${pct(-dipMid * 100)}` },
        { ko: "실제로 닿는 자리만 고릅니다", en: "keeping only the depths it truly reaches",
          val: plan ? `${plan.slices.length}${t("개 가격", " prices")}` : "…" },
        { ko: "닿을 확률만큼 수량을 나눕니다", en: "weighting the size by how likely each is",
          val: plan?.slices[0] ? `${t("첫 자리 ", "top ")}${plan.slices[0].qty.toLocaleString()}${t("주", " sh")}` : "…" },
        { ko: "시장가는 쓰지 않습니다 — 5개 가격 확정", en: "no market price - FIVE chosen prices settled",
          val: plan ? `${filled.toLocaleString()}${t("주", " sh")}` : "…" },
      ];

  useEffect(() => { onSteps?.(steps, step); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(steps), step]);


  return (
    <div className="mt-1.5 px-2.5 py-2 rounded-lg"
         style={{ background: "rgba(106,27,154,0.06)", border: "1px solid rgba(106,27,154,0.25)" }}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] font-extrabold" style={{ color: "#6a1b9a" }}>
          🎯 {t("실시간 가격 선택 — 이 주문을 어디에 나눠 걸까", "real-time price choosing — where this order is split")}
        </span>
        {(["BUY", "SELL"] as const).map((s) => (
          <button key={s} onClick={() => setSide(s)}
            className="text-[10px] px-2 py-[1px] rounded-full border font-bold"
            style={{ borderColor: "#6a1b9a", background: side === s ? "#6a1b9a" : "transparent",
                     color: side === s ? "#fff" : "var(--text-primary)" }}>
            {s === "BUY" ? t("살 때", "buying") : t("팔 때", "selling")}</button>))}
        <span className="text-[10px] text-[var(--text-muted)]">{t("수량", "shares")}</span>
        <input type="number" value={qty} min={1}
               onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))}
               className="text-[10.5px] w-[70px] px-1.5 py-[1px] rounded border tabular-nums text-right"
               style={{ borderColor: "rgba(106,27,154,0.45)", background: "var(--bg-input)",
                        color: "var(--text-primary)" }} />
        <span className="text-[10px] text-[var(--text-muted)]">
          {t("주 — 표에 🎯로 자리를 표시합니다", "sh — the 🎯 rows below are those places")}</span>
      </div>

      {/* THE PLAN ON THE LEFT, THE WORK ON THE RIGHT (boss 2026-09-09: "on the
          right side there is a space, so please show the process like thinking,
          checking, choosing prices"). */}
      {plan && (
        <div className="text-[10.5px] mt-1.5 leading-[1.55]" style={{ color: "var(--text-secondary)" }}>
          {t(plan.headKo, plan.headEn)}
        </div>)}

      {!plan && (
        <div className="text-[10.5px] mt-1.5 text-[var(--text-muted)]">
          {side === "BUY" && bars === null ? t("최근 3개월 가격을 읽는 중…", "reading the last three months…")
            : side === "BUY" ? t("과거 가격이 아직 없어 매수 자리를 계산할 수 없습니다.",
                                 "no history yet, so buy levels cannot be worked out.")
            : t("호가창이 아직 오지 않았습니다.", "the order book has not arrived yet.")}
        </div>)}

      {plan && (
        <table className="w-full text-[10.5px] tabular-nums mt-1.5">
          <thead>
            <tr className="text-[9.5px] text-[var(--text-muted)] text-left">
              <th className="py-[2px] pr-2">{t("조각", "slice")}</th>
              <th className="pr-2">{t("가격", "price")}</th>
              <th className="pr-2 text-right">{t("수량", "shares")}</th>
              <th className="pr-2 text-right">{t("금액", "value")}</th>
              <th className="pr-2 text-right">{t("그 자리 잔량", "others waiting")}</th>
              <th>{t("왜 이 가격인가", "why this price")}</th>
            </tr>
          </thead>
          <tbody>
            {plan.slices.map((s, i) => (
              <tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.18)" }}>
                <td className="py-[3px] pr-2 font-bold" style={{ color: "#6a1b9a" }}>
                  {i + 1}{s.now && <span className="ml-1 text-[9px] px-1 rounded"
                    style={{ background: "#6a1b9a", color: "#fff" }}>
                    {t("지금", "now")}</span>}
                </td>
                <td className="pr-2 font-bold">{won(s.px)}</td>
                <td className="pr-2 text-right font-bold">{s.qty.toLocaleString()}</td>
                <td className="pr-2 text-right text-[var(--text-muted)]">{won(s.px * s.qty)}</td>
                {/* HOW MANY OTHERS ARE ALREADY QUEUED THERE (boss 2026-09-09:
                    "we wanna see others volume also"). Our slice joins a line
                    that already exists; its length is the whole reason one
                    price is a better place to stand than another. */}
                <td className="pr-2 text-right" style={{ color: "var(--text-muted)" }}>
                  {(() => {
                    const lvl = [...(book?.asks || []), ...(book?.bids || [])]
                      .find(([p]) => Math.abs(p - s.px) < 0.5);
                    return lvl ? lvl[1].toLocaleString() + t("주", " sh")
                               : t("대기 없음", "none");
                  })()}</td>
                <td className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
                  {t(s.ko, s.en)}</td>
              </tr>))}
          </tbody>
        </table>)}

      {plan && (
        <div className="text-[10px] mt-1.5" style={{ color: "var(--text-muted)" }}>
          {plan.slices.length > 1
            ? t(`합계 ${filled.toLocaleString()}주 · ${won(plan.slices.reduce((a, s) => a + s.px * s.qty, 0))} — 한 가격에 다 넣지 않는 이유는 그것이 모든 값이 똑같이 나올 거라고 가정하는 일이기 때문입니다. 위 배분은 실제로 그렇게 믿는 만큼입니다.`,
                `${filled.toLocaleString()} sh · ${won(plan.slices.reduce((a, s) => a + s.px * s.qty, 0))} — one price for the whole order would assume every level is equally likely. These weights say what we actually believe.`)
            : t(`합계 ${filled.toLocaleString()}주 · ${won(plan.slices.reduce((a, s) => a + s.px * s.qty, 0))} — 매도는 나눠 걸지 않습니다. 나가야 할 물량은 가장 두꺼운 벽보다 한 호가 먼저 서서 한 번에 비웁니다.`,
                `${filled.toLocaleString()} sh · ${won(plan.slices.reduce((a, s) => a + s.px * s.qty, 0))} — a sell is not spread. Stock that has to leave stands one tick in front of the thickest wall and clears in one go.`)}
        </div>)}

    </div>);
}
