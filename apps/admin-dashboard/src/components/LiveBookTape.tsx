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
import { useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

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
type Lad = { ok: boolean; side: string; qty: number; price: number;
             slices: { px: number; qty: number; kind: string }[]; ko: string; en: string };

const fmt = (n?: number | null) => (n == null ? "-" : n.toLocaleString());

export default function LiveBookTape({ code, tapeHeight = 300 }:
  { code: string; tapeHeight?: number }) {
  const { t } = useLanguage();
  const [book, setBook] = useState<Book | null>(null);
  const [execs, setExecs] = useState<Execs | null>(null);
  const [lad, setLad] = useState<Lad | null>(null);
  const [ladSide, setLadSide] = useState<"BUY" | "SELL">("BUY");
  const [ladHelp, setLadHelp] = useState(false);
  const sideRef = useRef(ladSide); sideRef.current = ladSide;

  // one poll for the three windows, on the desk's own 3s clock. `dead` guards
  // the in-flight replies: a popup closed mid-request must not setState.
  useEffect(() => {
    if (!code) return;
    let dead = false;
    setBook(null); setExecs(null); setLad(null);       // never show the last stock's book
    const pull = () => {
      api<Book>(`/paper-desk/live/book?code=${code}`)
        .then((d) => { if (!dead) setBook(d); }).catch(() => {});
      api<Lad>(`/paper-desk/live/ladder?code=${code}&side=${sideRef.current}`)
        .then((d) => { if (!dead) setLad(d?.ok ? d : null); }).catch(() => { if (!dead) setLad(null); });
      api<Execs>(`/paper-desk/live/execs?code=${code}&n=120`)
        .then((d) => { if (!dead) setExecs(d); }).catch(() => {});
    };
    pull();
    const iv = setInterval(pull, 3000);
    return () => { dead = true; clearInterval(iv); };
  }, [code]);

  // flipping 살 때/팔 때 redraws immediately instead of after the 3s tick
  useEffect(() => {
    if (!code) return;
    let dead = false;
    api<Lad>(`/paper-desk/live/ladder?code=${code}&side=${ladSide}`)
      .then((d) => { if (!dead) setLad(d?.ok ? d : null); }).catch(() => {});
    return () => { dead = true; };
  }, [ladSide, code]);

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
          {/* THE LADDER DEMO (boss 2026-09-07): the same split the desk uses
              when he approves, drawn on THIS book so he can see where each
              20% would stand among the waiting orders. */}
          <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
            <span className="text-[10.5px] font-bold" style={{ color: PURPLE }}>
              🪜 {t("1,000만원을 5조각으로 나누면", "₩10m split into 5 slices")}</span>
            {(["BUY", "SELL"] as const).map((sd) => (
              <button key={sd} onClick={() => setLadSide(sd)}
                className="text-[10px] px-2 py-[1px] rounded-full border"
                style={{ borderColor: PURPLE,
                         background: ladSide === sd ? PURPLE : "transparent",
                         color: ladSide === sd ? "#fff" : "var(--text-primary)" }}>
                {sd === "BUY" ? t("살 때", "buying") : t("팔 때", "selling")}</button>))}
            {lad && <span className="text-[10px] text-[var(--text-muted)]">
              {lad.qty.toLocaleString()}{t("주", " sh")} · {t("아래 표에 🪜로 표시", "marked 🪜 in the table below")}</span>}
            <button onClick={() => setLadHelp(!ladHelp)}
              className="text-[10px] font-bold underline"
              style={{ color: PURPLE, textUnderlineOffset: 3 }}>
              {t(`사다리가 뭔가요? ${ladHelp ? "▲" : "▼"}`, `what is the ladder? ${ladHelp ? "▲" : "▼"}`)}
            </button>
          </div>
          {/* WHAT THE LADDER IS, IN WORDS (boss 2026-09-08: "사다리 그것도 설명
              넣어줘야 돼"). The arithmetic is services/approval_desk.py
              book_ladder — first slice at market so the decision always
              executes, the rest resting one tick better each. */}
          {ladHelp && (
            <div className="mt-1.5 px-2.5 py-2 rounded-lg text-[10.5px] leading-[1.65]"
                 style={{ background: "rgba(106,27,154,0.07)", color: "var(--text-secondary)" }}>
              <div className="font-bold mb-1" style={{ color: PURPLE }}>
                🪜 {t("한 가격에 다 넣지 않고, 다섯 조각으로 나눠 겁니다",
                      "one order becomes five, instead of everything at a single price")}</div>
              {t("한 번에 한 가격으로 다 사고 팔면, 그 뒤에 가격이 우리 쪽으로 움직여도 얻는 게 없습니다. 그래서 주문을 20%씩 다섯 조각으로 나눕니다:",
                 "putting the whole order at one price gives away every move that comes after it. So the order is split into five 20% slices:")}
              <div className="mt-1.5 pl-1">
                <div>
                  <b style={{ color: PURPLE }}>{t("① 첫 조각 — 지금 바로 체결", "① slice 1 — deals right now")}</b>{" "}
                  {ladSide === "BUY"
                    ? t("가장 싼 매도호가를 그대로 칩니다. 결정이 났으면 반드시 들어간다는 뜻입니다 (나누다 남는 수량도 이 조각에 얹습니다 — 확실한 다리가 가장 짧으면 안 되니까).",
                        "it lifts the cheapest ask. A decision that was made always gets in — and the rounding remainder rides this slice, so the guaranteed leg is never the short one.")
                    : t("가장 비싼 매수호가를 그대로 받습니다. 나가야 할 물량은 반드시 나간다는 뜻입니다 (나누다 남는 수량도 이 조각에 얹습니다 — 확실한 다리가 가장 짧으면 안 되니까).",
                        "it hits the highest bid. Stock that must leave always gets out — and the rounding remainder rides this slice, so the guaranteed leg is never the short one.")}
                </div>
                <div className="mt-1">
                  <b style={{ color: PURPLE }}>{t("② ~ ⑤ 나머지 네 조각 — 한 호가씩 더 유리하게 걸어둠",
                                                  "② – ⑤ the other four — resting one tick better each")}</b>{" "}
                  {ladSide === "BUY"
                    ? t("한 호가씩 아래에 지정가로 줄을 세웁니다. 가격이 더 내려오면 그만큼 싸게 사고, 안 내려오면 첫 조각은 이미 샀으니 기회를 놓치지 않습니다.",
                        "they queue one tick lower each. If the price comes down we buy that much cheaper; if it does not, slice 1 already bought, so the chance is not missed.")
                    : t("한 호가씩 위에 지정가로 줄을 세웁니다. 가격이 더 오르면 그만큼 비싸게 팔고, 안 오르면 첫 조각은 이미 팔았으니 기회를 놓치지 않습니다.",
                        "they queue one tick higher each. If the price rises we sell that much dearer; if it does not, slice 1 already sold, so the chance is not missed.")}
                </div>
              </div>
              {/* WHY ② IS NOT ONE TICK FROM ① (한국전력 2026-09-08: slice 1 dealt
                  at ₩34,300 while slice 2 rested at ₩33,800). The resting legs
                  step from the biggest WALL, which is his standing law - so the
                  jump is the law working, not a gap in the arithmetic. */}
              <div className="mt-1.5">
                {ladSide === "BUY"
                  ? t("②의 자리는 ①에서 한 호가가 아니라, 가장 두꺼운 매수벽 바로 한 호가 앞에서 시작해 거기서부터 한 칸씩 내려갑니다 — 벽 앞에 줄 선 사람들보다 먼저 체결되게 하는 회장님의 기존 법칙 그대로입니다. 그래서 ①과 ② 사이가 벌어져 보일 수 있습니다.",
                      "slice ② does not start one tick from ①: it starts one tick in FRONT of the biggest bid wall and steps down from there — the standing law that puts us ahead of everyone queued at that wall. That is why ① and ② can sit far apart.")
                  : t("②의 자리는 ①에서 한 호가가 아니라, 가장 두꺼운 매도벽 바로 한 호가 앞에서 시작해 거기서부터 한 칸씩 올라갑니다 — 벽 앞에 줄 선 사람들보다 먼저 체결되게 하는 회장님의 기존 법칙 그대로입니다. 그래서 ①과 ② 사이가 벌어져 보일 수 있습니다.",
                      "slice ② does not start one tick from ①: it starts one tick in FRONT of the biggest ask wall and steps up from there — the standing law that puts us ahead of everyone queued at that wall. That is why ① and ② can sit far apart.")}
              </div>
              <div className="mt-1.5">
                {t("아래 표의 보라색 🪜 칸이 그 다섯 조각이 실제로 설 자리입니다 — 지금 이 호가창 위에 그린 것이고, 승인 버튼을 누를 때 데스크가 쓰는 계산과 똑같은 함수입니다. 호가 한 칸의 크기는 그 종목의 가격대가 정합니다.",
                   "the purple 🪜 rows below are where those five slices would actually stand — drawn on THIS live book, by the very same function the desk uses when you press Approve. One tick's size is set by the stock's own price band.")}
              </div>
            </div>)}
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
              const sl = (lad?.slices || []).find((x) => Math.abs(x.px - p) < 0.5
                && (ladSide === "BUY" ? x.kind === "market" : x.kind === "limit"));
              // OUR slice sits in OUR column: a buy order of ours belongs among
              // the buyers (right), a sell of ours among the sellers (left) -
              // whatever price row it happens to stand on (boss 2026-09-07)
              const mark = sl ? `🪜 ${sl.qty.toLocaleString()}${t("주", "sh")}${sl.kind === "market" ? t(" 지금 체결", " deals now") : ""}` : "";
              const mine = ladSide === "BUY";
              return (
              <tr key={"a" + i} className="border-t border-[var(--border-default)]/30"
                  style={sl ? { background: "rgba(106,27,154,0.10)" } : undefined}>
                <td className="text-right px-3 py-[2px]" style={{ color: mark && !mine ? PURPLE : BLUE }}>
                  {mark && !mine ? mark : fmt(q)}</td>
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
              const sl = (lad?.slices || []).find((x) => Math.abs(x.px - p) < 0.5
                && (ladSide === "SELL" ? x.kind === "market" : x.kind === "limit"));
              const mark = sl ? `🪜 ${sl.qty.toLocaleString()}${t("주", "sh")}${sl.kind === "market" ? t(" 지금 체결", " deals now") : ""}` : "";
              const mine = ladSide === "BUY";
              return (
              <tr key={"b" + i} className="border-t border-[var(--border-default)]/30"
                  style={sl ? { background: "rgba(106,27,154,0.10)" } : undefined}>
                <td className="text-right px-3 text-[10px]" style={{ color: PURPLE }}>
                  {mark && !mine ? mark : ""}</td>
                <td className="text-center px-2 font-bold" style={{ color: RED }}>
                  ₩{fmt(p)}{p === book?.best_bid && <span className="text-[9px]"> {t("← 여기서 팔면 바로 체결", "← selling here deals now")}</span>}
                </td>
                <td className="text-left px-3 py-[2px]" style={{ color: mark && mine ? PURPLE : RED }}>
                  {mark && mine ? mark : fmt(q)}</td>
              </tr>);
            })}
          </tbody>
        </table>
        {!book && (
          <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
            {t("호가 불러오는 중…", "loading the book…")}</div>)}
        {book && !book.ok && (
          <div className="px-4 py-6 text-center text-[11.5px] text-[var(--text-muted)]">
            {t("지금 이 종목의 호가가 오지 않습니다 (장이 닫혀 있거나 키움 응답 없음).",
               "no book for this stock right now (market closed, or Kiwoom did not answer).")}</div>)}
        {lad && (
          <div className="px-4 py-2 text-[10.5px] border-t"
               style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
            {t(lad.ko, lad.en)}
          </div>)}
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
