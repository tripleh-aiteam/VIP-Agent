"use client";
/* 📗📼 LIVE BOOK + TAPE — the two live windows, as ONE component.

   They were born inside /testing/live as inline JSX bound to that page's
   state. The boss then asked (2026-09-08) that clicking a stock in Menu 3's
   관문 증명 open the SAME two windows over the page — "기존 설명은 있는 그대로
   띄우고, 실시간 호가 창과 실시간 체결 창은 기존 섹션을 팝업으로". Two copies
   of a screen he compares against his Kiwoom HTS would drift apart within a
   week, so the block moved here and BOTH places render this one file.

   Self-contained on purpose: give it a code and it feeds itself, so a popup
   needs no plumbing from whatever page opened it. Polling stops the moment it
   unmounts — one stock's book at a time, because every poll is a real Kiwoom
   REST call on a shared session.

   Every endpoint here is per-code and works for ANY stock, not only the six:
   /live/book asks Kiwoom on demand, and the tape collector watches his six
   plus the checklist's fourteen — exactly the twenty the gate board shows. */
import { useEffect, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import PricePlan, { type Plan } from "@/components/PricePlan";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";
const PURPLE = "#6a1b9a";

type Book = { ok: boolean; code: string; name?: string; asks: [number, number][];
              bids: [number, number][]; best_ask?: number; best_bid?: number;
              last?: number; prev_close?: number; change_pct?: number;
              market?: string; tot_ask?: number; tot_bid?: number };
type Execs = { ok: boolean; prev_close?: number; total: number;
               rows: { t: string; px: number; qty: number }[] };

const fmt = (n?: number | null) => (n == null ? "-" : n.toLocaleString());

export default function LiveBookTape({ code, tapeHeight = 300 }:
  { code: string; tapeHeight?: number }) {
  const { t } = useLanguage();
  const [book, setBook] = useState<Book | null>(null);
  const [execs, setExecs] = useState<Execs | null>(null);
  // 🎯 the price plan the panel below works out, lifted here so the book table
  // can mark the very rows those slices would stand on
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planSide, setPlanSide] = useState<"BUY" | "SELL">("BUY");

  // one poll for the three windows, on the desk's own 3s clock. `dead` guards
  // the in-flight replies: a popup closed mid-request must not setState.
  useEffect(() => {
    if (!code) return;
    let dead = false;
    setBook(null); setExecs(null); setPlan(null);     // never show the last stock's book
    const pull = () => {
      api<Book>(`/paper-desk/live/book?code=${code}`)
        .then((d) => { if (!dead) setBook(d); }).catch(() => {});
      api<Execs>(`/paper-desk/live/execs?code=${code}&n=120`)
        .then((d) => { if (!dead) setExecs(d); }).catch(() => {});
    };
    pull();
    const iv = setInterval(pull, 3000);
    return () => { dead = true; clearInterval(iv); };
  }, [code]);

  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
      {/* 호가 — who is waiting to buy and to sell */}
      <div className="rounded-xl border overflow-hidden" style={{ borderColor: TEAL }}>
        <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
          <b className="text-[13px]" style={{ color: TEAL }}>
            📗 {t("실시간 호가 — 사려는 사람과 팔려는 사람", "live order book - buyers and sellers waiting")}
            {/* WHOSE BOOK, AND WHICH BOOK (boss 2026-09-07: the table carried
                neither the stock's name nor the market, so it could be read
                as belonging to whatever card was above it, and compared
                against a Kiwoom screen showing the unified book) */}
            {book?.name && <span className="ml-1">— {book.name} ({code})</span>}
            {book?.market && (
              <span className="ml-1 text-[10px] font-normal opacity-75">
                {book.market === "AL" ? t("통합호가 KRX+NXT (키움 HTS와 동일)", "unified book KRX+NXT (same as the Kiwoom HTS)")
                                      : t("KRX 단독", "KRX only")}</span>)}
          </b>
          <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
            {t("사면 가장 싼 매도호가를, 팔면 가장 비싼 매수호가를 잡습니다 — 그 차이가 왕복 비용의 절반입니다.",
               "a buy takes the cheapest ask, a sell takes the highest bid - that gap is half the round-trip cost.")}
          </div>
          {/* 🎯 REAL-TIME PRICE CHOOSING (boss 2026-09-09). This replaced the
              🪜 ladder demo, which split evenly and priced both sides the same
              way. A sell and a buy are not the same question: the sell is
              answered by the wall in this book right now, the buy by where the
              stock has actually traded for three months. */}
          <PricePlan code={code} book={book}
                     onPlan={(p, sd) => { setPlan(p); setPlanSide(sd); }} />
        </div>
        <table className="w-full text-[11.5px] tabular-nums">
          <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
            <th className="text-right px-3 py-1" style={{ color: BLUE }}>
              {t("매도 잔량 (파는 사람)", "sellers waiting")}</th>
            <th className="text-center px-2">{t("호가", "price")}</th>
            <th className="text-left px-3" style={{ color: RED }}>
              {t("매수 잔량 (사는 사람)", "buyers waiting")}</th>
          </tr></thead>
          <tbody>
            <tr><td colSpan={3} className="px-3 pt-1 text-[9.5px] font-bold"
                    style={{ color: BLUE }}>
              {t("▲ 위쪽 = 파는 사람들이 기다리는 자리 (매도호가) — 위로 갈수록 비쌉니다",
                 "▲ above = where sellers are waiting (asks) - dearer as it goes up")}</td></tr>
            {(book?.asks ?? []).slice().reverse().map(([p, q], i) => {
              // ONE SLICE, ONE BLOCK. A price can exist on both sides at the
              // same instant (삼성전자 sat locked at ₩268,500 while this was
              // written), so a slice matched on price alone printed twice. The
              // leg that deals now belongs to the side it trades INTO - a buy
              // lifts an ask, a sell hits a bid - and the resting legs belong
              // where they will actually wait.
              // OUR slice on THIS row. A price can stand on both sides at
              // one instant (삼성전자 sat locked at ₩268,500 while this was
              // written), so the leg that deals NOW belongs to the side it
              // trades into — a buy lifts an ask, a sell hits a bid — and the
              // resting legs belong where they will actually wait.
              const sl = (plan?.slices || []).find((x) => Math.abs(x.px - p) < 0.5
                && (planSide === "BUY" ? x.now : !x.now));
              // OUR slice sits in OUR column: a buy order of ours belongs among
              // the buyers (right), a sell of ours among the sellers (left) -
              // whatever price row it happens to stand on (boss 2026-09-07)
              const mark = sl ? `🎯 ${sl.qty.toLocaleString()}${t("주", "sh")}${sl.now ? t(" 지금 체결", " deals now") : ""}` : "";
              const mine = planSide === "BUY";
              return (
              <tr key={"a" + i} className="border-t border-[var(--border-default)]/30"
                  style={sl ? { background: "rgba(106,27,154,0.10)" } : undefined}>
                {/* BOTH NUMBERS, NEVER ONE INSTEAD OF THE OTHER (boss
                    2026-09-09: "we wanna see others volume also, please do not
                    hide it"). The crowd's queue keeps the place next to the
                    price - it is what he checks against the Kiwoom HTS - and
                    our own slice stands beside it. */}
                <td className="text-right px-3 py-[2px]" style={{ color: BLUE }}>
                  {mark && !mine && (
                    <span className="mr-1.5" style={{ color: PURPLE }}>{mark}</span>)}
                  {fmt(q)}</td>
                <td className="text-center px-2 font-bold" style={{ color: BLUE }}>
                  ₩{fmt(p)}{p === book?.best_ask && <span className="text-[9px]"> {t("← 여기서 사면 바로 체결", "← buying here deals now")}</span>}
                </td>
                <td className="text-left px-3 text-[10px]" style={{ color: PURPLE }}>
                  {mark && mine ? mark : ""}</td>
              </tr>);
            })}
            <tr><td colSpan={3} className="px-3 pt-1 text-[9.5px] font-bold"
                    style={{ color: RED }}>
              {t("▼ 아래쪽 = 사는 사람들이 기다리는 자리 (매수호가) — 아래로 갈수록 쌉니다",
                 "▼ below = where buyers are waiting (bids) - cheaper as it goes down")}</td></tr>
            {(book?.bids ?? []).map(([p, q], i) => {
              const sl = (plan?.slices || []).find((x) => Math.abs(x.px - p) < 0.5
                && (planSide === "SELL" ? x.now : !x.now));
              const mark = sl ? `🎯 ${sl.qty.toLocaleString()}${t("주", "sh")}${sl.now ? t(" 지금 체결", " deals now") : ""}` : "";
              const mine = planSide === "BUY";
              return (
              <tr key={"b" + i} className="border-t border-[var(--border-default)]/30"
                  style={sl ? { background: "rgba(106,27,154,0.10)" } : undefined}>
                <td className="text-right px-3 text-[10px]" style={{ color: PURPLE }}>
                  {mark && !mine ? mark : ""}</td>
                <td className="text-center px-2 font-bold" style={{ color: RED }}>
                  ₩{fmt(p)}{p === book?.best_bid && <span className="text-[9px]"> {t("← 여기서 팔면 바로 체결", "← selling here deals now")}</span>}
                </td>
                <td className="text-left px-3 py-[2px]" style={{ color: RED }}>
                  {fmt(q)}
                  {mark && mine && (
                    <span className="ml-1.5" style={{ color: PURPLE }}>{mark}</span>)}</td>
              </tr>);
            })}
            {/* THE SLICES THE BOOK CANNOT REACH (boss 2026-09-09: "in case of
                buying I can not see 5 different prices"). A buy stands at five
                depths this stock actually reaches over three months, and four
                of them are normally far below the ten levels a book shows - so
                only the leg that deals now ever got a 🎯. They are named here
                instead of being silently absent. */}
            {(() => {
              const ps = [...(book?.asks || []), ...(book?.bids || [])].map(([p]) => p);
              if (!ps.length || !(plan?.slices || []).length) return null;
              const lo = Math.min(...ps), hi = Math.max(...ps);
              const off = (plan?.slices || []).filter((s) => s.px < lo || s.px > hi);
              if (!off.length) return null;
              return (
                <tr><td colSpan={3} className="px-3 py-1 text-[10px] leading-[1.45]"
                        style={{ color: PURPLE, background: "rgba(106,27,154,0.06)" }}>
                  🎯 {t(`이 호가창 10단계 밖에 ${off.length}자리 더 — 아래 표에 전부 있습니다: `,
                        `${off.length} more of our prices sit outside these ten levels - all of them are in the table below: `)}
                  {off.map((s) => `₩${fmt(s.px)} · ${s.qty.toLocaleString()}${t("주", " sh")}`).join("   ")}
                </td></tr>);
            })()}
          </tbody>
        </table>
        {!book && (
          <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
            {t("호가 불러오는 중…", "loading the book…")}</div>)}
        {book && !book.ok && (
          <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
            {t("지금 이 종목의 호가가 오지 않습니다 (장이 닫혀 있거나 키움 응답 없음).",
               "no book for this stock right now (market closed, or Kiwoom did not answer).")}</div>)}
      </div>

      {/* 체결 — the deals themselves, with their time */}
      <div className="rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
        <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
          <b className="text-[13px]" style={{ color: GOLD }}>
            📼 {t("실시간 체결 — 체결 시각과 가격", "live executions - deal time and price")}
          </b>
          <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
            {t(`같은 초에 여러 건이 찍힙니다. 차트의 봉은 바로 이 체결들을 묶은 것입니다 — 지금까지 ${fmt(execs?.total)}건 수집.`,
               `several print within one second. The bars are these very executions grouped - ${fmt(execs?.total)} collected so far.`)}
          </div>
        </div>
        <div className="overflow-y-auto" style={{ maxHeight: tapeHeight }}>
          <table className="w-full text-[11.5px] tabular-nums">
            <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
              <th className="text-left px-3 py-1">{t("체결시각", "time")}</th>
              <th className="text-right px-2">{t("체결가", "price")}</th>
              <th className="text-right px-2">{t("전일대비", "vs prev close")}</th>
              <th className="text-right px-3">{t("체결량", "qty")}</th>
            </tr></thead>
            <tbody>
              {(execs?.rows ?? []).map((r, i) => {
                const prev = (execs?.rows ?? [])[i + 1];
                const up = prev && prev.px < r.px;
                const dn = prev && prev.px > r.px;
                const d = execs?.prev_close ? Math.round(r.px - execs.prev_close) : null;
                return (
                  <tr key={i} className="border-t border-[var(--border-default)]/30">
                    <td className="px-3 py-[2px] text-[var(--text-muted)]">{r.t}</td>
                    <td className="text-right px-2 font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>
                      ₩{fmt(r.px)} {up ? "▲" : dn ? "▼" : ""}
                    </td>
                    <td className="text-right px-2 font-bold" style={{ color: d == null ? "var(--text-muted)" : d > 0 ? RED : d < 0 ? BLUE : "var(--text-muted)" }}>
                      {d == null ? "-" : d === 0 ? "0" : `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}`}
                    </td>
                    <td className="text-right px-3">{fmt(r.qty)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {execs && !(execs.rows || []).length && (
            <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
              {t("아직 오늘 이 종목의 체결이 수집되지 않았습니다 — 수집기는 장중에만 테이프를 쌓습니다.",
                 "no executions collected for this stock yet — the tape only grows while the market is open.")}</div>)}
          {!execs && (
            <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
              {t("체결 불러오는 중…", "loading executions…")}</div>)}
        </div>
      </div>
    </div>
  );
}
