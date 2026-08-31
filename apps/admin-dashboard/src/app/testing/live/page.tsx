"use client";

/**
 * 📡 LIVE KIWOOM DESK — the same charts and tables as the artificial labs, on REAL money
 * prices. Deliberately a separate page from /testing/proof and /testing/lab: the
 * artificial market must keep trading undisturbed (boss 2026-08-04), and two pages that
 * share no code cannot break each other.
 *
 * THE ONE THING THAT MAKES THIS HARD. Kiwoom's tick endpoint returns the last ~900
 * executions and nothing older — forty seconds for 삼성전자. There is no way to ask for
 * an hour of real ticks. So the backend COLLECTS the tape continuously and this page
 * draws from what has been gathered. The chart therefore starts thin and deepens through
 * the day, which is the honest behaviour and not a fault.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

/** What each column of the stock-picking table means, in plain words — the boss reads
 *  this table every morning and should never have to ask what "flex" is. Order in each
 *  entry: [Korean title, English title, Korean body, English body]. The weights are the
 *  real ones from services/daily_pick.py WEIGHTS. */
type RawDaily = {
  ok: boolean; code: string; name: string; table: string;
  rows: { date: string; open: number; high: number; low: number; close: number;
          volume: number; chg?: number | null }[];
  flows: { date: string; foreign: number; inst: number; retail: number }[];
  flow_latest: string | null;
};

const COL_HELP: Record<string, [string, string, string, string]> = {
  rank: ["순위", "rank",
    "오늘 총점이 높은 순서입니다. 후보 종목 전체를 매일 아침 다시 채점해서 다시 줄을 세웁니다. 어제 1등이 오늘 20등이 될 수 있습니다.",
    "The order of today's total score. Every candidate is re-scored and re-ranked each morning, so yesterday's #1 can be today's #20."],
  stock: ["종목", "stock",
    "종목 이름입니다. 📌 표시는 점수와 상관없이 항상 데스크에 넣는 고정 종목(SK하이닉스·삼성전자)이라는 뜻입니다. 나머지는 그날 점수로 뽑힌 상위 5개입니다.",
    "The stock's name. 📌 means it is a fixed name (SK하이닉스, 삼성전자) that goes on the desk regardless of its score. The rest are the day's top five by score."],
  score: ["평균 점수 — 계산식", "Average score - the formula",
    "합산제 (2026-08-26 확정): 100개 항목 각각의 가중치(측정+논문 근거)를 통과분만큼 더한 값이 최종 점수 — 평균이 아닙니다. 최대 배점: 볼륨 가족 15 (#46:5+#47:4+#21:4+#69:2) · 외국인 6 · 정배열 5 · 집행 관문 8 · 엔진 법 항목 28은 모든 거래에 강제되므로 부여. 기본 최대 92 + 뉴스 레이어 8 = 100. 종목명(🧮)을 클릭하면 항목 단위 계산이 펼쳐집니다.",
    "Average = (Market + Issue/Supply + Stock selection + Execution mgmt) / 4. Each of the four columns is scored out of 100 and plainly averaged. Per column: Market = weighted share of O over checklist #11-25 & market-grade items (direction x2, bad news x2, plunge x3, others x1) - Issue/Supply = weighted sum of #31/32/34/43 - Stock selection = the automated #46-75 (trend25+liquidity20+flexibility20+levels15+momentum10)/90 - Execution = share of O over #76/79/82/83. Click a company name (the calculator mark) to unfold its full item-level calculation. Only data from before the day is used."],
  trend: ["추세 — 비중 25 (가장 중요)", "trend - weight 25 (the biggest)",
    "값이 위로 질서 있게 움직이고 있는가. ① 5일선 > 20일선 > 60일선 정배열(35%) ② 1년 동안 얼마나 곧게 움직였는지, 위아래로 흔들리지 않고 한 방향으로 가는 성질(25%) ③ 20일 신고가 여부(20%) ④ 최근 20일선 위에서 보낸 날의 비율(20%). 우리 규칙은 3번 오르면 사기 때문에 위로 가는 종목에서만 통합니다.",
    "Is it moving up in an orderly way? (1) the 5-day line above the 20 above the 60 (35%), (2) how straight it travels over a year rather than sawing up and down (25%), (3) a 20-day new high (20%), (4) how much of the recent stretch it spent above its 20-day line (20%). Our rules buy after three rises, so they only work on stocks that actually go up."],
  liquidity: ["유동성 — 비중 20", "liquidity - weight 20",
    "우리가 사고팔 때 값이 밀리지 않을 만큼 거래가 많은가. ① 1년 평균 거래대금(60%) ② 거래량이 평소보다 확 늘어나는 날의 빈도(40%). 거래가 적으면 주문이 체결되지 않거나 불리한 값에 체결됩니다.",
    "Is there enough trading that our order does not move the price? (1) average trading value over a year (60%), (2) how often volume spikes well above normal (40%). Thin stocks either do not fill or fill at a worse price."],
  flexibility: ["유연성 — 비중 20 (호가 비용)", "flexibility - weight 20 (the tick cost)",
    "한 호가(한 칸)가 주가의 몇 %인가. 작을수록 좋습니다. 우리는 매매 한 번에 수수료 0.23% + 호가 1칸을 냅니다. 한 칸이 0.05%인 종목은 이 비용을 넘기 쉽고, 0.5%인 종목은 이겨도 손해가 납니다. 이 항목이 낮은 종목은 이기고도 돈을 잃는 종목입니다.",
    "How large one tick is as a percentage of the price - smaller is better. Every trade costs us 0.23% in fees plus one tick. Where a tick is 0.05% that cost is easy to clear; where it is 0.5% you can win the trade and still lose money. A low score here is the stock that wins and loses at the same time."],
  levels: ["지지저항 — 비중 15", "levels - weight 15",
    "지금 값이 자기 자리 어디쯤에 있는가. ① 볼린저밴드 안에서의 위치(50%) ② 어제 종가 대비 위치(50%). 너무 아래면 떨어지는 중이고, 꼭대기에 붙어 있으면 이미 늦어서 살 자리가 아닙니다. 가운데에서 위로 향할 때가 가장 좋습니다.",
    "Where the price sits against its own levels: (1) its position inside the Bollinger band (50%), (2) where it stands versus yesterday's close (50%). Too low means it is still falling; pinned at the top means we are late. Mid-band and rising is the good place to buy."],
  momentum: ["모멘텀 — 비중 10", "momentum - weight 10",
    "올라갈 힘이 남아 있는가. ① RSI가 55 근처일 때 만점(50%) — 70을 넘으면 과열이라 오히려 점수가 깎입니다 ② MACD 골든크로스 발생 여부(50%). '이미 다 올라간 종목'이 아니라 '지금 막 오르기 시작한 종목'을 고르기 위한 항목입니다.",
    "Is there room left to rise? (1) RSI nearest 55 scores best (50%) - above 70 is overheated and loses points, (2) a MACD golden cross (50%). This column is what separates a stock that is just starting to move from one that has already made its move."],
  flows: ["수급 — 비중 10", "flows - weight 10",
    "큰 손이 같은 편인가. ① 외국인 3일 순매수(45%) ② 기관 3일 순매수(30%) ③ 개인만 몰려 있는 종목이면 감점(15%) ④ 공매도 비중이 낮을수록 가점(10%). 외국인·기관이 파는 종목은 오전에 올라도 되돌림이 자주 나옵니다.",
    "Is the big money on our side? (1) foreigners' 3-day net buying (45%), (2) institutions' 3-day net (30%), (3) a penalty when only retail is crowded in (15%), (4) a bonus for low short interest (10%). Names that foreigners and institutions are selling tend to give back their morning gains."],
  // — checklist-CATEGORY columns (boss 2026-08-24: columns named after his checklist's
  //   own 분류, click shows the subcategories) + the live "지금" column —
  now: ["지금 (실시간 보정)", "now (live adjustment)",
    "챗봇 추천과 똑같은 계산입니다: 아침 점수 + 실시간 보정(당일 등락 ±4 · 연중구간 +2/−3 · 뉴스 위험−2씩/호재+1씩 · 거래량 서지 최대 +4, 총 ±12) = 합계. 거래량 항이 2026-08-25 신설 — 평소의 2~3배로 터지는 종목은 즉시 4점을 받아 톱5 자리를 빼앗을 수 있습니다. 표는 이 합계 순으로 정렬됩니다.",
    "The exact same math as the chatbot's recommendation: morning score + live adjustment (today's move ±4, year-zone +2/−3, news danger −2 each / good +1 each, volume surge up to +4; total cap ±12) = total. The volume term is new 2026-08-25 - a stock trading 2-3x its normal pace gains 4 points instantly and can take a top-5 seat. The table sorts by this total."],
  market: ["시장 (체크리스트 11~25번)", "market (checklist #11-25)",
    "종목이 아니라 오늘 시장 전체의 점수라 모든 줄이 같습니다. 11~25번 전 항목 자동 점검: #11 코스피/코스닥 방향 · #12 미국 증시 · #13 나스닥 선물 · #14 원달러 환율 · #15 국채 금리 · #16 유가 · #17 VIX · #18 경제지표 일정 · #19 정책 이벤트 · #20 지정학 · #21 시장 거래대금 · #22 시장 악재 · #24 장중 선물 흐름 · #25 시장 유동성 — 추가로 #36 만기일 · #95 급락일 · #100 마감 직전 · #28 정책수혜 · #30 배당/MSCI · #33 연기금 · #37 선물수급 · #39 섹터 순환매. (#23 종목뉴스는 종목별이라 이슈 칸에서 채점) 자세히는 챗봇에 \"체크리스트\"라고 물어보세요.",
    "Scores TODAY's market, not the stock, so every row shows the same number. ALL of #11-25 auto-checked: #11 KOSPI/KOSDAQ, #12 US close, #13 NASDAQ futures, #14 USD/KRW, #15 bond yields, #16 oil, #17 VIX, #18 econ-data schedule, #19 policy events, #20 geopolitics, #21 market value, #22 market-wide bad news, #24 intraday futures, #25 liquidity - plus #36 expiry, #95 plunge day, #100 near close, #28 policy benefit, #30 dividend/MSCI, #33 pension, #37 futures flows, #39 sector rotation. (#23 stock-news is per-stock, in the issue column.) Ask the chatbot \"checklist\" for the live detail."],
  issue: ["이슈/수급 (#26~45) — 총점 비중 10", "Issue/Supply&Demand (#26-45) - weight 10 of 100",
    "총점 100 중 10을 차지합니다. 소분류(클릭한 이 칸의 구성): #31 외국인 순매수 45% · #32 기관 순매수 30% · #34 개인 쏠림 감점 15% · #43 공매도 과열 감점 10%. 뉴스/테마(#26~30, #40~42, #44~45)는 Qwen 뉴스 엔진이 종목별 스탬프로 판독 — 챗봇 '근거 🔍'에서 봅니다.",
    "Carries 10 of the 100 total. Subcategories of this column: #31 foreign net buying 45%, #32 institutional net 30%, #34 retail-crowding penalty 15%, #43 short-overheat penalty 10%. News/theme items (#26-30, #40-42, #44-45) are read per stock by the Qwen news engine - see the chatbot's evidence 🔍."],
  stock_sel: ["종목선정 (#46~75) — 총점 비중 90", "Stock selection (#46-75) - weight 90 of 100",
    "총점 100 중 90을 차지하는 핵심 칸입니다. 소분류와 비중(2026-08-25 실측 재조정: 두 개의 독립 250일 검증에서 거래대금이 최강 예측력, RSI/MACD는 0, #67 전일등락은 마이너스): 유동성(#21·46·47·69) 45 · 추세(#50·51·52·58) 35 · 유연성(#48 호가비용) 10 · 수급 10 · 지지저항/모멘텀은 순위에서 제외(구간 규칙은 엔진이 매수 순간 별도 집행). 아래 '세부 6칸 보기'로 소분류별 점수를 봅니다.",
    "The core column - 90 of the 100 total. Weights re-measured 2026-08-25 (two independent 250-day backtests: turnover strongest predictor, RSI/MACD zero, #67 prev-day move negative): liquidity (#21·46·47·69) 45, trend (#50·51·52·58) 35, flexibility (#48 tick cost) 10, flows 10; levels/momentum left the ranking (the engine still enforces zone laws at buy time). Press 'show the 6 detail columns' below."],
  exec: ["실행/관리 (체크리스트 76~100번)", "execution (checklist #76-100)",
    "매수 타점(#76) · 손절/익절(#77·78) · 손익비(#79) · 진입 근거 3개(#82) 같은 항목은 '사는 순간'에 계산되는 것이라 아침 순위표에는 없습니다. 종목을 정한 뒤 챗봇에 \"종목명 체크리스트\"라고 물으면 이 항목들이 실시간으로 채점됩니다.",
    "Items like the entry point (#76), stop/target (#77·78), risk:reward (#79) and 3+ reasons (#82) are computed AT THE MOMENT OF BUYING, so they can't rank a morning table. Once you pick a stock, ask the chatbot \"<stock> checklist\" and these are scored live."],
};

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";

// STOCK SEARCH THAT UNDERSTANDS THE BOSS (2026-08-31: he typed "sktelecom"
// and the chart "showed another stock" - the old matcher demanded the EXACT
// Korean name, so free-typed English matched nothing and the chart simply
// stayed put). English aliases + partial Korean + codes; a switch happens
// only on a UNIQUE confident match - ambiguity never jumps to a wrong stock.
const ALIAS9: Record<string, string[]> = {
  "017670": ["sktelecom", "skt", "telecom", "텔레콤"],
  "000660": ["skhynix", "hynix", "하이닉스"],
  "402340": ["sksquare", "square", "스퀘어"],
  "005930": ["samsung", "samsungelectronics", "samsungchonja", "삼전"],
  "009150": ["samsungelectro", "samsungjeongi", "전기"],
  "006400": ["samsungsdi", "sdi"],
  "207940": ["samsungbio", "biologics", "바이오"],
  "035420": ["naver", "네이버"],
  "034020": ["doosan", "doosanenerbility", "두산"],
  "042660": ["hanwhaocean", "ocean", "오션"],
  "012450": ["hanwhaaerospace", "aerospace", "에어로"],
  "042700": ["hanmi", "hanmisemiconductor", "한미"],
  "079550": ["lig", "lignex1", "디펜스"],
  "373220": ["lgenergy", "lges", "energysolution", "엔솔"],
  "329180": ["hdhyundai", "hyundaiheavy", "현대중"],
  "052690": ["kepcoenc", "kepco", "한전"],
  "010950": ["soil", "s-oil", "에쓰오일"],
};
function matchStock9(v: string, list: { code: string; name: string }[]) {
  const q = v.toLowerCase().replace(/[\s\-·.]/g, "");
  if (!q) return null;
  const norm = (s: string) => s.toLowerCase().replace(/[\s\-·.]/g, "");
  const exact = list.find((x) => x.code === q || norm(x.name) === q
    || (ALIAS9[x.code] || []).some((a) => norm(a) === q));
  if (exact) return exact;
  if (q.length < 2) return null;
  const part = list.filter((x) => norm(x.name).includes(q)
    || (ALIAS9[x.code] || []).some((a) => norm(a).startsWith(q)));
  return part.length === 1 ? part[0] : null;
}

type Bar = { time: number; hhmm: string; open: number; high: number; low: number;
             close: number; dir: number; vol: number; n: number; d8?: string };
type Tape = { ok: boolean; code: string; name?: string; clock: string; ticks: number;
              first?: string; last?: string; bars: Bar[]; note?: string;
              off?: number; total_bars?: number };
type Book = { ok: boolean; code: string; name?: string; asks: [number, number][];
              bids: [number, number][]; best_ask?: number; best_bid?: number;
              last?: number; prev_close?: number; change_pct?: number };
type Execs = { ok: boolean; prev_close?: number; total: number;
               rows: { t: string; px: number; qty: number }[] };
type RuleRow = { id: string; ko: string; en: string; dir: number; trips: number; wins: number;
                 vs?: number | null; vs_trips?: number | null;
                 net_won?: number | null; per_trade_won?: number | null;
                 losses: number; flats: number; win_pct: number; per_trade: number; net: number;
                 decided: number; thin: boolean; family?: string };
type Gate = { ok: boolean; day: string; go: number; total: number;
              rows: { code: string; name: string; go: boolean;
                      reason_ko: string; reason_en: string }[] };
type Pick = { ok: boolean; day: string; market_open?: boolean; applied?: boolean;
              picks: string[]; weights?: Record<string, number>;
              trading_now?: { code: string; name: string }[];
              pinned?: string[]; n_earned?: number; n_added?: number;
              mode?: string; desk?: string[]; missing?: string[];
              market_pct?: number | null; n_picks?: number;
              rows: { rank: number; code: string; name: string; score: number;
                      tick_pct: number; rsi: number; aligned: number; new_high: number;
                      why: string[]; groups: Record<string, number>;
                      pinned?: boolean; by_score?: boolean; on_desk?: boolean;
                      added?: boolean;
                      live_adj?: number; live_total?: number; live_chg?: number;
                      zone?: string; zone_pos?: number;
                      cats?: { market?: number | null; issue?: number | null;
                               stock_sel?: number | null; exec?: number | null } }[] };
type Screen = { ok: boolean; scored_on?: string; tested_on?: string;
                test_result?: { picked: { trades: number; win: number; won: number };
                                previous: { trades: number; win: number; won: number } };
                weights?: Record<string, number>;
                rows: { rank: number; code: string; name: string; score: number;
                        tick_pct: number; move_vs_cost: number; continue_pct: number;
                        fit_win: number; live: boolean;
                        g_cost?: number; g_liquidity?: number; g_movement?: number;
                        g_behavior?: number; g_flow?: number; g_fit?: number }[] };
type Rank = { ok: boolean; clock: string; fee_pct: number; original_12?: string[];
              days?: string[]; day?: string; auto_day?: boolean; today?: string;
              frm?: string; to?: string;
              stocks: { code: string; name: string; bars: number; from: string; to: string;
                        tick_size: number }[];
              variants: RuleRow[] };
type Ev = { close: number; book: { best_ask: number; best_bid: number; fill: number;
            spread: number }; seq: number[] };
type MLMeta = { p: number; bar: number; base_rate?: number; qty?: number;
                auc?: number | null; n_train?: number };
type RTrade = { code: string; name: string; day?: string; d8?: string; ml?: MLMeta | null;
                buy_i: number; sell_i: number; buy_t: string;
                entry: number; sell_t: string; exit: number; gross_pct: number;
                net_pct: number; exit_why?: string; result: "win" | "loss" | "flat";
                sharp?: boolean;
                wall?: { price: number; qty: number; ts: string } | null;
                parts?: { buys?: [number, number][] | null;
                          sells?: [number, number][] | null } | null;
                bars_held: number; tick_size: number; qty?: number; buy_ev?: Ev | null; sell_ev?: Ev | null };
type RDetail = { ok: boolean; id: string; ko: string; en: string; clock: string;
                 entry_n: number; kind: string; a: number; b?: number | null; dir: number;
                 vol_x?: number | null; max_run?: number | null; take?: number | null;
                 is_ml?: boolean;
                 trips: number; wins: number; losses: number; flats: number; win_pct: number;
                 decided: number; thin: boolean; shown: number;
                 net_total?: number; gross_total?: number; per_trade?: number;
                 net_won_sized?: number; shares_total?: number; capital_used?: number; budget?: number;
                 net_won_total?: number; per_trade_won?: number;
                 trades: RTrade[];
                 holding: { code: string; name: string; buy_t: string; entry: number;
                            last: number; unreal_pct: number }[];
                 chart: { code: string; name: string; off: number; candles: Bar[];
                          focus: { b: number; s: number } | null;
                          marks: { b: number; s: number; g: number; net: number;
                                   open?: boolean; part?: boolean;
                                   xb?: boolean; label?: string }[] } | null;
                 family?: string;
  dip?: { drop: number; sharp: number; ups: number; chop: number;
          look?: number; win_sec?: number } | null;
  ride?: { arm: number; give: number; downs: number; slow_ups: number;
           slow_take: number; sharp_rise: number } | null;
  take_ticks?: number | null; stop_pct?: number | null;
  scout?: { frac: number; confirm: number } | null;
  ladder?: { half_at: number; take: number; blues: number; give: number } | null;
  drip?: { step: number; up_frac: number; dn_frac?: number; stop_reset: number;
           rebuy?: boolean; pingpong?: boolean;
           reinforce?: { frac: number; max: number } | null } | null;
  us_habit?: boolean;
  rebound?: { low_win: number; near: number; day_gain: number;
              drop: number } | null;
  morning?: { until: string; vol_x: number; min_run: number } | null;
  burst?: { rise: number; win_min: number } | null;
  wall_price?: boolean; exact_entry?: boolean;
};
type DfRow = { hhmm: string; key: string; date: string; open: number; high: number;
               low: number; close: number; diff: number; dir: number; deal_count: number;
               vol: number; forming: boolean };
type Df = { ok: boolean; code: string; name: string; rows: DfRow[]; total_minutes: number;
            first?: string; last?: string; empty?: string };
type DfMin = DfRow & { ok: boolean; name: string;
                       seconds: { t: string; deals: { px: number; qty: number }[] }[];
                       traded: number[] };
type Status = { running: boolean; market_open: boolean; polls: number;
                errors: Record<string, string>;
                stocks: { code: string; name: string; ticks: number;
                          first?: string; last?: string;
                          gaps?: { from: string; to: string; seconds: number }[];
                          gap_sec?: number }[] };

/** The chart. Same library and the same continuous-bar convention as the labs, so a red
 *  bar means the same thing here as it does there. */

// bordered history cells (boss 2026-08-12: "make a table with border ...
// now it looks not easily understandable")
const CELL: React.CSSProperties = { border: "1px solid var(--border-default)" };

// every place the board names a rule goes through this one function
function ruleName(id: string): string {
  if (id === "chatbot") return "💬 chatbot";
  if (id === "D1") return "시나리오1";
  if (id === "D2") return "시나리오2";
  if (id === "D3") return "시나리오3";
  if (id === "OLD3") return "Old";
  if (id.startsWith("N")) return "Sharp";
  return id;
}

function LiveChart({ bars, marks, focus, off = 0, h = 320 }:
                   { bars: Bar[]; marks?: { b: number; s: number; g: number;
                                            open?: boolean; part?: boolean;
                                            xb?: boolean; label?: string }[];
                     focus?: number | null; off?: number; h?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cs = useRef<{ chart: any; series: any } | null>(null);
  const label = useRef<Map<number, string>>(new Map());
  const dlabel = useRef<Map<number, string>>(new Map());   // "MM/DD HH:MM:SS"
  const applied = useRef<number | null | undefined>(undefined);
  // HIS view vs OUR view. Every 3s poll re-feeds the data, and the library then
  // re-decides what is visible - which yanked the chart out from under him while he was
  // scrolled back studying a moment (boss 2026-08-06: "when i click and looking and
  // monitoring chart it should not reload"). We remember the range HE scrolled to
  // (prog guards our own programmatic moves from being mistaken for his), and after
  // every data update we put his view back - unless he is at the right edge, where
  // following the live market is what monitoring means.
  const userRange = useRef<{ from: number; to: number } | null>(null);
  const prog = useRef(false);
  // dataset identity: on the cumulative view a click can swap the WHOLE DAY under the
  // chart while the focus number stays the same - without this the zoom never fired
  // and "clicking 09:53 did not bring me there" (boss 2026-08-06)
  const sigRef = useRef("");
  const [ready, setReady] = useState(0);

  useEffect(() => {
    let alive = true; let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 320, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        // fixLeftEdge: zooming OUT used to open blank space left of the first bar —
        // both edges pinned, the zoom stops at the data (boss 2026-08-18)
        // 가격축 explicit (boss 2026-08-28: "on the y axis add the price") -
        // the right scale was default-on but is now pinned and bordered so it
        // can never be configured away
        rightPriceScale: { visible: true, borderColor: "rgba(128,128,128,0.4)" },
        timeScale: { timeVisible: true, secondsVisible: true, rightOffset: 2, fixRightEdge: true,
                     fixLeftEdge: true, borderColor: "rgba(128,128,128,0.4)",
                     // a tick bar has no duration, so its x value is a COUNT, not a clock;
                     // the real time of each bar lives in hhmm and is shown from there
                     tickMarkFormatter: (t: number) => (label.current.get(t) ?? "").slice(0, 5) },
        // the crosshair speaks DATE + TIME like Kiwoom (boss 2026-08-28: "if
        // I see yesterday it should show the date also")
        localization: { timeFormatter: (t: number) =>
          dlabel.current.get(t) ?? label.current.get(t) ?? "" },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE,
        wickUpColor: RED, wickDownColor: BLUE,
        priceFormat: { type: "price", precision: 0, minMove: 1 } }); // KRW — no decimals
      cs.current = { chart, series };
      chart.timeScale().subscribeVisibleTimeRangeChange(() => {
        if (prog.current) return;
        const r = chart.timeScale().getVisibleRange();
        if (r) userRange.current = r as never;
      });
      setReady((v) => v + 1);
      cleanup = () => { cs.current = null; chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {
    const c = cs.current;
    if (!c || !bars.length) return;
    // ABSOLUTE bar numbers, not window positions. The tape window SLIDES: on every
    // 2-3s poll, "bar #0" used to become a different bar, and the axis label cache could
    // briefly show the previous window's times - the "15:11 between 09:11 and 09:12"
    // the boss kept catching. With `off` (the bar's permanent position in the day) a bar
    // keeps one number for ever, so labels cannot mix and updates cannot shift.
    label.current = new Map(bars.map((b, i) => [off + i, b.hhmm]));
    dlabel.current = new Map(bars.map((b, i) => [off + i,
      (b.d8 ? `${b.d8.slice(4, 6)}/${b.d8.slice(6)} ` : "")
      + (b.hhmm.includes("/") ? "09:00" : b.hhmm)]));
    c.series.setData(bars.map((b, i) => {
      const col = b.dir > 0 ? RED : b.dir < 0 ? BLUE : "#9e9e9e";
      return { time: off + i, open: b.open, high: b.high, low: b.low, close: b.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    // arrows carry GROSS - the same number the trade table shows. Labelling one with net
    // while the table showed gross made one trade read as two results on the artificial
    // side, and there is no reason to repeat it here.
    const m = (marks ?? []).flatMap((k) => k.xb
      // EVERY buy of the clicked episode gets its own ▲ (boss 2026-08-21:
      // scout, army, reinforcement, re-board - each proves itself at its bar)
      ? [{ time: off + k.b, position: "belowBar", color: "#e65100",
           shape: "arrowUp", text: k.label || "▲" }]
      : k.part
      // a sold slice of a still-open ladder: one sell arrow, no buy pair
      // (매도 written on every sell arrow - boss 2026-08-27: "you wrote 매수
      // on the buy arrow but not 매도 on the sell")
      ? [{ time: off + k.s, position: "aboveBar", color: k.g > 0 ? RED : BLUE,
           shape: "arrowDown", text: `매도 조각 ${k.g > 0 ? "+" : ""}${k.g}%` }]
      : k.open
      // an OPEN position is proof of a buy, not of a sell: the buy arrow stands at
      // its bar and a gold badge rides the live edge - no fake sell arrow
      ? [
        { time: off + k.b, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
        { time: off + k.s, position: "aboveBar", color: GOLD, shape: "circle",
          text: `보유 중 ${k.g > 0 ? "+" : ""}${k.g}%` },
      ]
      : [
        { time: off + k.b, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
        { time: off + k.s, position: "aboveBar", color: k.g > 0 ? RED : BLUE,
          shape: "arrowDown", text: `매도 ${k.g > 0 ? "+" : ""}${k.g}%` },
      ]).filter((x) => (x.time as number) >= off && (x.time as number) < off + bars.length);
    // the clicked trade gets its own gold marker, so it is obvious WHICH of the arrows
    // on screen is the row he clicked
    if (focus != null && bars[focus]) {
      m.push({ time: off + focus, position: "aboveBar", color: GOLD, shape: "arrowDown",
               text: `\u25c6 ${bars[focus].hhmm.slice(0, 5)}` } as never);
    }
    m.sort((a2, b2) => (a2.time as number) - (b2.time as number));
    c.series.setMarkers(m as never);

    // Sliding the data is NOT enough: the chart keeps its own view, so a 2,500-bar
    // payload looks unchanged and the trade he clicked sits somewhere off screen. The
    // same thing was true on the Strategy Lab (2026-08-03: "if I click any time it is
    // not opening exact time"). Zoom to the trade, once per change of focus.
    const sig = `${off}|${bars[0]?.hhmm ?? ""}`;
    if (applied.current !== focus || sigRef.current !== sig) {
      applied.current = focus;
      sigRef.current = sig;
      prog.current = true;
      userRange.current = null;            // a click starts a fresh view
      if (focus != null && bars[focus]) {
        c.chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, focus - 70), to: Math.min(bars.length - 1, focus + 25) });
      } else {
        // no clicked trade, but an OPEN position: land on its buy arrow and the live
        // edge, so a clicked HOLDING shows its proof without hunting (boss 2026-08-12)
        const om = (marks ?? []).find((k) => k.open);
        if (om && bars[om.b]) {
          c.chart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, om.b - 40), to: bars.length + 2 });
        } else {
          c.chart.timeScale().fitContent();
        }
      }
      setTimeout(() => { prog.current = false; }, 0);
    } else if (userRange.current
               && (userRange.current.to as number) < off + bars.length - 2) {
      // he scrolled away from the live edge - pin his view through the refresh
      prog.current = true;
      try { c.chart.timeScale().setVisibleRange(userRange.current as never); } catch {}
      setTimeout(() => { prog.current = false; }, 0);
    }
  }, [ready, bars, marks, focus, off]);

  // height follows the prop (⛶ fullscreen mode passes the window height)
  useEffect(() => {
    try { cs.current?.chart?.applyOptions({ height: h }); } catch { /* no-op */ }
  }, [h, ready]);
  return <div ref={ref} style={{ width: "100%", height: h }} />;
}

// 🛡 SAFE BOX (boss 2026-08-25: "Application error: a client-side exception
// has occurred" - one broken panel must never blank the whole page): a React
// error boundary that renders a small notice instead of crashing the app.
class SafeBox extends React.Component<{ children?: React.ReactNode; label?: string },
  { broken: boolean }> {
  constructor(props: { children?: React.ReactNode; label?: string }) {
    super(props);
    this.state = { broken: false };
  }
  static getDerivedStateFromError() { return { broken: true }; }
  render() {
    if (this.state.broken) {
      return <div className="mt-2 px-3 py-1 rounded border text-[10.5px]"
        style={{ borderColor: "#b8860b", color: "#b8860b" }}>
        ⚠ {this.props.label || "panel"} error — the rest of the page keeps working (hard-refresh to retry)</div>;
    }
    return this.props.children as React.ReactNode;
  }
}

// 🎬 THE LIVE ORDER ROOM (boss 2026-08-26: "I wanna see how our agent buying
// order, selling order, when the price increasing rapidly or decreasing
// rapidly how our agent handling — real time monitoring"): every open
// position drawn on its own live 1-minute chart with the agent's actual
// lines — the -1% stop (red), the next +1% ladder rung (green), the base
// (gray) — plus a running event ticker of today's buys/sells with reasons.
// All real recorded data from the same boards the desk trades by.
// TRADE REPLAY (boss 2026-08-27: "this already happened - if I click the
// NAME of the company in the history it should show only this trading's past
// play recording; the buy TIME keeps the arrow jump"). Plays the recorded
// bars of that episode with the real fills appearing at their true minutes.
// Playback of the record - prices are never invented.
// SAME MINUTE = ONE LINE (boss 2026-08-28 final: "we buy 3% first and after
// +0.5% the other 97% - if the increase happened WITHIN THIS MINUTE do not
// show 2 split buyings, just merge; if in another time then show another
// time; keep reinforcement also"): buys landing in one minute merge into a
// single line at their weighted-average price, with the laws-count tag.
// A fill in a different minute always gets its own provable line.
function mergeBuys9(buys: [number, number, (string | null)?][]) {
  const out: [number, number, string | null, number][] = [];
  for (const b of buys) {
    const key = String(b[2] || "").slice(0, 5);
    const last = out.length ? out[out.length - 1] : null;
    if (last && String(last[2] || "").slice(0, 5) === key && key) {
      const cost = last[0] * last[1] + b[0] * b[1];
      last[1] += b[1];
      last[0] = last[1] ? cost / last[1] : b[0];   // weighted-average price
      last[3] += 1;
    } else out.push([b[0], b[1], (b[2] ?? null), 1]);
  }
  return out;
}

// HOLDING SHOWS ONLY WHAT REMAINS (boss 2026-08-31 11:5x: "if in the slice
// some part already sold out, in the holding only need to show remaining
// stock with buying time - sold out case should not be shown"): the sold
// quantity consumes the buys FIRST-IN-FIRST-OUT; a buy fully consumed
// disappears, a partially consumed one shows its remaining shares.
function remainBuys9(buys: [number, number, (string | null)?][],
                     soldQty: number): [number, number, (string | null)?][] {
  let left = soldQty;
  const out: [number, number, (string | null)?][] = [];
  for (const b of buys) {
    const take = Math.min(left, b[1]);
    left -= take;
    if (b[1] - take > 0) out.push([b[0], b[1] - take, b[2] ?? null]);
  }
  return out;
}

function TradeReplay({ ep, t, onClose }: {
  ep: { code: string; name: string; entry?: number; base?: number;
        buy_t?: string; sell_t?: string; live?: boolean; exit?: number;
        exit_why?: string; qty?: number; rule?: string; net_pct?: number;
        sig?: unknown; wall?: { price?: number; qty?: number } | null;
        judge?: unknown;
        parts?: { buys?: unknown[][]; sells?: unknown[][] } };
  t: (ko: string, en: string) => string; onClose: () => void }) {
  type Bar = { hhmm: string; open: number; high: number; low: number; close: number };
  // ① why THIS stock - the recorded checklist rank at the buy moment
  const [rank, setRank] = useState<{ rank?: number | null; avg?: number;
    of?: number; in_top?: boolean } | null>(null);
  useEffect(() => {
    let live = true;
    api<{ ok: boolean; rank?: number | null; avg?: number; of?: number; in_top?: boolean }>(
      `/paper-desk/live/reco-rank-at?code=${ep.code}&t=${encodeURIComponent(String(ep.buy_t || "").slice(0, 8))}`)
      .then((d) => { if (live && d?.ok) setRank(d); })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ep]);
  const [bars, setBars] = useState<Bar[]>([]);
  const [pos, setPos] = useState(0);
  const [run, setRun] = useState(true);
  const [speed, setSpeed] = useState(3);
  const cv = useRef<HTMLCanvasElement | null>(null);
  const base = Number(ep.base ?? ep.entry ?? 0);
  const from = String(ep.buy_t || "09:00").slice(0, 5);
  const to = ep.live ? "15:30" : String(ep.sell_t || "15:30").slice(0, 5);
  const pb = (ep.parts?.buys || []) as [number, number, (string | null)?][];
  const buys: [string, number, number][] = pb.length
    ? pb.map((b) => [String(b[2] || ep.buy_t || from), Number(b[0]), Number(b[1])])
    : [[from, base, Number(ep.qty || 0)]];
  const ps = (ep.parts?.sells || []) as [number, number, string, unknown, unknown, unknown?][];
  const sells: [string, number, number, string][] = ps.length
    ? ps.map((s) => [String(s[2] || to), Number(s[0]), Number(s[1]), String(s[5] ?? "")])
    : (ep.exit ? [[to, Number(ep.exit), Number(ep.qty || 0), String(ep.exit_why || "")]] : []);
  useEffect(() => {
    let live = true;
    api<{ bars?: Bar[] }>(`/paper-desk/live/tape?code=${ep.code}&period=60&bars=100000`)
      .then((d) => {
        if (!live || !d?.bars?.length) return;
        const all = d.bars;
        let i0 = all.findIndex((b) => String(b.hhmm).slice(0, 5) >= from);
        if (i0 < 0) i0 = 0;
        let i1 = all.findIndex((b) => String(b.hhmm).slice(0, 5) > to);
        if (i1 < 0) i1 = all.length;
        setBars(all.slice(Math.max(0, i0 - 15), Math.min(all.length, i1 + 10)));
        setPos(0); setRun(true);
      }).catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ep]);
  useEffect(() => {
    if (!run || !bars.length) return;
    const h = setInterval(() => setPos((p) => {
      const np = Math.min(p + speed, bars.length);
      if (np >= bars.length) setRun(false);
      return np;
    }), 600);
    return () => clearInterval(h);
  }, [run, speed, bars]);
  useEffect(() => {
    const c = cv.current;
    if (!c || !bars.length) return;
    const bs = bars.slice(0, Math.max(1, pos));
    const W = c.clientWidth, H = c.clientHeight;
    c.width = W * 2; c.height = H * 2;
    const g = c.getContext("2d");
    if (!g) return;
    g.scale(2, 2); g.clearRect(0, 0, W, H);
    const stop = base ? base * 0.99 : 0;
    const lo = Math.min(...bars.map((b) => b.low), stop || Infinity) * 0.999;
    const hi = Math.max(...bars.map((b) => b.high), base || 0) * 1.001;
    const y = (v: number) => 8 + (hi - v) / Math.max(1e-9, hi - lo) * (H - 30);
    const bw = (W - 56) / bars.length;
    g.font = "9px sans-serif";
    bs.forEach((b, i) => {
      const x = 56 + i * bw + bw / 2;
      const up = b.close >= b.open;
      g.strokeStyle = g.fillStyle = up ? "#d32f2f" : "#1565c0";
      g.beginPath(); g.moveTo(x, y(b.high)); g.lineTo(x, y(b.low)); g.stroke();
      g.fillRect(x - Math.max(1, bw * 0.3), y(Math.max(b.open, b.close)),
                 Math.max(2, bw * 0.6), Math.max(1, Math.abs(y(b.open) - y(b.close))));
      // TIME EVERY FEW BARS (boss 2026-08-28: "the x axis is not showing
      // fully to prove it - every 20 minutes; add minute-based"): as dense
      // as fits without overlap - every minute when zoomed in
      const step9 = Math.max(1, Math.ceil(36 / bw));
      if (i % step9 === 0) { g.fillStyle = "#888"; g.fillText(String(b.hhmm).slice(0, 5), x - 12, H - 6); }
    });
    const line = (v: number, col: string, lab: string) => {
      if (!v) return;
      g.strokeStyle = col; g.setLineDash([5, 3]);
      g.beginPath(); g.moveTo(56, y(v)); g.lineTo(W - 2, y(v)); g.stroke();
      g.setLineDash([]); g.fillStyle = col; g.fillText(lab, 2, y(v) + 3);
    };
    line(base, "#9e9e9e", `기준 ${Math.round(base).toLocaleString()}`);
    line(stop, "#c62828", `-1% ${Math.round(stop).toLocaleString()}`);
    const clock = bs.length ? String(bs[bs.length - 1].hhmm).slice(0, 5) : "";
    const mark = (tt: string, px: number, up: boolean, lab: string) => {
      if (!tt || String(tt).slice(0, 5) > clock) return;
      let bi = bars.findIndex((b) => String(b.hhmm).slice(0, 5) >= String(tt).slice(0, 5));
      if (bi < 0) bi = bars.length - 1;
      const x = 56 + bi * bw + bw / 2;
      g.fillStyle = up ? "#2e7d32" : "#e65100";
      g.beginPath();
      if (up) { g.moveTo(x, y(px) + 12); g.lineTo(x - 5, y(px) + 20); g.lineTo(x + 5, y(px) + 20); }
      else { g.moveTo(x, y(px) - 12); g.lineTo(x - 5, y(px) - 20); g.lineTo(x + 5, y(px) - 20); }
      g.closePath(); g.fill();
      g.fillText(lab, Math.min(x + 4, W - 60), up ? y(px) + 30 : y(px) - 24);
    };
    for (const [tt, px, qy] of buys) mark(tt, px, true, `▲매수 ${qy ? qy + "주" : ""}`);
    for (const [tt, px, qy, why] of sells) mark(tt, px, false,
      `▼매도${why ? " " + String(why).slice(0, 6) : ""} ${qy ? qy + "주" : ""}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos, bars]);
  return (
    <div id="trade-replay9" className="my-2 px-2 py-1.5 rounded-lg border-2" style={{ borderColor: "#4a148c", background: "rgba(74,20,140,0.04)" }}>
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        <b style={{ color: "#4a148c" }}>
          🎞 {ep.name} {from}→{ep.live ? t("지금", "now") : to} {t("다시보기 — 실제 기록 재생 (봉도 체결도 그날 그대로)", "replay - the real recording, bars and fills as they happened")}</b>
        <button onClick={() => setRun((r) => !r)} className="px-2.5 py-0.5 rounded border font-bold"
          style={{ background: run ? "#fff" : "#4a148c", color: run ? "#4a148c" : "#fff", borderColor: "#4a148c" }}>
          {run ? t("⏸ 일시정지", "⏸ pause") : t("▶ 재생", "▶ play")}</button>
        <button onClick={() => { setPos(0); setRun(true); }} className="px-2 py-0.5 rounded border"
          style={{ borderColor: "#4a148c", color: "#4a148c" }}>⏮ {t("처음부터", "restart")}</button>
        {[1, 3, 10].map((s) => (
          <button key={s} onClick={() => setSpeed(s)} className="px-2 py-0.5 rounded border"
            style={speed === s ? { background: "#4a148c", color: "#fff", borderColor: "#4a148c" }
                               : { borderColor: "#4a148c", color: "#4a148c" }}>×{s}</button>
        ))}
        <span className="tabular-nums opacity-70">
          {bars.length ? `${String((bars[Math.max(0, pos - 1)] || {}).hhmm || "").slice(0, 5)} · ${pos}/${bars.length}${t("봉", " bars")}` : t("기록 로딩…", "loading the record…")}</span>
        <button onClick={onClose} className="ml-auto px-2 py-0.5 rounded border"
          style={{ borderColor: "#4a148c", color: "#4a148c" }}>✕ {t("닫기", "close")}</button>
      </div>
      {/* THE WHOLE PROCESS (boss 2026-08-27: "show how we selected this
          stock, how we decided the price with the order book, how we bought,
          how we sold - all detail"): four numbered blocks, all from the RECORD */}
      <div className="mt-1.5 grid gap-1.5 text-[11px]" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
        <div className="px-2 py-1.5 rounded border" style={{ borderColor: "var(--border-default)" }}>
          <b style={{ color: "#4a148c" }}>{t("① 왜 이 종목을 골랐나", "① why this stock")}</b>
          <div className="mt-0.5">
            {rank && rank.rank != null
              ? t(`매수 순간의 100항목 체크리스트 기록: 전체 ${rank.of ?? "?"}종목 중 ${rank.rank}위 (점수 ${rank.avg ?? "?"}) — ${rank.in_top ? "상위 자리(좌석) 안이라 매수 자격이 있었습니다" : "당시 순위 기록"}`,
                  `the recorded 100-item checklist at the buy moment: rank ${rank.rank} of ${rank.of ?? "?"} (score ${rank.avg ?? "?"}) — ${rank.in_top ? "inside the top seats, so it was allowed to buy" : "the rank at that time"}`)
              : t("회장님의 고정 6종목 데스크 — 이 종목은 항상 감시·매매 대상입니다 (메뉴2 종목이면 매수 순간의 순위 기록이 여기 뜹니다)",
                  "the boss's fixed six-stock desk - always watched and traded (a menu-2 stock shows its recorded rank here)")}
          </div>
        </div>
        <div className="px-2 py-1.5 rounded border" style={{ borderColor: "var(--border-default)" }}>
          <b style={{ color: "#4a148c" }}>{t("② 가격을 어떻게 정했나 (호가창)", "② how the price was decided (order book)")}</b>
          <div className="mt-0.5">
            {ep.wall && ep.wall.price
              ? t(`호가창의 제일 큰 매수벽 ₩${Number(ep.wall.price).toLocaleString()}${ep.wall.qty ? ` (${Number(ep.wall.qty).toLocaleString()}주)` : ""} 바로 한 틱 위 ₩${Math.round(base).toLocaleString()}에 제시 — 벽보다 먼저 체결되는 자리에 줄을 섰습니다`,
                  `queued at ₩${Math.round(base).toLocaleString()}, one tick in front of the book's biggest buy wall ₩${Number(ep.wall.price).toLocaleString()}${ep.wall.qty ? ` (${Number(ep.wall.qty).toLocaleString()}sh)` : ""} - the spot that fills before the wall`)
              : t(`신호봉의 가격 ₩${Math.round(base).toLocaleString()}으로 지정가 제시, 2봉 대기 법 — 오지 않으면 추격하지 않고 포기합니다`,
                  `offered the signal bar's price ₩${Math.round(base).toLocaleString()} as a limit, 2-bar wait law - if the market doesn't come, we don't chase`)}
          </div>
        </div>
        <div className="px-2 py-1.5 rounded border" style={{ borderColor: "var(--border-default)" }}>
          <b style={{ color: "#2e7d32" }}>{t("③ 어떻게 샀나", "③ how it bought")}</b>
          <div className="mt-0.5 tabular-nums">
            {buys.map(([tt, px, qy], i) => (
              <div key={i}>▲ {String(tt).slice(0, 5)} ₩{Math.round(px).toLocaleString()}{qy ? ` × ${qy}${t("주", "sh")}` : ""}
                {i === 0 ? t(" — 규칙의 문이 열려 진입", " - the rule's door opened") : t(" — 보유 중 추가 매수", " - add-on while holding")}</div>
            ))}
          </div>
        </div>
        <div className="px-2 py-1.5 rounded border" style={{ borderColor: "var(--border-default)" }}>
          <b style={{ color: "#e65100" }}>{t("④ 어떻게 팔았나", "④ how it sold")}</b>
          <div className="mt-0.5 tabular-nums">
            {sells.length ? sells.map(([tt, px, qy, why], i) => (
              <div key={i}>▼ {String(tt).slice(0, 5)} ₩{Math.round(px).toLocaleString()}{qy ? ` × ${qy}${t("주", "sh")}` : ""}
                {why ? ` — ${String(why).slice(0, 22)}` : ""}</div>
            )) : <div>{ep.live ? t("아직 보유 중 — 매도 기록이 생기면 여기 쌓입니다", "still holding - sells will stack here") : "-"}</div>}
            {ep.exit_why && <div className="opacity-80">✔ {String(ep.exit_why).slice(0, 40)}{ep.net_pct != null ? ` · ${t("실수익", "net")} ${ep.net_pct}%` : ""}</div>}
          </div>
        </div>
      </div>
      <canvas ref={cv} className="w-full mt-1 rounded border" style={{ height: 240, borderColor: "rgba(74,20,140,0.3)" }} />
      <div className="text-[9.5px] opacity-70">
        {t("▲초록 = 실제 매수 · ▼주황 = 실제 매도 — 재생 시계가 그 분을 지나는 순간 나타납니다",
           "▲green = real buys · ▼orange = real sells - each appears as the replay clock passes its minute")}</div>
    </div>
  );
}

// ORDER ROOM, one stock BIG (boss 2026-08-27: "just a list of stock
// companies - if I click SK hynix we watch ONLY SK hynix's upcoming tradings
// in real time, big screen; right now table, chart and info are too small").
// The desk's stocks are buttons; the chosen one fills the screen: big live
// chart with the agent's lines, the order book with OUR order written in it,
// that stock's own step, holding and events. Nothing else competes for space.
function OrderRoom({ t, desk }: { t: (ko: string, en: string) => string;
                                  desk: "m1" | "m2" }) {
  type Hold = { code: string; name: string; base?: number; entry?: number;
    qty_left?: number; unreal_pct?: number; rule?: string; buy_t?: string;
    slices?: [number, number, string, number, number][] };
  type Row = { code: string; name: string; buy_t?: string; sell_t?: string;
    net_pct?: number; exit_why?: string; rule?: string; entry?: number;
    parts?: { buys?: unknown[][]; sells?: unknown[][] } };
  type Bar = { hhmm: string; open: number; high: number; low: number; close: number };
  const SIX9: { code: string; name: string }[] = [
    { code: "000660", name: "SK하이닉스" }, { code: "005930", name: "삼성전자" },
    { code: "035420", name: "NAVER" }, { code: "017670", name: "SK텔레콤" },
    { code: "042660", name: "한화오션" }, { code: "034020", name: "두산에너빌리티" }];
  const [fam9, setFam9] = useState<"d1" | "d2" | "d3" | "d4">("d2");
  const [open9, setOpen9] = useState(false);
  const [sel9, setSel9] = useState("");
  const [watchList9, setWatchList9] = useState<{ code: string; name: string }[]>([]);
  const [holds9, setHolds9] = useState<Hold[]>([]);
  const [chat9, setChat9] = useState<Hold[]>([]);
  const [waitsAll9, setWaitsAll9] = useState<{ code: string; name?: string; side: string;
    qty?: number; px: number; wall?: number | null; chat: boolean }[]>([]);
  const [evts9, setEvts9] = useState<{ code: string; txt: string }[]>([]);
  const [ts9, setTs9] = useState("");
  const [fire9, setFire9] = useState<{ txt: string; buy: boolean; id: number }[]>([]);
  const prevEv9 = useRef<Set<string> | null>(null);
  const prevWait9 = useRef<Set<number> | null>(null);
  const fireId9 = useRef(1);
  const pushFire9 = (txt: string, buy: boolean) => {
    const id = fireId9.current++;
    setFire9((f) => [{ txt, buy, id }, ...f].slice(0, 3));
    setTimeout(() => setFire9((f) => f.filter((x) => x.id !== id)), 14000);
  };
  const [bars9, setBars9] = useState<Bar[]>([]);
  const [book9, setBook9] = useState<{ asks: [number, number][];
    bids: [number, number][]; prev?: number } | null>(null);
  // 1분 / 일봉 (boss 2026-08-27: "in the chart put 2 buttons, 1 minute and
  // daily") - daily = the year's candles WITH the zone lines, so the chosen
  // stock's top/bottom law is visible in the same screen
  const [per9, setPer9] = useState<"1m" | "day">("1m");
  const [dayC9, setDayC9] = useState<{ candles: { d8: string; open: number;
    high: number; low: number; close: number }[]; lines?: { no_buy_85?: number;
    caution_60?: number; bottom_20?: number } } | null>(null);
  useEffect(() => {
    if (!open9 || !sel9 || per9 !== "day") return;
    let live = true;
    api<{ candles?: { d8: string; open: number; high: number; low: number;
      close: number }[]; lines?: { no_buy_85?: number; caution_60?: number;
      bottom_20?: number } }>(`/paper-desk/live/daily-chart?code=${sel9}`)
      .then((d) => { if (live && d?.candles?.length)
        setDayC9({ candles: d.candles, lines: d.lines }); })
      .catch(() => {});
    return () => { live = false; };
  }, [open9, sel9, per9]);
  const deskList9 = desk === "m1" ? SIX9 : watchList9;
  useEffect(() => {
    if (!open9 || desk === "m1") return;
    api<{ stocks: { code: string; name: string }[] }>("/paper-desk/live/status")
      .then((d) => { if (d?.stocks) setWatchList9(d.stocks.map(
        (s) => ({ code: s.code, name: s.name || s.code }))); })
      .catch(() => {});
  }, [open9, desk]);
  // the desk's living state - holdings, waiting offers, events - every 12s
  useEffect(() => {
    if (!open9) return;
    let live = true;
    const load = async () => {
      try {
        let codes = "";
        if (desk === "m2") {
          const st = await api<{ stocks: { code: string }[] }>("/paper-desk/live/status");
          codes = (st?.stocks || []).map((s) => s.code).join(",");
        }
        const q = codes ? `&codes=${codes}` : "";
        const r = await api<{ computing?: boolean; rows?: Row[]; holding?: Hold[];
          waiting?: { code: string; name: string; t?: string; px?: number;
            qty?: number; wall?: { price?: number } | number | null }[] }>(
          `/paper-desk/live/rules/family-trades?family=${fam9}&period=60&day=&frm=&to=&gate=1&auto=1${q}`);
        if (!live || !r || r.computing) return;
        const hh = (r.holding || []).filter((h) => h.rule !== "chatbot");
        setHolds9(hh);
        // 💬 the boss's own chat lots ride the room too (boss 2026-08-28:
        // "I wanna use the chatbot - buy 10 shares, then watch it in real
        // time") - shown beside the algo position in the one-stock view
        setChat9((r.holding || []).filter((h) => h.rule === "chatbot"));
        const ev: [string, string, string][] = [];
        for (const x of (r.rows || [])) {
          if (x.rule === "chatbot") continue;
          if (x.buy_t) ev.push([String(x.buy_t), x.code, `🟢 ${t("매수", "BUY")} ${x.name} (${String(x.buy_t).slice(0, 5)})`]);
          if (x.sell_t && x.exit_why) ev.push([String(x.sell_t), x.code, `✔ ${x.name} ${t("종료", "closed")} ${x.net_pct ?? ""}% — ${String(x.exit_why).slice(0, 22)} (${String(x.sell_t).slice(0, 5)})`]);
        }
        for (const h of hh) {
          if (h.slices) for (const s of h.slices) {
            const why = String(s[2] ?? "");
            // rule-check chip: the fill price verified against the rule's own
            // formula at the base RECORDED at that sale (s[4])
            const b0 = Number(s[4] ?? h.base ?? 0);
            const px = Number(s[0] ?? 0);
            let chip = "";
            if (b0 && px) {
              if (why.startsWith("+")) {
                const k = parseFloat(why) || 1;
                chip = px >= b0 * (1 + (k * 1.0 - 0.15) / 100) * 0.999
                  ? " ✓" + t("규칙가", "rule px") : " ⚠" + t("규칙과 다름", "off-rule");
              } else if (why.includes("-1") || why.includes("손절")) {
                chip = (px <= b0 * 0.9905 && px >= b0 * 0.982)
                  ? " ✓" + t("손절가", "stop px") : " ⚠" + t("규칙과 다름", "off-rule");
              }
            }
            ev.push([String(s[3] ?? ""), h.code, `🔴 ${t("매도", "SELL")} ${h.name} ${s[1]}주 @ ${Number(s[0]).toLocaleString()} — ${why.slice(0, 16)}${chip}`]);
          }
        }
        ev.sort((a, b) => (a[0] < b[0] ? 1 : -1));
        const evRows = ev.slice(0, 40).map((e) => ({ code: e[1], txt: e[2] }));
        if (prevEv9.current) {
          for (const e of evRows.slice(0, 14)) {
            if (!prevEv9.current.has(e.txt)) pushFire9(e.txt, e.txt.startsWith("🟢"));
          }
        }
        prevEv9.current = new Set(evRows.slice(0, 14).map((e) => e.txt));
        setEvts9(evRows);
        setTs9(new Date().toLocaleTimeString("ko-KR", { hour12: false }));
        // waiting offers: the algo's working pends + the boss's chat limits
        // (menu 1 is the six, pure - boss 2026-08-27)
        const SIXSET = new Set(SIX9.map((s) => s.code));
        const merged: { code: string; name?: string; side: string; qty?: number;
          px: number; wall?: number | null; chat: boolean }[] =
          (r.waiting || []).map((w) => ({
            code: w.code, name: w.name, side: "BUY", qty: w.qty, px: Number(w.px || 0),
            wall: (typeof w.wall === "object" && w.wall ? w.wall.price : w.wall) as number | null,
            chat: false }));
        try {
          const stt = await api<{ open_orders?: { id?: number; ticker?: string;
            name?: string; side?: string; qty?: number; limit_price?: number }[];
            history?: { id?: number; status?: string; name?: string; side?: string;
              qty?: number; fill_price?: number }[] }>("/paper-desk/state");
          const oo = stt?.open_orders || [];
          if (prevWait9.current) {
            for (const pid of Array.from(prevWait9.current)) {
              if (!oo.some((o) => o.id === pid)) {
                const hrow = (stt?.history || []).find((x) => x.id === pid);
                if (hrow && hrow.status === "FILLED") {
                  pushFire9(`🔥 지정가 도달 — 체결! ${hrow.side === "BUY" ? "매수" : "매도"} `
                    + `${hrow.name} ${hrow.qty}주 @ ${Number(hrow.fill_price || 0).toLocaleString()}`,
                    hrow.side === "BUY");
                }
              }
            }
          }
          prevWait9.current = new Set(oo.map((o) => Number(o.id)));
          for (const w of oo) {
            const cc = String(w.ticker || "");
            if (desk === "m1" && !SIXSET.has(cc)) continue;
            merged.push({ code: cc, name: w.name || cc, side: String(w.side || "BUY"),
                          qty: w.qty, px: Number(w.limit_price || 0), wall: null, chat: true });
          }
        } catch { /* state optional */ }
        if (live) setWaitsAll9(merged);
      } catch { /* keep the last good frame */ }
    };
    load();
    const h2 = setInterval(load, 12000);
    return () => { live = false; clearInterval(h2); };
  }, [open9, fam9, desk, t]);
  // THE ONE BIG STOCK - its tape and book, 8s (nothing loads until a click)
  useEffect(() => {
    if (!open9 || !sel9) return;
    let live = true;
    const load = async () => {
      try {
        const tp = await api<{ bars?: Bar[] }>(
          `/paper-desk/live/tape?code=${sel9}&period=60&bars=240`);
        if (live && tp?.bars) setBars9(tp.bars);
        const bk = await api<{ asks?: [number, number][]; bids?: [number, number][];
          prev_close?: number }>(`/paper-desk/live/book?code=${sel9}`);
        if (live && bk?.asks) setBook9({ asks: bk.asks, bids: bk.bids || [],
                                         prev: bk.prev_close });
      } catch { /* keep last */ }
    };
    setBars9([]); setBook9(null);
    load();
    const h = setInterval(load, 8000);
    return () => { live = false; clearInterval(h); };
  }, [open9, sel9]);
  const selHold9 = holds9.find((h) => h.code === sel9) || null;
  const selChat9 = chat9.find((h) => h.code === sel9) || null;
  const selWaits9 = waitsAll9.filter((w) => w.code === sel9);
  const cv9 = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const c = cv9.current;
    if (!c) return;
    // 일봉: the year with the zone laws drawn (매도구간 85 / 주의 60 / 매수구간 20)
    if (per9 === "day") {
      const cd = dayC9?.candles || [];
      if (!cd.length) return;
      const W = c.clientWidth, H = c.clientHeight;
      c.width = W * 2; c.height = H * 2;
      const g = c.getContext("2d");
      if (!g) return;
      g.scale(2, 2); g.clearRect(0, 0, W, H);
      const lo = Math.min(...cd.map((b) => b.low)) * 0.995;
      const hi = Math.max(...cd.map((b) => b.high)) * 1.005;
      const y = (v: number) => 8 + (hi - v) / Math.max(1e-9, hi - lo) * (H - 30);
      const bw = (W - 60) / cd.length;
      g.font = "10px sans-serif";
      cd.forEach((b, i) => {
        const x = 60 + i * bw + bw / 2;
        const up = b.close >= b.open;
        g.strokeStyle = g.fillStyle = up ? "#d32f2f" : "#1565c0";
        g.beginPath(); g.moveTo(x, y(b.high)); g.lineTo(x, y(b.low)); g.stroke();
        g.fillRect(x - Math.max(0.5, bw * 0.3), y(Math.max(b.open, b.close)),
                   Math.max(1, bw * 0.6), Math.max(1, Math.abs(y(b.open) - y(b.close))));
        if (i % 50 === 0) { g.fillStyle = "#888";
          g.fillText(`${b.d8.slice(4, 6)}/${b.d8.slice(6)}`, x - 12, H - 8); }
      });
      g.fillStyle = "#888";
      for (let i = 0; i <= 4; i++) {
        const v = lo + (hi - lo) * i / 4;
        g.fillText(Math.round(v).toLocaleString(), 2, y(v) + 3);
      }
      const zline = (v: number | undefined, col: string, lab: string) => {
        if (!v) return;
        g.strokeStyle = col; g.setLineDash([6, 4]);
        g.beginPath(); g.moveTo(60, y(v)); g.lineTo(W - 2, y(v)); g.stroke();
        g.setLineDash([]); g.fillStyle = col; g.font = "11px sans-serif";
        g.fillText(lab, 64, y(v) - 4); g.font = "10px sans-serif";
      };
      zline(dayC9?.lines?.no_buy_85, "#c62828", "🔴 매도구간 (85% — 신규 매수 금지)");
      zline(dayC9?.lines?.caution_60, "#e65100", "🟠 주의 (60% — 절반만 매수)");
      zline(dayC9?.lines?.bottom_20, "#2e7d32", "🟢 매수구간 (20% — 바닥)");
      // today, marked on the last candle
      const lx = 60 + (cd.length - 1) * bw + bw / 2;
      g.fillStyle = "#6a1b9a"; g.font = "11px sans-serif";
      g.fillText("← 오늘", Math.min(lx + 4, W - 44), y(cd[cd.length - 1].close));
      return;
    }
    if (!bars9.length) return;
    const W = c.clientWidth, H = c.clientHeight;
    c.width = W * 2; c.height = H * 2;
    const g = c.getContext("2d");
    if (!g) return;
    g.scale(2, 2); g.clearRect(0, 0, W, H);
    const base0 = Number(selHold9?.base ?? selHold9?.entry ?? 0);
    const k9 = (selHold9?.slices || []).filter((s) => String(s[2] ?? "").startsWith("+")).length;
    const stop9 = base0 ? base0 * 0.99 : 0;
    const step9 = base0 && fam9 !== "d3" ? base0 * (1 + ((k9 + 1) * 1.0 - 0.15) / 100) : 0;
    const lo = Math.min(...bars9.map((b) => b.low), stop9 || Infinity) * 0.999;
    const hi = Math.max(...bars9.map((b) => b.high), step9 || 0, base0 || 0) * 1.001;
    const y = (v: number) => 8 + (hi - v) / Math.max(1e-9, hi - lo) * (H - 30);
    const bw = (W - 60) / bars9.length;
    g.font = "10px sans-serif";
    bars9.forEach((b, i) => {
      const x = 60 + i * bw + bw / 2;
      const up = b.close >= b.open;
      g.strokeStyle = g.fillStyle = up ? "#d32f2f" : "#1565c0";
      g.beginPath(); g.moveTo(x, y(b.high)); g.lineTo(x, y(b.low)); g.stroke();
      g.fillRect(x - Math.max(1, bw * 0.3), y(Math.max(b.open, b.close)),
                 Math.max(2, bw * 0.6), Math.max(1, Math.abs(y(b.open) - y(b.close))));
      const stp9 = Math.max(1, Math.ceil(38 / bw));
      if (i % stp9 === 0) { g.fillStyle = "#888"; g.fillText(String(b.hhmm).slice(0, 5), x - 14, H - 8); }
    });
    g.fillStyle = "#888";
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * i / 4;
      g.fillText(Math.round(v).toLocaleString(), 2, y(v) + 3);
    }
    const line = (v: number, col: string, lab: string) => {
      if (!v) return;
      g.strokeStyle = col; g.setLineDash([5, 3]);
      g.beginPath(); g.moveTo(60, y(v)); g.lineTo(W - 2, y(v)); g.stroke();
      g.setLineDash([]); g.fillStyle = col; g.font = "11px sans-serif";
      g.fillText(lab, 2, y(v) - 4); g.font = "10px sans-serif";
    };
    if (base0) {
      line(stop9, "#c62828", `-1% 전량 ${Math.round(stop9).toLocaleString()}`);
      if (step9) line(step9, "#2e7d32", `+${k9 + 1}% ${fam9 === "d1" ? "50%" : "10%"} 매도 ${Math.round(step9).toLocaleString()}`);
      line(base0, "#9e9e9e", `기준 ${Math.round(base0).toLocaleString()}`);
    }
    for (const w of selWaits9) {
      line(w.px, "#b8860b", `⏳ ${w.side === "BUY" ? "매수" : "매도"} 제시 ${Math.round(w.px).toLocaleString()}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars9, selHold9, fam9, sel9, waitsAll9, per9, dayC9]);
  const selName9 = (deskList9.find((s) => s.code === sel9) || { name: sel9 }).name;
  const selEvts9 = evts9.filter((e) => e.code === sel9);
  const step9now = selHold9 ? 4 : selWaits9.length ? 2 : 1;
  const maxA9 = book9 ? Math.max(0, ...book9.asks.slice(0, 7).map((a) => a[1])) : 0;
  const maxB9 = book9 ? Math.max(0, ...book9.bids.slice(0, 7).map((a) => a[1])) : 0;
  return (
    <div className="mt-2 rounded-xl text-[11px] overflow-hidden border-2"
      style={{ borderColor: "#37474f" }}>
      <div className="px-3 py-2 flex items-center gap-2 flex-wrap cursor-pointer select-none"
        style={{ background: "#37474f", color: "#eceff1" }}
        onClick={() => setOpen9((o) => !o)}>
        <b className="text-[13px]">🗼 {t("오더룸", "ORDER ROOM")} · {desk === "m1" ? t("메뉴1 — 6종목", "Menu 1 - the six") : t("메뉴2 — 체크리스트", "Menu 2 - checklist")}</b>
        <span style={{ opacity: 0.75 }}>
          {t("종목을 고르면 그 종목만 크게 — 실시간", "pick a stock, watch only it - big, live")}
          {ts9 && ` · ${ts9}`}</span>
        <span className="ml-auto font-bold">{open9 ? "▲" : t("▼ 열기", "▼ open")}</span>
      </div>
      {open9 && (
      <div className="px-3 py-2" style={{ background: "var(--bg-elevated)" }}>
        {fire9.map((f) => (
          <div key={f.id} className="mb-2 px-4 py-3 rounded-xl font-extrabold animate-pulse tabular-nums"
            style={{ fontSize: 19, background: f.buy ? "rgba(46,125,50,0.14)" : "rgba(198,40,40,0.14)",
                     border: `3px solid ${f.buy ? "#2e7d32" : "#c62828"}`,
                     color: f.buy ? "#2e7d32" : "#c62828" }}>
            🔥 {f.txt}
          </div>
        ))}
        <div className="flex gap-1 flex-wrap items-center">
          {(["d1", "d2", "d3", "d4"] as const).map((f) => (
            <button key={f} onClick={() => setFam9(f)}
              className="px-2 py-0.5 rounded border text-[10.5px]"
              style={fam9 === f ? { background: "#37474f", color: "#fff", borderColor: "#37474f", fontWeight: 700 }
                                : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
              {f === "d4" ? t("알고4 (갭룰)", "Algo 4 (gap)") : t(`알고${f[1]}`, `Algo ${f[1]}`)}</button>
          ))}
          <span className="mx-1 opacity-40">|</span>
          {deskList9.map((s) => {
            const isH = holds9.some((h) => h.code === s.code);
            const isW = waitsAll9.some((w) => w.code === s.code);
            return (
              <button key={s.code} onClick={() => setSel9(sel9 === s.code ? "" : s.code)}
                className="px-2 py-1 rounded-lg border text-[11.5px] font-bold"
                style={sel9 === s.code
                  ? { background: "#1565c0", color: "#fff", borderColor: "#1565c0" }
                  : { borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
                {s.name}{isH ? " 🟢" : isW ? " ⏳" : ""}</button>
            );
          })}
        </div>
        {!sel9 ? (
          <div className="mt-2 text-[var(--text-muted)] text-[11.5px]">
            {t("종목 이름을 누르면 그 종목의 매매만 크게 실시간으로 봅니다 — 🟢 보유 중 · ⏳ 주문 대기 중. (지난 매매 다시보기는 아래 매매 내역에서 회사 이름을 누르세요)",
               "click a stock to watch ONLY its trading, big and live - 🟢 holding · ⏳ order waiting. (to replay a past trade, click the company NAME in the trading history below)")}</div>
        ) : (
        <div className="mt-2">
          <div className="flex items-baseline gap-3 flex-wrap tabular-nums">
            <b className="text-[17px]">{selName9}</b>
            {bars9.length > 0 && (
              <b className="text-[17px]">₩{Math.round(bars9[bars9.length - 1].close).toLocaleString()}</b>
            )}
            {selHold9 ? (
              <span className="text-[13px] font-bold">
                🟢 {t("보유", "holding")} {selHold9.qty_left ?? "?"}{t("주", "sh")}{" "}
                <span style={{ color: (selHold9.unreal_pct ?? 0) >= 0 ? "#d32f2f" : "#1565c0" }}>
                  {(selHold9.unreal_pct ?? 0) >= 0 ? "+" : ""}{selHold9.unreal_pct}%</span>
                {" · "}{t(`사다리 ${(selHold9.slices || []).filter((s) => String(s[2] ?? "").startsWith("+")).length}칸 판매됨`,
                          `${(selHold9.slices || []).filter((s) => String(s[2] ?? "").startsWith("+")).length} rungs banked`)}
              </span>
            ) : selWaits9.length ? (
              <span className="text-[13px] font-bold" style={{ color: "#b8860b" }}>
                ⏳ {t("주문이 호가창에 줄 서 있음", "order queued in the book")} — ₩{selWaits9[0].px.toLocaleString()}
              </span>
            ) : (
              <span className="text-[12px] text-[var(--text-muted)]">
                {t("포지션 없음 — 규칙의 문이 조건을 기다리는 중", "no position - the rule's doors are watching for their condition")}</span>
            )}
            {selChat9 && (
              <span className="text-[13px] font-bold" style={{ color: "#6a1b9a" }}>
                💬 {t("내 주문", "MY order")} {selChat9.qty_left ?? "?"}{t("주", "sh")} @ ₩{Math.round(Number(selChat9.entry || selChat9.base || 0)).toLocaleString()}{" "}
                <span style={{ color: (selChat9.unreal_pct ?? 0) >= 0 ? "#d32f2f" : "#1565c0" }}>
                  {(selChat9.unreal_pct ?? 0) >= 0 ? "+" : ""}{selChat9.unreal_pct}%</span>
                {" · "}{t("알고2가 자동 관리 중 (+1% 계단 10% 매도 · -1% 가드 · 15:19 종)", "auto-managed by Algo2 (+1% rungs · -1% guard · 15:19 bell)")}
              </span>
            )}
            <span className="ml-auto flex gap-1">
              {([["1m", t("1분", "1min")], ["day", t("일봉 (연간+구간선)", "daily (year+zones)")]] as const).map(([pp, lab]) => (
                <button key={pp} onClick={() => setPer9(pp)}
                  className="px-2 py-0.5 rounded border text-[10.5px] font-bold"
                  style={per9 === pp ? { background: "#1565c0", color: "#fff", borderColor: "#1565c0" }
                                     : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                  {lab}</button>
              ))}
            </span>
          </div>
          {/* 🧭 THE PROCESS, live (boss 2026-08-27: "how we can see process?"):
              the step road with the current step lit, and the exact next move
              with the live distance to it - the story tells itself each refresh */}
          <div className="mt-1.5 px-2 py-1.5 rounded border text-[12px] font-bold tabular-nums"
            style={{ borderColor: "#1565c0", background: "rgba(21,101,192,0.05)" }}>
            <span className="flex gap-1 flex-wrap items-center">
              {[t("① 신호", "① signal"), t("② 줄서기", "② queue"), t("③ 체결", "③ fill"),
                t("④ 보유·사다리", "④ hold·ladder"), t("⑤ 완료", "⑤ done")].map((s2, i) => (
                <span key={i} className="px-1.5 py-0.5 rounded"
                  style={i + 1 === step9now || (step9now === 4 && i === 2)
                    ? { background: "#1565c0", color: "#fff" }
                    : { background: "rgba(21,101,192,0.08)", color: "var(--text-secondary)" }}>
                  {s2}{i < 4 ? " →" : ""}</span>
              ))}
            </span>
            <div className="mt-1">
              {(() => {
                const last = bars9.length ? bars9[bars9.length - 1].close : 0;
                if (selHold9) {
                  const b0 = Number(selHold9.base ?? selHold9.entry ?? 0);
                  const k9c = (selHold9.slices || []).filter((s) => String(s[2] ?? "").startsWith("+")).length;
                  const rung = b0 * (1 + ((k9c + 1) * 1.0 - 0.15) / 100);
                  const stop = b0 * 0.99;
                  const dRung = last ? (rung / last - 1) * 100 : 0;
                  const dStop = last ? (stop / last - 1) * 100 : 0;
                  return <>
                    🟢 {t(`${String(selHold9.buy_t || "").slice(0, 5)} 체결 ₩${Math.round(b0).toLocaleString()} → 보유 중`,
                          `filled ${String(selHold9.buy_t || "").slice(0, 5)} @ ₩${Math.round(b0).toLocaleString()} → holding`)}
                    {fam9 === "d3"
                      ? t(" · 다음: 고점 후 3음봉이면 전량 매도", " · next: sells ALL at the 3rd blue after a peak")
                      : t(` · 다음: ₩${Math.round(rung).toLocaleString()} (+${(k9c + 1)}% 계단, 지금가에서 ${dRung >= 0 ? "+" : ""}${dRung.toFixed(2)}%) 도달 시 ${fam9 === "d1" ? "50%" : "10%"} 매도`,
                          ` · next: sell ${fam9 === "d1" ? "50%" : "10%"} at ₩${Math.round(rung).toLocaleString()} (+${(k9c + 1)}% rung, ${dRung >= 0 ? "+" : ""}${dRung.toFixed(2)}% from here)`)}
                    {t(` · ₩${Math.round(stop).toLocaleString()} (-1%선, ${dStop.toFixed(2)}%) 터치 시 전량 → ⑤ 완료로`,
                       ` · ALL out at ₩${Math.round(stop).toLocaleString()} (the -1% line, ${dStop.toFixed(2)}% away) → then ⑤ done`)}
                  </>;
                }
                if (selWaits9.length) {
                  const w = selWaits9[0];
                  const dPx = last ? (w.px / last - 1) * 100 : 0;
                  return <>
                    ⏳ {t(`조건 충족 → ₩${w.px.toLocaleString()} 제시하고 호가창에 줄 서 있음`,
                          `condition MET → offered ₩${w.px.toLocaleString()}, queued in the book`)}
                    {w.wall ? t(` (🧱 벽 ₩${Number(w.wall).toLocaleString()} 바로 앞)`, ` (right in front of the 🧱 ₩${Number(w.wall).toLocaleString()} wall)`) : ""}
                    {t(` · 지금가에서 ${dPx >= 0 ? "+" : ""}${dPx.toFixed(2)}% — 닿는 순간 🔥 체결 → ④ 보유로`,
                       ` · ${dPx >= 0 ? "+" : ""}${dPx.toFixed(2)}% from here - touches → 🔥 filled → ④ holding`)}
                  </>;
                }
                // ⛔ 갭상승 WAIT, visible (boss 2026-08-27 night: "how do we
                // KNOW?") - the law's own state, shown live: gap %, and how
                // far the price stands from its own open (the release line)
                const op9 = bars9.length ? bars9[0].open : 0;
                const pv9 = book9?.prev || 0;
                if (op9 && pv9 && (op9 / pv9 - 1) * 100 >= 1.5 && last >= op9) {
                  const gp9 = (op9 / pv9 - 1) * 100;
                  const dOp9 = (last / op9 - 1) * 100;
                  return <span style={{ color: "#b71c1c" }}>
                    ⛔ {t(`갭상승 +${gp9.toFixed(1)}% (전일 ₩${Math.round(pv9).toLocaleString()} → 시가 ₩${Math.round(op9).toLocaleString()}) — 규칙에 따라 매수 대기 중. 가격이 시가 아래로 내려오는 순간 문이 열립니다 (지금 시가 대비 +${dOp9.toFixed(2)}%). 장중 대형 호재(30분 내 서로 다른 언론사 3곳+의 호재)가 오면 예외로 합류합니다. 그 외에는 오늘 사지 않습니다 — 그것이 법입니다.`,
                          `gap-up +${gp9.toFixed(1)}% (prev ₩${Math.round(pv9).toLocaleString()} → open ₩${Math.round(op9).toLocaleString()}) — waiting by law. The doors open the moment price falls below its own open (now +${dOp9.toFixed(2)}% above it). BIG news during the session (호재 from 3+ DIFFERENT outlets within 30min) lifts the pause as the exception. Otherwise we don't buy today — that is the law.`)}</span>;
                }
                return <>👀 {t("① 규칙의 문이 조건을 기다리는 중 — 조건이 맞으면 ② 호가창에 가격을 제시하고, 체결되면 ④ 보유·사다리가 시작됩니다. 아래 기록에서 오늘 이 종목의 지난 과정을 볼 수 있습니다.",
                               "① the rule's doors are watching - when the condition fits it ② offers a price in the book, and a fill starts ④ the holding ladder. Today's past steps for this stock are in the log below.")}</>;
              })()}
            </div>
          </div>
          <div className="mt-1.5 flex gap-2 flex-wrap">
            <canvas ref={cv9} className="rounded border"
              style={{ height: 340, flex: "1 1 560px", minWidth: 320, borderColor: "rgba(21,101,192,0.35)" }} />
            <div className="text-[11.5px] tabular-nums leading-[1.65] shrink-0 rounded border px-2 py-1.5"
              style={{ minWidth: 190, borderColor: "var(--border-default)" }}>
              <b>{t("호가창 — 대기 리스트", "order book - the waiting list")}</b>
              {(book9?.asks || []).slice(0, 7).reverse().map(([p, q], j) => (
                <div key={`a${j}`} style={{ color: "#1565c0" }}>
                  {q === maxA9 ? "🧱" : ""}{p.toLocaleString()} <span className="opacity-60">{q.toLocaleString()}</span>
                  {selWaits9.some((w) => w.side === "SELL" && Math.abs(p - w.px) < p * 0.0012)
                    ? <b style={{ color: "#b8860b" }}> ←{t("우리 주문", "OUR order")}</b> : ""}
                </div>))}
              {selWaits9.filter((w) => w.side === "SELL"
                  && !(book9?.asks || []).slice(0, 7).some(([p]) => Math.abs(p - w.px) < p * 0.0012))
                .map((w, j) => (
                  <div key={`sx${j}`} style={{ color: "#b8860b", fontWeight: 700 }}>
                    {w.px.toLocaleString()} ←{t("우리 주문 (여기 줄)", "OUR order queued here")}</div>))}
              <div className="border-t my-0.5" style={{ borderColor: "rgba(128,128,128,0.35)" }} />
              {selWaits9.filter((w) => w.side === "BUY"
                  && !(book9?.bids || []).slice(0, 7).some(([p]) => Math.abs(p - w.px) < p * 0.0012))
                .map((w, j) => (
                  <div key={`bx${j}`} style={{ color: "#b8860b", fontWeight: 700 }}>
                    {w.px.toLocaleString()} ←{t("우리 주문 (여기 줄)", "OUR order queued here")}</div>))}
              {(book9?.bids || []).slice(0, 7).map(([p, q], j) => (
                <div key={`b${j}`} style={{ color: "#d32f2f" }}>
                  {q === maxB9 ? "🧱" : ""}{p.toLocaleString()} <span className="opacity-60">{q.toLocaleString()}</span>
                  {selWaits9.some((w) => w.side === "BUY" && Math.abs(p - w.px) < p * 0.0012)
                    ? <b style={{ color: "#b8860b" }}> ←{t("우리 주문", "OUR order")}</b> : ""}
                </div>))}
              <div className="mt-1 text-[9.5px] opacity-70">
                {t("살 때: 제일 큰 사자 벽(🧱) 한 틱 위 · 팔 때: 제일 싼 팔자 한 틱 아래",
                   "buy: one tick above the biggest 🧱 buy wall · sell: one tick under the cheapest ask")}</div>
            </div>
          </div>
          <div className="mt-1.5">
            <b style={{ color: "#1565c0" }}>{selName9} {t("오늘의 매매 기록 (최신순)", "today's trades (newest first)")}</b>
            <div className="mt-0.5 max-h-[150px] overflow-y-auto leading-relaxed tabular-nums text-[12px]">
              {selEvts9.length ? selEvts9.map((e, i) => <div key={i}>{e.txt}</div>)
                : <div className="opacity-60">{t("오늘 이 종목의 매매가 아직 없습니다", "no trades on this stock yet today")}</div>}
            </div>
          </div>
        </div>
        )}
      </div>
      )}
    </div>
  );
}

// 🔬 THE INSPECTION ROOM (boss 2026-08-25: "an interactive table including the
// 100 checklist — the agent checking each one and calculating the score in
// real time, then choosing the stock — we need more proof"). Every row is a
// REAL recorded check: base items from the latest 5-minute checklist pass
// (timestamped), the live adjustment from the true 4-second pulse. The sweep
// cursor replays the latest pass item by item — presentation of real data,
// never invented numbers.
function InspectionRoom({ t }: { t: (ko: string, en: string) => string }) {
  type Item = { no?: number | string; q: string; v?: string; ok?: boolean | null;
    s?: number; w?: number; grp: string };
  type PRow = { code: string; name: string; by_score?: boolean;
    cats?: { market?: number; issue?: number; stock_sel?: number; exec?: number; avg?: number };
    detail?: Record<string, { k: string; v: string; s: number; w: number }[]>;
    exec_items?: { no: number; q: string; ok: boolean | null; d: string }[] };
  const [dp9, setDp9] = useState<{ rows?: PRow[]; market_items?: { no: number; q: string;
    q_en?: string; ok: boolean | null; d: string; w?: number }[] } | null>(null);
  const [pl9, setPl9] = useState<{ t?: string; checks?: number;
    top?: { code: string; name: string; avg?: number }[] } | null>(null);
  const [topN9, setTopN9] = useState(5);
  const [sel9, setSel9] = useState("");
  const [cur9, setCur9] = useState(0);
  const [open9, setOpen9] = useState(false);
  useEffect(() => {
    let live = true;
    const loadBase = () => api<{ rows?: PRow[] }>("/paper-desk/daily-pick")
      .then((d) => { if (live) setDp9(d as never); }).catch(() => {});
    const loadPulse = () => api<{ ok: boolean; top_n?: number }>("/paper-desk/live/reco-rank-log?n=10")
      .then((d) => { if (live && d?.ok) { setTopN9(d.top_n || 5);
        setPl9((d as unknown as { live?: { t?: string; checks?: number;
          top?: { code: string; name: string; avg?: number }[] } }).live || null); } })
      .catch(() => {});
    loadBase(); loadPulse();
    const h1 = setInterval(loadBase, 60000);
    const h2 = setInterval(loadPulse, 5000);
    return () => { live = false; clearInterval(h1); clearInterval(h2); };
  }, []);
  const rows9 = (dp9?.rows || []).filter((r) => r.detail);
  const tabs9 = (pl9?.top || []).slice(0, 8).filter((x) => rows9.some((r) => r.code === x.code));
  const code9 = sel9 || tabs9[0]?.code || rows9[0]?.code || "";
  const row9 = rows9.find((r) => r.code === code9);
  // assemble the REAL item list, column order ① market ② issue ③ selection ④ exec
  const items9: Item[] = [];
  (dp9?.market_items || []).forEach((m) => items9.push({ no: m.no, q: m.q, v: m.d,
    ok: m.ok, w: m.w, grp: t("① 시장", "① Market") }));
  const det9 = row9?.detail || {};
  (det9.flows || []).forEach((x) => items9.push({ q: x.k, v: x.v, s: x.s, w: x.w,
    grp: t("② 이슈·수급", "② Issue/Supply") }));
  ([["liquidity", "유동성"], ["flexibility", "탄력"], ["trend", "추세"],
    ["levels", "레벨"], ["momentum", "모멘텀"]] as const).forEach(([k9]) => {
    (det9[k9] || []).forEach((x) => items9.push({ q: x.k, v: x.v, s: x.s, w: x.w,
      grp: t("③ 종목선정", "③ Selection") }));
  });
  (row9?.exec_items || []).forEach((e) => items9.push({ no: e.no, q: e.q, v: e.d,
    ok: e.ok, grp: t("④ 실행·관리", "④ Execution") }));
  // the sweep cursor: replays the latest real pass, one item every ~140ms
  useEffect(() => {
    if (!open9 || !items9.length) return;
    const h = setInterval(() => setCur9((c) => (c + 1) % (items9.length + 6)), 140);
    return () => clearInterval(h);
  }, [open9, items9.length, code9]);
  if (!rows9.length) return null;
  const cats9 = row9?.cats;
  const live9 = (pl9?.top || []).find((x) => x.code === code9)?.avg;
  const adj9 = live9 != null && cats9?.avg != null ? Math.round((live9 - cats9.avg) * 10) / 10 : null;
  const inTop9 = (pl9?.top || []).slice(0, topN9).some((x) => x.code === code9);
  const done9 = cur9 >= items9.length;
  return (
    <div className="mt-2 px-3 py-2 rounded-xl border text-[11px]"
      style={{ borderColor: "#6a1b9a", background: "rgba(106,27,154,0.04)" }}>
      <div className="flex items-center gap-2 flex-wrap cursor-pointer select-none"
        onClick={() => setOpen9((o) => !o)}>
        <b style={{ color: "#6a1b9a" }}>🔬 {t("검사실 — 100문항 실사 중계", "Inspection Room — the 100 items, live")}</b>
        <span className="text-[var(--text-muted)]">
          {t(`기초검사 5분마다 · 실시간 보정 4초마다 (마지막 ${pl9?.t ?? "?"}) · 오늘 ${pl9?.checks ?? "?"}회`,
             `base pass every 5min · live adj every 4s (last ${pl9?.t ?? "?"}) · ${pl9?.checks ?? "?"} checks today`)}</span>
        <span className="ml-auto" style={{ color: "#6a1b9a" }}>{open9 ? "▲" : t("▼ 펼치기", "▼ open")}</span>
      </div>
      {open9 && (
        <>
          <div className="mt-1.5 flex gap-1 flex-wrap">
            {tabs9.map((x) => (
              <button key={x.code} onClick={() => { setSel9(x.code); setCur9(0); }}
                className="px-2 py-0.5 rounded border text-[10.5px]"
                style={x.code === code9
                  ? { borderColor: "#6a1b9a", background: "#6a1b9a", color: "#fff", fontWeight: 700 }
                  : { borderColor: "rgba(106,27,154,0.4)", color: "#6a1b9a" }}>
                {x.name} {x.avg ?? ""}</button>
            ))}
          </div>
          <div className="mt-1 tabular-nums" style={{ color: "#6a1b9a", fontWeight: 700 }}>
            {done9
              ? t(`합산 완료 → 아래 점수줄 확인`, `sum complete → see the score line below`)
              : t(`검사 중: ${Math.min(cur9 + 1, items9.length)} / ${items9.length}번째 자동 항목 — ${items9[cur9]?.q ?? ""}`,
                  `checking item ${Math.min(cur9 + 1, items9.length)} / ${items9.length} — ${items9[cur9]?.q ?? ""}`)}
            <span className="ml-2 inline-block align-middle flex-1 h-1 rounded" style={{ background: "rgba(106,27,154,0.15)", width: 120 }}>
              <span className="block h-1 rounded" style={{ width: `${Math.round(Math.min(1, cur9 / Math.max(1, items9.length)) * 100)}%`, background: "#6a1b9a", transition: "width 0.14s linear" }} />
            </span>
          </div>
          <div className="mt-1 max-h-[300px] overflow-y-auto rounded border" style={{ borderColor: "rgba(106,27,154,0.25)" }}>
            <table className="w-full text-[10.5px] tabular-nums">
              <tbody>
                {items9.map((it, i) => {
                  const active = i === cur9;
                  const passed = i < cur9 || done9;
                  const good = it.ok === true || (it.s != null && it.s >= 50);
                  return (
                    <tr key={`${it.grp}-${i}`}
                      ref={active ? ((el) => { el?.scrollIntoView({ block: "nearest" }); }) : undefined}
                      style={{
                        background: active ? "rgba(106,27,154,0.18)" : undefined,
                        opacity: passed || active ? 1 : 0.35,
                        borderBottom: "1px solid rgba(128,128,128,0.08)" }}>
                      <td className="px-1.5 py-0.5 whitespace-nowrap" style={{ color: "#6a1b9a" }}>{it.grp}</td>
                      <td className="px-1.5 py-0.5 whitespace-nowrap opacity-60">{it.no != null ? `#${it.no}` : ""}</td>
                      <td className="px-1.5 py-0.5">{it.q}</td>
                      <td className="px-1.5 py-0.5 whitespace-nowrap opacity-80">{it.v ?? ""}</td>
                      <td className="px-1.5 py-0.5 whitespace-nowrap text-right">
                        {(passed || active) ? (it.s != null
                          ? <b style={{ color: good ? "#2e7d32" : "#c62828" }}>{it.s}</b>
                          : it.ok == null ? <span className="opacity-50">—</span>
                          : <b style={{ color: it.ok ? "#2e7d32" : "#c62828" }}>{it.ok ? "✓" : "✗"}</b>) : "·"}
                      </td>
                      <td className="px-1.5 py-0.5 whitespace-nowrap text-right opacity-60">{it.w != null ? `×${it.w}` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {cats9 && (
            <div className="mt-1 tabular-nums font-bold" style={{ color: "#6a1b9a" }}>
              {t("점수(합산제)", "score (sum)")}: <b>{cats9.avg ?? "—"}</b> {t("· 참고 칸", "· ref columns")}: ① {cats9.market ?? "—"} · ② {cats9.issue != null ? Math.round(cats9.issue) : "—"} · ③ {cats9.stock_sel ?? "—"} · ④ {cats9.exec ?? "—"}
              {adj9 != null && <> {adj9 >= 0 ? "+" : "−"} {t("실시간", "live")} {Math.abs(adj9)} = <span style={{ fontSize: "1.1em" }}>{live9}</span></>}
              {" · "}
              {inTop9
                ? <span style={{ color: "#2e7d32" }}>✅ {t(`톱${topN9} 선정 — 매수 후보`, `in the top-${topN9} — buy candidate`)}</span>
                : <span style={{ color: "#c62828" }}>{t(`톱${topN9} 밖 — 매수 금지`, `outside top-${topN9} — no buying`)}</span>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// 🔄 THE VISIBLE HEARTBEAT (boss 2026-08-25: "show the process that every 20
// sec is rechecking — real-time, interactive"): a live monitor of the rank
// logger — countdown to the next re-check, and a feed where every completed
// 40-stock examination prints one line, rank changes highlighted.
function RecoLiveCheckPanel({ t, lang }: { t: (ko: string, en: string) => string; lang: string }) {
  type Snap = { t: string; rows: { code: string; name: string; avg?: number }[] };
  const [snaps, setSnaps] = useState<Snap[]>([]);
  const [topN, setTopN] = useState(3);
  const [uni9, setUni9] = useState(0);
  const [count9, setCount9] = useState(0);
  const [pulse9, setPulse9] = useState<{ t?: string; checks?: number;
    top?: { code: string; name: string; avg?: number }[] } | null>(null);
  const [nowS, setNowS] = useState(Date.now());
  useEffect(() => {
    let live = true;
    const load = () => api<{ ok: boolean; top_n: number; universe?: number;
      count?: number; snaps: Snap[] }>(
      "/paper-desk/live/reco-rank-log?n=15")
      .then((d) => { if (live && d?.ok) { setSnaps(d.snaps || []); setTopN(d.top_n || 3);
                                          setUni9(d.universe || 0); setCount9(d.count || 0);
                                          setPulse9((d as unknown as { live?: { t?: string;
                                            checks?: number; top?: { code: string; name: string;
                                            avg?: number }[] } }).live || null); } })
      .catch(() => {});
    load();
    const h = setInterval(load, 5000);
    const h2 = setInterval(() => setNowS(Date.now()), 1000);
    return () => { live = false; clearInterval(h); clearInterval(h2); };
  }, []);
  if (!snaps.length && !pulse9?.t) return null;
  // the TRUE pulse: the last CHECK time (every 4s), not the last written
  // record (only on rank changes / 60s heartbeats)
  const tSrc = pulse9?.t || snaps[snaps.length - 1]?.t || "0:0:0";
  const [hh, mm, ss] = tSrc.split(":").map(Number);
  const lastMs = new Date().setHours(hh, mm, ss, 0);
  const ago = Math.max(0, Math.round((nowS - lastMs) / 1000));
  const nextIn = Math.max(0, 4 - (ago % 4));
  const frac = Math.min(1, (4 - nextIn) / 4);
  return (
    <div className="mt-2 px-3 py-2 rounded-xl border text-[11px]"
      style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.04)" }}>
      <div className="flex items-center gap-2 flex-wrap tabular-nums">
        <b style={{ color: "#e65100" }}>🔄 {t("실시간 재검사", "live re-check")}</b>
        <span className="inline-block w-2 h-2 rounded-full"
          style={{ background: ago < 40 ? "#2e7d32" : "#b8860b",
                   animation: ago < 40 ? "pulse 1.2s infinite" : undefined }} />
        <span>{t(`마지막 검사 ${tSrc} (${ago}초 전)`, `last check ${tSrc} (${ago}s ago)`)}</span>
        <span className="text-[var(--text-muted)]">{t(`· 다음 ~${nextIn}초 후`, `· next in ~${nextIn}s`)}</span>
        <span className="ml-1 flex-1 h-1.5 rounded" style={{ background: "rgba(128,128,128,0.15)", minWidth: 60, maxWidth: 160 }}>
          <span className="block h-1.5 rounded" style={{ width: `${Math.round(frac * 100)}%`, background: "#e65100", transition: "width 1s linear" }} />
        </span>
        <span className="text-[var(--text-muted)]">
          {t(`${uni9 || "?"}종목 × 100문항 → 톱${topN} · 오늘 실제 검사 ${pulse9?.checks ?? "?"}회 · 순위변동 기록 ${count9}건`,
             `${uni9 || "?"} stocks × 100 items → top-${topN} · ${pulse9?.checks ?? "?"} real checks today · ${count9} rank records`)}</span>
      </div>
      {pulse9?.top && pulse9.top.length > 0 && (
        <div className="mt-1 tabular-nums font-bold" style={{ color: "#e65100" }}>
          {t("지금 톱", "LIVE top ")}{topN}: {pulse9.top.slice(0, topN)
            .map((x) => `${x.name} ${x.avg ?? ""}`).join(" · ")}
          <span className="ml-1 font-normal opacity-60">{t("(4초 갱신)", "(4s refresh)")}</span>
        </div>
      )}
      <div className="mt-1 max-h-[92px] overflow-y-auto leading-relaxed">
        {snaps.slice().reverse().map((s9, i9) => {
          const prev = snaps[snaps.length - 2 - i9];
          const tops = (s9.rows || []).slice(0, topN);
          const prevTops = prev ? (prev.rows || []).slice(0, topN).map((x) => x.code) : null;
          const entered = prevTops ? tops.filter((x) => !prevTops.includes(x.code)) : [];
          const left9 = prevTops && prev ? (prev.rows || []).slice(0, topN).filter((x) => !tops.some((y) => y.code === x.code)) : [];
          const changed = entered.length > 0 || left9.length > 0;
          return (
            <div key={`${s9.t}-${i9}`} style={changed ? { color: "#e65100", fontWeight: 700 } : undefined}>
              {s9.t} {changed ? "🔄" : "✓"} {t(`톱${topN}: `, `top-${topN}: `)}
              {tops.map((x) => `${x.name} ${x.avg ?? ""}`).join(" · ")}
              {changed && entered.length > 0 && <span> {t("↑진입 ", "↑in ")}{entered.map((x) => x.name).join(",")}</span>}
              {changed && left9.length > 0 && <span> {t("↓이탈(신규 매수 중단) ", "↓out (new buys stop) ")}{left9.map((x) => x.name).join(",")}</span>}
              {!changed && <span className="opacity-50"> {t("변동 없음", "no change")}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 🤝 Auto / Semi-auto for the reco desk (boss 2026-08-25). Auto = the picks trade
// themselves on the 100-checklist recommendation. Semi = algo BUY signals appear here
// as suggestions and the FINAL CLICK IS HUMAN; SELLs/stops always execute on their own.
function RecoTradeModePanel({ t }: { t: (ko: string, en: string) => string }) {
  type Sug = { id: string; ts: number; ticker: string; name: string; side: string;
               qty: number; source: string; status: string };
  const [mode, setMode] = useState<string>("auto");
  const [pending, setPending] = useState<Sug[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    const load = () => api<{ mode: string; pending: Sug[] }>("/paper-desk/reco-trade-mode")
      .then((d) => { if (live) { setMode(d.mode || "auto"); setPending(d.pending || []); } })
      .catch(() => {});
    load();
    const h = setInterval(load, 8000);
    return () => { live = false; clearInterval(h); };
  }, []);
  const setModeReq = (m: string) => {
    if (m === mode) return;
    setBusy(true);
    api<{ mode: string }>(`/paper-desk/reco-trade-mode?mode=${m}`, { method: "POST" })
      .then((d) => setMode(d.mode)).catch(() => {}).finally(() => setBusy(false));
  };
  const decideReq = (id: string, approve: boolean) => {
    setBusy(true);
    api(`/paper-desk/suggestions/${id}?approve=${approve ? 1 : 0}`, { method: "POST" })
      .then(() => api<{ mode: string; pending: Sug[] }>("/paper-desk/reco-trade-mode"))
      .then((d) => setPending(d.pending || [])).catch(() => {}).finally(() => setBusy(false));
  };
  return (
    <div className="mt-3 rounded-xl border px-4 py-2" style={{ borderColor: "#6a1b9a", background: "rgba(106,27,154,0.04)" }}>
      <div className="flex items-center gap-2 flex-wrap">
        <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
          🤝 {t("추천 매매 방식", "reco trading mode")}
        </b>
        {([["auto", t("⚡ 자동 — 체크리스트 추천대로 자동매매", "⚡ Auto — trades the checklist picks itself")],
           ["semi", t("🤝 반자동 — 제안하면 내가 최종 클릭", "🤝 Semi — it suggests, my click is final")]] as const)
          .map(([m, lab]) => (
          <button key={m} disabled={busy} onClick={() => setModeReq(m)}
            className="text-[11px] font-bold px-2.5 py-1 rounded-lg border"
            style={mode === m ? { background: "#6a1b9a", color: "#fff", borderColor: "#6a1b9a" }
                              : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
            {mode === m ? "● " : "○ "}{lab}
          </button>
        ))}
        <span className="text-[10px] text-[var(--text-muted)]">
          {t("매도/손절은 항상 자동 실행 · 내 6종목은 항상 자동", "sells/stops always execute · my 6 always auto")}
        </span>
      </div>
      {mode === "semi" && (
        <div className="mt-2 space-y-1">
          {pending.length === 0 ? (
            <div className="text-[11px] text-[var(--text-muted)]">
              {t("대기 중인 매수 제안 없음 — 알고리즘이 신호를 잡으면 여기에 나타납니다.",
                 "no pending buy suggestions — they appear here when an algorithm fires.")}
            </div>
          ) : pending.map((s) => (
            <div key={s.id} className="flex items-center gap-2 flex-wrap rounded-lg border px-3 py-1.5"
              style={{ borderColor: "#2e7d32", background: "rgba(46,125,50,0.06)" }}>
              <b className="text-[12px]" style={{ color: "#2e7d32" }}>🟢 {s.side}</b>
              <b className="text-[12px] text-[var(--text-primary)]">{s.name}</b>
              <span className="text-[11px] text-[var(--text-secondary)]">
                {s.qty.toLocaleString()}{t("주 · 시장가", " sh · market")} · {s.source} ·
                {" "}{new Date(s.ts * 1000).toLocaleTimeString()}
              </span>
              <span className="ml-auto flex gap-1.5">
                <button disabled={busy} onClick={() => decideReq(s.id, true)}
                  className="text-[11px] font-bold px-3 py-1 rounded-lg text-white" style={{ background: "#2e7d32" }}>
                  {t("✔ 승인 (매수 실행)", "✔ Approve (buy)")}
                </button>
                <button disabled={busy} onClick={() => decideReq(s.id, false)}
                  className="text-[11px] font-bold px-3 py-1 rounded-lg border"
                  style={{ borderColor: "#b02a2a", color: "#b02a2a" }}>
                  {t("✖ 거절", "✖ Reject")}
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function LiveDeskPage() {
  const { t, lang } = useLanguage();
  // RECO DESK VIEW (boss 2026-08-24: a second menu for the checklist's recommended
  // stocks, same UI as the live desk): /testing/live?desk=reco filters the stock tabs
  // to today's top-5 by score. Read once from the URL — client page, no useSearchParams
  // (that would force a Suspense boundary at build time).
  // /testing/reco is the reco desk's OWN route. Both routes render THIS component, so
  // Next PRESERVES it across navigation (no remount) — a one-shot useState kept showing
  // the previous desk (boss 2026-08-24 ×2: "it shows again Live Kiwoom Desk"). Derive
  // the view REACTIVELY from usePathname so every navigation re-evaluates it.
  const pathname = usePathname();
  const deskView: string | null = (pathname || "").includes("/testing/reco")
    ? "reco"
    : (typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("desk") : null);
  const [code, setCode] = useState("005930");
  // which desk's stocks the rules/trades replay covers (assigned each render below)
  const deskCodesRef = useRef("");
  // DEFAULT = 1분 (boss 2026-08-12, demo morning): Sharp lives on the minute chart,
  // so the board opens on it. 5틱 remains one click away in the 봉 dropdown.
  const [period, setPeriod] = useState(60);
  const [tick, setTick] = useState(5);
  const [clockIn, setClockIn] = useState("");
  const [tape, setTape] = useState<Tape | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [execs, setExecs] = useState<Execs | null>(null);
  const [st, setSt] = useState<Status | null>(null);
  const [rank, setRank] = useState<Rank | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  // THE TWELVE, and only the twelve. The server no longer computes the six reversal
  // rules for this desk at all — the boss asked for them removed, not filtered.
  //
  // The list is still named here, because the server-side removal and this page shipped
  // together and the backend runs with NO --reload: until it is restarted the old server
  // still returns eighteen, and without this the six would reappear on screen. It costs
  // nothing once the server is restarted, and it is what lets the removal take effect
  // immediately instead of waiting for a restart that would cost ~72s of real tape.
  const ORIGINAL_12 = ["3u3d", "2u2d", "3u2d", "2u3d", "3u4d", "4u3d",
                       "3u+0.3", "3u+0.5", "3u+1.0", "2u+0.5", "3u+0.5s", "4u+1.0",
                       // The take-profit experiment, added 2026-08-05 at his request after
                       // it went onto the Strategy Lab. These four buy after FALLS - the
                       // only reversal rules on this desk - so without naming them here
                       // the page would silently drop every one of them.
                       // ALL down-entry rules removed (boss 2026-08-05): up-only desk
                       // + ML twins of the six winners (boss 2026-08-06): each trades in
                       // parallel with its plain twin, trained on PRIOR days' real tape
                       "3u+0.3ML", "3u+0.5ML", "2u+0.5ML", "4u3dML", "3u+1.0ML", "4u+1.0ML",
                       // ...and the remaining six, so ALL 12 are paired (boss 2026-08-06)
                       "3u3dML", "2u2dML", "3u2dML", "2u3dML", "3u4dML", "3u+0.5sML"];
  // UNION, not preference. The server's list names only the 12 plain rules, and
  // preferring it filtered every ML twin OFF THE SCREEN while they traded - the boss
  // watched "rules + ML" sit empty for an hour (2026-08-06). Neither list alone can
  // hide rows the other knows about.
  const twelve = Array.from(new Set([...(rank?.original_12 ?? []), ...ORIGINAL_12]));
  // NOTE: shownRules is computed BELOW the mlView state it filters by — referencing a
  // `const` before its line runs is a temporal-dead-zone crash, and this exact mistake
  // white-screened the whole page on 2026-08-06 ("Application error: a client-side
  // exception"). Declaration order in a component body is load-bearing.
  const [det, setDet] = useState<RDetail | null>(null);
  // The money for the OPEN rule. Prefer the server's figure - it is summed over every
  // trade, while the list on screen is cut to `limit`. Fall back to adding up the rows
  // only when they are all here, and to null (nothing shown) when they are not.
  const moneyRows = det?.trades ?? [];
  const moneyAll = !!det && det.shown === det.trips;
  const moneyNet = det?.net_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + r.net_pct, 0) * 100) / 100 : null);
  const moneyPer = det?.per_trade ?? (moneyAll && moneyRows.length
    ? Math.round((moneyRows.reduce((x, r) => x + r.net_pct, 0) / moneyRows.length) * 1000) / 1000
    : null);
  // Money in WON. `entry` is one share and net_pct is a percentage OF that entry, so
  // entry x net_pct / 100 is exactly what one share of that trade gained or lost. Done
  // here as well as on the server because the backend runs with NO --reload: until it is
  // restarted the won fields are absent, and this is the number the boss asked to see.
  // NOT rounded here. Rounding to the nearest won per SHARE and then multiplying by the
  // quantity multiplies the rounding error too: at 100,000 shares a ₩0.07 rounding became
  // ₩7,000, and the row disagreed with the server's total, which rounds only at the end
  // (boss 2026-08-05 checked a trade by hand and found it). Callers round once, after
  // multiplying by the size.
  const wonOf = (entry: number, netPct: number) => entry * netPct / 100;
  const won = (n: number) => `${n < 0 ? "-" : "+"}\u20a9${Math.abs(Math.round(n)).toLocaleString()}`;
  // the fallbacks multiply by the SIZE \u2014 they summed one share per trade while the rows
  // beneath them showed the real quantity
  const moneyWon = det?.net_won_sized ?? det?.net_won_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0))
    : null);
  const moneyWonPer = det?.per_trade_won ?? (moneyAll && moneyRows.length
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0)
                 / moneyRows.length)
    : null);
  const [pick, setPick] = useState<number | null>(null);
  // chart CLOSED by default during market hours (boss 2026-08-27: "we do not
  // need this open by default — if we wanna then we will click time, then it
  // should open") — clicking a trade opens it; off-hours it starts open for
  // review. Closed = fewer tape polls = lighter backend.
  const [chartOpen9, setChartOpen9] = useState<boolean>(() => {
    const d = new Date(); const m = d.getHours() * 60 + d.getMinutes();
    return !(m >= 540 && m <= 930);
  });
  // 🎞 replay of one finished trade, opened by clicking the company NAME in
  // the history (boss 2026-08-27; the buy TIME keeps its arrow jump)
  const [repEp9, setRepEp9] = useState<{ code: string; name: string; entry?: number;
    buy_t?: string; sell_t?: string; exit?: number; exit_why?: string; qty?: number;
    live?: boolean; rule?: string; net_pct?: number;
    wall?: { price?: number; qty?: number } | null;
    parts?: { buys?: unknown[][]; sells?: unknown[][] } } | null>(null);
  // ⛶ FULL-SCREEN market chart + 어제+오늘 two-day view (boss 2026-08-28: "I
  // need full screen of the Kiwoom chart... I wanna see and check 갭상승 so I
  // need the previous day's chart also") — yesterday's whole day prepended to
  // today's, the overnight gap visible at the seam.
  const [fsMkt9, setFsMkt9] = useState(false);
  // the FULL PAST, one chart (boss 2026-08-28): every stored day's bars,
  // fetched once per stock/candle-size, with a date label at each day's
  // first bar - today's live bars are appended by the normal 3s pull
  const [hist9, setHist9] = useState<Bar[]>([]);
  const chartWanted9 = chartOpen9 || pick !== null;
  // ALL ~40 STOCKS in the chart (boss 2026-08-28: "in the dropdown add all 40
  // stocks; if I search by name it should show real-time price, max, min,
  // volume and the full-screen chart like Kiwoom"). Watched stocks keep the
  // live minute tape; the rest show a 5s live QUOTE card + the daily candles
  // (no minute tape exists for them - stated on screen, never faked).
  const watchSet9 = new Set((st?.stocks ?? []).map((x) => x.code));
  const isWatch9 = watchSet9.has(code);
  const [quote9, setQuote9] = useState<{ price?: number; open?: number;
    high?: number; low?: number; change_pct?: number; name?: string } | null>(null);
  const [dbars9, setDbars9] = useState<Bar[]>([]);
  const [stockQ9, setStockQ9] = useState("");
  useEffect(() => {
    if (!chartWanted9 || !code || isWatch9) { setQuote9(null); setDbars9([]); return; }
    let live = true;
    const loadQ = () => api<typeof quote9>(`/paper-desk/quote?q=${code}`)
      .then((d) => { if (live && d) setQuote9(d); }).catch(() => {});
    api<RawDaily>(`/paper-desk/raw-daily?code=${code}&days=250`)
      .then((d) => {
        if (!live || !d?.rows) return;
        setDbars9(d.rows.map((r, i) => {
          const d8 = String(r.date).replace(/-/g, "");
          return { time: i, hhmm: `${d8.slice(4, 6)}/${d8.slice(6)}`, d8,
                   open: r.open, high: r.high, low: r.low, close: r.close,
                   dir: r.close >= r.open ? 1 : -1, vol: r.volume, n: 0 };
        }));
      }).catch(() => {});
    loadQ();
    const h = setInterval(loadQ, 5000);
    return () => { live = false; clearInterval(h); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartWanted9, code, isWatch9]);
  useEffect(() => {
    if (!chartWanted9 || !code) { setHist9([]); return; }
    let live = true;
    const q = period ? `period=${period}` : `tick=${tick}`;
    api<{ bars?: Bar[]; days?: { d8: string; i: number }[] }>(
      `/paper-desk/live/tape-hist?code=${code}&${q}`)
      .then((d) => {
        if (!live || !d?.bars) return;
        const bs = d.bars.map((b) => ({ ...b }));
        const dys = (d.days || []) as { d8: string; i: number; n?: number }[];
        for (let k = 0; k < dys.length; k++) {
          const dd = dys[k];
          const end = k + 1 < dys.length ? dys[k + 1].i : bs.length;
          for (let j = dd.i; j < end; j++) bs[j].d8 = dd.d8;
          if (bs[dd.i]) bs[dd.i].hhmm = `${dd.d8.slice(4, 6)}/${dd.d8.slice(6)}`;
        }
        setHist9(bs);
      })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartWanted9, code, tick, period]);
  // ONE truth for "is any chart wanted right now" — the strip was pressed OR a
  // trade row is picked. NOT `sel`: an open drill-down's trades TABLE stays on
  // screen with the chart folded, and folding must never clear `sel` — clearing
  // it left the table's clicks dead (`if (sel)` guards) and the chart frozen on
  // another company (boss 2026-08-27 morning). The ref lets pull() skip the
  // multi-thousand-bar tape download entirely while folded ("it makes heavy
  // our app").
  const chartOn9 = chartOpen9 || pick !== null;
  const chartOn9Ref = useRef(chartOn9); chartOn9Ref.current = chartOn9;
  const [money, setMoney] = useState(false);      // off until he asks - see the button
  // the rule's law-book text, folded by default (boss 2026-08-19: "it is showing
  // by default explanations" - the story opens when asked, like the money does)
  const [ruleDoc, setRuleDoc] = useState(false);
  // which side of the picked trade the chart centres on: clicking the BUY time shows the
  // buy with its arrow, the SELL time the sell (boss 2026-08-05: "if i click time it
  // should go to buying ... selling"). The row itself still defaults to the sell.
  const [focusSide, setFocusSide] = useState<"b" | "s">("s");
  // WHICH DAY the rules panel reads ("" = today, live). The boss lost sight of yesterday
  // twice at dawn because the desk only ever read today's empty file (2026-08-06).
  const [ruleDay, setRuleDay] = useState("");
  const ruleDayRef = useRef("");
  // optional hour window inside that day
  const [hourFrom, setHourFrom] = useState("");
  const [hourTo, setHourTo] = useState("");
  const hourFromRef = useRef(""); const hourToRef = useRef("");
  useEffect(() => { ruleDayRef.current = ruleDay; }, [ruleDay]);
  // WHICH DAY IS SELECTED before the first tick of the session. Showing 08-10's finished
  // trades under a picker that read "today" was read as "today has already traded"
  // (boss 2026-08-11, twice). So the picker is moved to the day actually being shown -
  // one date on screen, nowhere claiming to be today - and moved back to live by itself
  // the moment today's tape appears.
  const autoPinRef = useRef("");
  // ...and NEVER against the user's hand (boss 2026-08-11: he chose 오늘 and the picker
  // snapped straight back to 08-10, so today was unreachable). Touching the day picker
  // sets this and the auto-pin stands down for good; only the un-pin at the first tick
  // of today remains armed.
  const dayTouchedRef = useRef(false);
  useEffect(() => {
    if (!rank) return;
    const today = rank.today || "";
    const hasToday = !!today && (rank.days || []).includes(today);
    if (rank.auto_day && rank.day && ruleDay === "" && !hasToday
        && !dayTouchedRef.current && !autoPinRef.current) {
      autoPinRef.current = rank.day;
      setRuleDay(rank.day); ruleDayRef.current = rank.day;
      setDet(null); setSel(null);
    } else if (hasToday && autoPinRef.current && ruleDay === autoPinRef.current) {
      autoPinRef.current = "";
      setRuleDay(""); ruleDayRef.current = "";
      setDet(null); setSel(null);
    }
  }, [rank, ruleDay]);
  useEffect(() => { hourFromRef.current = hourFrom; }, [hourFrom]);
  useEffect(() => { hourToRef.current = hourTo; }, [hourTo]);
  // with ML / without ML / everything
  const [mlView, setMlView] = useState<"all" | "ml" | "plain">("all");
  // WHICH WAY (boss 2026-08-10): "we have to trade using both ways partially, old way
  // and new way also - by default it should show new way, and if I click old way it
  // should show our old way". Both families trade on every tape at once; this only
  // chooses which set of rows is on screen.
  const [way, setWay] = useState<"d1" | "d2" | "d3" | "d4" | "old" | "new" | "both">("d2");
  // the screener's ranking - loaded once, shown on demand (boss 2026-08-10)
  // the static year-based screener panel was superseded by the daily picker above;
  // /paper-desk/screener still serves its data for reference.
  // the morning GO / NO-GO verdict per stock (advisor's point 2)
  // TODAY's five, chosen by the checklist every morning
  const [dpick, setDpick] = useState<Pick | null>(null);
  // 🌙 what the US did overnight - fetched once, cached server-side per day
  type Ovn = { ok: boolean; fetched_at: string; mood_ko: string; mood_en: string;
               rows: { sym: string; name: string; chg_pct: number; close: number }[] };
  const [ovn, setOvn] = useState<Ovn | null>(null);
  useEffect(() => { api<Ovn>("/paper-desk/overnight").then((d) => d?.ok && setOvn(d)).catch(() => {}); }, []);
  const [pickOpen, setPickOpen] = useState(true);      // the desk is worth seeing at once
  const [pickAll, setPickAll] = useState(false);       // the other 32 stay behind a button
  const [pickCol, setPickCol] = useState("");          // a column header explains itself when clicked
  const [pickRow, setPickRow] = useState("");          // a clicked company opens its whole formula (boss 2026-08-25)
  const [pickDetail, setPickDetail] = useState(false); // category columns ↔ the 6 subcategory columns
  // Poll — the ranking carries the LIVE layer (same math as the chatbot), so it must
  // keep moving during the session (boss 2026-08-24: "the menu must match what the
  // chatbot answers, in real time"). Server caches 60s; 45s polling tracks it.
  useEffect(() => {
    const load = () => api<Pick>("/paper-desk/daily-pick").then(setDpick).catch(() => {});
    load();
    const h = setInterval(load, 45000);
    return () => clearInterval(h);
  }, []);
  // WHICH DESK TRADES (boss 2026-08-11): his six by default, or the checklist's top five
  // - and switching one ON turns the other OFF, collector included. During market hours
  // the swap needs confirming, because the stocks that leave abandon their tape.
  const [deskBusy, setDeskBusy] = useState(false);
  // WHERE THE NEW RULE STANDS per stock, live. It fires a few times a day by design,
  // so the quiet hours must say "condition not met yet" per stock instead of looking
  // broken (boss 2026-08-11). 15s cadence: it reads today's live bars server-side.
  type DipStat = { ok: boolean; rows: { code: string; name: string; stage: string;
                   ko: string; en: string; ups?: number }[] };
  const [dipStat, setDipStat] = useState<DipStat | null>(null);
  useEffect(() => {
    // the slim fixed panel no longer shows per-stock status (boss 2026-08-11): the
    // endpoint stays for the API, but the page stops polling it
    { setDipStat(null); return; }
    // eslint-disable-next-line no-unreachable
    let live = true;
    const pullDip = () => {
      const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
      api<DipStat>(`/paper-desk/live/dip-status?${q}`)
        .then((d) => { if (live) setDipStat(d?.ok ? d : null); })
        .catch(() => {});
    };
    pullDip();
    const h = setInterval(pullDip, 15000);
    return () => { live = false; clearInterval(h); };
  }, [dpick?.mode]);
  // TWO TOGGLES, ONE MODE (boss 2026-08-24: "if I do not turn off one of them, both
  // must continue trading"). Each desk is an on/off switch: six on + five on = "both"
  // (the default), six alone = "fixed", five alone = "score". Both off is refused —
  // the desk must never be empty. Turning a desk OFF mid-session abandons its stocks'
  // tape, so only that direction asks for confirmation.
  const switchDesk = useCallback((desk: "fixed" | "score") => {
    const cur = (dpick?.mode ?? "both") as "fixed" | "score" | "both";
    const sixOn = cur === "fixed" || cur === "both";
    const fiveOn = cur === "score" || cur === "both";
    const nextSix = desk === "fixed" ? !sixOn : sixOn;
    const nextFive = desk === "score" ? !fiveOn : fiveOn;
    if (!nextSix && !nextFive) {
      alert(t("적어도 하나의 데스크는 켜져 있어야 합니다 — 데스크를 비울 수는 없습니다.",
              "At least one desk must stay on — the desk can never be empty."));
      return;
    }
    const mode: "fixed" | "score" | "both" = nextSix && nextFive ? "both" : nextSix ? "fixed" : "score";
    if (mode === cur) return;
    const open = !!dpick?.market_open;
    const removing = (sixOn && !nextSix) || (fiveOn && !nextFive);
    if (open && removing && !confirm(t(
      "지금은 장중입니다. 데스크를 끄면 빠지는 종목의 오늘 체결 기록 수집이 중단됩니다. 끌까요?",
      "The market is open. Turning this desk off stops collecting today's tape for the stocks that leave. Turn it off anyway?"))) return;
    setDeskBusy(true);
    // mode=score turns the six OFF — the backend refuses it without confirm_six_off=1
    // (stale-page protection); we send it only here, after the confirm dialog above.
    const sixOff = mode === "score" ? "&confirm_six_off=1" : "";
    api<{ ok: boolean }>(`/paper-desk/desk-mode?mode=${mode}&force=${open ? 1 : 0}${sixOff}`, { method: "POST" })
      .then(() => api<Pick>("/paper-desk/daily-pick"))
      .then(setDpick)
      .catch(() => {})
      .finally(() => setDeskBusy(false));
  }, [dpick, t]);
  // THE SOURCE DATA. The boss asked to check the rows the picker reads without opening
  // Supabase (2026-08-10). One indexed query, ~20 rows - light enough to open on a click.
  const [histOpen, setHistOpen] = useState(false);
  const [rawCode, setRawCode] = useState("");
  const [rawDays, setRawDays] = useState(20);
  const [raw, setRaw] = useState<RawDaily | null>(null);
  useEffect(() => {
    if (!rawCode) { setRaw(null); return; }
    setRaw(null);
    api<RawDaily>(`/paper-desk/raw-daily?code=${rawCode}&days=${rawDays}`)
      .then(setRaw).catch(() => {});
  }, [rawCode, rawDays]);
  // THE DRILL (boss 2026-08-11): a recorded day opens into hours, an hour into
  // minutes, a minute into seconds - price, volume, direction at every level.
  type DrillRow = { t: string; key: string; open: number; high: number; low: number;
                    close: number; vol: number; n: number; chg: number | null; dir: number };
  const [drillDays, setDrillDays] = useState<string[]>([]);
  const [drill, setDrill] = useState<{ level: string; day: string; hour: string;
    minute: string; rows: DrillRow[] } | null>(null);
  const [drillBusy, setDrillBusy] = useState(false);
  const pullDrill = useCallback((day: string, level: string, hour = "", minute = "") => {
    setDrillBusy(true);
    api<{ ok: boolean; rows: DrillRow[] }>(
      `/paper-desk/drill?code=${rawCode}&day=${day}&level=${level}&hour=${hour}&minute=${minute}`)
      .then((d) => { if (d?.ok) setDrill({ level, day, hour, minute, rows: d.rows }); })
      .catch(() => {})
      .finally(() => setDrillBusy(false));
  }, [rawCode]);
  useEffect(() => {
    if (!histOpen || !rawCode) { setDrill(null); setDrillDays([]); return; }
    api<{ ok: boolean; days: string[] }>(`/paper-desk/drill-days?code=${rawCode}`)
      .then((d) => setDrillDays(d?.ok ? d.days : []))
      .catch(() => {});
    setDrill(null);
  }, [histOpen, rawCode]);
  const [gate, setGate] = useState<Gate | null>(null);
  useEffect(() => { api<Gate>("/paper-desk/gate").then(setGate).catch(() => {}); }, []);
  // VIEWING switch only: with the gate closed the board shows nothing, so this asks
  // "what WOULD the rules have done today?" The desk itself always trades gated.
  const [showBlocked, setShowBlocked] = useState(false);
  const showBlockedRef = useRef(false);
  useEffect(() => { showBlockedRef.current = showBlocked; }, [showBlocked]);
  // win% threshold: type 20, press ENTER - the box empties, a "≥20%" chip appears,
  // and only rules winning 20%+ stay. Click the chip's x to clear (boss 2026-08-06:
  // "after adding 20 then I should type enter then 20 should gone").
  const [winInput, setWinInput] = useState("");
  // DEFAULT 50: the board opens showing only rules winning 50%+ (boss 2026-08-07).
  // Enter on the empty box reveals everything; typing a number sets a new floor.
  const [minWin, setMinWin] = useState<number | null>(50);
  const shownRules = (rank?.variants ?? []).filter((v) => twelve.includes(v.id))
    .filter((v) => way === "both" ? true : (v.family ?? "old") === way)
    .filter((v) => mlView === "all" ? true
                 : mlView === "ml" ? v.id.endsWith("ML") : !v.id.endsWith("ML"))
    // the quiet floor hides weak OLD rules among many; the Sharp family is two
    // algorithms and hiding them made the whole view read as broken (2026-08-11)
    .filter((v) => (v.family ?? "old") !== "old" || minWin === null || v.win_pct >= minWin);
  // WON PER TRADE. 0 = the historical one share. One share is not equal risk - one share
  // of SK하이닉스 is ₩1.5M and one of 한화오션 is ₩85k - so a fixed budget is both fairer
  // and closer to a real account. It scales the money and never the win rate.
  const [budget, setBudget] = useState(0);
  const chartRef = useRef<HTMLDivElement | null>(null);
  // THE CHART IS ON DEMAND (boss 2026-08-11: the app got heavy). A click on a trade
  // time opens the trade's row and numbers only; the chart - and the 60,000-bar payload
  // behind it - loads when 차트 보기 is pressed. Same for the order-book evidence.
  const [chartOpen, setChartOpen] = useState(false);
  const chartOpenRef = useRef(false);
  useEffect(() => { chartOpenRef.current = chartOpen; }, [chartOpen]);
  const [evOpen, setEvOpen] = useState(false);
  // 🕰️ Data File - the minute record the rules read, built from the REAL executions.
  // The artificial lab has had this since 2026-08-03; the boss asked for the same thing
  // here, because a fill you cannot look up is a fill you cannot check.
  const [df, setDf] = useState<Df | null>(null);
  // how many bars the STANDING chart loads. 600 x 5틱 on 삼성전자 is ~5 minutes, which
  // read as "data before 15:00 is missing" (boss 2026-08-05) - it never was; the window
  // was just small. 전체 loads the whole day.
  // whole day always - the ONE continuous chart scrolls/zooms instead of
  // window presets (boss 2026-08-28: "one type of chart like normal Kiwoom")
  const [chartBars, setChartBars] = useState(100000);
  const chartBarsRef = useRef(600);
  useEffect(() => { chartBarsRef.current = chartBars; }, [chartBars]);
  const [dfMins, setDfMins] = useState<5 | 10 | 15>(10);
  // A fixed 5/10/15 cannot answer "show me the last 35 minutes", and it cannot go back to
  // a minute from this morning at all - the boss hit exactly that (2026-08-04). The
  // Strategy Lab has had a from/to range since the day it got a Data File; this is the
  // same control, and the server already accepted frm/to.
  const [dfFrom, setDfFrom] = useState("");
  const [dfTo, setDfTo] = useState("");
  const [dfOpen, setDfOpen] = useState<string | null>(null);
  const [dfMin, setDfMin] = useState<DfMin | null>(null);
  const detRef = useRef<RDetail | null>(null);
  const budgetRef = useRef(0);
  const loadDfRef = useRef<((c: string, m: number, f?: string, t?: string) => void) | null>(null);
  const dfMinsRef = useRef<number>(10);
  const dfFromRef = useRef("");
  const dfToRef = useRef("");

  const codeRef = useRef(code); codeRef.current = code;
  const perRef = useRef(period); perRef.current = period;
  const tickRef = useRef(tick); tickRef.current = tick;

  const detSeqRef = useRef(0);
  const lastReqRef = useRef<{ id: string; tradeIdx: number | null;
                              want: string } | null>(null);
  const [detBusy, setDetBusy] = useState(false);
  // the day (d8) of the chart inside `det` - the server draws the clicked trade's day
  const detDayRef = useRef("");
  const openRule = useCallback((id: string, tradeIdx: number | null = null,
                                tradeCode?: string) => {
    setPick(tradeIdx);
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    // WHICH COMPANY THE CHART DRAWS. Normally the stock button above the chart, but a
    // clicked TRADE overrides it - the trade table lists all three companies together,
    // and asking for 삼성전자's chart while he clicked an SK하이닉스 row is why clicking
    // a completed trade looked like it did nothing (boss 2026-08-04).
    //
    // Sent from here as well as being fixed on the server, because the backend runs with
    // NO --reload and a restart during market hours costs ~72s of real tape that cannot
    // be recovered. This makes the fix work against the server that is running right now.
    const want = tradeCode || codeRef.current;
    // remember exactly what the user asked for: the 3s refresher must re-ask
    // THIS, not the previous company - a cold key answers {computing} while
    // the old company's warm key answers instantly, so refreshing the old one
    // silently cancelled every cross-company click (boss 2026-08-20: "if I
    // click another company in the chart side it is not showing")
    lastReqRef.current = { id, tradeIdx, want };
    // and the whole page follows the named company AT ONCE - header, tape
    // chart, order book - so the click visibly lands even while the rule's
    // own chart is still being built (re-set to the same value is a no-op)
    if (tradeCode) { setCode(tradeCode); codeRef.current = tradeCode; }
    // LAST CLICK WINS. The detail payload is the whole day (60,000 bars, several MB), so
    // a response can land seconds after it was asked for - and an OLD response arriving
    // after a NEW click used to overwrite the click, which read as "clicking the buy time
    // shows late / jumps back" (boss 2026-08-06). Every request takes a number; only the
    // newest number is allowed to touch the screen.
    const my = ++detSeqRef.current;
    setDetBusy(true);
    api<RDetail>(`/paper-desk/live/rules/trades?variant=${encodeURIComponent(id)}&${q}`
      + `&code=${encodeURIComponent(want)}&bars=${chartOpenRef.current ? 60000 : 1200}`
      + `&around=${tradeIdx ?? -1}&budget=${budgetRef.current}`
      + `&day=${ruleDayRef.current}&frm=${encodeURIComponent(hourFromRef.current)}&to=${encodeURIComponent(hourToRef.current)}`
      + `&gate=${showBlockedRef.current ? 0 : 1}`
      + `&codes=${encodeURIComponent(deskCodesRef.current)}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`)
      .then((d) => { if (my !== detSeqRef.current) return;
                     if ((d as unknown as { computing?: boolean })?.computing) {
                       // cold key: the server answers instantly and replays in
                       // the background - keep the spinner and re-ask
                       setTimeout(() => { if (my === detSeqRef.current)
                                            openRuleRef.current?.(id, tradeIdx, tradeCode); }, 3000);
                       return;
                     }
                     setDetBusy(false);
                     const v = d?.ok ? d : null; detRef.current = v;
                     detDayRef.current = (tradeIdx != null
                       ? v?.trades?.[tradeIdx]?.d8 : v?.trades?.[0]?.d8) ?? "";
                     setDet(v); })
      .catch(() => { if (my !== detSeqRef.current) return;
                     setDetBusy(false);
                     detRef.current = null; setDet(null); });
  }, []);
  const openRuleRef = useRef<((id: string, tradeIdx?: number | null,
                               tradeCode?: string) => void) | null>(null);
  openRuleRef.current = openRule;

  // CLICK A STOCK, SEE ITS TRADES (boss 2026-08-11: on his own six the scores say
  // nothing he does not already know - what he wants from that panel is when it bought,
  // at what price, when it sold, and the chart). Points the existing detail panel at
  // that company and opens the best-ranked rule if none is open yet.
  const autoOpenRef = useRef("");
  // ONE TABLE FOR THE WHOLE FAMILY (boss 2026-08-11): rule, stock, buy, sell, result,
  // money - every trade the visible family made, merged and time-ordered, with each
  // buy/sell time clickable to open that exact trade on the chart as proof.
  type FamRow = { rule: string; rule_ko?: string; rule_en?: string; idx: number;
                  code: string; name?: string; d8?: string; buy_t: string; entry: number;
                  sell_t: string; exit: number; net_pct: number; exit_why?: string;
                  qty?: number; won: number; result: string;
                  judge?: { dp?: number | null; fuel?: number | null; news?: number;
                            top_half?: boolean; bot_boost?: boolean;
                            fuel_half?: boolean; news_half?: boolean } | null;
                  sig?: { drop: number; sx: number | null; rng: number; t?: string } | null;
                  wall?: { price: number; qty: number } | null;
                  parts?: { buys?: [number, number][] | null;
                            sells?: [number, number][] | null } | null;
                  partial?: boolean; guard?: string[] };
  type FamTrades = { ok: boolean; rows: FamRow[]; trips: number; wins: number;
                     losses: number; win_pct: number; net_won: number;
                     ep_wins?: number; ep_losses?: number; win_pct_ep?: number };
  const [famOpen, setFamOpen] = useState(true);
  // history filters (boss 2026-08-13: "searching bar/filtering - only particular
  // company, or time, or winning and losing") - applies to BOTH algorithms
  // the clicked SELL LINE, restated under the chart (stock, qty, price, gain,
  // and the law's reason)
  const [selSlice, setSelSlice] = useState<{ rule: string; name: string; t: string;
    px: number; qty: number; rem?: number | null; gain?: number | null;
    why?: string; side?: "b" | "s" } | null>(null);
  // THE JUDGES' STAMPS (boss 2026-08-21 night: "if we click it should show
  // exact steps - daily chart buying zone, volume, news analyzed by Qwen").
  // One small fetch per open company; renders as the layer story under the
  // clicked trade's line.
  const [layers9, setLayers9] = useState<{ ok?: boolean;
    steps?: { icon: string; name: string; name_en?: string; value: string;
      value_en?: string; verdict: string; verdict_en?: string }[];
    news?: { ts: string; stamp: string; title: string; why: string }[] } | null>(null);
  const layersCode9 = det?.chart?.code || null;
  // THE DAILY VIEW (boss 2026-08-24): the year map behind the charted stock
  const [dailyView, setDailyView] = useState(false);
  const [daily9, setDaily9] = useState<{ ok?: boolean; code?: string;
    candles?: { d8: string; open: number; high: number; low: number;
                close: number; vol: number }[];
    year_hi?: number; year_lo?: number; pos?: number | null;
    lines?: { no_buy_85: number; caution_60: number; bottom_20: number } } | null>(null);
  const dailyCode9 = dailyView ? (det?.chart?.code || code) : null;
  useEffect(() => {
    if (!dailyCode9) { setDaily9(null); return; }
    let on9 = true;
    api<typeof daily9>(`/paper-desk/live/daily-chart?code=${dailyCode9}`)
      .then((d) => { if (on9) setDaily9(d?.ok ? d : null); })
      .catch(() => { if (on9) setDaily9(null); });
    return () => { on9 = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dailyCode9]);
  useEffect(() => {
    if (!layersCode9) { setLayers9(null); return; }
    let on9 = true;
    api<{ ok?: boolean;
      steps?: { icon: string; name: string; name_en?: string; value: string;
        value_en?: string; verdict: string; verdict_en?: string }[];
      news?: { ts: string; stamp: string; title: string; why: string }[] }>(
      `/paper-desk/live/rules/layers?code=${layersCode9}`)
      .then((d) => { if (on9) setLayers9(d?.ok ? d : null); })
      .catch(() => { if (on9) setLayers9(null); });
    return () => { on9 = false; };
  }, [layersCode9]);
  const [fCode, setFCode] = useState("");
  const [fRes, setFRes] = useState("");
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [fam, setFam] = useState<FamTrades | null>(null);
  const [famBusy, setFamBusy] = useState(false);
  // LAST CLICK WINS, same law as the chart detail: the old family's fetch can take
  // 20s+ (its ML rules), and a switch to Sharp mid-flight used to get overwritten when
  // that slow response finally landed - Sharp showed nothing, or the wrong rows, or
  // Loading for ever (boss 2026-08-11, "make it consistent"). Only the newest request
  // may touch the screen.
  const famSeqRef = useRef(0);
  // each stored day's record for the SELECTED algorithm - the day dropdown wears
  // these numbers so "what was winning % on 08-12" is answered by looking
  const [famDaily, setFamDaily] = useState<Record<string,
    { trips: number; win_pct: number; net_won: number }>>({});
  useEffect(() => {
    setFamDaily({});
    // every algorithm view starts with money hidden (boss 2026-08-19: "by default
    // it should be hide money - if I wanna show I will click show money")
    setMoney(false);
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<{ ok: boolean; days: { d8: string; trips: number; win_pct: number;
                               net_won: number }[] }>(
      `/paper-desk/live/rules/family-daily?family=${way}&${q}`)
      .then((d) => { if (d?.ok) {
        const m: Record<string, { trips: number; win_pct: number; net_won: number }> = {};
        d.days.forEach((x) => { m[x.d8] = x; });
        setFamDaily(m);
      } })
      .catch(() => {});
  }, [way, tick, period]);
  const pullFam = useCallback(() => {
    // NO MIXED DESKS (boss 2026-08-25 x2): on the reco desk, wait until the
    // picks are known - an empty codes list must never fetch another desk's
    // table into this menu
    if (deskView === "reco" && !deskCodesRef.current) return;
    const my = ++famSeqRef.current;
    setFamBusy(true);
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<FamTrades>(`/paper-desk/live/rules/family-trades?family=${way}&${q}`
      + `&day=${ruleDayRef.current}&frm=${encodeURIComponent(hourFromRef.current)}`
      + `&to=${encodeURIComponent(hourToRef.current)}`
      + `&gate=${showBlockedRef.current ? 0 : 1}`
      // NO MIXED DESKS (boss 2026-08-25): each menu's history covers only
      // its own stocks - the six here, the checklist picks on the reco desk
      + `&codes=${encodeURIComponent(deskCodesRef.current)}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`)
      .then((d) => { if (my !== famSeqRef.current) return;
                     if ((d as unknown as { computing?: boolean })?.computing) {
                       // the server is replaying this view in the background -
                       // keep the current table on screen, stay in "updating",
                       // and ask again shortly (boss 2026-08-19: the history
                       // must never read as gone)
                       setTimeout(() => { if (my === famSeqRef.current) pullFamRef.current?.(); }, 4000);
                       return;
                     }
                     setFam(d?.ok ? d : null); setFamBusy(false); })
      .catch(() => { if (my !== famSeqRef.current) return; setFamBusy(false); });
  }, [way, deskView]);
  const pullFamRef = useRef<(() => void) | null>(null);
  pullFamRef.current = pullFam;
  useEffect(() => {
    if (deskView === "reco" && deskCodesRef.current) pullFamRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deskView, dpick]);
  useEffect(() => {
    // fetch even while the panel is collapsed - the server answers from a 4s cache,
    // and a collapsed header with no data read as "the history is gone" (2026-08-11)
    pullFam();
    const h = setInterval(pullFam, 20000);
    return () => clearInterval(h);
  }, [famOpen, pullFam, ruleDay, hourFrom, hourTo, tick, period, showBlocked]);
  // opening one trade from the merged table = the same proof path a rule row uses
  // CLICK THE ALGORITHM NAME, GET THE STORY (boss 2026-08-11: "other person also can
  // understand why this time bought, why sold"). Plain-words explanation of the buy,
  // the algorithm itself, and this trade's exit, built from the row's own numbers.
  const [famExp, setFamExp] = useState<string | null>(null);
  const explainTrade = (r: { rule: string; exit_why?: string; buy_t?: string;
                             entry: number; name?: string; sell_t?: string;
                             sig?: { drop: number; sx: number | null; rng: number; t?: string } | null }) => {
    // D-rules are the boss's scenarios - dip-door stories, NOT the old
    // 3-rises text (2026-08-19: every 알고리즘 slice wore the old algorithm's
    // "rose 3 times in a row" explanation because only N-names were known)
    const isDip = r.rule.startsWith("N") || r.rule.startsWith("D");
    // when the trade carries its own measured signal, the story uses THOSE numbers -
    // the proof the boss asked for, not a description (2026-08-11)
    const sg = r.sig;
    const buyKo = isDip
      ? (sg
        ? `측정된 사실: 직전 10분 안에 ${sg.drop}% 급락${sg.sx != null ? ` (평소 봉의 ${sg.sx}배)` : ""}, 구간 변동 ${sg.rng}%${sg.t ? `, 신호 시각 ${sg.t}` : ""}. 하락이 멈추고 첫 양봉이 완성된 뒤, 두 번째 양봉이 시작되는 값 ₩${Math.round(r.entry).toLocaleString()}에 샀습니다.`
        : `이 종목이 짧은 시간에 급하게 떨어졌고(평소 움직임의 3배 이상), 떨어짐이 멈춘 뒤 첫 양봉이 완성되어 두 번째 양봉이 시작되는 값 ₩${Math.round(r.entry).toLocaleString()}에 샀습니다.`)
      : `가격이 3번 연속 오르고 거래량이 평소보다 많아, 상승이 시작됐다고 보고 ₩${Math.round(r.entry).toLocaleString()}에 샀습니다 (예전 알고리즘).`;
    const buyEn = isDip
      ? (sg
        ? `Measured facts: within the prior 10 minutes it fell ${sg.drop}%${sg.sx != null ? ` (${sg.sx}× its normal bar)` : ""}, range ${sg.rng}%${sg.t ? `, signal at ${sg.t}` : ""}. The fall stopped, one up candle completed, and it bought at ₩${Math.round(r.entry).toLocaleString()} — the start of the second up candle.`
        : `The stock fell sharply (3× its normal move); when the fall stopped and one up candle completed it bought at ₩${Math.round(r.entry).toLocaleString()} — the start of the second up candle.`)
      : `The price rose 3 times in a row on above-average volume, so it judged a climb had started and bought at ₩${Math.round(r.entry).toLocaleString()} (the old algorithm).`;
    const w = r.exit_why || "";
    let sellKo = `매도 사유: ${w}`;
    let sellEn = `Exit: ${w}`;
    if (w.includes("손절")) {
      sellKo = "산 값에서 2% 떨어져 보호선(손절)이 자동으로 팔았습니다. 더 큰 손해를 막는 안전장치입니다.";
      sellEn = "The price fell 2% below the entry, so the protective stop sold automatically — the safety line against bigger damage.";
    } else if (w.includes("+1.0%") || w.includes("익절")) {
      sellKo = "오름이 완만해서 +1% 이익에 도달한 순간 팔았습니다. 느린 상승은 기다리지 않는 규칙입니다.";
      sellEn = "The rise was gentle, so it sold the moment profit reached +1%. A slow climb is not worth waiting on.";
    } else if (w.includes("음봉")) {
      sellKo = "급하게 오르던 흐름이 꺾여 첫 음봉이 완성되었고, 두 번째 음봉이 시작되는 값에 팔았습니다." + (w.includes("호가벽") ? " 매도 호가는 가장 두꺼운 매도벽 바로 앞에 걸었습니다." : "");
      sellEn = "The sharp climb turned: one down candle completed and it sold at the price where the second down candle began." + (w.includes("호가벽") ? " The sell was offered one tick in front of the biggest ask wall." : "");
    } else if (w.includes("마감")) {
      sellKo = "장 마감(15:20) 정리 — 밤 사이 위험을 안고 가지 않도록 마지막 가격에 팔았습니다.";
      sellEn = "The 15:20 close-out — sold at the last price so nothing is carried overnight.";
    } else if (w.includes("중단")) {
      sellKo = "이 종목의 체결이 10분 이상 끊겨, 마지막 거래 가격으로 정리했습니다.";
      sellEn = "This stock stopped printing for 10+ minutes, so the trade was closed at its last traded price.";
    }
    // the CURRENT book's exits, in plain words (2026-08-24: the old 2%/15:20
    // texts described a retired book)
    if (w.includes("-1") && w.includes("전량")) {
      sellKo = "기준가에서 -1% 보호선에 캔들의 저가가 닿는 순간 전량 매도했습니다 (장중 즉시 체결). 하락이 멈추고 양봉 3개가 연속으로 나오면 다시 들어갑니다.";
      sellEn = "The candle's low touched the -1% protection line and everything sold instantly (intrabar). It re-enters after 3 straight up-candles prove the fall ended.";
    } else if (w.includes("미상승")) {
      sellKo = "산 뒤 한 번도 +0.85%까지 오르지 못한 채 음봉 3개가 나와, -1%까지 기다리지 않고 일찍 정리했습니다 (출혈 방지 조기 정리).";
      sellEn = "It never rose to +0.85% and printed 3 down-candles below cost, so it was cut early instead of bleeding to -1% (the decay exit).";
    } else if (w.includes("이익 정리")) {
      sellKo = "마감 전 이익 정리 — 이익이 있는 상태로 마감 시간대에 들어와, 규칙이 이익을 확정했습니다.";
      sellEn = "The closing-hour harvest — it carried a gain into the close window, so the rule banked it.";
    } else if (w.includes("15:19") || w.includes("종")) {
      sellKo = "15:19 종 — 하루의 마지막 자유 매매 분에 전 종목을 전량 매도했습니다 (15:20부터는 동시호가).";
      sellEn = "The 15:19 bell — everything sells in the last free-trading minute (the closing auction starts at 15:20).";
    }
    return { buyKo, buyEn, sellKo, sellEn };
  };
  // THE JUDGES' STORY per trade (boss 2026-08-24: "when I click it must show
  // clear explanation why it bought based on daily, minute, volume, news") -
  // rendered from the bench's ACTUAL readings recorded at the buy moment
  const judgeStory = (j?: { dp?: number | null; fuel?: number | null;
    news?: number; top_half?: boolean; bot_boost?: boolean;
    fuel_half?: boolean; news_half?: boolean } | null) => {
    if (!j) return null;
    const L: string[] = [];
    if (j.dp != null) {
      const p9 = Math.round(j.dp * 100);
      L.push(t(`📅 일봉: 연중 ${p9}% 지점 → ${j.top_half ? "상단 조심 구역, 절반 매수" : j.bot_boost ? "바닥 매수 존, 1.5배 매수" : "중간 지대, 정상 사이즈"}`,
               `📅 daily: at ${p9}% of the year → ${j.top_half ? "upper caution zone, HALF size" : j.bot_boost ? "bottom buying zone, 1.5x size" : "middle band, normal size"}`));
    }
    if (j.fuel != null) {
      L.push(t(`📊 거래량: 평소의 ${j.fuel.toFixed(1)}배 → ${j.fuel_half ? "연료 부족, 절반 매수" : "연료 정상"}`,
               `📊 volume: ${j.fuel.toFixed(1)}x its usual → ${j.fuel_half ? "low fuel, HALF size" : "fuel normal"}`));
    }
    L.push(t(`📰 뉴스: 최근 1시간 위험 ${j.news ?? 0}건 → ${j.news_half ? "위험 신호, 절반 매수" : "이상 없음, 정상 사이즈"}`,
             `📰 news: ${j.news ?? 0} danger stamp${(j.news ?? 0) === 1 ? "" : "s"} in the last hour → ${j.news_half ? "danger, HALF size" : "clear, normal size"}`));
    return L;
  };
  // THE EVIDENCE LINKS (boss 2026-08-24: "put hyperlink so I can read the
  // full news"): when the expanded trade was news-judged, fetch that stock's
  // danger stamps so the ruling shows its articles
  const [newsSt9, setNewsSt9] = useState<{ ts: string; stamp: string;
    title: string; link: string; why: string }[] | null>(null);
  useEffect(() => {
    setNewsSt9(null);
    if (!famExp || !fam) return;
    const r9 = fam.rows.find((r) => `${r.rule}-${r.idx}` === famExp);
    if (!r9?.code || !((r9.judge?.news ?? 0) > 0)) return;
    let on9 = true;
    api<{ ok?: boolean; stamps?: { ts: string; stamp: string; title: string;
      link: string; why: string }[] }>(
      `/paper-desk/live/news-stamps?code=${r9.code}&stamp=${encodeURIComponent("위험")}`)
      .then((d) => { if (on9) setNewsSt9(d?.stamps || null); })
      .catch(() => {});
    return () => { on9 = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [famExp, fam]);
  // WHY THIS STOCK, WHY THIS TIME (boss 2026-08-25, menu 2): when a reco-desk
  // trade's story expands, fetch its recorded checklist rank at the buy moment
  const [rankAt9, setRankAt9] = useState<{ ok?: boolean; t?: string; rank?: number | null;
    of?: number; avg?: number | null; in_top?: boolean;
    top?: { code: string; name: string; avg?: number }[] } | null>(null);
  useEffect(() => {
    setRankAt9(null);
    if (deskView !== "reco" || !famExp || !fam) return;
    const r9 = fam.rows.find((r) => `${r.rule}-${r.idx}` === famExp);
    if (!r9?.code || !r9.buy_t) return;
    let on9 = true;
    api<typeof rankAt9>(`/paper-desk/live/reco-rank-at?code=${r9.code}&t=${encodeURIComponent(r9.buy_t.slice(0, 8))}`)
      .then((d) => { if (on9) setRankAt9(d?.ok ? d : null); })
      .catch(() => {});
    return () => { on9 = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [famExp, fam, deskView]);
  const openFamTrade = useCallback((r: FamRow, side: "b" | "s") => {
    setFocusSide(side);
    setSel(r.rule);
    autoOpenRef.current = r.rule;
    // clicking a TIME is the proof gesture - it must open the chart, not leave it
    // behind the 차트 보기 button (boss 2026-08-12: "when I click the time it is
    // not showing the chart")
    setChartOpen(true); chartOpenRef.current = true; setChartOpen9(true);
    openRule(r.rule, r.idx, r.code);
    if (chartOpenRef.current)
      setTimeout(() => chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRule]);
  
  // (auto-open removed 2026-08-11 with the totals table: the detail panel opens ONLY
  // from a click on a trade in the 매매 내역)
  const openStock = useCallback((c2: string) => {
    setCode(c2); codeRef.current = c2;
    // NEVER MIX ALGORITHMS (boss 2026-08-20: "if I am in Algo 3 and click,
    // it should show only that algorithm's trades"): with no rule open, a
    // stock click opens the CURRENT BOARD'S algorithm - falling back to the
    // rank list's top rule could silently chart a different algorithm
    const famRule = way === "d1" ? "D1" : way === "d2" ? "D2"
                  : way === "d3" ? "D3" : way === "d4" ? "D4" : null;
    const id = sel || famRule || shownRules[0]?.id;
    if (id) { setSel(id); openRule(id, null, c2); }
    setTimeout(() => document.getElementById("rule-detail")?.scrollIntoView(
      { behavior: "smooth", block: "start" }), 60);
  }, [sel, way, shownRules, openRule]);

  const loadDf = useCallback((c: string, mins: number, f = "", tt = "") => {
    if (!c) return;
    // a range wins over the minute buttons; with neither, "the last N minutes"
    const q = (f || tt) ? `frm=${encodeURIComponent(f)}&to=${encodeURIComponent(tt)}`
                        : `mins=${mins}`;
    api<Df>(`/paper-desk/live/datafile?code=${encodeURIComponent(c)}&${q}`)
      .then((d) => setDf(d?.ok ? d : null)).catch(() => setDf(null));
  }, []);

  const openMinute = useCallback((c: string, hhmm: string) => {
    const key = hhmm.slice(0, 5);
    setDfOpen((cur) => (cur === key ? null : key));
    setDfMin(null);
    api<DfMin>(`/paper-desk/live/datafile?code=${encodeURIComponent(c)}&hhmm=${encodeURIComponent(key)}`)
      .then((d) => setDfMin(d?.ok ? d : null)).catch(() => setDfMin(null));
  }, []);

  useEffect(() => { loadDfRef.current = loadDf; }, [loadDf]);
  useEffect(() => { dfMinsRef.current = dfMins; }, [dfMins]);
  useEffect(() => { budgetRef.current = budget; if (sel) openRule(sel, pick); }, [budget]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { dfFromRef.current = dfFrom; }, [dfFrom]);
  useEffect(() => { dfToRef.current = dfTo; }, [dfTo]);

  const pull = useCallback(() => {
    const c = codeRef.current;
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    // folded chart = no tape download at all (the bars payload is the heavy part)
    if (chartOn9Ref.current)
      api<Tape>(`/paper-desk/live/tape?code=${c}&${q}&bars=${chartBarsRef.current}`).then(setTape).catch(() => {});
    api<Book>(`/paper-desk/live/book?code=${c}`).then(setBook).catch(() => {});
    api<Execs>(`/paper-desk/live/execs?code=${c}&n=120`).then(setExecs).catch(() => {});
    api<Rank>(`/paper-desk/live/rules?${q}&gate=${showBlockedRef.current ? 0 : 1}&day=${ruleDayRef.current}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`
      + `&codes=${encodeURIComponent(deskCodesRef.current)}`
      + `&frm=${encodeURIComponent(hourFromRef.current)}&to=${encodeURIComponent(hourToRef.current)}`)
      // a cold server key answers {computing:true} instantly - keep what is on
      // screen; the 3s interval retries by itself (boss 2026-08-19: the board
      // must never go blank because the server is thinking)
      .then((d) => { if (!(d as unknown as { computing?: boolean })?.computing) setRank(d); })
      .catch(() => {});
    // follows the CHARTED company, so the minute rows always describe the bars above them
    loadDfRef.current?.(detRef.current?.chart?.code || c, dfMinsRef.current,
                        dfFromRef.current, dfToRef.current);
  }, []);

  // the moment a folded chart is opened (strip click or a trade-time click),
  // fetch the tape NOW instead of waiting up to 3s for the next interval tick
  useEffect(() => { if (chartOn9) pull(); }, [chartOn9, pull]);

  useEffect(() => {
    pull();
    api<Status>("/paper-desk/live/status").then(setSt).catch(() => {});
    // keep the clicked trade's company across the 3s refresh, or the chart snaps back to
    // the stock button a moment after he clicks
    const a = setInterval(() => {
      pull();
      // a stored day's trades cannot change - re-downloading the multi-MB detail every
      // 3s only churned the chart under his cursor. Refresh it live-only.
      if (sel && !ruleDayRef.current) {
        // refresh the USER'S LAST REQUEST, never the previous det's company -
        // while a cross-company click is still computing, refreshing the old
        // company's warm key overwrote the click every 3 seconds (boss
        // 2026-08-20). pick===null still covers a clicked HOLDING.
        const lr = lastReqRef.current;
        if (lr && lr.id === sel)
          openRule(lr.id, lr.tradeIdx, lr.want);
        else
          openRule(sel, pick, pick !== null ? detRef.current?.trades[pick]?.code
                                            : detRef.current?.chart?.code);
      }
    }, 3000);
    const b = setInterval(() => api<Status>("/paper-desk/live/status").then(setSt).catch(() => {}), 15000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [pull, sel, pick, openRule]);

  const fmt = (n?: number | null) => (n == null ? "-" : n.toLocaleString());
  const bars = tape?.bars ?? [];
  const me = st?.stocks.find((x) => x.code === code);
  // RECO DESK: the checklist's top-5 by score (★ rows) — the tabs filter to them and
  // the page opens on the first one. The DEFAULT view is the boss's six ONLY (his
  // 2026-08-24 order: "in the Live Kiwoom menu it should be the 6 predefined stocks");
  // either filter falls back to everything rather than ever showing an empty desk.
  const SIX_CODES = new Set(["000660", "005930", "035420", "017670", "042660", "034020"]);
  // reco tabs: SCORE ORDER, top → down (boss 2026-08-24: "only score based, from top
  // to less, no need our 6 prefixed") — a six-member appears here only if it EARNED a
  // score spot; the six have their own desk at /testing/live.
  const recoSetPre9 = new Set(((dpick?.rows || []).filter((r) => r.by_score)).map((r) => r.code));
  const _recoRows = (dpick?.rows || []).filter((r) => r.by_score)
    .sort((a, b) => (b.score || 0) - (a.score || 0));
  // LIVE top-N — ONE TRUTH (boss 2026-08-25 13:5x: the header showed one set
  // of numbers and the heartbeat another): the header chips now read the SAME
  // 4-second tape-scorer snapshot the heartbeat and the buy-gate use; the
  // slow dpick ordering remains only as a fallback before the first snapshot.
  const [rankHead9, setRankHead9] = useState<{ top_n?: number; universe?: number;
    rows?: { code: string; name: string; avg?: number; dp?: number | null }[] } | null>(null);
  useEffect(() => {
    if (deskView !== "reco") return;
    let live9 = true;
    const load9 = () => api<{ ok: boolean; top_n: number; universe?: number;
      snaps: { t: string; rows: { code: string; name: string; avg?: number; dp?: number | null }[] }[] }>(
      "/paper-desk/live/reco-rank-log?n=1")
      .then((d) => { if (live9 && d?.ok && d.snaps?.length)
        setRankHead9({ top_n: d.top_n, universe: d.universe,
                       rows: d.snaps[d.snaps.length - 1].rows }); })
      .catch(() => {});
    load9();
    const h9 = setInterval(load9, 5000);
    return () => { live9 = false; clearInterval(h9); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deskView]);
  // BENCH LAW (boss 2026-08-27): the board's N seats = the first N BUYABLE
  // stocks by rank - a top stock in the no-buy peak zone (dp >= 0.85) keeps
  // its rank in the table but its seat passes to the next in line, exactly
  // as the entry gate decides. Rows without dp count as buyable.
  const _recoLive = rankHead9?.rows
    ? rankHead9.rows
        .filter((x) => !(typeof x.dp === "number" && x.dp >= 0.85))
        .slice(0, rankHead9.top_n ?? 5).map((x) => ({
        code: x.code, name: x.name, live_total: x.avg, score: x.avg,
        by_score: recoSetPre9.has(x.code) }))
    : [...(dpick?.rows || [])]
        .sort((a, b) => (b.live_total ?? b.score ?? 0) - (a.live_total ?? a.score ?? 0))
        .slice(0, dpick?.n_picks ?? 5);
  const recoSet = recoSetPre9;
  const _recoTabs = _recoRows
    .map((r) => (st?.stocks ?? []).find((x) => x.code === r.code))
    .filter(Boolean) as NonNullable<typeof st>["stocks"];
  const _sixTabs = (st?.stocks ?? []).filter((x) => SIX_CODES.has(x.code));
  const tabStocks = deskView === "reco"
    ? (_recoTabs.length > 0 ? _recoTabs : (st?.stocks ?? []))
    : (_sixTabs.length > 0 ? _sixTabs : (st?.stocks ?? []));
  // the trades/rank replay covers only THIS desk's stocks (boss 2026-08-24: the two
  // desks' trading histories looked identical)
  // menu 2's replay universe = the FULL 20-stock watch (boss 2026-08-25:
  // "build with 20 including the menu-1 stocks") - the living top-3 gate
  // decides who may actually BUY at any moment, so the checklist can crown
  // any of the 20, six-members included, and the board shows it honestly
  deskCodesRef.current = deskView === "reco"
    ? ((st?.stocks ?? []).map((x) => x.code).join(",")
       || _recoRows.map((r) => r.code).join(","))
    : Array.from(SIX_CODES).join(",");
  useEffect(() => {
    if (deskView === "reco" && recoSet.size > 0 && !recoSet.has(code)) {
      // reco view opens on the top-scored pick
      const first = _recoTabs[0];
      if (first) { setCode(first.code); codeRef.current = first.code; }
    } else if (deskView !== "reco" && !SIX_CODES.has(code)) {
      // back on the six-desk: never leave a reco stock selected there
      setCode("005930"); codeRef.current = "005930";
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deskView, dpick, st]);
  // reco page defaults to the FULL ranking, all 40 top→down (boss 2026-08-24);
  // the live desk never shows the score ranking (boss 2026-08-25)
  useEffect(() => {
    setPickAll(deskView === "reco");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deskView]);

  return (
    <div className="p-5 max-w-[1400px]">
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">
          ← {t("알고리즘 선택", "algorithms")}
        </Link>
        <h1 className="text-[20px] font-extrabold text-[var(--text-primary)]">
          {deskView === "reco"
            ? <>🎯 {t("체크리스트 추천 데스크 — 100문항이 고른 종목", "Checklist Reco Desk — chosen by the 100 items")}
                <span className="ml-2 text-[10.5px] font-normal text-[var(--text-muted)]">
                  {t("아침 확정 5종목 (장중 교체 없음) · 챗봇의 '지금' 순위는 실시간 참고용",
                     "the 5 fixed at the morning bell (no mid-session swaps) · the chatbot's 'now' ranking is the live reference")}
                </span></>
            : <>📡 {t("실시간 키움 데스크 — 진짜 시장", "Live Kiwoom Desk — the real market")}</>}
        </h1>
      </div>

      {/* one status line - the how-it-works text and the per-stock tick counts were
          removed as weight (boss 2026-08-11); the hole and error warnings stay because
          they name real, unrecoverable problems */}
      <div className="mt-3 px-3 py-1.5 rounded-xl border text-[11.5px]" style={{ borderColor: TEAL, background: "rgba(0,131,143,0.05)" }}>
        <div className="flex items-center gap-3 flex-wrap tabular-nums">
          <b style={{ color: TEAL }}>📼 {t("체결 수집기", "tape collector")}</b>
          <span className="font-bold" style={{ color: st?.running ? "#2e7d32" : GOLD }}>
            {st?.running ? t("수집 중", "collecting") : t("정지", "stopped")}
          </span>
          <span style={{ color: st?.market_open ? "#2e7d32" : "var(--text-muted)" }}>
            {st?.market_open ? t("장중 (09:00~15:30)", "market open (09:00-15:30)") : t("장 마감", "market closed")}
          </span>
          {st && (
            <span className="text-[var(--text-muted)]">
              {tabStocks.length}{t("종목", " stocks")} · {fmt(tabStocks.reduce((a2, x) => a2 + x.ticks, 0))}{t("틱", " ticks")}
            </span>
          )}
          {/* A hole in the tape is not cosmetic. Kiwoom remembers ~40 seconds, so whatever
              traded while the collector was down is gone for good and cannot be
              backfilled. Drawing a spliced tape as one line would imply prices that were
              never observed, so every hole is named. */}
          {tabStocks.some((x) => (x.gap_sec ?? 0) > 0) && (
            <div className="w-full mt-1 text-[10.5px]" style={{ color: GOLD }}>
              ⚠ {t("수집이 끊긴 구간이 있습니다 — 그 사이 체결은 되살릴 수 없습니다(키움은 40초만 보관). 서버를 재시작하면 그때마다 구멍이 생깁니다:",
                    "there are holes where collection stopped - those executions cannot be recovered (Kiwoom keeps only 40s). Every server restart makes one:")}
              {tabStocks.filter((x) => (x.gap_sec ?? 0) > 0).map((x) => (
                <span key={x.code} className="ml-2">
                  {x.name} {(x.gaps ?? []).map((g) => `${g.from.slice(0, 5)}~${g.to.slice(0, 5)} (${g.seconds}s)`).join(", ")}
                </span>
              ))}
            </div>
          )}
          {st && Object.keys(st.errors).length > 0 && (
            <span style={{ color: RED }}>⚠ {JSON.stringify(st.errors).slice(0, 120)}</span>
          )}
        </div>
      </div>

      {ovn && (
        <div className="mt-3 rounded-xl border px-3 py-1.5 text-[11px] flex items-center gap-3 flex-wrap"
          style={{ borderColor: "#37474f", background: "rgba(55,71,79,0.06)" }}>
          <b style={{ color: "#37474f" }}>🌙 {t("밤사이 미국", "overnight US")}</b>
          {ovn.rows.map((r) => (
            <span key={r.sym} className="tabular-nums">
              {r.name}{" "}
              <b style={{ color: r.chg_pct > 0 ? "#b02a2a" : r.chg_pct < 0 ? "#1565c0" : "var(--text-muted)" }}>
                {r.chg_pct > 0 ? "+" : ""}{r.chg_pct}%
              </b>
            </span>
          ))}
          {(ovn.mood_ko || ovn.mood_en) && (
            <span className="font-bold" style={{ color: "#e65100" }}>
              → {lang === "ko" ? ovn.mood_ko : ovn.mood_en}
            </span>
          )}
        </div>
      )}
      {/* the SIX's simple name strip — so anyone can see what this desk trades, with
          no scores or rankings attached (boss 2026-08-25: "show the 6 stock names like
          before, no rankings") */}
      {deskView !== "reco" && (
        <div className="mt-3 rounded-xl border px-4 py-2 flex items-center gap-2 flex-wrap"
          style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.05)" }}>
          <b className="text-[13px]" style={{ color: "#e65100" }}>
            🎯 {t("매매 종목 — 고정 6", "trading — the fixed 6")}
          </b>
          <span className="text-[12px] font-bold text-[var(--text-primary)]">
            {tabStocks.map((x) => x.name).join(" · ")}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">
            {t("매일 이 여섯 종목만 — 추천 종목은 체크리스트 추천 데스크에서.",
               "these six every day — the checklist picks live on the Reco Desk.")}
          </span>
        </div>
      )}
      {/* the ranking board renders ONLY on the reco page — the Live Kiwoom Desk shows
          no rankings at all for the six (boss 2026-08-25: "do not show rankings of the
          6 fixed in the Live Kiwoom Desk") */}
      {deskView === "reco" && dpick?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#1565c0" }}>
          <div className="px-4 py-2 flex items-center gap-2 flex-wrap cursor-pointer"
            style={{ background: "rgba(21,101,192,0.06)" }} onClick={() => setPickOpen(!pickOpen)}>
            <b className="text-[13px]" style={{ color: "#1565c0" }}>
              {/* LIVE DESK shows ONLY the six; RECO DESK shows only score picks
                  (boss 2026-08-24: "in the Live Kiwoom Desk must be only the prefixed
                  6, nothing more") */}
              🎯 {deskView === "reco"
                ? t(`지금 순위 TOP ${_recoLive.length} — 4초 재검사(하트비트와 동일 점수) · 감시 ${rankHead9?.universe || (st?.stocks ?? []).length}종목 · 아침 스캔 ${(dpick.rows || []).length}종목 순위 아래`,
                    `LIVE top ${_recoLive.length} — the 4s re-check (same scores as the heartbeat) · watching ${rankHead9?.universe || (st?.stocks ?? []).length} · the morning's ${(dpick.rows || []).length}-stock scan ranked below`)
                : t("내 6종목 — 이 데스크는 항상 이 여섯만 매매합니다",
                    "my 6 stocks — this desk trades only these six")}
            </b>
            <span className="text-[11px] font-bold">
              {deskView === "reco"
                ? _recoLive.map((r) => `${r.name} ${r.live_total ?? r.score}${r.by_score ? " 🟢" : ""}`).join(" · ")
                : (dpick.rows || []).filter((r) => r.pinned).map((r) => r.name).join(" · ")}
            </span>
            <span className="text-[10px] text-[var(--text-muted)]">
              {deskView === "reco"
                ? t("챗봇과 같은 실시간 순위(45초 갱신) · 🟢 = 아침 확정으로 실제 자동매매 중. 내 6종목은 실시간 키움 데스크에서.",
                    "the same live ranking as the chatbot (45s refresh) · 🟢 = actually auto-trading (fixed at the bell). My 6 live on the Live Kiwoom Desk.")
                : (dpick.mode ?? "both") === "both"
                ? t("추천 상위 종목도 병렬로 매매 중 — 그쪽은 체크리스트 추천 데스크 메뉴에서 봅니다.",
                    "the checklist picks also trade in parallel — see them on the Checklist Reco Desk menu.")
                : t("직접 고른 고정 종목입니다. 종목을 누르면 매수·매도 시각과 차트가 아래에 열립니다.",
                    "your own fixed list. Click a stock to open its buy and sell times and its chart below.")}
            </span>
            {deskView === "reco" && (
              <span className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                <span className="text-[10px] font-bold text-[var(--text-muted)]">{t("자동매매 종목 수:", "auto-trade top:")}</span>
                {[3, 5, 7, 10].map((nOpt) => (
                  <button key={nOpt} disabled={deskBusy}
                    onClick={() => {
                      const open = !!dpick?.market_open;
                      if (open && !confirm(t(`장중입니다. 지금 ${nOpt}종목으로 바꾸면 빠지는 종목의 오늘 기록 수집이 중단됩니다. 바꿀까요?`,
                                             `Market is open. Changing to ${nOpt} now stops tape collection for the stocks that leave. Change?`))) return;
                      setDeskBusy(true);
                      api<{ ok: boolean }>(`/paper-desk/reco-n?n=${nOpt}&force=${open ? 1 : 0}`, { method: "POST" })
                        .then(() => api<Pick>("/paper-desk/daily-pick"))
                        .then(setDpick).catch(() => {}).finally(() => setDeskBusy(false));
                    }}
                    className="text-[10.5px] font-bold px-2 py-0.5 rounded border"
                    style={(dpick.n_picks ?? 5) === nOpt
                      ? { background: "#e65100", color: "#fff", borderColor: "#e65100" }
                      : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {nOpt}
                  </button>
                ))}
              </span>
            )}
            {!dpick.applied && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.14)", color: "#e65100" }}>
                {t(`오늘은 아직 이전 5종목으로 수집 중 (${(dpick.trading_now || []).map((x) => x.name).join(", ")}) — 내일 아침부터 적용`,
                   `still collecting the previous five today (${(dpick.trading_now || []).map((x) => x.name).join(", ")}) - applies from tomorrow morning`)}
              </span>
            )}
            <span className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              {/* NO score-based controls on the Live Kiwoom Desk (boss 2026-08-25, second
                  report: this menu is DEDICATED to the fixed six). The single reco
                  auto-trade switch lives on the reco page; the six are always on. */}
              {deskView === "reco" && (() => {
                const recoOn = (dpick.mode ?? "both") === "both";
                return (
                  <button disabled={deskBusy} onClick={() => switchDesk("score")}
                    title={t("추천 상위 종목 자동매매 켜기/끄기 (내 6종목은 항상 매매)",
                             "turn the reco picks' auto-trading on/off (my 6 always trade)")}
                    className="text-[10px] font-bold px-2 py-0.5 rounded border"
                    style={recoOn
                      ? { background: "#e65100", color: "#fff", borderColor: "#e65100" }
                      : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {recoOn ? "● " : "○ "}{t("추천 자동매매", "reco auto-trade")}
                  </button>
                );
              })()}
              <span className="text-[10.5px] ml-1" style={{ color: "#1565c0" }}>
                {pickOpen ? t("닫기 ▲", "close ▲") : t("순위 보기 ▼", "see the ranking ▼")}
              </span>
            </span>
          </div>
          {pickOpen && (dpick.mode ?? "both") !== "fixed" && (
            <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
              <table className="w-full text-[11.5px] tabular-nums">
                <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0"
                  style={{ background: "var(--bg-elevated)" }}>
                  {/* CATEGORY VIEW = the checklist's own 분류 as columns (boss 2026-08-24);
                      the button below swaps in the six subcategory columns. */}
                  {(pickDetail
                    ? [["rank", t("순위", "#"), "left"], ["stock", t("종목", "stock"), "left"],
                       ["score", t("점수", "score"), "right"], ["now", t("지금", "now"), "right"],
                       ["trend", t("추세", "trend"), "right"], ["liquidity", t("유동성", "liq"), "right"],
                       ["flexibility", t("유연성", "flex"), "right"], ["levels", t("지지저항", "levels"), "right"],
                       ["momentum", t("모멘텀", "mom"), "right"], ["flows", t("수급", "flows"), "right"]]
                    : [["rank", t("순위", "#"), "left"], ["stock", t("종목", "Stock"), "left"],
                       ["score", t("평균 점수", "Average score"), "right"],
                       ["market", t("시장", "Market"), "right"],
                       ["issue", t("이슈/수급 (10)", "Issue/Supply&Demand (10)"), "right"],
                       ["stock_sel", t("종목선정 (90)", "Stock selection (90)"), "right"],
                       ["exec", t("실행/관리", "Execution management"), "right"]]
                  ).map(([k, lab, al]) => (
                    <th key={k} onClick={() => setPickCol(pickCol === k ? "" : k)}
                      title={t("눌러서 소분류/설명 보기", "click for the subcategories / explanation")}
                      className={`px-2 py-1 cursor-pointer select-none whitespace-nowrap ${al === "left" ? "text-left" : "text-right"}`}
                      style={pickCol === k ? { color: "#1565c0", fontWeight: 800 } : undefined}>
                      <span style={{ borderBottom: "1px dotted currentColor" }}>{lab}</span>
                      <span className="ml-0.5 opacity-60">ⓘ</span>
                    </th>
                  ))}
                </tr></thead>
                <tbody>
                  {pickCol && COL_HELP[pickCol] && (
                    <tr><td colSpan={pickDetail ? 10 : 7} className="px-4 py-2.5 text-[11px] leading-relaxed border-b"
                      style={{ background: "rgba(21,101,192,0.07)", borderColor: "#1565c0",
                               color: "var(--text-secondary)" }}>
                      <div className="flex items-start gap-2">
                        <b className="text-[12px] shrink-0" style={{ color: "#1565c0" }}>
                          {COL_HELP[pickCol][lang === "ko" ? 0 : 1]}
                        </b>
                        <span>{COL_HELP[pickCol][lang === "ko" ? 2 : 3]}</span>
                        <button onClick={() => setPickCol("")}
                          className="ml-auto shrink-0 text-[10px] px-1.5 rounded"
                          style={{ color: "#1565c0" }}>{t("닫기 ✕", "close ✕")}</button>
                      </div>
                    </td></tr>
                  )}
                  {(pickAll
                      // full ranking sorted by the SAME number the rows display —
                      // heartbeat live score, else the sum-law base (boss 2026-08-27:
                      // 삼성SDI showed 65 ranked ABOVE 74.8 rows because the sort used
                      // the stale live_total while the cells showed the sum scores)
                      ? [...dpick.rows].sort((a, b) => {
                          const va = rankHead9?.rows?.find((x) => x.code === a.code)?.avg
                            ?? (a as { cats?: { avg?: number } }).cats?.avg
                            ?? a.live_total ?? a.score;
                          const vb = rankHead9?.rows?.find((x) => x.code === b.code)?.avg
                            ?? (b as { cats?: { avg?: number } }).cats?.avg
                            ?? b.live_total ?? b.score;
                          return (vb ?? 0) - (va ?? 0);
                        })
                      // desk view: the LIVE desk lists ONLY the six (boss 2026-08-25);
                      // the reco desk lists its own picks
                      : deskView === "reco"
                      ? _recoRows
                      : (dpick.rows || []).filter((r) => r.pinned)
                   ).map((r, ri) => (
                    <React.Fragment key={r.code}>
                    {pickAll && ri === 0 && (
                      <tr><td colSpan={pickDetail ? 10 : 7} className="px-3 py-1 text-[10px] font-bold text-center"
                        style={{ background: "rgba(128,128,128,0.10)", color: "var(--text-muted)" }}>
                        {t("▼ 100점 체크리스트 전체 순위 — '지금' 합계 순 (챗봇 추천과 동일) · ★ = 아침 점수 상위 5 · 색칠된 줄 = 내 종목",
                           "▼ the full 100-item checklist ranking — sorted by the 'now' total (same as the chatbot) · ★ = morning top 5 · shaded = your desk")}
                      </td></tr>
                    )}
                    <tr className="border-t border-[var(--border-default)]/40"
                      style={{ background: r.on_desk ? (r.pinned ? "rgba(230,81,0,0.09)" : "rgba(21,101,192,0.10)")
                                                     : "transparent" }}>
                      <td className="px-3 py-1 text-[var(--text-muted)]">{pickAll ? ri + 1 : r.rank}</td>
                      <td className="px-2 font-bold text-[var(--text-primary)] cursor-pointer select-none"
                        title={t("클릭: 이 종목의 총점 계산식 전체", "click: this stock's full score calculation")}
                        onClick={() => setPickRow(pickRow === r.code ? "" : r.code)}>
                        {r.by_score && deskView === "reco"
                          ? <span title={t("아침 점수 상위", "morning top by score")}
                              style={{ color: "#e65100" }}>★ </span> : ""}
                        <span style={{ borderBottom: "1px dotted currentColor" }}>{r.name}</span>
                        <span className="ml-0.5 opacity-50 text-[9px]">🧮</span></td>
                      {pickDetail ? (
                        <>
                          <td className="text-right px-3 font-extrabold" style={{ color: "#1565c0" }}>{r.score}</td>
                          <td className="text-right px-2 font-extrabold"
                            title={r.live_adj !== undefined
                              ? t(`아침 ${r.score} + 실시간 ${r.live_adj >= 0 ? "+" : ""}${r.live_adj}`,
                                  `morning ${r.score} + live ${r.live_adj >= 0 ? "+" : ""}${r.live_adj}`)
                              : t("상위 10만 실시간 계산", "live pass covers the top 10")}
                            style={{ color: r.live_total !== undefined
                              ? ((r.live_adj ?? 0) >= 0 ? "#0f5132" : "#b02a2a") : "var(--text-muted)" }}>
                            {r.live_total !== undefined ? r.live_total : "·"}
                          </td>
                        </>
                      ) : (
                        // AVERAGE SCORE (boss 2026-08-25: the four checklist
                        // categories, each scored, divided by their count)
                        (() => {
                          const base9 = (r.cats as { avg?: number } | undefined)?.avg
                            ?? (r.live_total !== undefined ? r.live_total : r.score);
                          const live9 = rankHead9?.rows?.find((x) => x.code === r.code)?.avg;
                          const adj9 = live9 != null && base9 != null
                            ? Math.round((live9 - base9) * 10) / 10 : null;
                          return (
                            <td className="text-right px-3 font-extrabold" style={{ color: "#1565c0" }}
                              title={adj9 != null
                                ? t(`합산 점수 ${base9} + 실시간(4초, 가격·구간·뉴스·거래량) ${adj9 >= 0 ? "+" : ""}${adj9} = ${live9} — 종목명 클릭(🧮)이 전체 계산식`,
                                    `sum score ${base9} + live adj (4s: price/zone/news/volume) ${adj9 >= 0 ? "+" : ""}${adj9} = ${live9} - click the name (🧮) for the full formula`)
                                : t("합산제 — 통과 항목의 가중치 합 (평균 아님, 2026-08-26 항목별 점수)",
                                    "average = the four columns ÷ their count")}>
                              {live9 ?? base9}
                              {adj9 != null && adj9 !== 0 && (
                                <span className="ml-0.5 text-[9px] font-normal"
                                  style={{ color: adj9 > 0 ? "#0f5132" : "#b02a2a" }}>
                                  {adj9 > 0 ? "▲" : "▼"}</span>)}
                            </td>
                          );
                        })()
                      )}
                      {(pickDetail
                        ? (["trend","liquidity","flexibility","levels","momentum","flows"] as const)
                            .map((g) => [g, r.groups?.[g]] as const)
                        : ([["market", r.cats?.market], ["issue", r.cats?.issue],
                            ["stock_sel", r.cats?.stock_sel], ["exec", r.cats?.exec]] as const)
                      ).map(([g, v]) => (
                        <td key={g} className="text-right px-2"
                          style={{ color: v == null ? "var(--text-muted)"
                                   : (v ?? 0) >= 70 ? "#0f5132"
                                   : (v ?? 0) >= 40 ? "var(--text-secondary)" : "#b02a2a" }}>
                          {v == null ? (g === "exec" ? "—" : "-") : v}
                        </td>
                      ))}
                    </tr>
                    {/* THE WHOLE FORMULA (boss 2026-08-25: "if I click any
                        company name it should show the calculation method and
                        formula with explanation") - the clicked stock unfolds
                        its complete score arithmetic: every group's weighted
                        contribution summing to the total, then every group's
                        own sub-checks */}
                    {pickRow === r.code && r.groups && (() => {
                      try {
                      const c9 = r.cats as { market?: number | null; issue?: number | null;
                        stock_sel?: number | null; exec?: number | null; avg?: number | null } | undefined;
                      const det9 = (r as unknown as { detail?: Record<string,
                        { k: string; v: string; s: number; w: number }[]> }).detail;
                      const ex9 = (r as unknown as { exec_items?: { no: number; q: string;
                        ok: boolean | null; d: string }[] }).exec_items;
                      const mi9 = (dpick as unknown as { market_items?: { no: number; q: string;
                        q_en?: string; ok: boolean | null; d: string; w?: number }[] } | null)?.market_items || [];
                      const W9: [string, string, string, number][] = [
                        ["liquidity", "볼륨 (46:5·47+69:6)", "volume (46:5·47+69:6)", 11],
                        ["trend", "추세 (51:5·52:2·50:1·58:2)", "trend (51:5·52:2·50:1·58:2)", 10],
                        ["flexibility", "유연성 (48:2)", "flexibility (48:2)", 2],
                        ["levels", "위치 (62·63·67) — 0점", "levels (62·63·67) - 0 pts", 0],
                        ["momentum", "모멘텀 (60·61) — 0점", "momentum (60·61) - 0 pts", 0]];
                      const parts9: [string, string, number | null | undefined][] = [
                        ["시장", "market", c9?.market], ["이슈·수급", "issue/supply", c9?.issue],
                        ["종목선정", "stock sel.", c9?.stock_sel], ["실행·관리", "exec mgmt", c9?.exec]];
                      const nn9 = parts9.filter(([, , v]) => v != null).length;
                      const OX = ({ ok }: { ok: boolean | null }) => (
                        <b className="mx-1" style={{ color: ok === true ? "#0f5132" : ok === false ? "#b02a2a" : "var(--text-muted)" }}>
                          {ok === true ? "O" : ok === false ? "X" : "—"}</b>);
                      return (
                        <tr><td colSpan={pickDetail ? 10 : 7} className="px-6 py-2 text-[10.5px] border-b"
                          style={{ background: "rgba(230,81,0,0.05)", borderColor: "#e65100",
                                   color: "var(--text-secondary)" }}>
                          <div><b style={{ color: "#e65100" }}>🧮 {r.name} — {t("점수 계산식 전체 (왼쪽 열부터 순서대로)", "the full score calculation, column by column left to right")}</b></div>
                          {c9 && (
                            <div className="mt-1 tabular-nums">
                              <b style={{ color: "#e65100" }}>{t("합산 점수 (항목별 가중치 합산제, 2026-08-26)", "SUM score (per-item weights, 2026-08-26)")}</b> = <b style={{ color: "#1565c0" }}>{c9.avg ?? "—"}</b>
                              <span className="opacity-70"> {t(`— 통과한 항목의 점수를 그대로 더한 값 (기본 최대 92 + 뉴스 레이어 8). 참고 칸: `, `— passed items' points added up (base max 92 + news layer 8). Reference columns: `)}
                              {parts9.map(([ko9, en9, v9], i9) => (
                                <span key={i9}>{i9 > 0 ? " · " : ""}
                                  {lang === "ko" ? ko9 : en9} {v9 != null ? Math.round(v9) : "—"}</span>
                              ))}</span>
                              {(() => {
                                const lv9 = rankHead9?.rows?.find((x) => x.code === r.code)?.avg;
                                const ad9 = lv9 != null && c9.avg != null
                                  ? Math.round((lv9 - c9.avg) * 10) / 10 : null;
                                return ad9 != null ? (
                                  <span className="ml-2">
                                    {t(`+ 실시간 조정(4초: 가격·구간·뉴스) ${ad9 >= 0 ? "+" : ""}${ad9} = `,
                                       `+ live adj (4s: price/zone/news) ${ad9 >= 0 ? "+" : ""}${ad9} = `)}
                                    <b style={{ color: "#e65100" }}>{lv9}</b>
                                    {t(" ← 지금 점수(칩과 동일)", " ← the LIVE score (same as the chips)")}
                                  </span>) : null; })()}
                            </div>
                          )}
                          {mi9.length > 0 && (
                            <div className="mt-1.5">
                              <b style={{ color: "#1565c0" }}>{t("① 시장의 계산 — 체크리스트 #11~25 + 시장급 항목 (모든 종목 공통)", "1) MARKET — checklist #11-25 + market-grade items (same for every stock)")}</b>
                              <div className="mt-0.5 opacity-80">
                                {t("계산법: 시장 = 통과(O)한 항목 가중치 합 ÷ 판정 가능한 항목 가중치 합 × 100. 가중치는 대부분 ×1이고, 위험한 것일수록 무겁습니다: #11 지수 방향 ×2 · #22 시장 악재 ×2 · #95 급락일 ×3. '—'(데이터 없음) 항목은 분모에서 제외됩니다.",
                                   "how: market = (weights of O items) ÷ (weights of answerable items) × 100. Most items are ×1; the dangerous ones weigh more: #11 index direction ×2, #22 market-wide bad news ×2, #95 plunge day ×3. '—' (no data) items are excluded from the denominator.")}
                              </div>
                              <div className="mt-0.5 grid gap-x-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))" }}>
                                {mi9.map((m9, i9) => (
                                  <div key={i9} className="whitespace-nowrap overflow-hidden text-ellipsis">
                                    <span className="opacity-60">#{m9.no}</span><OX ok={m9.ok} />
                                    {lang === "ko" ? m9.q : (m9.q_en || m9.q)}
                                    <span className="opacity-40 font-bold"> ×{m9.w ?? 1}</span>
                                    {m9.d && <span className="opacity-50"> — {m9.d}</span>}
                                  </div>
                                ))}
                              </div>
                              <div>
                                {(() => {
                                  const ans9 = mi9.filter((m9) => m9.ok !== null);
                                  const den9 = ans9.reduce((a9, m9) => a9 + (m9.w ?? 1), 0);
                                  const num9 = ans9.filter((m9) => m9.ok === true).reduce((a9, m9) => a9 + (m9.w ?? 1), 0);
                                  return t(`→ 시장 = 통과 ${num9} ÷ 판정가능 ${den9} × 100 = `,
                                           `→ market = passed ${num9} ÷ answerable ${den9} × 100 = `);
                                })()}
                                <b style={{ color: "#1565c0" }}>{c9?.market ?? "—"}</b>
                              </div>
                            </div>
                          )}
                          {det9?.flows && (
                            <div className="mt-1.5">
                              <b style={{ color: "#1565c0" }}>{t("② 이슈·수급의 계산 — 체크리스트 #31·32·34·43", "2) ISSUE/SUPPLY — checklist #31, 32, 34, 43")}</b>
                              {det9.flows.map((d9, i9) => (
                                <div key={i9} className="whitespace-nowrap overflow-hidden text-ellipsis">
                                  {d9.k}: <b className="text-[var(--text-primary)]">{d9.v}</b>
                                  <span style={{ color: d9.s >= 70 ? "#0f5132" : d9.s >= 40 ? "var(--text-secondary)" : "#b02a2a" }}> → {d9.s}{t("점", "pt")}</span>
                                  <span className="opacity-50"> ×{d9.w}%</span>
                                </div>
                              ))}
                              <div>{t("→ 가중 합 = 이슈·수급 ", "→ weighted sum = issue/supply ")}<b style={{ color: "#1565c0" }}>{c9?.issue ?? "—"}</b></div>
                            </div>
                          )}
                          <div className="mt-1.5 tabular-nums">
                            <b style={{ color: "#1565c0" }}>{t("③ 종목선정의 계산 — 체크리스트 #46~75 자동분", "3) STOCK SELECTION — checklist #46-75, the automated part")}</b>
                            <div className="mt-0.5">= ({W9.map(([k9, ko9, en9, w9], i9) => (
                              <span key={k9}>{i9 > 0 ? " + " : ""}
                                {lang === "ko" ? ko9 : en9} <b className="text-[var(--text-primary)]">{r.groups?.[k9 as keyof typeof r.groups] ?? 0}</b>
                                <span className="opacity-60">×{w9}</span></span>
                            ))}) ÷ 90 = <b style={{ color: "#1565c0" }}>{c9?.stock_sel ?? "—"}</b></div>
                            {det9 && W9.map(([k9, ko9, en9, w9]) => (
                              <div key={k9} className="mt-0.5 pl-3">
                                <b style={{ color: "#1565c0" }}>{lang === "ko" ? ko9 : en9} {r.groups?.[k9 as keyof typeof r.groups]}</b>
                                <span className="opacity-60"> (×{w9})</span>:
                                {(det9[k9] || []).map((d9, i9) => (
                                  <span key={i9} className="ml-2 whitespace-nowrap">
                                    {d9.k}: <b className="text-[var(--text-primary)]">{d9.v}</b>
                                    <span style={{ color: d9.s >= 70 ? "#0f5132" : d9.s >= 40 ? "var(--text-secondary)" : "#b02a2a" }}> →{d9.s}</span>
                                    <span className="opacity-50">×{d9.w}%</span>
                                  </span>
                                ))}
                              </div>
                            ))}
                          </div>
                          {ex9 && ex9.length > 0 && (
                            <div className="mt-1.5">
                              <b style={{ color: "#1565c0" }}>{t("④ 실행·관리의 계산 — 체크리스트 #76~100 자동분", "4) EXECUTION MGMT — checklist #76-100, the automated part")}</b>
                              {ex9.map((e9, i9) => (
                                <div key={i9} className="whitespace-nowrap overflow-hidden text-ellipsis">
                                  <span className="opacity-60">#{e9.no}</span><OX ok={e9.ok} />
                                  {e9.q}<span className="opacity-50"> — {e9.d.slice(0, 50)}</span>
                                </div>
                              ))}
                              <div>{t("→ O의 비율 = 실행·관리 ", "→ share of O = exec mgmt ")}<b style={{ color: "#1565c0" }}>{c9?.exec ?? "—"}</b></div>
                            </div>
                          )}
                          <div className="mt-1 opacity-60">
                            {t("네 칸의 결과가 맨 위 평균식으로 합쳍니다 · 시장 항목 전체 문장은 챗봇 \"체크리스트\"에서.",
                               "the four columns sum into the average at the top · full market sentences: ask the chatbot \"checklist\".")}
                          </div>
                        </td></tr>
                      );
                      } catch { return null; }
                    })()}
                    {/* THE OPEN CALCULATION (boss 2026-08-25: "clicking each
                        column shows the sub-checks with the actual calculation
                        and score") - a clicked group unfolds its checklist
                        items with the measured value, sub-score, and weight,
                        under EVERY row */}
                    {pickCol && (r as unknown as { detail?: Record<string,
                        { k: string; v: string; s: number; w: number }[]> })
                        .detail?.[pickCol] && (
                      <tr><td colSpan={pickDetail ? 10 : 7}
                        className="px-6 py-1 text-[10.5px] border-b"
                        style={{ background: "rgba(21,101,192,0.04)",
                                 borderColor: "var(--border-default)",
                                 color: "var(--text-secondary)" }}>
                        <b style={{ color: "#1565c0" }}>{r.name}</b>
                        {((r as unknown as { detail: Record<string,
                            { k: string; v: string; s: number; w: number }[]> })
                            .detail[pickCol]).map((d9, i9) => (
                          <span key={i9} className="ml-3 whitespace-nowrap">
                            {d9.k}: <b className="text-[var(--text-primary)]">{d9.v}</b>
                            <span className="ml-0.5" style={{ color: d9.s >= 70
                              ? "#0f5132" : d9.s >= 40
                              ? "var(--text-secondary)" : "#b02a2a" }}>
                              → {d9.s}{t("점", "pt")}</span>
                            <span className="opacity-60"> ×{d9.w}%</span>
                          </span>
                        ))}
                      </td></tr>
                    )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
              <div className="px-4 py-1.5 border-t text-center" style={{ borderColor: "var(--border-default)" }}>
                {/* score-view buttons live on the RECO page only (boss 2026-08-25:
                    the Live Kiwoom Desk is dedicated to the fixed six) */}
                {deskView === "reco" && (
                <button onClick={() => setPickAll(!pickAll)}
                  className="text-[10.5px] font-bold px-2.5 py-1 rounded-md border"
                  style={pickAll ? { background: "#1565c0", color: "#fff", borderColor: "#1565c0" }
                                 : { borderColor: "#1565c0", color: "#1565c0" }}>
                  {pickAll ? t("추천 종목만 보기 ▲", "show only the picks ▲")
                           : t(`100점 체크리스트 순위 보기 (${dpick.rows.length}종목) ▼`,
                               `see the 100-item checklist ranking (${dpick.rows.length} stocks) ▼`)}
                </button>
                )}
                <button onClick={() => { setPickDetail(!pickDetail); setPickCol(""); }}
                  className="ml-2 text-[10.5px] font-bold px-2.5 py-1 rounded-md border"
                  style={pickDetail ? { background: "#00838f", color: "#fff", borderColor: "#00838f" }
                                    : { borderColor: "#00838f", color: "#00838f" }}>
                  {pickDetail ? t("체크리스트 4분류로 보기", "show the 4 checklist categories")
                              : t("세부 6칸 보기 (추세·유동성…)", "show the 6 detail columns")}
                </button>
                <button onClick={() => setRawCode(rawCode ? "" : (dpick.rows[0]?.code || ""))}
                  className="ml-2 text-[10.5px] font-bold px-2.5 py-1 rounded-md border"
                  style={rawCode ? { background: "#6a1b9a", color: "#fff", borderColor: "#6a1b9a" }
                                 : { borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                  {rawCode ? t("원자료 닫기 ▲", "close the source data ▲")
                           : t("📄 원자료 보기 (이 점수의 근거)", "📄 see the source data behind these scores")}
                </button>
              </div>
              {rawCode && (
                <div className="border-t px-4 py-3" style={{ borderColor: "#6a1b9a",
                     background: "rgba(106,27,154,0.05)" }}>
                  <div className="flex items-center gap-2 flex-wrap mb-2">
                    <b className="text-[12px]" style={{ color: "#6a1b9a" }}>
                      {t("원자료 — 위 점수는 전부 이 표에서 계산됩니다",
                         "the source rows - every score above is computed from this table")}
                    </b>
                    <select value={rawCode} onChange={(e) => setRawCode(e.target.value)}
                      className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                      style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                      {dpick.rows.map((r) => (
                        <option key={r.code} value={r.code} style={{ color: "#000" }}>
                          {r.pinned ? "📌 " : ""}{r.name}
                        </option>
                      ))}
                    </select>
                    <select value={rawDays} onChange={(e) => setRawDays(Number(e.target.value))}
                      className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                      style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                      {[5, 10, 20, 60, 120].map((d) => (
                        <option key={d} value={d} style={{ color: "#000" }}>
                          {t(`최근 ${d}일`, `last ${d} days`)}
                        </option>
                      ))}
                    </select>
                    <span className="text-[10px] text-[var(--text-muted)]">
                      {t("테이블: raw_daily_prices (일봉) · korean_investor_flows (수급)",
                         "tables: raw_daily_prices (daily candles) - korean_investor_flows (flows)")}
                    </span>
                  </div>
                  {!raw ? (
                    <div className="text-[11px] text-[var(--text-muted)] py-2">
                      {t("불러오는 중…", "loading…")}
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="text-[11px] tabular-nums w-full">
                        <thead><tr className="text-[10px] text-[var(--text-muted)]">
                          <th className="text-left px-2 py-1">{t("날짜", "date")}</th>
                          <th className="text-right px-2">{t("시가", "open")}</th>
                          <th className="text-right px-2">{t("고가", "high")}</th>
                          <th className="text-right px-2">{t("저가", "low")}</th>
                          <th className="text-right px-2">{t("종가", "close")}</th>
                          <th className="text-right px-2">{t("등락", "chg")}</th>
                          <th className="text-right px-2">{t("거래량", "volume")}</th>
                          <th className="text-right px-3">{t("외국인", "foreign")}</th>
                          <th className="text-right px-2">{t("기관", "inst")}</th>
                        </tr></thead>
                        <tbody>
                          {raw.rows.map((r) => {
                            const f = raw.flows.find((x) => x.date === r.date);
                            return (
                              <tr key={r.date} className="border-t border-[var(--border-default)]/30">
                                <td className="px-2 py-0.5 text-[var(--text-secondary)]">{r.date}</td>
                                <td className="text-right px-2">{Math.round(r.open).toLocaleString()}</td>
                                <td className="text-right px-2">{Math.round(r.high).toLocaleString()}</td>
                                <td className="text-right px-2">{Math.round(r.low).toLocaleString()}</td>
                                <td className="text-right px-2 font-bold">{Math.round(r.close).toLocaleString()}</td>
                                <td className="text-right px-2 font-bold"
                                  style={{ color: (r.chg ?? 0) > 0 ? "#b02a2a" : (r.chg ?? 0) < 0 ? "#1565c0" : "var(--text-muted)" }}>
                                  {r.chg == null ? "-" : `${r.chg > 0 ? "+" : ""}${r.chg}%`}
                                </td>
                                <td className="text-right px-2 text-[var(--text-secondary)]">
                                  {Math.round(r.volume).toLocaleString()}</td>
                                <td className="text-right px-3"
                                  style={{ color: f ? ((f.foreign > 0) ? "#b02a2a" : "#1565c0") : "var(--text-muted)" }}>
                                  {f ? `${f.foreign > 0 ? "+" : ""}${Math.round(f.foreign / 1e8).toLocaleString()}억` : "—"}</td>
                                <td className="text-right px-2"
                                  style={{ color: f ? ((f.inst > 0) ? "#b02a2a" : "#1565c0") : "var(--text-muted)" }}>
                                  {f ? `${f.inst > 0 ? "+" : ""}${Math.round(f.inst / 1e8).toLocaleString()}억` : "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <div className="text-[10px] text-[var(--text-muted)] mt-2">
                        {raw.flow_latest
                          ? t(`수급 자료 최신일: ${raw.flow_latest}. 이 날짜가 최근이 아니면 수급 점수는 오래된 자료로 계산된 것입니다.`,
                              `flow data goes up to ${raw.flow_latest}. If that is not recent, the flows score was computed from stale data.`)
                          : t("이 종목은 수급 자료가 전혀 없어 수급 점수는 중립 처리됩니다.",
                              "this stock has no flow data at all, so its flows score is filled in as neutral.")}
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div className="px-4 py-2 text-[10.5px] text-[var(--text-muted)] border-t"
                style={{ borderColor: "var(--border-default)" }}>
                {t("매매 종목은 직접 고정한 목록입니다 — SK하이닉스 · 삼성전자 · NAVER · SK텔레콤 · 한화오션 · 두산에너빌리티. 100점 체크리스트는 매일 아침 그대로 돌아가서 이 종목들의 점수와 순위를 매기지만, 누가 매매될지는 더 이상 정하지 않습니다. 위 버튼을 누르면 오늘 점수로 뽑혔을 상위 5종목(★)까지 전체 순위를 볼 수 있습니다. — 점수는 장기 성격(1년: 거래대금·호가비용·추세성·거래량 급증)과 당일 상태(이평 정배열·신고가·RSI·MACD·볼린저·3일 수급·공매도)로 계산하며, 전부 그 날 이전 자료만 씁니다.",
                   "The traded list is fixed by you - SK하이닉스, 삼성전자, NAVER, SK텔레콤, 한화오션, 두산에너빌리티. The 100-item checklist still runs every morning and scores them, but it no longer decides who trades. The button above opens the full ranking, where ★ marks the five the score would have picked today. - Scores combine long-run character (a year of trading value, tick cost, trendiness, volume surges) with today's condition (MA alignment, new highs, RSI, MACD, Bollinger, 3-day flows, short interest), always from data before the day.")}
              </div>
            </div>
          )}
        </div>
      )}
      {/* the GO/NO-GO strip only rides with the SCORE desk (boss 2026-08-11): on his
          fixed six the rules on the board ignore the gate anyway, so a wall of NO-GO
          over stocks that are trading regardless read as a contradiction */}
      {/* SEMI-AUTO control + pending suggestions (boss 2026-08-25: "Auto trades by the
          100-checklist recommendation; in Semi-auto it suggests and the final click is
          human"). SELLs/stops always execute; the six always auto-trade. */}
      {deskView === "reco" && <SafeBox label="live-check"><RecoLiveCheckPanel t={t} lang={lang} /></SafeBox>}
      {deskView === "reco" && <SafeBox label="inspection-room"><InspectionRoom t={t} /></SafeBox>}
      {/* the room follows ITS page: menu 1 shows only menu 1, menu 2 only
          menu 2 (boss 2026-08-27) */}
      <SafeBox label="order-room"><OrderRoom t={t} desk={deskView === "reco" ? "m2" : "m1"} /></SafeBox>
      {deskView === "reco" && <SafeBox label="auto/semi"><RecoTradeModePanel t={t} /></SafeBox>}
      {/* GO/NO-GO board removed at the boss's order (2026-08-25) — code preserved. */}
      {/* THE ALGORITHM CHOICE, its own bar above the panel (boss 2026-08-11: it was
          buried under the history table inside the toolbar and unreachable) */}
      <div className="mt-3 flex items-center gap-2">
        <b className="text-[12px]" style={{ color: "#6a1b9a" }}>{t("알고리즘:", "algorithm:")}</b>
        {/* TWO BUTTONS, no dropdown (boss 2026-08-11: the select misbehaved twice in one
            morning; a button cannot half-work). The pressed one is filled in. */}
        {/* THE DUEL (boss 2026-08-19): same doors, same sizes, same resets -
            알고1 harvests in HALVES, 알고2 in TENTHS. One law apart. */}
        {/* tooltips = the CURRENT book (2026-08-22): five doors + layer
            judges + iron rule + valve - stale -1.5%/four-door text retired */}
        {([["d1", t("① 알고리즘 1 — 50% 수확", "① Algorithm 1 — 50% harvest"),
            t("다섯 문(급락·아침·상승·반등·분출)으로 매수, 레이어 판정(연중 85%↑ 매수금지·60%↑ 절반·연료 부족 절반) → +1%마다 50%씩 매도 · 바닥 20%는 +2% 도달 후 첫 하락까지 음봉 매도 없음 · -1% 전량 후 3양봉 재진입, 하루 2회 손절이면 그 종목 종료 · 15:19 전량",
              "five doors in (dip/open/climb/rebound/burst), layer judges size it (>=85% of year: no buy · >=60%: half · low fuel: half) -> sells 50% per +1% rung · bottom fifth: no blue-candle sells until +2% then first dip · -1% stop + 3-red return, 2 stops = done for the day · 15:19 all out")],
           ["d2", t("② 알고리즘 2 — 10% 계단", "② Algorithm 2 — 10% drip"),
            t("같은 다섯 문·같은 레이어 판정 → +1%마다 10%씩 매도, 한 계단 내려오면 그 조각 되사기(핑퐁) · 바닥 20% +2% 밸브 · -1% 전량 후 3양봉, 2회면 종목 종료 · 15:19 전량 — 연간 성적 1위",
              "same five doors & judges -> sells 10% per +1% rung, buys the slice back one step lower (ping-pong) · bottom-fifth +2% valve · -1% stop + 3-red, 2 stops = done · 15:19 all out - the year's best earner")],
           ["d3", t("③ 알고리즘 3 — 정점 전량", "③ Algorithm 3 — ride to the peak"),
            t("같은 다섯 문 + 바닥 20%는 1.5배 매수(사장님의 일봉 서클) → 오르는 동안 보유, +0.85% 무장 후 3번째 음봉에 전량(최고가권은 2번째) · 큰 음봉 0.9% 즉시 · 미상승 3음봉 조기 정리 · 바닥은 +2% 밸브까지 인내 · -1% 전량+3양봉, 2회면 종료 · 15:19 전량",
              "same five doors + bottom fifth buys 1.5x (the boss's daily-chart circle) -> holds the whole climb, armed at +0.85% sells ALL on the 3rd blue (2nd near the record) · a 0.9% blue sells instantly · never-rose 3 blues = early cut · bottom-zone patience to the +2% valve · -1% stop + 3-red, 2 stops = done · 15:19 all out")],
           ["d4", t("④ 알고리즘 4 — 알고2+갭상승 룰", "④ Algorithm 4 — Algo2 + gap rule"),
            t("알고리즘 2와 완전히 같은 책 + 갭상승 규칙 하나: 시가가 전일 종가보다 +2% 이상 높게 출발한 종목은 가격이 자기 시가 아래로 내려올 때까지 신규 매수 금지 (하락을 기다렸다 진짜 바닥을 산다) · 연간 측정 +18.2M — 오늘의 하이닉스 +5.2% 갭 아침이 증거",
              "exactly Algo 2's book + ONE gap rule: a stock that opened >=2% above yesterday's close takes no NEW buys until its price falls below its own open (wait the decrease, buy the real bottom) · measured +18.2M/yr - today's +5.2% 하이닉스 gap morning is the exhibit")]] as const)
          .map(([k, lab, tip]) => (
          <button key={k} title={tip}
            onClick={() => { if (way === k) return;
                             setWay(k); setMlView("all");
                             setFamOpen(true); setFam(null);
                             setDet(null); setSel(null);
                             // the old algorithm's last request must not ride
                             // the refresher into the new board
                             lastReqRef.current = null; }}
            className="text-[11.5px] font-bold px-3 py-1 rounded-md border"
            style={way === k ? { background: "#6a1b9a", color: "#fff", borderColor: "#6a1b9a" }
                             : { borderColor: "#6a1b9a", color: "#6a1b9a", background: "transparent" }}>
            {way === k ? "● " : ""}{lab}
          </button>
        ))}

      </div>
      {/* ---- the rules, on real money prices. This is what he opens the page for. ---- */}
      {rank?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-2 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.06)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              🔬 {t("매매", "trading")}
            </b>
            {showBlocked && (
              <span className="text-[10px] font-extrabold px-1.5 py-0.5 rounded"
                style={{ background: "#e65100", color: "#fff" }}>
                {t("가상 결과 — 매수 금지된 종목 포함 (실제 매매 아님)",
                   "WOULD-HAVE numbers - includes blocked stocks (not real trading)")}
              </span>
            )}
            {/* Today has no tape before the opening bell, so the board reads the newest
                day that does rather than showing an empty desk (boss 2026-08-11). Says
                which day, so a past session is never mistaken for this one. */}
            {!ruleDay && rank?.auto_day && (
              <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                style={{ background: "rgba(21,101,192,0.10)", color: "#1565c0" }}>
                {t("오늘 — 아직 체결 없음 (09:00 개장부터 채워집니다)",
                   "today - no executions yet (fills from the 09:00 open)")}
              </span>
            )}
            {ruleDay && ruleDay === autoPinRef.current && (
              <span className="text-[10px] px-2 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.10)", color: "#e65100" }}>
                {t("오늘 장 시작 전 — 마지막 거래일 기록입니다",
                   "before today's open - this is the last trading day's record")}
              </span>
            )}
            {/* THE CLOCK, on the table itself. It lived only in small caption text and in
                buttons far below, so the boss pasted a full ranking and could not tell
                whether he was reading 5틱 or 1분 (2026-08-05). The whole point of the
                1분 group is comparing the same rule across clocks - the switch has to be
                where the numbers are. */}
            {/* One dropdown, 5틱 selected by default (boss 2026-08-06: four buttons PLUS a
                "showing" badge read as two different things). The select IS the badge now. */}
            <span className="text-[10px] text-[var(--text-muted)]">{t("봉:", "bars:")}</span>
            <select value={dailyView ? "daily" : period ? `p${period}` : `t${tick}`}
              onChange={(e) => { const val = e.target.value;
                                 // 일봉 = a VIEW, not a trading clock (boss
                                 // 2026-08-24): the engine keeps its 1분/5틱
                                 // book; the chart shows the year map with
                                 // the judges' zone lines
                                 if (val === "daily") { setDailyView(true); return; }
                                 setDailyView(false);
                                 const per = val[0] === "p" ? Number(val.slice(1)) : 0;
                                 const tk = val[0] === "t" ? Number(val.slice(1)) : 5;
                                 setTick(tk); setPeriod(per);
                                 tickRef.current = tk; perRef.current = per;
                                 setDet(null); setSel(null); setPick(null); pull(); }}
              className="text-[11px] font-bold px-1 py-0.5 rounded border bg-[var(--bg-primary)]"
              style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
              <option value="t5">5틱</option>
              <option value="p60">1분</option>
              <option value="daily">{t("일봉 (1년)", "daily (1yr)")}</option>
            </select>
            {/* WHICH DAY. "" = today's live tape; any stored day is one click. The ML
                models honestly re-train per day: viewing 08-05 uses only 08-04. */}
            {/* ONE dropdown instead of a button per day (boss 2026-08-06) - the day list
                grows for ever, so a row of buttons would too. Newest first, today on top. */}
            <span className="text-[10px] text-[var(--text-muted)] ml-2">{t("날짜:", "day:")}</span>
            <select value={ruleDay}
              onChange={(e) => { const val = e.target.value; dayTouchedRef.current = true;
                                 setRuleDay(val); ruleDayRef.current = val;
                                 setDet(null); setSel(null); setPick(null); pull(); }}
              className="text-[10px] font-bold px-1 py-0.5 rounded border bg-[var(--bg-primary)] text-[var(--text-primary)]"
              style={{ borderColor: ruleDay ? "#e65100" : "var(--border-default)" }}>
              <option value="">{t("오늘 (실시간)", "today (live)")}</option>
              <option value="all">{t("전체 누적 (모든 날)", "all days (total)")}</option>
              {/* today's own file appears in stored days the moment the market opens -
                  listing it again under "오늘 (실시간)" showed TWO todays (boss 08-06) */}
              {(rank.days ?? []).slice().reverse()
                .filter((d2) => d2 !== new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul" })
                  .format(new Date()).replace(/-/g, ""))
                .map((d2) => {
                  const st = famDaily[d2];
                  const lab = `${d2.slice(4, 6)}-${d2.slice(6)}`
                    + (st && st.trips
                       ? ` · ${st.trips}${t("건", "t")} ${st.win_pct}%`
                         + (money ? ` · ${st.net_won > 0 ? "+" : ""}₩${Math.round(st.net_won).toLocaleString()}` : "")
                       : "");
                  return <option key={d2} value={d2}>{lab}</option>;
                })}
            </select>
            <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("시간:", "hours:")}</span>
            <input value={hourFrom} onChange={(e) => setHourFrom(e.target.value)} placeholder="09:00"
              className="w-[52px] text-[10px] px-1 py-0.5 rounded border bg-transparent"
              style={{ borderColor: "var(--border-default)" }} />
            <span className="text-[10px] text-[var(--text-muted)]">~</span>
            <input value={hourTo} onChange={(e) => setHourTo(e.target.value)} placeholder="10:00"
              className="w-[52px] text-[10px] px-1 py-0.5 rounded border bg-transparent"
              style={{ borderColor: "var(--border-default)" }} />
            <button onClick={() => { hourFromRef.current = hourFrom; hourToRef.current = hourTo;
                                     setDet(null); setSel(null); pull(); }}
              className="text-[10px] font-bold px-1.5 py-0.5 rounded-md text-white" style={{ background: "#455a64" }}>
              {t("적용", "apply")}
            </button>
            {(hourFrom || hourTo) && (
              <button onClick={() => { setHourFrom(""); setHourTo(""); hourFromRef.current = ""; hourToRef.current = "";
                                       setDet(null); pull(); }}
                className="text-[10px] px-1.5 py-0.5 rounded border"
                style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                {t("해제", "clear")}
              </button>
            )}
            <div id="fam-table" className="w-full mb-1 pb-1 border-b" style={{ borderColor: "var(--border-default)" }}>
              <div className="flex items-center gap-2 flex-wrap cursor-pointer"
                onClick={() => setFamOpen(!famOpen)}>
                <b className="text-[11px]" style={{ color: "#0f5132" }}>
                  📋 {t("매매 내역", "trading history")}
                </b>
                {famBusy && !fam && (
                  <span className="text-[10.5px] font-bold" style={{ color: "#e65100" }}>
                    ⏳ {t("불러오는 중…", "Loading…")}
                  </span>
                )}
                {fam && fam.rows.length > 0 && (
                  <span className="text-[10.5px] text-[var(--text-secondary)]">
                    {Array.from(new Set(fam.rows.map((r) => ruleName(r.rule)))).map((ru) => {
                      const rs = fam.rows.filter((r) => (ruleName(r.rule)) === ru);
                      const w2 = rs.filter((r) => r.result === "win").length;
                      const l2 = rs.filter((r) => r.result === "loss").length;
                      return `${ru} ${rs.length}${lang === "ko" ? "건" : "t"} ${w2}${lang === "ko" ? "승" : "W"}${l2}${lang === "ko" ? "패" : "L"}`;
                    }).join(" · ")}
                  </span>
                )}
                {fam && (
                  <span className="text-[10.5px] font-bold text-[var(--text-primary)]">
                    {/* BOTH counts (boss 2026-08-22: episode = money truth,
                        slices = how hard it actually worked; replacing one
                        with the other would decorate, showing both is honest) */}
                    {fam.rows.filter((r) => !r.partial).length}{t("판", " ep")}
                    <span className="font-normal text-[var(--text-muted)]">
                      {t(` (조각매도 ${fam.rows.reduce((a, r) => a + (r.parts?.sells?.length || 0), 0)}회)`,
                         ` (${fam.rows.reduce((a, r) => a + (r.parts?.sells?.length || 0), 0)} slice-sells)`)}
                    </span>
                    {" · "}
                    {(() => {
                      // THE FILTER IS THE LENS (boss 2026-08-13): with any filter
                      // active - a time window like 09:00~14:30, one stock, wins
                      // only - the header recounts for exactly what is shown.
                      const filt = !!(fCode || fRes || fFrom || fTo);
                      const inWin = (t2?: string) => {
                        if (!t2) return false;
                        const hm = t2.slice(0, 5);
                        return (!fFrom || hm >= fFrom) && (!fTo || hm <= fTo);
                      };
                      const rowsF = !filt ? fam.rows : fam.rows.filter((r) =>
                        (!fCode || r.code === fCode) && (!fRes || r.result === fRes)
                        && (!(fFrom || fTo) || inWin(r.buy_t) || inWin(r.sell_t)));
                      // PIECE-COUNT LAW (boss 2026-08-28 17:1x, his explicit
                      // choice over the trip ruler): every ▼ sell line is one
                      // count, judged against its own at-the-moment base. The
                      // trip ruler rides along small - the money column keeps
                      // everyone honest.
                      let sw = 0, sl2 = 0, ew = 0, el = 0;
                      for (const r of rowsF) {
                        if (!r.partial) {
                          const n2 = r.net_pct ?? 0;
                          if (n2 > 0) ew++;
                          else if (n2 < 0) el++;
                        }
                        const sells = (r.parts?.sells || []) as unknown as (number | string | null)[][];
                        let counted = false;
                        if (sells.length && sells[0].length >= 7) {
                          for (const sr of sells) {
                            if (fFrom || fTo) { const tt = (sr[2] as string) || ""; if (!inWin(tt)) continue; }
                            const b2 = (sr[6] ?? r.entry) as number | null;
                            const p2 = sr[0] as number;
                            if (!b2) continue;
                            counted = true;
                            if (p2 > b2) sw++;
                            else if (p2 < b2) sl2++;
                          }
                        }
                        if (!counted && !r.partial) {
                          const n2 = r.net_pct ?? 0;
                          if (n2 > 0) sw++;
                          else if (n2 < 0) sl2++;
                        }
                      }
                      const wp = (ew + el) ? Math.round(ew / (ew + el) * 100) : 0;
                      const sp = (sw + sl2) ? Math.round(sw / (sw + sl2) * 100) : 0;
                      const useSrv = !filt && fam.win_pct != null;
                      const mainPct = useSrv ? fam.win_pct : sp;
                      const mainW = useSrv ? fam.wins : sw;
                      const mainL = useSrv ? fam.losses : sl2;
                      const epPct = useSrv ? (fam.win_pct_ep ?? wp) : wp;
                      return (<>
                        <span style={{ color: mainPct >= 50 ? "#b02a2a" : "#1565c0" }}
                          title={t("매도 한 조각 = 한 건 - 그 순간의 기준가 대비 +면 승 (사장님 룰 2026-08-28)",
                                   "each sell piece = one count; positive vs its at-the-moment base is a W (boss's rule)")}>
                          {t(`승률 ${mainPct}%`, `win ${mainPct}%`)}
                        </span>
                        {" "}({mainW}{t("승", "W")} {mainL}{t("패", "L")})
                        <span className="ml-1 text-[10px] text-[var(--text-muted)]"
                          title={t("판 단위(매수 전부→매도 전부, 수수료 포함 실수익) 승률 - 돈과 항상 일치하는 자", "by whole round trips, after fee - the ruler that always matches the money")}>
                          {t(`· 판당 ${epPct}%`, `· trips ${epPct}%`)}
                        </span>
                        {filt && (
                          <span className="ml-1 text-[10px] font-bold px-1 py-0.5 rounded"
                            style={{ background: "rgba(230,81,0,0.14)", color: "#e65100" }}>
                            {t("선택 구간 기준", "for the filtered window")}
                          </span>
                        )}
                      </>);
                    })()}
                    {((fam as { holding?: unknown[] }).holding?.length ?? 0) > 0 &&
                      t(` · 보유 ${(fam as { holding?: unknown[] }).holding!.length}`,
                        ` · holding ${(fam as { holding?: unknown[] }).holding!.length}`)}
                  </span>
                )}
                <button onClick={(e) => { e.stopPropagation(); setMoney((v) => !v); }}
                  className="text-[10px] px-1.5 py-0.5 rounded border"
                  style={{ borderColor: money ? "#e65100" : "var(--border-default)",
                           color: money ? "#e65100" : "var(--text-muted)" }}>
                  {money ? t("💰 손익 숨기기", "💰 hide money") : t("💰 손익 보기", "💰 show money")}
                </button>
                {money && fam && (() => {
                  // the money follows the filter too (boss 2026-08-21: header
                  // said +1,394,832 while the filtered rows summed +229k -
                  // percentages were window-recounted but money was not)
                  const filt9 = !!(fCode || fRes || fFrom || fTo);
                  const inWin9 = (t2?: string) => {
                    if (!t2) return false;
                    const hm = t2.slice(0, 5);
                    return (!fFrom || hm >= fFrom) && (!fTo || hm <= fTo);
                  };
                  const mw = !filt9 ? fam.net_won : fam.rows.filter((r) =>
                    (!fCode || r.code === fCode) && (!fRes || r.result === fRes)
                    && (!(fFrom || fTo) || inWin9(r.buy_t) || inWin9(r.sell_t)))
                    .reduce((s2, r) => s2 + (r.won || 0), 0);
                  return (
                    <b className="text-[10.5px]" style={{ color: mw > 0 ? "#b02a2a" : mw < 0 ? "#1565c0" : "var(--text-muted)" }}>
                      {mw > 0 ? "+" : ""}₩{Math.round(mw).toLocaleString()}
                      {filt9 && <span className="ml-0.5 text-[9px] font-bold px-1 rounded"
                        style={{ background: "rgba(230,81,0,0.14)", color: "#e65100" }}>
                        {t("선택 구간", "window")}</span>}
                    </b>
                  );
                })()}
                <span className="ml-auto text-[10px]" style={{ color: "#0f5132" }}>
                  {famBusy ? t("갱신 중…", "updating…") : famOpen ? t("닫기 ▲", "close ▲") : t("펼치기 ▼", "open ▼")}
                </span>
              </div>
              {famOpen && fam && (fam.rows.length > 0
                  || ((fam as { holding?: unknown[] }).holding?.length ?? 0) > 0) && (
                <>
                <div className="mt-1 flex items-center gap-2 flex-wrap text-[10.5px]">
                  <b style={{ color: "#6a1b9a" }}>🔎 {t("찾기:", "filter:")}</b>
                  <select value={fCode} onChange={(e) => setFCode(e.target.value)}
                    className="px-1 py-0.5 rounded border bg-[var(--bg-primary)] text-[var(--text-primary)]"
                    style={{ borderColor: fCode ? "#6a1b9a" : "var(--border-default)" }}>
                    <option value="">{t("모든 종목", "all stocks")}</option>
                    {(() => {
                      // the filter lists THIS desk's stocks plus anything that
                      // actually traded today (boss 2026-08-25: menu 2's
                      // filter still showed only the six - today's trading
                      // companies were missing)
                      const opts9 = new Map<string, string>();
                      if (deskView === "reco") {
                        (st?.stocks ?? []).forEach((x) => opts9.set(x.code, x.name));
                      } else {
                        [["000660","SK하이닉스"],["005930","삼성전자"],["035420","NAVER"],
                         ["017670","SK텔레콤"],["042660","한화오션"],["034020","두산에너빌리티"]]
                          .forEach(([c2, n2]) => opts9.set(c2, n2));
                      }
                      (fam?.rows ?? []).forEach((r) => {
                        if (r.code && !opts9.has(r.code)) opts9.set(r.code, r.name || r.code);
                      });
                      return Array.from(opts9.entries()).map(([c2, n2]) =>
                        <option key={c2} value={c2}>{n2}</option>);
                    })()}
                  </select>
                  <select value={fRes} onChange={(e) => setFRes(e.target.value)}
                    className="px-1 py-0.5 rounded border bg-[var(--bg-primary)] text-[var(--text-primary)]"
                    style={{ borderColor: fRes ? "#6a1b9a" : "var(--border-default)" }}>
                    <option value="">{t("승·패 전부", "wins & losses")}</option>
                    <option value="win">{t("승만", "wins only")}</option>
                    <option value="loss">{t("패만", "losses only")}</option>
                  </select>
                  <input value={fFrom} onChange={(e) => setFFrom(e.target.value)}
                    placeholder="10:00"
                    className="w-[62px] px-1 py-0.5 rounded border bg-transparent text-[var(--text-primary)]"
                    style={{ borderColor: fFrom ? "#6a1b9a" : "var(--border-default)" }} />
                  <span className="text-[var(--text-muted)]">~</span>
                  <input value={fTo} onChange={(e) => setFTo(e.target.value)}
                    placeholder="11:00"
                    className="w-[62px] px-1 py-0.5 rounded border bg-transparent text-[var(--text-primary)]"
                    style={{ borderColor: fTo ? "#6a1b9a" : "var(--border-default)" }} />
                  {(fCode || fRes || fFrom || fTo) && (
                    <button onClick={() => { setFCode(""); setFRes(""); setFFrom(""); setFTo(""); }}
                      className="px-1.5 py-0.5 rounded border text-[10px] font-bold"
                      style={{ borderColor: "#e65100", color: "#e65100" }}>
                      {t("전체 보기", "clear")}</button>
                  )}
                </div>
                {/* 🎞 the replay player lives right here, above the table
                    where the name was clicked (boss 2026-08-27) */}
                {repEp9 && <TradeReplay ep={repEp9} t={t} onClose={() => setRepEp9(null)} />}
                <div className="overflow-x-auto mt-1">
                  <table className="w-full text-[11px] tabular-nums"
                    style={{ borderCollapse: "collapse" }}>
                    <thead><tr className="text-[10px] text-[var(--text-muted)]"
                      style={{ background: "rgba(106,27,154,0.06)" }}>
                      <th className="text-left px-2 py-1" style={CELL}>{t("종목", "stock")}</th>
                      <th className="text-left px-2" style={CELL}>{t("매수 내역 (가격 × 수량)", "buys (price × qty)")}</th>
                      <th className="text-left px-2" style={CELL}>{t("매도 내역 (시각 · 가격 × 수량 · 잔여) — 줄을 누르면 차트 증거", "sells (time · price × qty · left) — click a line for chart proof")}</th>
                      {/* the money column exists only after 💰 (boss 2026-08-19:
                          "by default it should be hide money") */}
                      {money && <th className="text-right px-2" style={CELL}>{t("실현 금액", "money")}</th>}
                    </tr></thead>
                    <tbody>
                      {/* THE ORDER IS THE BOSS'S (2026-08-11): what the desk is doing
                          RIGHT NOW comes first - open positions - then what is already
                          finished. Each half is labelled so the seam is visible. */}
                      {(((fam as unknown as { holding?: unknown[] }).holding?.length ?? 0) > 0) && (
                        <tr><td colSpan={money ? 4 : 3} className="px-3 py-1 text-[10px] font-bold"
                          style={{ background: "rgba(230,81,0,0.10)", color: "#e65100" }}>
                          ● {t("지금 보유 중 — 아직 매매가 진행 중입니다", "holding now - the trade is still running")}
                        </td></tr>
                      )}
                      {((fam as unknown as { holding?: { rule: string; code: string; name?: string;
                          buy_t?: string; entry: number; last: number; unreal_pct: number }[] }).holding || [])
                        .filter((h) => !fCode || h.code === fCode)
                        .map((h, k, arrH) => (
                        <React.Fragment key={`h-${h.rule}-${k}`}>
                        <tr className="border-t border-[var(--border-default)]/30"
                          style={{ background: "rgba(230,81,0,0.05)",
                                   ...(k > 0 && arrH[k - 1].code !== h.code
                                      ? { borderTop: "3px solid rgba(106,27,154,0.85)" } : {}) }}>
                          <td className="px-2 py-0.5 font-bold cursor-pointer underline decoration-dotted"
                            title={t("누르면 왜 샀고 왜 아직 들고 있는지 설명이 열립니다", "click: why it bought and why it is still holding")}
                            onClick={() => { const kk = `h-${h.rule}-${k}`;
                                             const open = famExp === kk;
                                             setFamExp(open ? null : kk);
                                             if (!open) {
                                               setChartOpen(true); chartOpenRef.current = true; setChartOpen9(true);
                                               setSel(h.rule); autoOpenRef.current = h.rule;
                                               openRule(h.rule, null, h.code);
                                             } }}
                            style={{ display: "none" }}></td>
                          <td className="px-2 font-bold cursor-pointer underline decoration-dotted text-[var(--text-primary)]" style={CELL}
                            title={t("클릭: 이 매매의 전 과정 재생 + 설명", "click: replay this trade's whole process + the story")}
                            onClick={() => { const kk = `h-${h.rule}-${k}`;
                                             setFamExp(famExp === kk ? null : kk);
                                             const hh9 = h as unknown as { entry?: number;
                                               qty_left?: number; wall?: { price?: number; qty?: number } | null;
                                               parts?: { buys?: unknown[][]; sells?: unknown[][] } };
                                             setRepEp9({ code: h.code, name: h.name || h.code,
                                               entry: hh9.entry, buy_t: h.buy_t, live: true,
                                               qty: hh9.qty_left, rule: h.rule, wall: hh9.wall,
                                               parts: hh9.parts });
                                             setTimeout(() => document.getElementById("trade-replay9")
                                               ?.scrollIntoView({ behavior: "smooth", block: "center" }), 300); }}>
                            🎞 {h.name || h.code}</td>
                          <td className="px-2" style={CELL}>
                            {(() => {
                              const hp9 = (h as unknown as { parts?: {
                                buys?: [number, number, (string | null)?][];
                                sells?: [number, number, ...unknown[]][] } }).parts;
                              const hb0 = hp9?.buys;
                              // holding shows only what REMAINS (boss 2026-08-31):
                              // sold slices consume the buys FIFO; sold-out fills
                              // disappear from the holding row
                              const soldQ9 = (hp9?.sells || []).reduce((a, s) => a + (Number(s[1]) || 0), 0);
                              const hb = hb0 && soldQ9 > 0 ? remainBuys9(hb0, soldQ9) : hb0;
                              // every buy line is CLICKABLE PROOF (boss 2026-08-20):
                              // the chart opens on this stock with every ▲ marked at
                              // its own bar, and the caption restates this exact fill
                              return hb && hb.length ? mergeBuys9(hb).map(([p2, q2, t3, nlaws], k2) => (
                                <div key={k2} className="cursor-pointer underline decoration-dotted"
                                  title={t("클릭: 이 매수를 차트에서 증명", "click: prove this buy on the chart")}
                                  onClick={() => { setSelSlice({ rule: h.rule, name: h.name || h.code,
                                                    t: (t3 as string) || h.buy_t || "", px: p2, qty: q2,
                                                    side: "b",
                                                    why: k2 === 0 ? t("진입 매수", "entry buy")
                                                                  : t("보유 중 추가 매수 (보강·재매수·확정)", "add-on buy (reinforce/reload/confirm)") });
                                                   setFocusSide("b");
                                                   setChartOpen(true); chartOpenRef.current = true; setChartOpen9(true);
                                                   setSel(h.rule); autoOpenRef.current = h.rule;
                                                   openRule(h.rule, null, h.code);
                                                   setTimeout(() => chartRef.current?.scrollIntoView(
                                                     { behavior: "smooth", block: "center" }), 150); }}>
                                  {/* a WAITING (unfilled) chat limit order must not wear the
                                      bought-arrow — the ▲ made the queued NAVER look already
                                      bought (boss 2026-08-26, twice) */}
                                  {(h as unknown as { waiting?: boolean }).waiting
                                    ? <span className="text-[9.5px] font-bold text-[var(--text-muted)]">🕐 {t3 ? String(t3).slice(0, 5) + " " : ""}{t("줄서는 가격 ", "queued at ")}</span>
                                    : (t3 ? <span className="text-[9.5px] font-bold" style={{ color: RED }}>▲ {String(t3).slice(0, 5)} </span> : null)}
                                  ₩{Math.round(p2).toLocaleString()}
                                  <span className="text-[9.5px] text-[var(--text-muted)]"> × {q2}{t("주", "sh")}{Number(nlaws) > 1 ? t(` (${nlaws}개 법 합산)`, ` (${nlaws} laws merged)`) : ""}{(h as unknown as { waiting?: boolean }).waiting ? t(" (미체결)", " (not filled)") : ""}</span></div>
                              )) : <div>₩{Math.round(h.entry).toLocaleString()}</div>;
                            })()}</td>
                          <td className="px-2" style={{ ...CELL, color: "#e65100" }}>
                            {(() => {
                              // HOLDING SHOWS ONLY THE HOLDING (boss 2026-08-21:
                              // the sold slices appeared here AND in the completed
                              // part - now they live only in the completed part).
                              // This cell: remaining shares + the live % vs cost.
                              // 🕐 an OPEN chatbot limit order still queued in the book
                              // (boss 2026-08-26: the unfilled NAVER buy was invisible)
                              const w9 = (h as unknown as { waiting?: boolean; side?: string });
                              if (w9.waiting) return (
                                <div className="font-bold">
                                  🕐 {w9.side === "SELL"
                                    ? t("매도 주문 대기 — 지정가에 줄 서 있음 (아직 미체결)",
                                        "SELL order waiting — queued at the limit price, not filled yet")
                                    : t("매수 주문 대기 — 지정가에 줄 서 있음 (아직 미체결)",
                                        "BUY order waiting — queued at the limit price, not filled yet")}
                                </div>
                              );
                              const hs = (h as unknown as { parts?: { sells?: unknown[] } }).parts?.sells;
                              const hl = (h as unknown as { qty_left?: number }).qty_left;
                              if (hs && hs.length) return (
                                <div className="font-bold">{t(`보유 중 · 잔여 ${hl != null ? hl.toLocaleString() : "?"}주`,
                                        `holding · ${hl != null ? hl.toLocaleString() : "?"}sh left`)}
                                  {h.unreal_pct != null && (
                                    <b className="ml-1 text-[11px] tabular-nums"
                                      style={{ color: h.unreal_pct > 0 ? "#b02a2a" : h.unreal_pct < 0 ? "#1565c0" : "var(--text-muted)" }}>
                                      {h.unreal_pct > 0 ? "+" : ""}{Math.abs(h.unreal_pct) < 0.01 ? h.unreal_pct.toFixed(3) : h.unreal_pct.toFixed(2)}%
                                    </b>
                                  )}</div>
                              );
                              // the LIVE unrealized % rides the status line (boss
                              // 2026-08-20: "while holding show current gaining/
                              // losing % - it should automatically change with the
                              // real-time price") - refreshed by every board poll
                              const _txt = (h as unknown as { chop?: boolean }).chop
                                ? t("보유 중 — 지금 횡보 구간, 매도 판단 정지", "holding — market flat now, exit judging paused")
                                : t("보유 중 — 아직 매도 전", "holding — not sold yet");
                              const _u = h.unreal_pct;
                              return (<>
                                {_txt}
                                {_u != null && (
                                  <b className="ml-1 text-[11px] tabular-nums"
                                    style={{ color: _u > 0 ? "#b02a2a" : _u < 0 ? "#1565c0" : "var(--text-muted)" }}>
                                    {_u > 0 ? "+" : ""}{Math.abs(_u) < 0.01 ? _u.toFixed(3) : _u.toFixed(2)}%
                                  </b>
                                )}
                              </>);
                            })()}</td>
                          {money && <td className="text-right px-2 text-[var(--text-muted)]" style={CELL}
                            title={t("평가손익 (수수료 전)", "unrealized, before fees")}>—</td>}
                        </tr>
                        {famExp === `h-${h.rule}-${k}` && (() => {
                          const ex = explainTrade({ rule: h.rule, entry: h.entry,
                            sig: (h as unknown as { sig?: { drop: number; sx: number | null;
                                  rng: number; t?: string } | null }).sig });
                          return (
                            <tr><td colSpan={money ? 4 : 3} className="px-4 py-2 text-[10.5px] leading-relaxed"
                              style={{ background: "rgba(230,81,0,0.06)", color: "var(--text-secondary)" }}>
                              <div><b style={{ color: RED }}>{t("왜 샀나 — ", "why it bought — ")}</b>{lang === "ko" ? ex.buyKo : ex.buyEn}</div>
                              {(h as unknown as { chop?: boolean }).chop && (
                                <div className="mt-0.5 font-bold" style={{ color: "#e65100" }}>
                                  ⏸ {t("지금 이 종목은 횡보(오실레이션) 구간입니다 — 규칙대로 매도 판단을 멈추고 보유합니다. 움직임이 돌아오면 다시 판단합니다. (-2% 손절과 15:20 정리는 계속 살아 있습니다)",
                                        "this stock is in an oscillation (flat) stretch right now - by the rule, exit judging is paused and we hold. Judging resumes when movement returns. (The -2% stop and the 15:20 close stay armed.)")}
                                </div>
                              )}
                              <div className="mt-0.5"><b style={{ color: "#e65100" }}>{t("왜 들고 있나 — ", "why it is holding — ")}</b>
                                {lang === "ko"
                                  ? "아직 파는 조건이 오지 않았습니다: 내리기 시작해 두 번째 음봉이 시작되면 매도, 손실 -2%면 손절, 15:20이면 장 마감 정리 — 이 중 먼저 오는 것이 팝니다."
                                  : "No sell condition has arrived yet: it sells at the start of the 2nd down candle when the fall begins, at the −2% stop, or at the 15:20 close — whichever comes first."}</div>
                              <div className="mt-1 text-[10px]" style={{ color: "#6a1b9a" }}>
                                📈 {t("아래 차트에서 ▲ 매수 지점과 그 직전의 급락을 직접 확인하세요 — 열린 포지션은 매수부터 현재까지 표시됩니다.",
                                      "check the ▲ buy and the fall before it on the chart below - an open position is marked from its buy to now.")}
                              </div>
                            </td></tr>
                          );
                        })()}
                        </React.Fragment>
                      ))}
                      {fam.rows.length > 0 && (
                        <tr><td colSpan={money ? 4 : 3} className="px-3 py-1 text-[10px] font-bold"
                          style={{ background: "rgba(15,81,50,0.08)", color: "#0f5132" }}>
                          ✓ {t("매매 완료 — 이미 팔린 거래", "completed - already sold")}
                        </td></tr>
                      )}
                      {fam.rows
                        .filter((r) => {
                          if (fCode && r.code !== fCode) return false;
                          if (fRes && r.result !== fRes) return false;
                          if (fFrom || fTo) {
                            const inWin = (t2?: string) => {
                              if (!t2) return false;
                              const hm = t2.slice(0, 5);
                              return (!fFrom || hm >= fFrom) && (!fTo || hm <= fTo);
                            };
                            if (!inWin(r.buy_t) && !inWin(r.sell_t)) return false;
                          }
                          return true;
                        })
                        .map((r, i, arrF) => (
                        <React.Fragment key={`${r.rule}-${r.idx}-${i}`}>
                        <tr className="border-t border-[var(--border-default)]/30"
                          style={i > 0 && arrF[i - 1].code !== r.code
                            ? { borderTop: "3px solid rgba(106,27,154,0.85)" } : undefined}>
                          <td className="px-2 py-0.5 font-bold cursor-pointer underline decoration-dotted"
                            title={t("누르면 설명과 함께 차트에 매수·매도 지점이 증거로 표시됩니다",
                                     "click: the story opens AND the chart marks the buy and sell as proof")}
                            onClick={() => { const k = `${r.rule}-${r.idx}`;
                                             const open = famExp === k;
                                             setFamExp(open ? null : k);
                                             if (!open) {
                                               setChartOpen(true); chartOpenRef.current = true; setChartOpen9(true);
                                               openFamTrade(r, "b");
                                             } }}
                            style={{ display: "none" }}></td>
                          <td className="px-2 font-bold cursor-pointer underline decoration-dotted text-[var(--text-primary)]" style={CELL}
                            title={t("클릭: 이 매매의 기록을 그대로 재생 (🎞 다시보기)", "click: replay this trade's recording (🎞)")}
                            onClick={() => { setRepEp9({
                              code: r.code, name: r.name || r.code,
                              entry: (r as unknown as { entry?: number }).entry,
                              buy_t: r.buy_t, sell_t: r.sell_t,
                              exit: (r as unknown as { exit?: number }).exit,
                              exit_why: (r as unknown as { exit_why?: string }).exit_why,
                              qty: (r as unknown as { qty?: number }).qty,
                              net_pct: r.net_pct, rule: r.rule,
                              wall: (r as unknown as { wall?: { price?: number;
                                qty?: number } | null }).wall,
                              parts: (r as unknown as { parts?: { buys?: unknown[][];
                                sells?: unknown[][] } }).parts });
                              setTimeout(() => document.getElementById("trade-replay9")
                                ?.scrollIntoView({ behavior: "smooth", block: "center" }), 300); }}>
                            🎞 {r.name || r.code}
                            {r.partial && <span className="ml-1 text-[9px] font-bold px-1 py-0.5 rounded"
                              style={{ background: "rgba(230,81,0,0.14)", color: "#e65100" }}
                              title={t("보유 중인 포지션의 계단 매도 조각 — 나머지는 아직 보유 중",
                                       "a ladder slice of a still-open position - the rest is still held")}>
                              {t("조각", "slice")}</span>}
                            {/* THE GUARD (boss 2026-08-28): a row that broke a
                                law wears a small red ? - hover for the charges */}
                            {!!r.guard?.length && <span className="ml-1 text-[10px] font-bold"
                              style={{ color: "#c62828", cursor: "help" }}
                              onClick={(e) => e.stopPropagation()}
                              title={r.guard.join("\n")}>?</span>}</td>
                          <td className="px-2" style={CELL}>
                            {/* SAME EPISODE'S SLICES SHARE ONE BUY LIST (boss
                                2026-08-21: "same buying showing 4 times makes
                                confusion") - only the first slice row prints
                                the buys; siblings wear a small ditto */}
                            {(i > 0 && r.partial && arrF[i - 1].partial
                              && arrF[i - 1].rule === r.rule
                              && arrF[i - 1].buy_t === r.buy_t
                              && arrF[i - 1].code === r.code) ? (
                              <div className="text-[10px] text-[var(--text-muted)]">
                                〃 {t("위와 같은 매수", "same buys as above")}</div>
                            ) : r.parts?.buys && r.parts.buys.length ? mergeBuys9((() => {
                              const bb = r.parts.buys as unknown as [number, number, (string | null)?][];
                              if (!r.partial) return bb;
                              // a still-open episode's row also shows only the
                              // remaining shares (boss 2026-08-31, FIFO)
                              const sq = ((r.parts?.sells || []) as unknown as [number, number][]).reduce((a, s) => a + (Number(s[1]) || 0), 0);
                              return sq > 0 ? remainBuys9(bb, sq) : bb;
                            })()).map(([p2, q2, t3, nlaws], k2) => (
                              <div key={k2} className="cursor-pointer underline decoration-dotted"
                                title={t("클릭: 이 매수를 차트에서 증명", "click: prove this buy on the chart")}
                                onClick={() => { setSelSlice({ rule: r.rule, name: r.name || r.code,
                                                  t: (t3 as string) || r.buy_t || "", px: p2, qty: q2,
                                                  side: "b",
                                                  why: k2 === 0 ? t("진입 매수", "entry buy")
                                                                : t("보유 중 추가 매수 (보강·재매수·확정)", "add-on buy (reinforce/reload/confirm)") });
                                                 openFamTrade(r, "b"); }}>
                                {t3 ? <span className="text-[9.5px] font-bold" style={{ color: RED }}>▲ {String(t3).slice(0, 5)} </span> : null}
                                ₩{Math.round(p2).toLocaleString()}
                                <span className="text-[9.5px] text-[var(--text-muted)]"> × {q2}{t("주", "sh")}</span></div>
                            )) : <div>₩{Math.round(r.entry).toLocaleString()}</div>}</td>
                          <td className="px-2" style={CELL}>
                            {r.parts?.sells && r.parts.sells.length ? (r.parts.sells as unknown as
                              [number, number, string?, number?, number?, string?][]).map(([p2, q2, t2, _i2, rem2, why2], k2) => (
                              <div key={k2} className="cursor-pointer underline decoration-dotted"
                                style={{ color: BLUE }}
                                title={t("클릭: 차트에서 이 조각 확인", "click: this slice on the chart")}
                                onClick={() => { setSelSlice({ rule: r.rule, name: r.name || r.code,
                                                  t: (t2 as string) || r.sell_t || "", px: p2, qty: q2,
                                                  rem: rem2 as number | undefined,
                                                  gain: r.entry ? (p2 / r.entry - 1) * 100 : null,
                                                  why: (why2 as string) || r.exit_why || "" });
                                                openFamTrade(r, "s"); }}>
                                ▼ {((t2 as string) || r.sell_t || "").slice(0, 5)} ₩{Math.round(p2).toLocaleString()}
                                <span className="text-[9.5px] text-[var(--text-muted)]"> × {q2}{t("주", "sh")}{rem2 != null ? t(` (잔여 ${Number(rem2).toLocaleString()})`, ` (left ${Number(rem2).toLocaleString()})`) : ""}</span>
                                {(() => {
                                  const row6 = (r.parts!.sells as unknown as (number | string | null)[][])[k2];
                                  const b2 = (row6 && row6.length > 6 ? row6[6] : null) as number | null;
                                  const g2 = b2 ? (p2 / b2 - 1) * 100 : null;
                                  // the fee line (boss 2026-08-21: a green %
                                  // under +0.23% is a loss in disguise) -
                                  // amber warns the eye. The 15:19 bell is
                                  // exempt (boss 2026-08-28): it MUST sell
                                  // whatever the price - no shame badge on a
                                  // sell that had no choice
                                  const feeLine = g2 != null && g2 > 0 && g2 < 0.23
                                    && !String(why2 || "").includes("마감");
                                  return g2 != null ? (
                                    <b className="ml-1 text-[10px]"
                                      title={feeLine ? t("이익이 수수료(0.23%)보다 작음 - 실제로는 손실", "gain smaller than the 0.23% fee - actually a loss") : undefined}
                                      style={{ color: feeLine ? "#b8860b" : g2 > 0 ? "#b02a2a" : g2 < 0 ? "#1565c0" : "var(--text-muted)" }}>
                                      {g2 > 0 ? "+" : ""}{g2.toFixed(2)}%{feeLine ? "⚠" : ""}</b>
                                  ) : null;
                                })()}</div>
                            )) : (
                              <div className="cursor-pointer underline decoration-dotted" style={{ color: BLUE }}
                                onClick={() => openFamTrade(r, "s")}>
                                ▼ {r.sell_t?.slice(0, 5)} ₩{Math.round(r.exit).toLocaleString()}</div>
                            )}</td>
                          {money && <td className="text-right px-2 font-bold"
                            style={{ ...CELL, color: r.won > 0 ? "#b02a2a" : r.won < 0 ? "#1565c0" : "var(--text-muted)" }}>
                            {r.won > 0 ? "+" : ""}₩{Math.round(r.won).toLocaleString()}</td>}
                        </tr>
                        {famExp === `${r.rule}-${r.idx}` && (() => {
                          const ex = explainTrade(r);
                          return (
                            <tr><td colSpan={money ? 4 : 3} className="px-4 py-2 text-[10.5px] leading-relaxed border-t"
                              style={{ background: "rgba(106,27,154,0.05)", color: "var(--text-secondary)" }}>
                              <div><b style={{ color: "#6a1b9a" }}>{lang === "ko" ? r.rule_ko : r.rule_en}</b></div>
                              <div className="mt-1"><b style={{ color: RED }}>{t("왜 샀나 — ", "why it bought — ")}</b>{lang === "ko" ? ex.buyKo : ex.buyEn}</div>
                              {deskView === "reco" && rankAt9 && famExp === `${r.rule}-${r.idx}` && (
                                <div className="mt-0.5 pl-2" style={{ borderLeft: "2px solid #e65100" }}>
                                  <b style={{ color: "#e65100" }}>{t("왜 이 종목, 왜 이 시각 — ", "why this stock, why this time — ")}</b>
                                  {rankAt9.rank != null
                                    ? t(`매수 시각(${rankAt9.t}) 체크리스트 순위 #${rankAt9.rank}/${rankAt9.of} · 평균 ${rankAt9.avg ?? "—"}점${rankAt9.in_top ? " → 톱3 자격으로 매수 허용" : " (톱3 밖 — 순위 기록 이전의 유예 매수)"}`,
                                        `at ${rankAt9.t} it ranked #${rankAt9.rank}/${rankAt9.of} on the checklist, avg ${rankAt9.avg ?? "—"}${rankAt9.in_top ? " → in the top-3, entry allowed" : " (outside top-3 - a grace entry from before logging began)"}`)
                                    : t("이 시각의 순위 기록 없음 (기록 시작 전 매수 — 유예)", "no rank record at this time (bought before logging began - grace)")}
                                  {rankAt9.top && rankAt9.top.length > 0 && (
                                    <span className="ml-1 opacity-60">
                                      {t("· 그 순간의 톱3: ", "· the top-3 then: ")}
                                      {rankAt9.top.map((x9) => `${x9.name} ${x9.avg ?? ""}`).join(" · ")}
                                    </span>
                                  )}
                                </div>
                              )}
                              {(() => { const js9 = judgeStory(r.judge);
                                return js9 ? (
                                  <div className="mt-0.5 pl-2" style={{ borderLeft: "2px solid #2e7d32" }}>
                                    <b style={{ color: "#2e7d32" }}>{t("그 순간의 레이어 판정: ", "the judges at that moment: ")}</b>
                                    {js9.map((l9, i9) => <div key={i9}>{l9}</div>)}
                                  </div>) : null; })()}
                              {(r.judge?.news ?? 0) > 0 && newsSt9 && newsSt9.length > 0 && (
                                <div className="mt-0.5 pl-2" style={{ borderLeft: "2px solid #b02a2a" }}>
                                  <b style={{ color: "#b02a2a" }}>{t("판정 근거가 된 위험 뉴스 (클릭하면 기사 원문): ", "the danger news behind the ruling (click to read): ")}</b>
                                  {newsSt9.slice(0, 5).map((n9, i9) => (
                                    <div key={i9}>
                                      <a href={n9.link} target="_blank" rel="noreferrer"
                                        className="underline decoration-dotted" style={{ color: "#1565c0" }}>
                                        📰 {n9.title.slice(0, 85)}</a>
                                      <span className="ml-1 text-[10px] text-[var(--text-muted)]">
                                        {n9.ts.slice(11, 16)}{n9.why ? ` — ${n9.why.slice(0, 70)}` : ""}</span>
                                    </div>
                                  ))}
                                </div>
                              )}
                              <div className="mt-0.5"><b style={{ color: BLUE }}>{t("왜 팔았나 — ", "why it sold — ")}</b>{lang === "ko" ? ex.sellKo : ex.sellEn}</div>
                              <div className="mt-1 text-[10px]" style={{ color: "#6a1b9a" }}>
                                📈 {r.rule.startsWith("N")
                                  ? t("증거는 아래 차트에 있습니다: ▲ 매수 직전의 급락과 두 번째 양봉, ▼ 매도 지점을 캔들로 직접 확인하세요. ▲/▼ 시간을 누르면 차트가 그 지점으로 이동합니다.",
                                      "the proof is on the chart below: see the sharp fall before ▲, the second up candle it bought on, and the ▼ sell. Click the ▲/▼ times to jump the chart to each point.")
                                  : t("증거는 아래 차트에 있습니다: ▲ 매수와 ▼ 매도 지점이 캔들 위에 표시됩니다.",
                                      "the proof is on the chart below: ▲ buy and ▼ sell are marked on the candles.")}
                              </div>
                            </td></tr>
                          );
                        })()}
                        </React.Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
                </>
              )}
              {famOpen && fam && ((fam as { holding?: unknown[] }).holding?.length ?? 0) > 0 && false && (
                <div className="text-[10.5px] px-2 py-1 flex gap-3 flex-wrap"
                  style={{ background: "rgba(230,81,0,0.06)" }}>
                  <b style={{ color: "#e65100" }}>{t("보유 중", "holding now")}</b>
                  {((fam as unknown as { holding: { rule: string; code: string; name?: string;
                     buy_t?: string; entry: number; last: number; unreal_pct: number }[] }).holding)
                    .map((h, k) => (
                    <span key={k}>
                      {h.rule} · {h.name || h.code} {h.buy_t?.slice(0, 5)} @₩{Math.round(h.entry).toLocaleString()}
                      {" → "}
                      <b style={{ color: h.unreal_pct > 0 ? "#b02a2a" : "#1565c0" }}>
                        {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%</b>
                    </span>
                  ))}
                </div>
              )}
              {famOpen && !fam && famBusy && (
                <div className="text-[11px] font-bold py-2 text-center" style={{ color: "#e65100" }}>
                  ⏳ {t("매매 내역을 불러오는 중입니다…", "Loading the trading history…")}
                </div>
              )}
              {famOpen && fam && fam.rows.length === 0
                  && ((fam as { holding?: unknown[] }).holding?.length ?? 0) === 0 && (
                <div className="text-[10.5px] text-[var(--text-muted)] py-1">
                  {t("이 기간에 완료된 매매가 없습니다.", "no completed trades in this window.")}
                </div>
              )}
            </div>
            <span className="text-[10px] text-[var(--text-muted)]">
              {rank.stocks.length}{t("종목", " stocks")} · {rank.stocks.reduce((a2, x) => a2 + x.bars, 0).toLocaleString()}{t("봉", " bars")}
            </span>
          </div>
          <div className="px-4 py-2 flex items-center gap-2 flex-wrap">
            {/* THE MONEY, off by default (boss 2026-08-04). A win rate and a P&L answer
                different questions, and mixing them by default is how a rule winning 56%
                of its trades read as a good one while losing on every single trade. One
                button shows the per-trade figure and the running total together: a total
                without a per-trade hides how it was earned, and a per-trade without a
                total hides how much it came to. */}
            <button onClick={() => setMoney((v) => !v)}
              className="text-[10.5px] font-bold px-2 py-1 rounded-md border"
              style={{ borderColor: money ? "#e65100" : "var(--border-default)",
                       background: money ? "rgba(230,81,0,0.10)" : "transparent",
                       color: money ? "#e65100" : "var(--text-secondary)" }}
              title={t("승률만으로는 돈을 벌었는지 알 수 없습니다 - 실제로 번 돈을 원으로 보여줍니다",
                       "a win rate alone cannot say whether it made money - this shows what was actually made, in won")}>
              {money ? t("\ud83d\udcb0 \uc190\uc775 \uc228\uae30\uae30", "\ud83d\udcb0 hide the money")
                     : t("\ud83d\udcb0 \uc2e4\uc81c \uc190\uc775 \ubcf4\uae30", "\ud83d\udcb0 show the money")}
            </button>
            {money && (
              <span className="text-[10.5px]" style={{ color: "var(--text-muted)" }}>
                {t(`수수료 ${rank.fee_pct}% 뺀 뒤입니다.`, `after the ${rank.fee_pct}% round trip.`)}
              </span>
            )}
            {/* The won-budget buttons (1주 / ₩1,000만 / ₩5,000만 / ₩1억) were removed
                2026-08-05 at the boss's request. They predated the 10/100/1,000 share
                bands and ended up as a second sizing system fighting the one he chose -
                worse, the "1주" label had gone stale: with the band caps live, budget=0
                actually traded 10/100/1,000 shares, so the button said one thing and did
                another. The `budget` query param survives on the API for analysis. */}
            {money && (
              <span className="text-[10.5px] ml-2" style={{ color: "var(--text-muted)" }}>
                {t("수량: 가격대별 한도 (100만원 초과 10주 · 10만원 초과 100주 · 그 아래 1,000주)",
                   "size: price-band caps (over ₩1m → 10 · over ₩100k → 100 · below → 1,000 shares)")}
              </span>
            )}
          </div>
          {/* the per-algorithm totals table was REMOVED (boss 2026-08-11): the 매매
              내역 is the one view, and detail opens by clicking a trade in it */}
        </div>
      )}

      {/* a click must never look dead: while the whole-day detail loads (a few
          seconds on first open), say so (boss 2026-08-07: "it is not opening and
          showing me nothing") */}
      {sel && !det && (
        <div className="mt-3 rounded-xl border px-4 py-6 text-center text-[12px]"
          style={{ borderColor: "#6a1b9a", color: "var(--text-muted)" }}>
          ⏳ {t("이 규칙의 하루 전체 기록을 불러오는 중입니다 — 몇 초 걸립니다…",
               "loading this rule's full-day record — takes a few seconds…")}
        </div>
      )}
      {/* ---- one rule's real trades ---- */}
      {sel && !det && detBusy && (
        <div className="mt-3 rounded-xl border px-4 py-3 text-[12px] font-bold text-center"
          style={{ borderColor: "#6a1b9a", color: "#e65100" }}>
          ⏳ {t("이 알고리즘의 매매를 불러오는 중입니다…", "Loading this algorithm's trades…")}
        </div>
      )}
      {sel && det?.ok && (
        <div id="rule-detail" className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-3 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.07)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              <button onClick={() => { setSel(null); setDet(null); setPick(null); }}
                className="mr-1 text-[10px] px-1.5 py-0.5 rounded border align-middle"
                style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                ✕ {t("닫기", "close")}
              </button>
              🔎 {lang === "ko" ? det.ko : det.en}
              {" — "}
              <span style={{ color: "#1565c0" }}>
                {(st?.stocks || []).find((x) => x.code === code)?.name || code}
              </span>
              {" "}{t("에서 이 규칙이 한 매매", "- what this rule did on this stock")}
            </b>
            <span className="text-[12px] tabular-nums">{det.trips}{t("회전", " trips")}</span>
            <span className="text-[12px] tabular-nums" style={{ color: RED }}>{det.wins}{t("승", "W")}</span>
            <span className="text-[12px] tabular-nums" style={{ color: BLUE }}>{det.losses}{t("패", "L")}</span>
            {det.flats > 0 && (
              <span className="text-[12px] tabular-nums" style={{ color: "var(--text-muted)" }}>
                {det.flats}{t("무", " flat")}
              </span>
            )}
            <span className="text-[12px] tabular-nums font-extrabold" style={{ color: det.win_pct >= 50 ? "#2e7d32" : GOLD }}>
              {det.win_pct}% {t("승률", "win")}
            </span>
            {/* the same 💰 that rules the history table - here too, so money can be
                shown/hidden without scrolling (boss 2026-08-19) */}
            <button onClick={() => setMoney((v) => !v)}
              className="text-[10px] px-1.5 py-0.5 rounded border"
              style={{ borderColor: money ? "#e65100" : "var(--border-default)",
                       color: money ? "#e65100" : "var(--text-muted)" }}>
              {money ? t("💰 손익 숨기기", "💰 hide money") : t("💰 손익 보기", "💰 show money")}
            </button>
                          {/* Added up from the rows on screen when the server does not send a total.
                  The backend runs with NO --reload, so until it restarts `net_total` is
                  absent and this header would read "total 0%%" - a confidently wrong
                  number, which is the one thing this panel must never print. Exact
                  whenever the list is complete, and hidden entirely when it is not. */}
{money && moneyWon !== null && (
              <span className="text-[12px] tabular-nums font-extrabold px-2 py-0.5 rounded"
                style={{ background: moneyWon >= 0 ? "rgba(198,40,40,0.10)" : "rgba(21,101,192,0.12)",
                         color: moneyWon >= 0 ? RED : BLUE }}
                title={t("이 규칙이 낸 모든 매매의 합계 (수수료 뺀 뒤)",
                         "every trade this rule made, added up, after fees")}>
                {t("합계", "total")} {moneyWon === null ? "-" : won(moneyWon)}
              </span>
            )}
            {det.thin && (
              <span className="text-[10.5px] font-bold px-2 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                {t(`⚠ 승+패 ${det.decided}건뿐입니다 — 아직 실력이 아니라 우연입니다`,
                   `⚠ only ${det.decided} decided - that is luck, not a measurement`)}
              </span>
            )}
          </div>

          {/* WHY THIS RULE - buy steps, sell conditions, and the measurements behind
              each condition (boss 2026-08-07: "if I click any rule it should open
              explanation ... in English mode then in english otherwise in Korean") */}
          <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(15,81,50,0.04)" }}>
            {/* folded by default (boss 2026-08-19: "in the completed - already sold
                this part it is showing by default explanations") - one click opens */}
            <div className="cursor-pointer flex items-center" onClick={() => setRuleDoc((v) => !v)}>
              <b style={{ color: "#0f5132" }}>📖 {t("이 규칙의 설명", "how this rule works")}</b>
              <span className="ml-auto text-[10px] font-bold" style={{ color: "#0f5132" }}>
                {ruleDoc ? t("닫기 ▲", "close ▲") : t("펼치기 ▼", "open ▼")}</span>
            </div>
            {ruleDoc && (<>
            {det.wall_price && (
              <div className="mt-1 text-[11px] px-2 py-1 rounded"
                style={{ background: "rgba(15,81,50,0.07)", color: "var(--text-secondary)" }}>
                🕐 {t("1분봉 차트가 사고팔 시점을 정하고, 호가창이 주문 가격을 정합니다 — 매수: 가장 두꺼운 매수벽 바로 위 1호가(단, 신호 가격을 넘지 않음) · 매도: 가장 두꺼운 매도벽 바로 아래 1호가(2봉 안에 체결되지 않으면 다시 가격을 잡음)",
                    "the 1-minute chart decides WHEN to buy and sell; the order book decides the PRICE — buy: offered 1 tick above the biggest bid wall (never above the signal price) · sell: offered 1 tick in front of the biggest ask wall (re-priced if not filled within 2 bars)")}
              </div>
            )}
            <div className="mt-1 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
              <div>
                <b className="text-[10.5px]" style={{ color: RED }}>{t("사는 조건 — 질문에 전부 \"예\"여야 삽니다", "BUY — every question must answer YES")}</b>
                <ol className="mt-1 ml-4 list-decimal space-y-[3px] text-[var(--text-secondary)]">
                  {det.dip && (<>
                    <li>{t(`"먼저 급락이 있었는가?" — 최근 ${Math.round((det.dip.win_sec ?? 600) / 60)}분의 최고가에서 ${det.dip.drop}% 이상 떨어졌고, 그 낙폭이 이 종목의 평소 한 봉 움직임(최소 1호가)의 ${det.dip.sharp}배를 넘어야 합니다. 천천히 흘러내린 것은 급락으로 치지 않으며, 장 시작 후 2분간은 판단하지 않습니다.`,
                           `"Was there a sharp drop first?" — the price must have fallen at least ${det.dip.drop}% from the highest close of the last ${Math.round((det.dip.win_sec ?? 600) / 60)} minutes, AND that fall must exceed ${det.dip.sharp}× this stock's normal bar move (floored at one tick). A slow drift does not count, and the first 2 minutes of the session are never judged.`)}</li>
                    <li>{t(`"멈췄다가 다시 오르기 시작했는가?" — 바닥 뒤 양봉 ${det.dip.ups}개가 완성되면, 즉 ${det.dip.ups + 1}번째 양봉이 시작되는 그 가격에 삽니다. (가격이 그대로인 봉은 숫자를 멈출 뿐 0으로 되돌리지 않습니다)`,
                           `"Has it stopped falling and turned up?" — after ${det.dip.ups} completed up candle${det.dip.ups > 1 ? "s" : ""} off the low, it buys at the price where the ${det.dip.ups + 1 === 2 ? "2nd" : `${det.dip.ups + 1}th`} up candle begins. (A flat bar pauses the count, it never resets it)`)}</li>
                    <li>{t(`"횡보장이 아닌가?" — 최근 ${Math.round((det.dip.win_sec ?? 600) / 60)}분의 고저 폭이 ${det.dip.chop}% 미만이면 아무것도 하지 않습니다. 움직임이 없는 시장에서는 매매 자체를 안 합니다 — 손실도 이익도 없습니다.`,
                           `"Is the market actually moving?" — if the last ${Math.round((det.dip.win_sec ?? 600) / 60)} minutes ranged less than ${det.dip.chop}%, nothing is traded at all. In a flat market there is no trade, so no loss and no gain.`)}</li>
                    {det.scout && <li>{t(`"우선 ${Math.round(det.scout.frac * 100)}%만 산다" — 신호에서는 정찰병 ${Math.round(det.scout.frac * 100)}%만 사고, +${det.scout.confirm}% 상승으로 확인되면 나머지 ${100 - Math.round(det.scout.frac * 100)}%를 추가합니다. 급락이 다시 이어지면 손해는 정찰병에서 끝납니다.`,
                           `"Buy ${Math.round(det.scout.frac * 100)}% first" — only a ${Math.round(det.scout.frac * 100)}% scout is bought at the signal; the remaining ${100 - Math.round(det.scout.frac * 100)}% is added once a +${det.scout.confirm}% rise confirms the turn. If the drop resumes, the damage ends at the scout.`)}</li>}
                    <li>{t(`"같은 급락은 두 번 사지 않는다" — 매도 후 새 고점이 만들어져야 다음 매수가 허용됩니다 (딥당 1회).`,
                           `"The same dip is never bought twice" — a new high must form after the last sell before the next buy is allowed (one trade per dip).`)}</li>
                    {det.morning && <li>{t(`"바쁜 아침의 강세는 산다" — 09:00~${det.morning.until} 사이, 시초 5분 거래량이 이 종목 평소의 ${det.morning.vol_x}배 이상이고 가격이 당일 신고가·시가 대비 +${det.morning.min_run}% 이상이면 매수합니다. (미국 상승 조건은 검증에서 탈락 — 거래량이 진짜 신호)`,
                           `"Buy a busy morning's strength" — between 09:00 and ${det.morning.until}, when the first five minutes trade ${det.morning.vol_x}x this stock's usual volume and the price is at a session high, up ${det.morning.min_run}%+ from the open, it buys. (The US-up condition was tested and rejected — volume is the true signal.)`)}</li>}
                    {det.burst && <li>{t(`"떨어진 적 없는 급등도 산다" — 최근 ${det.burst.win_min}분의 최저가에서 ${det.burst.rise}% 이상 올라 당일 신고가에 섰으면 급등의 남은 구간에 올라탑니다. (2026-08-20 설치 — 아침 급등 5건이 4개의 문을 모두 지나쳐 간 날)`,
                           `"A rise that never fell is bought too" — up ${det.burst.rise}%+ from the lowest close of the last ${det.burst.win_min} minutes, standing at a session high, it boards the burst's remaining leg. (Installed 2026-08-20 - the day five morning bursts slipped past all four doors.)`)}</li>}
                    {det.rebound && <li>{t(`"바닥 반등의 눌림은 산다" — 어제 종가가 최근 ${det.rebound.low_win}일 최저가의 ${det.rebound.near}% 이내(바닥권)였고 오늘 전일 대비 +${det.rebound.day_gain}% 이상 오른 날은, 장중 ${det.rebound.drop}% 이상의 작은 눌림이 전환(2번째 양봉)하면 그것도 매수합니다 — 일봉과 분봉이 같은 방향을 말할 때는 작은 눌림도 신뢰합니다.`,
                           `"Buy the pullback of a bottom rebound" — when yesterday closed within ${det.rebound.near}% of the ${det.rebound.low_win}-day low AND today is up ${det.rebound.day_gain}%+ from yesterday, an intraday pullback of ${det.rebound.drop}%+ that turns (2nd red) is also bought — when the daily and minute charts agree, small pullbacks earn trust.`)}</li>}
                    {det.drip?.reinforce && <li>{t(`"내 원가보다 싸게 파는 새 급락은 보강 매수" — 보유 중이라도 새 급락이 전환(2번째 양봉)까지 완성되고 그 가격이 내 평균 원가보다 낮으면 원래 수량의 ${Math.round((det.drip.reinforce.frac ?? 0.5) * 100)}%를 추가 매수합니다 (에피소드당 최대 ${det.drip.reinforce.max ?? 2}회). 기준가가 내려가고 +1% 계단이 새 기준에서 다시 시작됩니다.`,
                           `"A new dip selling below MY cost reinforces" — even while holding, a fresh sharp decrease that completes its 2nd-red turn at a price below our average cost buys ${Math.round((det.drip.reinforce.frac ?? 0.5) * 100)}% more of the original size (at most ${det.drip.reinforce.max ?? 2} per episode). The base re-blends lower and the +1% ladder re-arms from it.`)}</li>}
                  </>)}
                  {!det.dip && <li>{t(`"가격이 ${det.entry_n}번 연속 올랐는가?" — 봉의 마감 가격이 직전 봉보다 높으면 상승 1번. 그런 봉이 ${det.entry_n}개 연속. (가격이 그대로인 봉은 세던 숫자를 잠시 멈출 뿐, 0으로 되돌리지 않습니다)`,
                         `"Did the price rise ${det.entry_n} times in a row?" — a bar closing higher than the one before = one rise; ${det.entry_n} such bars back-to-back. (An unchanged bar pauses the count — it never resets it)`)}</li>}
                  {det.vol_x && <li>{t(`"시장이 붐비는가?" — 신호 봉에서 거래된 주식 수가 이 종목의 최근 20개 봉 평균보다 ${det.vol_x}배 이상 많아야 합니다. 조용한 시장에서 가격만 오르는 것은 믿지 않습니다.`,
                                       `"Is the market busy?" — the shares traded on the signal bar must be at least ${det.vol_x}× this stock's own recent average (last 20 bars). A price rising in a quiet market is not trusted.`)}</li>}
                  {det.max_run && <li>{t(`"상승이 아직 작은가?" — 오르기 시작한 지점부터 지금까지 전부 합쳐 ${det.max_run}% 미만이어야 합니다 (만원짜리 주식이면 ${Math.round(10000*det.max_run/100)}원도 안 오른 상태). 이미 크게 오른 상승은 끝물이라 사지 않습니다.`,
                                         `"Is the climb still small?" — from where the rise began until now, the total climb must be under ${det.max_run}% (for a ₩10,000 stock, that's less than ₩${Math.round(10000*det.max_run/100)}). A rise that already moved a lot is finishing, not starting — skip it.`)}</li>}
                  {det.is_ml && <li>{t(`"AI가 허락하는가?" — 이 종목 전용 인공지능이 과거 데이터와 비교해 "평소보다 이길 확률이 높다"고 할 때만 삽니다. 거절하면 그 신호는 그냥 지나갑니다.`,
                                       `"Does the AI approve?" — this stock's own AI compares the moment with the past and must say "better odds than usual." A refusal means the signal is simply skipped.`)}</li>}
                  {!det.dip && <li>{t(`"횡보장이 아닌가?" — 최근 10분의 고저 폭이 0.4% 미만이면 모든 규칙이 매매하지 않습니다.`,
                         `"Is the market actually moving?" — if the last 10 minutes ranged under 0.4%, NO rule trades at all.`)}</li>}
                  <li>{t(`"빈손인가?" — 이 규칙이 아직 아무 주식도 들고 있지 않아야 합니다. 들고 있으면 다 팔 때까지 새로 사지 않습니다.`,
                         `"Are the hands empty?" — the rule must not be holding anything. While holding, it never buys again until it sells.`)}</li>
                  <li>{t(`"레이어 판정을 통과했는가?" — 연중 85% 이상(자기 최고가권)이면 매수 금지, 60% 이상이면 절반 수량, 바닥 20%는 알고3에서 1.5배. 최근 10분 연료(거래량)가 평소의 0.7배 이하면 절반. 하루 -1% 손절 2번이면 그 종목의 문은 내일까지 닫힙니다. 뉴스(Qwen3)는 매 분 스탬프를 기록만 합니다 — 아직 투표권 없음.`,
                         `"Did the layer judges pass it?" — at >=85% of the 1-year range buying is BANNED; >=60% halves the size; the bottom fifth buys 1.5x on Algo 3. Fuel (last 10 min volume) under 0.7x usual halves it. Two -1% stops in one day close that stock's doors until tomorrow. News (Qwen3) stamps every minute but only observes - no vote yet.`)}</li>
                </ol>
              </div>
              <div>
                <b className="text-[10.5px]" style={{ color: BLUE }}>{t("파는 조건 — 자동, 먼저 오는 쪽", "SELL — automatic, whichever comes first")}</b>
                <ul className="mt-1 ml-4 list-disc space-y-[3px] text-[var(--text-secondary)]">
                  {det.drip ? (<>
                    <li>{det.drip.pingpong
                      ? t(`+${det.drip.step}% 오를 때마다 ${Math.round(det.drip.up_frac * 100)}%씩 지정가로 팝니다. 판 뒤 마지막 매도 단계보다 한 계단(-${det.drip.step}%) 아래로 내려오면 그 조각을 다시 싸게 사서 채우고, 다시 오르면 같은 단계에서 또 팝니다 — 왕복으로 이익을 수확합니다.`,
                          `Every +${det.drip.step}% step sells ${Math.round(det.drip.up_frac * 100)}% at a resting limit. A fall back one full step below the last sold level BUYS the slice back cheaper; on the next rise the same level sells again — harvesting the swing both ways.`)
                      : (det.drip.dn_frac ?? 0) > 0
                      ? t(`+${det.drip.step}% 오를 때마다 ${Math.round(det.drip.up_frac * 100)}%씩 지정가로 팝니다(가격은 호가 단위에 맞춘 실제 주문가). 고점을 찍은 뒤에는 -${det.drip.step}% 내려갈 때마다 ${Math.round((det.drip.dn_frac ?? 0.1) * 100)}%씩 매도벽 앞에 팝니다.`,
                          `Every +${det.drip.step}% step sells ${Math.round(det.drip.up_frac * 100)}% at a resting limit (a real tick-grid price). After the top, every -${det.drip.step}% below the highest step sells ${Math.round((det.drip.dn_frac ?? 0.1) * 100)}% more, in front of the ask wall.`)
                      : t(`+${det.drip.step}% 오를 때마다 ${Math.round(det.drip.up_frac * 100)}%씩 지정가로 팝니다. 완만한 되돌림에는 팔지도 사지도 않고 나머지를 그대로 듭니다.`,
                          `Every +${det.drip.step}% step sells ${Math.round(det.drip.up_frac * 100)}% at a resting limit. Through calm pullbacks the rest is simply HELD - nothing sold, nothing bought.`)}</li>
                    <li>{t(`기준가에서 -${det.drip.stop_reset}%면 전량 매도 — 그리고 하락이 멈추고 3번째 양봉이 뜨면 다시 매수합니다 (정찰 3%부터). 그 외의 하락에는 팔지 않고 버팁니다.`,
                           `At -${det.drip.stop_reset}% from the base it sells ALL — and when the fall stops and the 3rd up-candle prints, it re-enters (scout 3% first). No other decline sells.`)}</li>
                    <li>{t(`연중 바닥 20% 구간에서는 상승을 음봉으로 팔지 않습니다 — +2%에 도달한 뒤 첫 하락이 오면 그때 팝니다 (바닥 밸브). 최고가권(85% 이상)에서는 반대로 한 박자 빨리 정리합니다.`,
                           `In the bottom fifth of the year a rise is never sold on falling candles — once +2% is reached, the FIRST dip sells (the bottom valve). Near the record (>=85%) it lets go one beat earlier instead.`)}</li>
                    <li>{t(`15:19 종 — 전 종목 전량 매도, 예외 없음. 15:20부터는 동시호가라 자유 매매가 끝납니다. 빈손으로 잠듭니다.`,
                           `The 15:19 bell — everything sells, no exceptions. From 15:20 the closing auction ends free trading. We sleep flat.`)}</li>
                    {det.drip.rebuy && <li>{t("보유 중에도 새 급락 신호가 오면 판 만큼을 다시 사서 100%로 채웁니다 (시나리오2).",
                           "While holding, a fresh sharp-drop signal buys back what was sold, topping up to 100% (Scenario 2).")}</li>}
                    {det.us_habit && <li>{t("미국 습관: 반도체지수(SOX)가 밤사이 -1.5% 이하로 떨어진 다음 날은 ⅓ 수량으로만 삽니다. 그 외에는 평소대로 — 매일 9시 정각부터 매매합니다.",
                           "US habit: after a night the SOX chip index fell -1.5% or more, buys are one-third size. Otherwise normal - trading starts 09:00 sharp, every day.")}</li>}
                    <li>{t("15:20 이후 새로 사지 않고 남은 것은 전부 정리 — 밤을 넘기지 않습니다.",
                           "After 15:20 nothing new is bought and whatever remains is closed - nothing is carried overnight.")}</li>
                  </>) : det.ride ? (<>
                    {det.ladder && <li>{t(`이익이 +${det.ladder.half_at}%에 닿는 순간 절반을 지정가로 팝니다 — 절반의 이익은 그 자리에서 확정. 나머지 절반은 넷 중 먼저 오는 것에 팝니다: ① 상승이 이어진 뒤 2번째 음봉 시작 ② +${det.ladder.take}% 도달 ③ 음봉 ${det.ladder.blues}연속 ④ 반등 고점 -${det.ladder.give}% — 매도는 매도벽 바로 앞 1호가. +${det.ladder.half_at}% 전에는 음봉이 나와도 팔지 않습니다(손절만 살아 있음).`,
                           `The moment profit touches +${det.ladder.half_at}%, HALF sells at that exact price — locked in. The other half sells at the FIRST of: ① the 2nd down candle after a renewed rise ② +${det.ladder.take}% total ③ ${det.ladder.blues} straight down candles ④ -${det.ladder.give}% below the post-half peak — offered 1 tick in front of the ask wall. Before +${det.ladder.half_at}% no down candle sells (only the stop is live).`)}</li>}
                    {!det.ladder && <li>{(det.ride.arm ?? 0) > 0
                      ? t(`이익이 +${det.ride.arm}%에 도달하면 매도 감시가 켜집니다. 그 뒤 내리기 시작해 음봉 1개가 완성되면, 두 번째 음봉이 시작되는 가격에 팝니다. +${det.ride.arm}% 전에는 음봉이 나와도 팔지 않고 기다립니다(손절 -2%만 살아 있음). 매도 호가는 가장 두꺼운 매도벽 바로 앞에 겁니다.`,
                          `Reaching +${det.ride.arm}% arms the exit. After that, when the fall starts and one down candle completes, it sells at the start of the 2nd down candle. Before +${det.ride.arm}%, down candles are ignored - it waits (only the -2% stop is live). The sell is offered one tick in front of the biggest ask wall.`)
                      : t(`내리기 시작하면 팝니다 — 음봉 1개가 완성되는 순간, 두 번째 음봉이 시작되는 가격에 매도.`,
                          `It sells when the fall starts - one completed down candle, at the start of the 2nd.`)}</li>}
                    <li>{t(`손실이 -${det.stop_pct ?? 2}%까지 밀리면 → 손절. 단, 종목별 최저선(원 단위) 아래로는 팔지 않고 들고 있습니다.`,
                           `If the loss slips to −${det.stop_pct ?? 2}% → the stop sells; but never below this stock's own won floor, where it holds instead.`)}</li>
                    <li>{t("15:20 이후에는 새로 사지 않고, 들고 있던 것은 마지막 가격으로 정리합니다. 밤을 넘기지 않습니다.",
                           "After 15:20 nothing new is bought and anything still open is closed at the last traded price. Nothing is carried overnight.")}</li>
                  </>) : det.take_ticks ? (<>
                    <li>{t(`+${det.take_ticks}호가에 걸어둔 매도 주문이 체결되면 → 익절. 호가 한 칸은 이 종목의 가격대에서 정해집니다.`,
                           `A resting sell order ${det.take_ticks} ticks above the entry fills → that is the take. One tick is set by the stock's own price band.`)}</li>
                    <li>{t(`손실이 -${det.stop_pct ?? 2}%까지 밀리면 → 손절. 종목별 최저선 아래로는 팔지 않습니다.`,
                           `The loss slipping to −${det.stop_pct ?? 2}% triggers the stop, which never sells below the stock's won floor.`)}</li>
                    <li>{t("15:20 이후 정리 — 밤을 넘겨 들고 가지 않습니다.",
                           "Closed after 15:20 - nothing is carried overnight.")}</li>
                  </>) : det.kind !== "candle" ? (<>
                    <li>{t(`이익이 +${det.a}%에 닿으면 → 자동으로 팝니다 (익절). 만원에 샀다면 ${(10000*(1+det.a/100)).toLocaleString()}원이 된 순간입니다.`,
                           `profit touches +${det.a}% → sells automatically (the take). If bought at ₩10,000, that's the moment it reaches ₩${(10000*(1+det.a/100)).toLocaleString()}.`)}</li>
                    <li>{t(`손실이 -${det.b}%까지 밀리면 → 자동으로 팝니다 (손절). 만원에 샀다면 ${(10000*(1-(det.b??0)/100)).toLocaleString()}원까지 내려온 순간 — 더 큰 손해를 막는 보호선입니다.`,
                           `loss slips to −${det.b}% → sells automatically (the stop). If bought at ₩10,000, that's it falling to ₩${(10000*(1-(det.b??0)/100)).toLocaleString()} — the protection line against bigger damage.`)}</li>
                  </>) : (<>
                    {det.take && <li>{t(`이익이 +${det.take}%에 닿으면 → 자동으로 팝니다 (익절). 만원 기준 ${(10000*(1+det.take/100)).toLocaleString()}원.`,
                                        `profit touches +${det.take}% → sells automatically (the take). ₩${(10000*(1+det.take/100)).toLocaleString()} on a ₩10,000 stock.`)}</li>}
                    <li>{t(`가격이 ${det.a}번 연속 내리면 → 팝니다. 이 규칙에서는 "연속 하락"이 손절선 역할을 합니다 — 정해진 % 대신 시장의 모양을 보고 나갑니다.`,
                           `the price falls ${det.a} times in a row → sell. In this rule the falling pattern IS the stop — it exits on the market's shape instead of a fixed %.`)}</li>
                  </>)}
                  <li>{t(`장이 끝날 때까지 어느 쪽도 안 오면 → "보유 중"으로만 표시되고, 밤을 넘겨 들고 가지 않습니다.`,
                         `if neither line is touched by the close → shown as "holding" only; positions are never carried overnight.`)}</li>
                </ul>
              </div>
              <div>
                <b className="text-[10.5px]" style={{ color: "#0f5132" }}>{t("왜 이 규칙인가 (측정으로 선택)", "WHY selected (measured, not assumed)")}</b>
                <ul className="mt-1 ml-4 list-disc space-y-[2px] text-[var(--text-secondary)]">
                  {det.max_run && <li>{t("매매 1,271건 분석: 크게 오른 뒤 산 매매가 최다 패배 — 작은 상승만 사자 승률 45→51%",
                                         "1,271-trade analysis: buys after big runs lost most — small-run-only lifted win 45→51%")}</li>}
                  {det.vol_x && <li>{t("얇은 거래량의 상승은 쉽게 무너짐 — 거래량 조건으로 승률 46→50%",
                                       "thin-volume rises collapse — the volume gate lifted win 46→50%")}</li>}
                  {!det.ride && det.kind !== "candle" && (det.b ?? 0) >= 1.5 && <li>{t("손절 폭 전수 실험: 좁을수록 나빠짐 (0.4%: 22% → 1.5%: 62%) — 출렁임을 버티는 폭",
                                       "stop-width sweep: tighter was always worse (0.4%: 22% → 1.5%: 62%) — wide enough to survive wobble")}</li>}
                  {!det.ride && !det.dip && det.kind !== "candle" && det.a <= 0.3 && <li>{t("+0.3%는 이 시장에서 가장 자주 도달하는 목표 (실험으로 확인)",
                                       "+0.3% is the most-often-reached target on these stocks (measured)")}</li>}
                  {det.is_ml && <li>{t("모델은 매수만 거름 — 같은 규칙 대비 승률 +3~21p (매일 비교 중)",
                                       "the model filters buys only — +3–21p win vs the same rule bare (compared daily)")}</li>}
                  {det.take && det.kind === "candle" && <li>{t("회장님 설계: 패턴 손절 + % 익절의 결합 — 원본과 나란히 검증 중",
                                       "the boss's design: pattern stop + % take, validated beside the originals")}</li>}
                  {!det.ride && !det.dip && <li>{t("236개 조합 전수 실험에서 승률 상위만 채택 — 무작위 아님",
                         "chosen from a 236-combination sweep by win rate — nothing random")}</li>}
                  {det.dip && <li>{t(`급락 기준 ${det.dip.drop}%/${Math.round((det.dip.win_sec ?? 600) / 60)}분은 250일 실측으로 선택 — 종목당 하루 2회 안팎의 진짜 급락만 통과하는 문턱`,
                         `the ${det.dip.drop}%/${Math.round((det.dip.win_sec ?? 600) / 60)}-min drop line was chosen from 250 measured days — a threshold only ~2 real sharp drops per stock per day can pass`)}</li>}
                  {det.dip && det.scout && <li>{t("정찰 3%→97%와 +1% 무장은 회장님 설계 — 되밀림에는 작게 다치고, 진짜 반등은 끝까지 태웁니다",
                         "the 3%→97% scout and the +1% arming are the boss's design — small damage on a relapse, the full ride on a real rebound")}</li>}
                  {det.ride && !det.dip && <li>{t(`회장님 설계: 정확히 ${det.entry_n}번째 상승에 매수, 2번째 음봉에 매도 — Sharp와 같은 판·같은 종에서 나란히 비교 중`,
                         `the boss's design: buy at the exact ${det.entry_n}rd rise, sell at the 2nd down candle — running beside Sharp on the same tape, same bell`)}</li>}
                </ul>
              </div>
            </div>
            {/* the moment-by-moment STORY (boss 2026-08-07: "after 3 up wait and look
                market something like this") - one sentence-flow, built per recipe */}
            <div className="mt-2 pt-2 border-t text-[11px] leading-relaxed" style={{ borderColor: "var(--border-default)" }}>
              <b style={{ color: "#0f5132" }}>⏱ {t("실제 흐름", "how it plays out, moment by moment")}: </b>
              <span className="text-[var(--text-secondary)]">
                {det.dip ? t(`각 1분봉이 완성될 때마다 판단합니다 → 최근 ${Math.round((det.dip.win_sec ?? 600) / 60)}분 최고가에서 ${det.dip.drop}% 이상 급락 → 바닥 뒤 양봉 ${det.dip.ups}개 완성 → ${det.dip.ups + 1}번째 양봉이 시작되는 순간, 매수벽 위 1호가에 정찰 ${det.scout ? Math.round(det.scout.frac * 100) : 100}% 매수${det.scout ? ` → +${det.scout.confirm}% 확인되면 나머지 ${100 - Math.round(det.scout.frac * 100)}% 추가` : ""}${det.ladder ? ` → +${det.ladder.half_at}%에 절반 지정가 매도(확정) → 나머지는 상승 후 2번째 음봉 / +${det.ladder.take}% / ${det.ladder.blues}연속 음봉 / 반등고점 −${det.ladder.give}% 중 먼저 오는 것에 매도벽 앞 매도` : ` → +${det.ride?.arm ?? 1}% 도달로 매도 감시 시작 → 음봉 1개 완성 → 2번째 음봉 시작에 매도벽 앞 1호가로 매도`} (횡보 중엔 팔지 않고 보유, 손절 −${det.stop_pct ?? 2}%는 항상 살아 있음) → 빈손 복귀. 같은 급락은 다시 사지 않습니다.`,
                   `each completed 1-minute bar is judged → a drop of ≥${det.dip.drop}% from the ${Math.round((det.dip.win_sec ?? 600) / 60)}-min high → ${det.dip.ups} up candle${det.dip.ups > 1 ? "s" : ""} complete${det.dip.ups > 1 ? "" : "s"} off the low → at the start of the ${det.dip.ups + 1 === 2 ? "2nd" : `${det.dip.ups + 1}th`} up candle, a ${det.scout ? Math.round(det.scout.frac * 100) : 100}% scout is offered 1 tick above the bid wall${det.scout ? ` → a +${det.scout.confirm}% rise confirms → the ${100 - Math.round(det.scout.frac * 100)}% is added` : ""}${det.ladder ? ` → at +${det.ladder.half_at}% HALF sells at that price (locked) → the rest sells at the first of: 2nd blue after a renewed rise / +${det.ladder.take}% / ${det.ladder.blues} straight blues / −${det.ladder.give}% off the post-half peak, in front of the ask wall` : ` → +${det.ride?.arm ?? 1}% arms the exit → one down candle completes → sold at the start of the 2nd, in front of the ask wall`} (during chop it holds instead; the −${det.stop_pct ?? 2}% stop is always live) → empty-handed, and the same dip is never bought again.`)
                : det.ride ? t(`봉이 완성될 때마다: 종가가 직전보다 높으면 "상승 1" (보합은 유지) → 정확히 ${det.entry_n}번째 상승이 완성되는 그 순간에만 매수 — 놓친 상승은 추격하지 않습니다 → 매수벽 위 1호가에 주문 → 오르는 동안에는 작은 이익에도 팔지 않고 계속 보유 → 음봉 1개 완성 → 2번째 음봉이 시작되는 순간 매도벽 앞 1호가로 매도 → 손절 −${det.stop_pct ?? 2}%는 항상 살아 있음 → 빈손으로 다음 신호 대기 (한 손 법칙).`,
                   `each completed bar: a close above the previous counts "+1 rise" (a flat keeps the count) → the buy exists only at the exact moment the ${det.entry_n === 3 ? "3rd" : `${det.entry_n}th`} rise completes — a missed rise is never chased → offered 1 tick above the bid wall → while it keeps climbing nothing is sold, however small the gain → one down candle completes → sold at the start of the 2nd, in front of the ask wall → the −${det.stop_pct ?? 2}% stop is always live → empty-handed until the next signal (one-position law).`)
                : (<>
                {t(`봉이 하나 완성될 때마다 규칙이 지켜봅니다 → 종가가 직전보다 높으면 "상승 1"로 셉니다 (보합이면 세던 숫자 유지) → ${det.entry_n}번째 상승이 뜨는 순간`,
                   `the rule watches each bar as it completes → a higher close counts "+1 rise" (a flat keeps the count) → the moment the ${det.entry_n}th rise prints`)}
                {det.vol_x ? t(`, 그 봉의 거래량을 최근 평균과 비교하고(${det.vol_x}배 미만이면 포기)`,
                               `, it checks that bar's volume vs recent average (below ${det.vol_x}× → walk away)`) : ""}
                {det.max_run ? t(`, 상승 전체 폭을 재고(${det.max_run}% 이상이면 포기)`,
                                 `, measures the whole climb (already ${det.max_run}%+ → walk away)`) : ""}
                {det.is_ml ? t(", AI 모델에게 확인받고(거절하면 포기)", ", asks the AI model (a refusal → walk away)") : ""}
                {t(` → 전부 통과하면 그 봉 종가+1호가에 즉시 매수 → 이후 봉마다 가격만 지켜봅니다: `,
                   ` → all pass: buy instantly at that close+1 tick → then it only watches price, bar by bar: `)}
                {det.kind !== "candle"
                  ? t(`+${det.a}%에 닿으면 그 봉에서 익절, -${det.b}%로 밀리면 그 봉에서 손절`,
                      `touch +${det.a}% → sell that bar (take); slip to −${det.b}% → sell that bar (stop)`)
                  : t(`${det.take ? `+${det.take}% 먼저 닿으면 익절, 아니면 ` : ""}${det.a}연속 하락이 나오면 매도`,
                      `${det.take ? `+${det.take}% first → take; otherwise ` : ""}${det.a} straight falls → sell`)}
                {t(" → 팔고 나면 빈손으로 돌아가 다음 신호를 기다립니다. 그 사이의 다른 신호들은 전부 무시합니다 (한 손 법칙).",
                   " → after selling it returns empty-handed and waits for the next signal. Every signal in between is ignored (one-position law).")}
                </>)}
              </span>
            </div>
            </>)}
          </div>
          {det.id.endsWith("ML") && (
            <div className="px-4 py-1.5 border-b text-[11px]" style={{ borderColor: "var(--border-default)", background: "rgba(21,101,192,0.06)", color: "#1565c0" }}>
              🤖 {t("이 표의 매매는 모두 모델이 승인한 매수입니다 — 신호마다 모델이 사기/건너뛰기를 정했고, 수량도 모델이 정했습니다(확신이 클수록 많이). 매도는 모델이 아니라 항상 규칙이 합니다. 모델이 건너뛴 신호는 여기에 없습니다 — 규칙 이름이 같은 ML 없는 행에서 전부 볼 수 있습니다.",
                    "Every trade here is a buy the model APPROVED - at each signal the model chose buy/skip, and chose the share count (more when more confident). Selling is always the rule, never the model. Signals the model skipped are not in this table - the plain row with the same rule name shows all of them.")}
            </div>
          )}
          {/* THE CHART, ABOVE THE TRADE TABLE. It used to sit below everything, so
              clicking a row scrolled the page down and away from the very thing the click
              was supposed to show. The Strategy Lab puts the chart directly above its
              trade history and the boss asked for the same here (2026-08-04). */}
          {!chartOpen && (
            <div className="mx-4 my-2">
              <button onClick={() => { setChartOpen(true); chartOpenRef.current = true; setChartOpen9(true);
                                       if (sel) openRule(sel, pick, det?.chart?.code); }}
                className="text-[11px] font-bold px-3 py-1 rounded-md border"
                style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                📈 {t("차트 보기", "show the chart")}
              </button>
            </div>
          )}
          <div ref={chartRef} className="mx-4 my-2 rounded-xl border p-2"
            style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)",
                     display: chartOpen ? undefined : "none" }}>
            <div className="px-2 pt-1 pb-2 text-[11.5px]" style={{ color: "#6a1b9a" }}>
              <b>📈 {(sel && det?.chart ? det.chart.name : tape?.name) ?? ""} — {tape?.clock ?? ""} {t("차트", "chart")}</b>
              {sel && det?.chart && det.chart.code !== code && (
                <span className="ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded"
                  style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                  {t(`선택한 매매의 종목입니다 (${det.chart.name})`,
                     `following the trade you clicked (${det.chart.name})`)}
                </span>
              )}
              {/* the SWITCHING banner: the clicked company's chart is being
                  built - without this the old chart just sat there and a
                  cross-company click read as "not working" (boss 2026-08-20) */}
              {detBusy && lastReqRef.current && det?.chart
                && lastReqRef.current.want !== det.chart.code && (
                <span className="ml-2 text-[11px] font-bold px-2 py-0.5 rounded animate-pulse"
                  style={{ background: "rgba(106,27,154,0.15)", color: "#6a1b9a" }}>
                  ⏳ {t(`${(st?.stocks || []).find((x) => x.code === lastReqRef.current!.want)?.name || ""} 차트 준비 중 — 몇 초 걸립니다`,
                       `building ${(st?.stocks || []).find((x) => x.code === lastReqRef.current!.want)?.name || "the"} chart — a few seconds`)}
                </span>
              )}
              {sel && det?.chart?.focus && (
                <span className="ml-2 text-[10px]" style={{ color: GOLD }}>
                  {t("◆ 금색 표시가 클릭한 매매입니다", "◆ the gold mark is the trade you clicked")}
                </span>
              )}
              <span className="text-[10px] text-[var(--text-muted)] ml-2">
                {sel && det?.chart
                  ? t(`${det.chart.candles.length.toLocaleString()}봉${ruleDay === "all" ? " · 전체 누적 — 차트는 클릭한 매매의 날" : ruleDay ? ` · ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} 저장된 하루` : " · 오늘"}`,
                      `${det.chart.candles.length.toLocaleString()} bars${ruleDay === "all" ? " · all days - chart shows the clicked trade's day" : ruleDay ? ` · stored day ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)}` : " · today"}`)
                  : bars.length
                  ? t(`${bars.length}봉 · ${tape?.first}~${tape?.last} 사이 체결 ${fmt(tape?.ticks)}건으로 만들었습니다`,
                      `${bars.length} bars, built from ${fmt(tape?.ticks)} executions between ${tape?.first} and ${tape?.last}`)
                  : t("아직 봉을 만들 만큼 체결이 모이지 않았습니다", "not enough executions collected to form a bar yet")}
              </span>
            </div>
            {/* When a rule is open its OWN chart wins, whatever stock it is for. This used to
                require det.chart.code === code, so clicking an SK하이닉스 trade while the
                stock button said 삼성전자 threw the rule's chart away and drew the bare tape
                with no arrows at all - which is why clicking a completed trade looked like it
                did nothing (boss 2026-08-04). The header below names the company actually
                drawn, so the two can never disagree on screen. */}
            {/* keyed by DATASET IDENTITY. The x-axis uses bar NUMBERS as positions, so
                when this same chart swaps between the live tape and a rule's window (or
                the clock changes), the library can briefly keep tick labels cached from
                the previous dataset - which is how a "15:11" from the full-day tape
                appeared between 09:11 and 09:12 inside a morning window (boss
                2026-08-05). A changed key remounts the chart: fresh axis, no stale
                labels, provably ordered data underneath (checked: 0 out-of-order rows
                in every payload). */}
            {/* Gate on the dataset actually DRAWN. This tested the LIVE tape's length,
                so before 09:00 (or after a restart) a stored day's fully loaded chart was
                replaced by "market is closed" - the boss could not review 08-04/08-05 at
                dawn (2026-08-06). A rule's own chart must show whenever IT has bars. */}
            {/* CHART ON DEMAND (boss 2026-08-27): collapsed during the session
                until a trade is clicked or the strip is pressed */}
            {!chartOn9 && (
              <div className="mx-1 my-1 px-3 py-2 rounded-lg border text-[11.5px] cursor-pointer select-none text-center"
                style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}
                onClick={() => setChartOpen9(true)}>
                📈 {t("차트 접혀 있음 — 매매 내역의 시간을 클릭하거나 여기를 눌러 열기 (닫아두면 서버가 가볍습니다)",
                      "chart collapsed — click a trade's time in the history, or press here to open (keeping it closed lightens the server)")}
              </div>
            )}
            {chartOn9 && (
              <div className="mx-1 text-right">
                <button className="text-[10px] px-2 py-0.5 rounded border"
                  style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}
                  onClick={() => { setChartOpen9(false); setPick(null); }}>
                  {t("차트 접기 ▲", "collapse chart ▲")}</button>
              </div>
            )}
            {/* THE DAILY VIEW (boss 2026-08-24): a year of daily candles with
                the judges' zone lines - the map the layers read, visible */}
            {chartOn9 && dailyView && daily9?.candles?.length ? (<>
              <div className="mx-1 mb-1 px-2 py-1 rounded text-[11px] tabular-nums"
                style={{ background: "rgba(46,125,50,0.06)", border: "1px solid var(--border-default)" }}>
                <b style={{ color: "#2e7d32" }}>📅 {t("일봉 1년", "daily, 1 year")}</b>
                <b className="ml-2 text-[var(--text-primary)]">{st?.stocks.find((x) => x.code === daily9!.code)?.name || daily9.code}</b>
                {daily9.pos != null && <b className="ml-2" style={{ color: daily9.pos >= 0.85 ? "#b02a2a" : daily9.pos >= 0.6 ? "#b8860b" : daily9.pos <= 0.2 ? "#1565c0" : "var(--text-secondary)" }}>
                  {t(`연중 ${Math.round(daily9.pos * 100)}% 지점`, `at ${Math.round(daily9.pos * 100)}% of the year`)}</b>}
                <span className="ml-2 text-[var(--text-muted)]">
                  {t("금지선(85%)", "no-buy(85%)")} ₩{Math.round(daily9.lines!.no_buy_85).toLocaleString()}
                  {" · "}{t("조심선(60%)", "caution(60%)")} ₩{Math.round(daily9.lines!.caution_60).toLocaleString()}
                  {" · "}{t("바닥선(20%)", "bottom(20%)")} ₩{Math.round(daily9.lines!.bottom_20).toLocaleString()}
                  {" · "}{t("연중 최저", "yr low")} ₩{Math.round(daily9.year_lo!).toLocaleString()}
                  {" ~ "}{t("최고", "hi")} ₩{Math.round(daily9.year_hi!).toLocaleString()}</span>
                {(() => {
                  // ZONE PROOF for the clicked trade (boss 2026-08-27: "put a
                  // daily option to PROVE is it actually selling/buying zone
                  // (top, bottom)") — the entry price placed on the year map
                  const ptr9 = pick !== null && det ? det.trades[pick] : null;
                  if (!ptr9 || ptr9.code !== daily9.code
                      || !daily9.year_hi || daily9.year_lo == null) return null;
                  const dp9 = (ptr9.entry - daily9.year_lo)
                    / Math.max(1e-9, daily9.year_hi - daily9.year_lo);
                  const zc9 = dp9 >= 0.85 ? "#b02a2a" : dp9 >= 0.6 ? "#b8860b"
                    : dp9 <= 0.2 ? "#1565c0" : "var(--text-secondary)";
                  const zl9 = dp9 >= 0.85 ? t("🔴 매도구간(금지) — 규칙 위반!", "🔴 SELLING zone (banned) — violation!")
                    : dp9 >= 0.6 ? t("🟡 조심 구간 (절반 매수 규칙)", "🟡 caution band (half-size rule)")
                    : dp9 <= 0.2 ? t("🟢 매수구간(바닥)", "🟢 BUYING zone (bottom)")
                    : t("중간 구간 (매수 허용)", "mid-range (buying allowed)");
                  return (
                    <div className="mt-0.5 font-bold" style={{ color: zc9 }}>
                      🔎 {t("클릭한 매매", "clicked trade")}: {String(ptr9.buy_t || "").slice(0, 5)} {t("매수", "buy")} ₩{Math.round(ptr9.entry).toLocaleString()}
                      {" → "}{t(`연중 ${Math.round(dp9 * 100)}% 지점`, `${Math.round(dp9 * 100)}% of the year`)} — {zl9}
                    </div>);
                })()}
              </div>
              <LiveChart key={`daily-${daily9.code}`} off={0} focus={null}
                bars={daily9.candles.map((d9, i9) => ({
                  time: i9, hhmm: `${d9.d8.slice(4, 6)}/${d9.d8.slice(6)}`,
                  open: d9.open, high: d9.high, low: d9.low, close: d9.close,
                  vol: d9.vol })) as unknown as Bar[]}
                marks={(() => {
                  const ptr9 = pick !== null && det ? det.trades[pick] : null;
                  if (!ptr9 || ptr9.code !== daily9.code || !daily9.candles?.length) return undefined;
                  const li9 = daily9.candles.length - 1;   // the trade's day = today
                  return [{ b: li9, s: li9, g: 0, net: 0, xb: true,
                            label: `${t("매수", "buy")} ₩${Math.round(ptr9.entry).toLocaleString()}` }];
                })() as never} />
            </>) : null}
            {chartOn9 && !dailyView && (sel && det?.chart ? det.chart.candles.length : bars.length) ? <LiveChart
                key={`det-${sel ?? "tape"}-${det?.chart?.code ?? code}-${tick}-${period}`}
                off={sel && det?.chart ? det.chart.off : (tape?.off ?? 0)}
                bars={sel && det?.chart ? det.chart.candles : bars}
                                      marks={sel && det?.chart ? (() => {
                  // the clicked episode's OTHER buys join the chart as ▲s
                  // (boss 2026-08-21: the 14:27 SK텔레콤 army buy had a time
                  // but no mark - "in the chart I can not see")
                  const base9 = det.chart.marks || [];
                  const ptr9 = pick !== null ? det.trades[pick] : null;
                  if (!ptr9 || ptr9.code !== det.chart.code) return base9;
                  const buys9 = (ptr9.parts?.buys || []) as unknown as (number | string | null)[][];
                  if (buys9.length < 2) return base9;
                  const cds9 = det.chart.candles;
                  const extra9 = buys9.map((b9, i9) => {
                    const tt9 = typeof b9[2] === "string" ? (b9[2] as string).slice(0, 5) : "";
                    if (!tt9) return null;
                    let ix9 = -1;
                    for (let j9 = 0; j9 < cds9.length; j9++) {
                      if ((cds9[j9].hhmm || "").slice(0, 5) >= tt9) { ix9 = j9; break; }
                    }
                    if (ix9 < 0) return null;
                    return { b: ix9, s: ix9, g: 0, net: 0, xb: true,
                             label: (i9 === 0 ? t("진입", "entry") : t("추가", "add"))
                                    + ` ₩${Math.round(b9[0] as number).toLocaleString()}` };
                  }).filter(Boolean) as { b: number; s: number; g: number; net: number;
                                          xb: boolean; label: string }[];
                  return [...base9, ...extra9];
                })() : undefined}
                                      focus={sel && det?.chart
                  ? (() => {
                      // INSTANT JUMP. The chart already holds the whole day and every
                      // trade row carries its bar positions (buy_i/sell_i are indices
                      // into that same day), so a click on a same-company trade is pure
                      // arithmetic - no waiting for the server round-trip the boss felt
                      // as delay (2026-08-06). The fetch still runs behind it, for the
                      // order-book evidence; when it lands the focus is already right.
                      const ptr = pick !== null ? det.trades[pick] : null;
                      if (ptr && ptr.code === det.chart.code
                          && (ptr.d8 ?? "") === detDayRef.current) {
                        const f = (focusSide === "b" ? ptr.buy_i : ptr.sell_i) - det.chart.off;
                        if (f >= 0 && f < det.chart.candles.length) return f;
                      }
                      return (focusSide === "b" ? det.chart.focus?.b : det.chart.focus?.s) ?? null;
                    })()
                  : null} /> : dailyView ? null : (
              <div className="px-4 py-10 text-center text-[12px] text-[var(--text-muted)]">
                {ruleDay
                  ? (ruleDay === "all"
                     ? t("전체 누적을 보는 중 — 위에서 규칙을 클릭하면 모든 날의 매매가 열립니다.",
                         "viewing all days - click a rule above to open every day's trades.")
                     : t(`${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} 저장된 하루를 보는 중 — 위에서 규칙을 클릭하면 그 날의 차트와 매매가 열립니다.`,
                         `viewing stored day ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} - click a rule above to open that day's chart and trades.`))
                  : st?.market_open
                  ? t("수집 중입니다 — 잠시 뒤 첫 봉이 그려집니다.", "collecting - the first bars appear shortly.")
                  : t("장이 열려야 새 체결이 들어옵니다 (09:00~15:30).", "new executions only arrive while the market is open (09:00-15:30).")}
              </div>
            )}
            {/* THE CLICKED TRADE, RESTATED UNDER THE CHART (boss 2026-08-12: "if I
                click any time these trading information should come under the chart
                so we can easily compare and check, we do not need to remember"). The
                same line the history shows - buy, sell (or holding), net, why. */}
            {sel && det?.chart && selSlice && (
              <div className="mt-1 mx-1 px-2 py-1.5 rounded text-[11.5px] tabular-nums"
                style={{ background: "rgba(21,101,192,0.08)", border: "1px solid var(--border-default)" }}>
                <b style={{ color: "#6a1b9a" }}>{ruleName(selSlice.rule)}</b>
                <b className="ml-2 text-[var(--text-primary)]">{selSlice.name}</b>
                <span className="ml-2" style={{ color: selSlice.side === "b" ? RED : BLUE }}>
                  {selSlice.side === "b" ? "▲" : "▼"} {selSlice.t.slice(0, 8)} @₩{Math.round(selSlice.px).toLocaleString()} × {selSlice.qty}{t("주", "sh")}</span>
                {selSlice.rem != null && <span className="ml-2 text-[var(--text-muted)]">{t(`잔여 ${Number(selSlice.rem).toLocaleString()}주`, `${Number(selSlice.rem).toLocaleString()}sh left`)}</span>}
                {selSlice.gain != null && <b className="ml-2" style={{ color: selSlice.gain > 0 ? RED : BLUE }}>{selSlice.gain > 0 ? "+" : ""}{selSlice.gain.toFixed(2)}%</b>}
                {selSlice.why && <span className="ml-2 text-[var(--text-secondary)]">[{selSlice.why}]</span>}
                <button className="ml-2 text-[10px] px-1 rounded border" style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}
                  onClick={() => setSelSlice(null)}>{t("닫기", "close")}</button>
              </div>
            )}
            {/* THE LAYER STORY (boss 2026-08-21 night): daily zone -> volume
                fuel -> Qwen's news stamps -> minute trigger, the exact steps
                behind every decision on this company, in his order. */}
            {sel && det?.chart && layers9?.steps && (
              <div className="mt-1 mx-1 px-2 py-1.5 rounded text-[11.5px]"
                style={{ background: "rgba(46,125,50,0.06)", border: "1px solid var(--border-default)" }}>
                <b style={{ color: "#2e7d32" }}>🧭 {t("레이어 판정 — 이 종목의 오늘 단계", "layer verdicts — this stock's steps today")}</b>
                {layers9.steps.map((s9, i9) => (
                  <div key={i9} className="mt-0.5">
                    <span>{s9.icon}</span> <b className="text-[var(--text-primary)]">{t(s9.name, s9.name_en || s9.name)}</b>
                    <span className="ml-1 text-[var(--text-secondary)]">{t(s9.value, s9.value_en || s9.value)}</span>
                    <span className="ml-1 text-[var(--text-muted)]">→ {t(s9.verdict, s9.verdict_en || s9.verdict)}</span>
                  </div>
                ))}
                {(layers9.news?.length ?? 0) > 0 && (
                  <div className="mt-1 pt-1" style={{ borderTop: "1px dashed var(--border-default)" }}>
                    {layers9.news!.map((n9, i9) => (
                      <div key={i9} className="text-[11px]">
                        <b style={{ color: n9.stamp === "호재" ? RED : n9.stamp === "위험" ? BLUE : "var(--text-muted)" }}>[{t(n9.stamp, n9.stamp === "호재" ? "GOOD" : n9.stamp === "위험" ? "DANGER" : "NEUTRAL")}]</b>
                        <span className="ml-1 text-[var(--text-secondary)]">{n9.title.slice(0, 70)}</span>
                        {n9.why && <span className="ml-1 text-[var(--text-muted)]">— {n9.why.slice(0, 70)}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {sel && det?.chart && (() => {
              const ruleNm = ruleName(sel);
              const ptr = pick !== null ? det.trades[pick] : null;
              if (ptr && ptr.code === det.chart.code) {
                return (
                  <div className="mt-1 mx-1 px-2 py-1.5 rounded text-[11.5px] tabular-nums"
                    style={{ background: "rgba(230,81,0,0.07)", border: "1px solid var(--border-default)" }}>
                    <b style={{ color: "#e65100" }}>{ruleNm}</b>
                    <b className="ml-2 text-[var(--text-primary)]">{ptr.name}</b>
                    <span className="ml-2" style={{ color: RED }}>
                      ▲ {ptr.buy_t} {ptr.parts?.buys && ptr.parts.buys.length >= 2
                        ? "@" + ptr.parts.buys.map(([p2, q2]) => `₩${Math.round(p2).toLocaleString()}(${q2})`).join(" + ")
                        : `@₩${Math.round(ptr.entry).toLocaleString()}`}{ptr.wall ? " 🧱" : ""}</span>
                    <span className="ml-2" style={{ color: BLUE }}>
                      ▼ {ptr.sell_t} {ptr.parts?.sells && ptr.parts.sells.length >= 2
                        ? "@" + ptr.parts.sells.map(([p2, q2]) => `₩${Math.round(p2).toLocaleString()}(${q2})`).join(" + ")
                        : `@₩${Math.round(ptr.exit).toLocaleString()}`}</span>
                    <b className="ml-2" style={{ color: ptr.net_pct > 0 ? RED : ptr.net_pct < 0 ? BLUE : "var(--text-muted)" }}>
                      {ptr.net_pct > 0 ? "+" : ""}{ptr.net_pct}%</b>
                    {ptr.exit_why && <span className="ml-2 text-[var(--text-secondary)]">[{ptr.exit_why}]</span>}
                  </div>);
              }
              const h = det.holding.find((x) => x.code === det.chart?.code);
              if (h) {
                return (
                  <div className="mt-1 mx-1 px-2 py-1.5 rounded text-[11.5px] tabular-nums"
                    style={{ background: "rgba(230,81,0,0.07)", border: "1px solid var(--border-default)" }}>
                    <b style={{ color: "#e65100" }}>{ruleNm}</b>
                    <b className="ml-2 text-[var(--text-primary)]">{h.name}</b>
                    <span className="ml-2" style={{ color: RED }}>
                      ▲ {h.buy_t} @₩{Math.round(h.entry).toLocaleString()}</span>
                    <span className="ml-2" style={{ color: GOLD }}>
                      ● {t("보유 중", "holding")} ₩{Math.round(h.last).toLocaleString()}</span>
                    <b className="ml-2" style={{ color: h.unreal_pct > 0 ? RED : h.unreal_pct < 0 ? BLUE : "var(--text-muted)" }}>
                      {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%</b>
                    <span className="ml-2 text-[var(--text-secondary)]">
                      [{t("아직 매도 전 — 수수료 반영 전", "not sold yet - before fees")}]</span>
                  </div>);
              }
              return null;
            })()}
          </div>

          {/* holdings + per-trade table: on TODAY-LIVE these duplicated the 매매 내역
              below - same trades, GROSS here vs NET there, two numbers for one truth
              (boss 2026-08-12: "what are they"). Live keeps ONE list (the history
              below); this pair now serves only the stored-day / all-days drilldown. */}
          {ruleDay !== "" && (<>
          <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(230,81,0,0.05)" }}>
            <b style={{ color: GOLD }}>📌 {t("보유 중", "holding now")}</b>
            {det.holding.length === 0 ? (
              <span className="ml-2 text-[var(--text-muted)]">{t("0건 — 지금은 아무것도 들고 있지 않습니다", "0 - nothing open right now")}</span>
            ) : (
              <span className="ml-2 tabular-nums">
                {det.holding.map((h, i) => (
                  <span key={i} className="mr-4">
                    <b className="text-[var(--text-primary)]">{h.name}</b> ▲ {h.buy_t.slice(0, 5)} ₩{h.entry.toLocaleString()}
                    {" → "}₩{h.last.toLocaleString()}
                    <b className="ml-1" style={{ color: h.unreal_pct > 0 ? RED : h.unreal_pct < 0 ? BLUE : "var(--text-muted)" }}>
                      {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%
                    </b>
                  </span>
                ))}
              </span>
            )}
          </div>

          {/* the trades */}
          {repEp9 && <TradeReplay ep={repEp9} t={t} onClose={() => setRepEp9(null)} />}
          <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
            <table className="w-full text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "stock")}</th>
                <th className="text-left px-2">{t("매수 시각", "bought")}</th>
                <th className="text-right px-2">{t("매수가", "buy price")}</th>
                <th className="text-left px-2">{t("매도 시각", "sold")}</th>
                <th className="text-right px-2">{t("매도가", "sell price")}</th>
                {money && <th className="text-right px-2">{t("차이", "diff")}</th>}
                <th className="text-right px-2">{t("손익", "P&L")}</th>
                {/* the won-gain column obeys the 💰 button, same as the board total -
                    with money hidden it must not leak here (boss 2026-08-07) */}
                <th className="text-right px-2">{t("수량", "shares")}</th>
                {money && <th className="text-right px-2">{t("수수료 뺀 실수익", "actually gained")}</th>}
                <th className="text-right px-3">{t("결과", "result")}</th>
              </tr></thead>
              <tbody>
                {det.trades.map((tr, i) => {
                  const col = tr.result === "win" ? RED : tr.result === "loss" ? BLUE : "var(--text-muted)";
                  return (
                    <tr key={i} onClick={() => { const off2 = pick === i ? null : i;
                          // the rule this table belongs to, even if `sel` was cleared
                          // by a fold — a dead click here froze the chart on another
                          // company (boss 2026-08-27)
                          const rid = sel ?? lastReqRef.current?.id ?? null;
                          setPick(off2);
                          if (rid) { if (!sel) setSel(rid);
                                     openRule(rid, off2, off2 === null ? undefined : tr.code); }
                          // the chart sits ABOVE this table, so a click that only reloads
                          // it looks like nothing happened - put it on screen
                          if (off2 !== null) chartRef.current?.scrollIntoView(
                            { behavior: "smooth", block: "center" }); }}
                      className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                      style={{ background: pick === i ? "rgba(230,81,0,0.10)" : "transparent" }}>
                      <td className="px-3 py-1 font-bold text-[var(--text-primary)] cursor-pointer underline decoration-dotted"
                        title={t("클릭: 이 매매의 기록을 그대로 재생 (🎞 다시보기)", "click: replay this trade's recording (🎞)")}
                        onClick={(e) => { e.stopPropagation();
                          setRepEp9({ code: tr.code, name: tr.name, entry: tr.entry,
                            buy_t: tr.buy_t, sell_t: tr.sell_t, exit: tr.exit,
                            exit_why: (tr as unknown as { exit_why?: string }).exit_why,
                            qty: (tr as unknown as { qty?: number }).qty,
                            parts: (tr as unknown as { parts?: { buys?: unknown[][];
                              sells?: unknown[][] } }).parts }); }}>
                        🎞 {pick === i ? "▶ " : ""}{tr.name}
                        {tr.day && (
                          <span className="ml-1 text-[9px] font-bold px-1 py-0.5 rounded"
                            style={{ background: "rgba(230,81,0,0.12)", color: "#e65100" }}>{tr.day}</span>
                        )}</td>
                      <td className="px-2 cursor-pointer underline decoration-dotted" style={{ color: RED }}
                        title={t(`클릭하면 차트가 이 매수로 이동합니다 (${tr.buy_t})`, `click: chart jumps to this BUY (${tr.buy_t})`)}
                        onClick={(e) => { e.stopPropagation(); setFocusSide("b");
                                          const rid = sel ?? lastReqRef.current?.id ?? null;
                                          if (pick !== i || detRef.current?.chart?.code !== tr.code
                                              || (tr.d8 ?? "") !== detDayRef.current) {
                                            setPick(i);
                                            if (rid) { if (!sel) setSel(rid); openRule(rid, i, tr.code); }
                                          }
                                          if (chartOpenRef.current) chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>
                        ▲ {tr.buy_t.slice(0, 5)}
                        {tr.wall && <span title={t(
                          `호가벽 앞 매수: ${tr.wall.qty.toLocaleString()}주 벽(₩${tr.wall.price.toLocaleString()}) 바로 위 1틱에 주문`,
                          `bought one tick in front of a ${tr.wall.qty.toLocaleString()}-share wall at ₩${tr.wall.price.toLocaleString()}`)}> 🧱</span>}</td>
                      <td className="text-right px-2">₩{tr.entry.toLocaleString()}</td>
                      <td className="px-2 cursor-pointer underline decoration-dotted" style={{ color: BLUE }}
                        title={t(`클릭하면 차트가 이 매도로 이동합니다 (${tr.sell_t})`, `click: chart jumps to this SELL (${tr.sell_t})`)}
                        onClick={(e) => { e.stopPropagation(); setFocusSide("s");
                                          const rid = sel ?? lastReqRef.current?.id ?? null;
                                          if (pick !== i || detRef.current?.chart?.code !== tr.code
                                              || (tr.d8 ?? "") !== detDayRef.current) {
                                            setPick(i);
                                            if (rid) { if (!sel) setSel(rid); openRule(rid, i, tr.code); }
                                          }
                                          if (chartOpenRef.current) chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>
                        ▼ {tr.sell_t.slice(0, 5)}</td>
                      <td className="text-right px-2">₩{tr.exit.toLocaleString()}</td>
                      {money && (
                        <td className="text-right px-2" style={{ color: col }}>
                          {tr.exit - tr.entry > 0 ? "+" : ""}{(tr.exit - tr.entry).toLocaleString()}
                        </td>
                      )}
                      <td className="text-right px-2 font-bold" style={{ color: col }}>
                        {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                      </td>
                      {/* What the trade actually GAINED. The P&L beside it is gross, and on
                          a +0.3% target the 0.23% round trip eats three quarters of it - so
                          the gross figure is not the money (boss 2026-08-04). */}
                      <td className="text-right px-2 tabular-nums"
                          style={{ color: (tr.qty ?? 1) > 1 ? "#1565c0" : "var(--text-muted)" }}>
                          {(tr.qty ?? 1).toLocaleString()}
                        </td>
                      {money && (
                        <td className="text-right px-2 font-bold tabular-nums"
                          style={{ color: tr.net_pct > 0 ? RED : tr.net_pct < 0 ? BLUE : "var(--text-muted)" }}
                          title={t(`${(tr.qty ?? 1).toLocaleString()}주 x ₩${tr.entry.toLocaleString()} · 수수료 뺀 ${tr.net_pct}%`,
                                   `${(tr.qty ?? 1).toLocaleString()} shares x ₩${tr.entry.toLocaleString()} · ${tr.net_pct}% after the round trip`)}>
                          {won(Math.round(wonOf(tr.entry, tr.net_pct) * (tr.qty ?? 1)))}
                        </td>
                      )}
                      <td className="text-right px-3 font-bold" style={{ color: col }}>
                        {tr.result === "win" ? t("승", "WIN") : tr.result === "loss" ? t("패", "LOSS") : t("무", "flat")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          </>)}

          {/* (5) 🕰️ THE DATA FILE - the minute record every rule reads. A trade is only
              believable if the price it claims can be found in the minute it claims, at a
              price that really printed. The artificial lab has had this; the real desk had
              nothing to check a fill against (boss 2026-08-04). */}
          {df && (
            <div className="px-4 py-3 border-t" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.03)" }}>
              <div className="flex items-center gap-2 flex-wrap">
                <b className="text-[12.5px]" style={{ color: GOLD }}>🕰️ {t("데이터 파일", "Data File")} — {df.name}</b>
                <span className="text-[10.5px] text-[var(--text-muted)]">
                  {t(`${df.rows.length}분 보는 중 (수집된 ${df.total_minutes}분 중) · 한 줄을 누르면 그 1분의 체결이 전부 나옵니다`,
                     `showing ${df.rows.length} of ${df.total_minutes} minutes collected · click a row for every deal in that minute`)}
                </span>
                {([5, 10, 15] as const).map((m) => (
                  <button key={m} onClick={() => { setDfMins(m); setDfFrom(""); setDfTo("");
                                                   loadDf(df.code, m); }}
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                    style={dfMins === m && !dfFrom && !dfTo
                             ? { borderColor: GOLD, color: GOLD, background: "rgba(230,81,0,0.10)" }
                             : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {t(`최근 ${m}분`, `last ${m} min`)}
                  </button>
                ))}
                <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("구간", "range")}:</span>
                <input value={dfFrom} onChange={(e) => setDfFrom(e.target.value)} placeholder="--:--"
                  className="w-[62px] text-[10.5px] px-1 py-0.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--border-default)" }} />
                <span className="text-[10px] text-[var(--text-muted)]">→</span>
                <input value={dfTo} onChange={(e) => setDfTo(e.target.value)} placeholder="--:--"
                  className="w-[62px] text-[10.5px] px-1 py-0.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--border-default)" }} />
                <button onClick={() => loadDf(df.code, dfMins, dfFrom, dfTo)}
                  className="text-[10.5px] font-bold px-2 py-0.5 rounded-md text-white"
                  style={{ background: "#455a64" }}>
                  {t("적용", "apply")}
                </button>
                {(dfFrom || dfTo) && (
                  <button onClick={() => { setDfFrom(""); setDfTo(""); loadDf(df.code, dfMins); }}
                    className="text-[10px] px-1.5 py-0.5 rounded border"
                    style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {t("구간 해제", "clear")}
                  </button>
                )}
              </div>
              <div className="mt-2 rounded-lg border overflow-y-auto" style={{ borderColor: "var(--border-default)", maxHeight: 320 }}>
                <table className="w-full text-[11.5px] tabular-nums">
                  <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                    <th className="text-left px-3 py-1">{t("분", "minute")}</th>
                    <th className="text-right px-2">{t("시가", "open")}</th>
                    <th className="text-right px-2">{t("종가", "close")}</th>
                    <th className="text-right px-2">{t("차이", "difference")}</th>
                    <th className="text-right px-2">{t("체결 수", "deals")}</th>
                    <th className="text-center px-3">{t("판정", "verdict")}</th>
                  </tr></thead>
                  <tbody>
                    {df.rows.map((r) => {
                      const col = r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-muted)";
                      const on = dfOpen === r.hhmm.slice(0, 5);
                      return (
                        <React.Fragment key={r.key}>
                          <tr onClick={() => openMinute(df.code, r.hhmm)}
                            className="border-t border-[var(--border-default)]/30 cursor-pointer hover:bg-[var(--bg-elevated)]"
                            style={{ background: on ? "rgba(230,81,0,0.08)" : "transparent" }}>
                            {/* the minute still running is shown - a trade that just
                                executed must be checkable at once - but never as settled */}
                            <td className="px-3 py-[2px] font-bold text-[var(--text-primary)]">
                              {r.forming ? "⏳ " : on ? "▾ " : "▸ "}{r.hhmm}
                              {r.forming && <span className="ml-1 text-[9.5px] font-normal" style={{ color: GOLD }}>{t("진행 중", "running")}</span>}
                            </td>
                            <td className="text-right px-2">₩{r.open.toLocaleString()}</td>
                            <td className="text-right px-2 font-extrabold" style={{ color: col }}>₩{r.close.toLocaleString()}</td>
                            <td className="text-right px-2 font-bold" style={{ color: col }}>
                              {r.diff === 0 ? "0" : `${r.diff > 0 ? "+" : "\u2212"}\u20a9${Math.abs(r.diff).toLocaleString()}`}
                            </td>
                            <td className="text-right px-2 text-[var(--text-muted)]">{r.deal_count.toLocaleString()}</td>
                            <td className="text-center px-3 font-bold" style={{ color: col }}>
                              {r.dir > 0 ? t("🔴▲ 상승", "🔴▲ rise") : r.dir < 0 ? t("🔵▼ 하락", "🔵▼ fall") : t("⚪ 보합", "⚪ flat")}
                            </td>
                          </tr>
                          {on && (
                            <tr>
                              <td colSpan={6} className="px-5 py-2" style={{ background: "rgba(128,128,128,0.05)" }}>
                                {dfMin && dfMin.key === r.key ? (
                                  <>
                                    <div className="text-[10px] font-bold text-[var(--text-muted)] mb-1">
                                      🎬 {t(`${dfMin.hhmm} 의 체결 전부 — ${dfMin.deal_count}건 · 한 초에 여러 건이 찍힙니다`,
                                             `every execution in ${dfMin.hhmm} — ${dfMin.deal_count} of them · several print within one second`)}
                                    </div>
                                    <div className="overflow-y-auto" style={{ maxHeight: 190 }}>
                                      {dfMin.seconds.map((sec) => (
                                        <div key={sec.t} className="flex gap-2 items-start text-[10.5px] py-[1px]">
                                          <span className="text-[var(--text-muted)] w-16 shrink-0">{sec.t.slice(3)}</span>
                                          <span className="flex flex-wrap gap-1">
                                            {sec.deals.map((x, j) => (
                                              <span key={j} className="px-1 rounded" style={{ background: "rgba(128,128,128,0.10)" }}>
                                                ₩{x.px.toLocaleString()}
                                                <span className="text-[9px] text-[var(--text-muted)] ml-[2px]">×{x.qty.toLocaleString()}</span>
                                              </span>
                                            ))}
                                          </span>
                                        </div>
                                      ))}
                                    </div>
                                  </>
                                ) : (
                                  <span className="text-[10.5px] text-[var(--text-muted)]">{t("불러오는 중…", "loading…")}</span>
                                )}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* the evidence for one trade */}
          {pick !== null && det.trades[pick] && (() => {
            const tr = det.trades[pick];
            const n = det.entry_n;
            const down = det.dir < 0;
            const exitTxt = det.kind === "candle"
              ? t(`${det.a}연속 ${down ? "상승" : "하락"}`, `${det.a} ${down ? "rising" : "falling"} bars in a row`)
              : t(`+${det.a}% 익절 또는 -${det.b}% 손절`, `+${det.a}% take or -${det.b}% stop`);
            const Side = ({ ev, side }: { ev?: Ev | null; side: "BUY" | "SELL" }) => {
              if (!ev?.book) return null;
              return (
                <div className="rounded-lg border p-2" style={{ borderColor: "var(--border-default)" }}>
                  <div className="text-[10px] font-bold mb-1" style={{ color: side === "BUY" ? RED : BLUE }}>
                    {side === "BUY" ? t("① 매수 체결가가 이렇게 정해졌습니다", "① how the buy price was set")
                                    : t("② 매도 체결가가 이렇게 정해졌습니다", "② how the sell price was set")}
                  </div>
                  <div className="text-[10.5px] tabular-nums space-y-[1px]">
                    <div>{t("이 봉의 종가", "this bar's close")}: <b>₩{ev.close.toLocaleString()}</b></div>
                    <div>{t("호가 단위", "tick size")}: ₩{ev.book.spread.toLocaleString()}</div>
                    <div className="pt-1 font-bold" style={{ color: side === "BUY" ? RED : BLUE }}>
                      {side === "BUY"
                        ? t(`→ 살 때는 매도호가를 쳐야 하므로 종가+1호가 = ₩${ev.book.fill.toLocaleString()}`,
                            `→ a buy lifts the ask, so close + one tick = ₩${ev.book.fill.toLocaleString()}`)
                        : t(`→ 팔 때는 매수호가를 받으므로 = ₩${ev.book.fill.toLocaleString()}`,
                            `→ a sell hits the bid = ₩${ev.book.fill.toLocaleString()}`)}
                    </div>
                  </div>
                </div>
              );
            };
            return (
              <div className="px-4 py-3 border-t" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.04)" }}>
                <b className="text-[12.5px]" style={{ color: GOLD }}>🔍 {tr.name} — {tr.buy_t} → {tr.sell_t}</b>
                <div className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
                  <b className="text-[var(--text-primary)]">{t("이 규칙이 하는 일", "what this rule does")}: </b>
                  {t(`${det.clock} 봉을 하나씩 보다가, 종가가 앞의 봉보다 ${n}번 연속 ${down ? "내렸으면" : "올랐으면"} 그 ${n}번째 봉에서 삽니다. 그 뒤 ${exitTxt}이면 팝니다.`,
                     `it reads ${det.clock} bars one by one; when the close ${down ? "falls below" : "rises above"} the previous bar ${n} times in a row it buys on that ${n}th bar, then sells on ${exitTxt}.`)}
                </div>
                <div className="mt-1 text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("매수 근거", "why it bought")}: </b>
                  <span style={{ color: RED }}>{(tr.buy_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}</span>
                  <span className="text-[10.5px] text-[var(--text-muted)] ml-1">
                    {t(`(${n}번 연속 ${down ? "하락" : "상승"} — 마지막이 ${n}번째)`, `(${n} in a row - the last is the ${n}th)`)}
                  </span>
                </div>
                <div className="text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("매도 근거", "why it sold")}: </b>
                  <span style={{ color: BLUE }}>{tr.exit_why || "-"}</span>
                  <span className="ml-1">{(tr.sell_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}</span>
                </div>
                {tr.ml && (
                  <div className="mt-2 rounded-lg border p-2" style={{ borderColor: "#1565c0", background: "rgba(21,101,192,0.05)" }}>
                    <div className="text-[10px] font-bold mb-1" style={{ color: "#1565c0" }}>
                      🤖 {t("모델의 결정 — 매수 신호가 뜬 순간", "the model's decision - at the moment the signal fired")}
                    </div>
                    <div className="text-[11px] leading-relaxed space-y-[2px]">
                      <div>
                        <b>{t("질문", "question")}: </b>
                        {det.kind === "candle"
                          ? t(`지금 사서 ${det.a}연속 ${det.dir < 0 ? "상승" : "하락"}에 팔면, 수수료를 빼고도 이익일까?`,
                              `if we buy now and sell at ${det.a} ${det.dir < 0 ? "rising" : "falling"} bars in a row, do we gain after fees?`)
                          : t(`+${det.a}% 익절이 -${det.b}% 손절보다 먼저 올까?`,
                              `will the +${det.a}% take arrive before the -${det.b}% stop?`)}
                      </div>
                      <div className="tabular-nums">
                        <b>{t("모델의 답", "the model's answer")}: </b>
                        {t(`이익으로 끝날 확률 ${(tr.ml.p * 100).toFixed(1)}% — 이 규칙의 통과 기준 ${(tr.ml.bar * 100).toFixed(1)}% 이상`,
                           `${(tr.ml.p * 100).toFixed(1)}% chance this trade ends in profit — above this rule's own bar of ${(tr.ml.bar * 100).toFixed(1)}%`)}
                        <b style={{ color: RED }}> → {t("매수", "BUY")}</b>
                      </div>
                      <div className="tabular-nums">
                        <b>{t("수량", "shares")}: </b>
                        {t(`확신에 비례해 ${(tr.ml.qty ?? tr.qty ?? 1).toLocaleString()}주 (확률이 높을수록 많이)`,
                           `${(tr.ml.qty ?? tr.qty ?? 1).toLocaleString()} shares, scaled by that confidence`)}
                      </div>
                      <div>
                        <b>{t("사후 판정", "verdict")}: </b>
                        {tr.result === "win"
                          ? <span style={{ color: RED }}>{t("모델이 맞았습니다 — 이 매매는 이익으로 끝났습니다.", "the model was right - this trade ended in profit.")}</span>
                          : tr.result === "loss"
                          ? <span style={{ color: BLUE }}>{t(`모델이 틀렸습니다 — ${(tr.ml.p * 100).toFixed(0)}%는 확신이지 보장이 아닙니다. 이런 틀림도 기록에 그대로 남습니다.`,
                                                            `the model was wrong - ${(tr.ml.p * 100).toFixed(0)}% is confidence, not a guarantee. Wrong calls stay on the record.`)}</span>
                          : <span className="text-[var(--text-muted)]">{t("본전 — 맞음도 틀림도 아닙니다.", "flat - neither right nor wrong.")}</span>}
                      </div>
                      <div className="text-[9.5px] text-[var(--text-muted)]">
                        {t("모델은 매수만 결정합니다 — 매도는 항상 규칙이 합니다. 모델이 거른 신호는 이 표에 없고, 같은 이름의 ML 없는 행에 있습니다.",
                           "the model only decides the buy - selling is always the rule. Signals it skipped are not here; see the same rule without ML.")}
                      </div>
                    </div>
                  </div>
                )}
                {!evOpen ? (
                  <button onClick={() => setEvOpen(true)}
                    className="mt-2 text-[10.5px] px-2 py-0.5 rounded border"
                    style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    📖 {t("호가 근거 보기 (체결가가 정해진 과정)", "show the book evidence (how the fills were set)")}
                  </button>
                ) : (
                  <div className="mt-2 grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
                    <Side ev={tr.buy_ev} side="BUY" />
                    <Side ev={tr.sell_ev} side="SELL" />
                  </div>
                )}
                <div className="mt-2 text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("결과", "result")}: </b>
                  ₩{tr.entry.toLocaleString()} → ₩{tr.exit.toLocaleString()}
                  <b className="ml-1" style={{ color: tr.result === "win" ? RED : tr.result === "loss" ? BLUE : "var(--text-muted)" }}>
                    {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                  </b>
                  <span className="text-[10.5px] text-[var(--text-muted)] ml-2">
                    {t(`수수료 0.23%를 빼면 ${tr.net_pct > 0 ? "+" : ""}${tr.net_pct}% · ${tr.bars_held}봉 보유`,
                       `after the 0.23% round trip: ${tr.net_pct > 0 ? "+" : ""}${tr.net_pct}% · held ${tr.bars_held} bars`)}
                  </span>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* stock + clock */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {/* Two dropdowns instead of 3 stock buttons + 10 clock buttons (boss 2026-08-06:
            "it is making confussion"). Same shared tick/period state as before. */}
        <span className="text-[10.5px] text-[var(--text-muted)]">{t("종목", "stock")}</span>
        <select value={code}
          onChange={(e) => { const c2 = e.target.value;
                             // THE dropdown the boss actually uses (found
                             // 2026-08-20 after three fixes elsewhere): it only
                             // switched the tape, and an open rule detail's
                             // chart WINS over the tape - so with SK텔레콤's
                             // detail open, picking SK하이닉스 here changed
                             // nothing, ever. It now re-points the open detail
                             // too, same as every other stock click.
                             setCode(c2); codeRef.current = c2; pull();
                             if (sel) openRule(sel, null, c2); }}
          className="text-[12px] font-extrabold px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)]"
          style={{ borderColor: TEAL, color: TEAL }}>
          {(tabStocks.length ? tabStocks : [{ code: "005930", name: "삼성전자", ticks: 0 }]).map((x) => (
            <option key={x.code} value={x.code}>{x.name}</option>
          ))}
          {(dpick?.rows ?? []).filter((r) => !watchSet9.has(r.code)).map((r) => (
            <option key={r.code} value={r.code}>{r.name} (일봉)</option>
          ))}
        </select>
        <input list="all-stocks-9" value={stockQ9} placeholder={t("종목 검색…", "search stock…")}
          onChange={(e) => {
            const v = e.target.value; setStockQ9(v);
            const all9 = [...(st?.stocks ?? []), ...((dpick?.rows ?? []) as { code: string; name: string }[])];
            const hit = matchStock9(v, all9);
            if (hit) { setCode(hit.code); codeRef.current = hit.code; pull();
                       setChartOpen9(true); setStockQ9(""); }
          }}
          className="text-[11px] px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] w-[110px]"
          style={{ borderColor: TEAL, color: "var(--text-primary)" }} />
        <datalist id="all-stocks-9">
          {[...(st?.stocks ?? []), ...((dpick?.rows ?? []) as { code: string; name: string }[])]
            .filter((x, i, a) => a.findIndex((y) => y.code === x.code) === i)
            .map((x) => <option key={x.code} value={x.name} />)}
        </datalist>
        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">{t("캔들", "candle")}</span>
        <select value={period ? `p${period}` : `t${tick}`}
          onChange={(e) => { const val = e.target.value;
                             if (val[0] === "t") { const n = Number(val.slice(1));
                               setTick(n); setPeriod(0); perRef.current = 0; tickRef.current = n; setClockIn("");
                             } else { const n = Number(val.slice(1));
                               setPeriod(n); perRef.current = n; setClockIn(String(n)); }
                             pull(); }}
          className="text-[11.5px] font-bold px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)]"
          style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
          {[1, 5, 10, 30].map((n) => (
            <option key={"t" + n} value={`t${n}`}>{n}{t("틱", "-tick")}</option>
          ))}
          {[3, 6, 15, 30, 40, 60].map((n) => (
            <option key={"s" + n} value={`p${n}`}>{n === 60 ? t("1분", "1-min") : `${n}${t("초", "s")}`}</option>
          ))}
        </select>
        <input value={clockIn} onChange={(e) => setClockIn(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            const n = Math.max(0, Math.min(600, parseInt(clockIn, 10) || 0));
            if (!n) return;
            setPeriod(n); perRef.current = n; pull();
          }}
          placeholder={t("초", "sec")}
          title={t("초 단위 캔들 — 숫자를 쓰고 Enter", "candle size in seconds - type a number and press Enter")}
          className="w-[54px] text-[11.5px] px-2 py-1 rounded-lg border bg-transparent"
          style={{ borderColor: "var(--border-default)" }} />
      </div>

      {/* price header — quote card for non-watch stocks (Kiwoom-style:
          현재가/등락/시가/고가/저가, 5s live) */}
      {!isWatch9 && quote9?.price && (
        <div className="mt-3 flex items-baseline gap-3 flex-wrap tabular-nums">
          <b className="text-[22px]" style={{ color: (quote9.change_pct ?? 0) > 0 ? RED : (quote9.change_pct ?? 0) < 0 ? BLUE : "var(--text-primary)" }}>
            ₩{Math.round(quote9.price).toLocaleString()}
          </b>
          <span className="text-[13px] font-bold" style={{ color: (quote9.change_pct ?? 0) > 0 ? RED : BLUE }}>
            {(quote9.change_pct ?? 0) > 0 ? "▲" : "▼"} {Math.abs(quote9.change_pct ?? 0).toFixed(2)}%
          </span>
          {quote9.open ? <span className="text-[11.5px] text-[var(--text-muted)]">{t("시가", "open")} ₩{Math.round(quote9.open).toLocaleString()}</span> : null}
          {quote9.high ? <span className="text-[11.5px]" style={{ color: RED }}>{t("고가", "high")} ₩{Math.round(quote9.high).toLocaleString()}</span> : null}
          {quote9.low ? <span className="text-[11.5px]" style={{ color: BLUE }}>{t("저가", "low")} ₩{Math.round(quote9.low).toLocaleString()}</span> : null}
          {dbars9.length ? <span className="text-[11.5px] text-[var(--text-muted)]">{t("전일 거래량", "prev-day vol")} {Math.round(dbars9[dbars9.length - 1].vol).toLocaleString()}</span> : null}
          <span className="text-[10px] text-[var(--text-muted)]">
            {t("이 종목은 분봉 수집 대상이 아니라 일봉 차트 + 5초 실시간 시세로 보여드립니다", "not a tape-collected stock: daily candles + 5s live quote")}</span>
        </div>
      )}
      {isWatch9 && book?.ok && (
        <div className="mt-3 flex items-baseline gap-3 flex-wrap tabular-nums">
          <b className="text-[22px]" style={{ color: (book.change_pct ?? 0) > 0 ? RED : (book.change_pct ?? 0) < 0 ? BLUE : "var(--text-primary)" }}>
            ₩{fmt(book.last)}
          </b>
          <span className="text-[13px] font-bold" style={{ color: (book.change_pct ?? 0) > 0 ? RED : BLUE }}>
            {(book.change_pct ?? 0) > 0 ? "▲" : "▼"} {Math.abs(book.change_pct ?? 0).toFixed(2)}%
          </span>
          <span className="text-[11.5px] text-[var(--text-muted)]">
            {t("전일종가", "prev close")} ₩{fmt(book.prev_close)}
          </span>
          <span className="text-[11.5px] text-[var(--text-muted)]">
            {t("호가 간격", "spread")} ₩{fmt((book.best_ask ?? 0) - (book.best_bid ?? 0))}
          </span>
        </div>
      )}

      {/* THE STANDING MARKET CHART, between the rules and the order book. The only chart
          on the page lived inside a rule's drill-down, so with nothing open the page
          jumped from the ranking straight to the 호가 - and the boss wants to WATCH the
          book move against the chart (2026-08-05: "I wanna monitor changings in the
          order book how effecting to the chart"). This one is always here, always the
          live tape, and refreshes on the same 2-3s pull as the book below it. */}
      {!chartOn9 ? (
        <div className="mt-3 w-full rounded-xl border px-4 py-2.5 text-[11.5px] flex items-center gap-2 flex-wrap"
          style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
          <span className="cursor-pointer" onClick={() => setChartOpen9(true)}>
            📈 {t("실시간 차트 접혀 있음 — 눌러서 열기", "live chart folded - press to open")}</span>
          <button onClick={() => { setChartOpen9(true); setFsMkt9(true); }}
            className="text-[10.5px] font-bold px-2 py-0.5 rounded border"
            style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
            ⛶ {t("전체화면으로 열기", "open FULL SCREEN")}</button>
        </div>
      ) : (
      <div className={fsMkt9 ? "p-3" : "mt-3 rounded-xl border p-2"}
        style={fsMkt9
          ? { position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
              zIndex: 9999, overflow: "auto",
              // SOLID ground - nothing from the page may show through
              // (boss 2026-08-28: "the background is showing other things
              // like tables - not appropriate to see")
              background: "var(--bg-primary, #ffffff)" }
          : { borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
        <div className="px-2 pt-1 pb-2 text-[11.5px] flex items-center gap-2 flex-wrap" style={{ color: "#6a1b9a" }}>
          <b>📈 {tape?.name ?? ""} — {tape?.clock ?? ""} {t("실시간 차트", "live chart")}</b>
          <button onClick={() => setFsMkt9((v) => !v)}
            className="text-[10.5px] font-bold px-2 py-0.5 rounded border"
            style={{ borderColor: "#6a1b9a", color: fsMkt9 ? "#fff" : "#6a1b9a",
                     background: fsMkt9 ? "#6a1b9a" : "transparent" }}>
            {fsMkt9 ? t("⛶ 전체화면 닫기", "⛶ exit full screen") : t("⛶ 전체화면", "⛶ full screen")}</button>
          {/* IN-FULLSCREEN stock switch (boss 2026-08-28: "in full screen we
              have no chance to change the stock - add a dropdown and search
              bar so whatever stock we wanna watch we could see") */}
          {fsMkt9 && (<>
            <select value={code}
              onChange={(e) => { const c2 = e.target.value;
                                 setCode(c2); codeRef.current = c2; pull();
                                 if (sel) openRule(sel, null, c2); }}
              className="text-[12px] font-extrabold px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)]"
              style={{ borderColor: TEAL, color: TEAL }}>
              {(tabStocks.length ? tabStocks : [{ code: "005930", name: "삼성전자", ticks: 0 }]).map((x) => (
                <option key={x.code} value={x.code}>{x.name}</option>
              ))}
              {(dpick?.rows ?? []).filter((r) => !watchSet9.has(r.code)).map((r) => (
                <option key={r.code} value={r.code}>{r.name} (일봉)</option>
              ))}
            </select>
            <input list="all-stocks-9" value={stockQ9} placeholder={t("종목 검색…", "search stock…")}
              onChange={(e) => {
                const v = e.target.value; setStockQ9(v);
                const all9 = [...(st?.stocks ?? []), ...((dpick?.rows ?? []) as { code: string; name: string }[])];
                const hit = matchStock9(v, all9);
                if (hit) { setCode(hit.code); codeRef.current = hit.code; pull(); setStockQ9(""); }
              }}
              className="text-[11px] px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] w-[120px]"
              style={{ borderColor: TEAL, color: "var(--text-primary)" }} />
          </>)}
          <button onClick={() => { setChartOpen9(false); setPick(null); }}
            className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
            style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
            {t("차트 접기 ▲", "fold chart ▲")}
          </button>
          <span className="text-[10px] text-[var(--text-muted)]">
            {bars.length
              ? t(`${bars[0]?.hhmm?.slice(0, 5)}~${bars[bars.length - 1]?.hhmm?.slice(0, 5)} 구간 · ${bars.length}봉 보는 중 (하루 전체 ${(tape?.total_bars ?? bars.length).toLocaleString()}봉)`,
                  `showing ${bars[0]?.hhmm?.slice(0, 5)}~${bars[bars.length - 1]?.hhmm?.slice(0, 5)} · ${bars.length} of ${(tape?.total_bars ?? bars.length).toLocaleString()} bars today`)
              : t("아직 봉이 없습니다", "no bars yet")}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">
            {t(`과거 ${hist9.length ? Math.max(1, (hist9.filter((b) => b.hhmm.includes("/")).length)) : 0}일 + 오늘 이어짐 — 왼쪽으로 스크롤/휠 줌으로 과거 이동`,
               `${hist9.length ? hist9.filter((b) => b.hhmm.includes("/")).length : 0} past days + today, one chart - scroll left / wheel-zoom into history`)}</span>
          {hist9.length > 0 && bars.length > 0 && (() => {
            const pc9 = hist9[hist9.length - 1].close;
            const op9 = bars[0].open;
            const g9 = (op9 / pc9 - 1) * 100;
            return <b className="text-[11px] tabular-nums" style={{ color: g9 >= 1.5 ? "#b71c1c" : "var(--text-secondary)" }}>
              {t(`어제 종가 ₩${Math.round(pc9).toLocaleString()} → 오늘 시가 ₩${Math.round(op9).toLocaleString()} = 갭 ${g9 >= 0 ? "+" : ""}${g9.toFixed(2)}%${g9 >= 1.5 ? " ⛔ 갭상승" : g9 <= -1.5 ? " 갭하락" : ""}`,
                    `prev close ₩${Math.round(pc9).toLocaleString()} → open ₩${Math.round(op9).toLocaleString()} = gap ${g9 >= 0 ? "+" : ""}${g9.toFixed(2)}%${g9 >= 1.5 ? " ⛔" : ""}`)}</b>;
          })()}
          {book && (
            <span className="ml-auto text-[10.5px] tabular-nums">
              <span style={{ color: RED }}>{t("매도호가", "ask")} ₩{fmt(book.best_ask)}</span>
              <span className="mx-1 text-[var(--text-muted)]">|</span>
              <span style={{ color: BLUE }}>{t("매수호가", "bid")} ₩{fmt(book.best_bid)}</span>
            </span>
          )}
        </div>
        {(!isWatch9 && dbars9.length) || bars.length || hist9.length
          ? <LiveChart key={`mkt-${code}-${tick}-${period}-h${hist9.length}-d${dbars9.length}-${fsMkt9 ? "fs" : "n"}`}
                                  off={0}
                                  bars={!isWatch9 && dbars9.length ? dbars9
                                        : [...hist9, ...bars.map((b) => ({ ...b,
                                    d8: new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul" })
                                      .format(new Date()).replace(/-/g, "") }))]}
                                  focus={isWatch9 && hist9.length && bars.length ? hist9.length : null}
                                  h={fsMkt9 ? (typeof window !== "undefined" ? window.innerHeight - 130 : 600) : 320} /> : (
          <div className="px-4 py-8 text-center text-[12px] text-[var(--text-muted)]">
            {st?.market_open
              ? t("수집 중입니다 — 잠시 뒤 봉이 그려집니다.", "collecting - bars appear shortly.")
              : t("장이 닫혀 있습니다 (09:00~15:30).", "market closed (09:00-15:30).")}
          </div>
        )}
      </div>
      )}

      <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
        {/* 호가 — who is waiting to buy and to sell */}
        <div className="rounded-xl border overflow-hidden" style={{ borderColor: TEAL }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: TEAL }}>
              📗 {t("실시간 호가 — 사려는 사람과 팔려는 사람", "live order book - buyers and sellers waiting")}
            </b>
            <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
              {t("사면 가장 싼 매도호가를, 팔면 가장 비싼 매수호가를 잡습니다 — 그 차이가 왕복 비용의 절반입니다.",
                 "a buy takes the cheapest ask, a sell takes the highest bid - that gap is half the round-trip cost.")}
            </div>
          </div>
          <table className="w-full text-[11.5px] tabular-nums">
            <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
              <th className="text-right px-3 py-1">{t("잔량", "qty")}</th>
              <th className="text-center px-2">{t("호가", "price")}</th>
              <th className="text-left px-3">{t("잔량", "qty")}</th>
            </tr></thead>
            <tbody>
              {(book?.asks ?? []).slice().reverse().map(([p, q], i) => (
                <tr key={"a" + i} className="border-t border-[var(--border-default)]/30">
                  <td className="text-right px-3 py-[2px]" style={{ color: BLUE }}>{fmt(q)}</td>
                  <td className="text-center px-2 font-bold" style={{ color: BLUE }}>
                    ₩{fmt(p)}{p === book?.best_ask && <span className="text-[9px]"> {t("← 매수 체결", "← buy fills here")}</span>}
                  </td>
                  <td />
                </tr>
              ))}
              {(book?.bids ?? []).map(([p, q], i) => (
                <tr key={"b" + i} className="border-t border-[var(--border-default)]/30">
                  <td />
                  <td className="text-center px-2 font-bold" style={{ color: RED }}>
                    ₩{fmt(p)}{p === book?.best_bid && <span className="text-[9px]"> {t("← 매도 체결", "← sell fills here")}</span>}
                  </td>
                  <td className="text-left px-3 py-[2px]" style={{ color: RED }}>{fmt(q)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 체결 — the deals themselves, with their time */}
        <div className="rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: GOLD }}>
              📼 {t("실시간 체결 — 체결 시각과 가격", "live executions - deal time and price")}
            </b>
            <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
              {t(`같은 초에 여러 건이 찍힙니다. 위 차트의 봉은 바로 이 체결들을 묶은 것입니다 — 지금까지 ${fmt(execs?.total)}건 수집.`,
                 `several print within one second. The bars above are these very executions grouped - ${fmt(execs?.total)} collected so far.`)}
            </div>
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
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
          </div>
        </div>
      </div>

      {/* THE HISTORICAL RECORD (boss 2026-08-11): the daily table the agent actually
          reads - open/high/low/close, volume, and who was buying (foreigners,
          institutions) - straight from raw_daily_prices and korean_investor_flows.
          Closed by default; fetches only when opened. */}
      <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
        <div className="px-4 py-2 flex items-center gap-2 flex-wrap cursor-pointer"
          style={{ background: "rgba(106,27,154,0.06)" }}
          onClick={() => { const nx = !histOpen; setHistOpen(nx);
                           if (nx && !rawCode) setRawCode((dpick?.picks || [])[0] || "005930"); }}>
          <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
            📚 {t("과거 기록 — 일봉·거래량·수급", "historical record - daily candles, volume, flows")}
          </b>
          <span className="text-[10px] text-[var(--text-muted)]">
            {t("에이전트가 기억하는 원자료: 시가·고가·저가·종가 · 거래량 · 외국인/기관 순매수",
               "the raw rows the agent remembers: open/high/low/close, volume, foreign & institutional net buying")}
          </span>
          <span className="ml-auto text-[10.5px]" style={{ color: "#6a1b9a" }}>
            {histOpen ? t("닫기 ▲", "close ▲") : t("열기 ▼", "open ▼")}
          </span>
        </div>
        {histOpen && (
          <div className="px-4 py-2">
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <select value={rawCode} onChange={(e) => setRawCode(e.target.value)}
                className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                {(dpick?.rows || []).filter((r) => r.on_desk).map((r) => (
                  <option key={r.code} value={r.code} style={{ color: "#000" }}>{r.name}</option>
                ))}
              </select>
              <select value={rawDays} onChange={(e) => setRawDays(Number(e.target.value))}
                className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                {[5, 10, 20, 60, 120, 250].map((d) => (
                  <option key={d} value={d} style={{ color: "#000" }}>{t(`최근 ${d}일`, `last ${d} days`)}</option>
                ))}
              </select>
              {!raw && rawCode && (
                <span className="text-[10.5px] font-bold" style={{ color: "#e65100" }}>
                  ⏳ {t("불러오는 중…", "Loading…")}
                </span>
              )}
            </div>
            {raw && (
              <div className="overflow-x-auto">
                <div className="text-[11.5px] font-bold mb-1" style={{ color: "#6a1b9a" }}>
                  📅 {t(`일별 기록 — ${raw.name} · 하루 한 줄 (최대 250일)`,
                        `daily table — ${raw.name} · one row per day (up to 250 days)`)}
                </div>
                <table className="text-[11px] tabular-nums w-full">
                  <thead><tr className="text-[10px] text-[var(--text-muted)]">
                    <th className="text-left px-2 py-1">{t("날짜", "date")}</th>
                    <th className="text-right px-2">{t("시가", "open")}</th>
                    <th className="text-right px-2">{t("고가(최대)", "high (max)")}</th>
                    <th className="text-right px-2">{t("저가(최소)", "low (min)")}</th>
                    <th className="text-right px-2">{t("종가", "close")}</th>
                    <th className="text-right px-2">{t("등락", "chg")}</th>
                    <th className="text-right px-2">{t("거래량", "volume")}</th>
                    <th className="text-right px-3">{t("외국인", "foreign")}</th>
                    <th className="text-right px-2">{t("기관", "inst")}</th>
                  </tr></thead>
                  <tbody>
                    {[...raw.rows].reverse().map((r) => {
                      const f = raw.flows.find((x) => x.date === r.date);
                      return (
                        <tr key={r.date} className="border-t border-[var(--border-default)]/30">
                          <td className="px-2 py-0.5 text-[var(--text-secondary)]">{r.date}</td>
                          <td className="text-right px-2">{Math.round(r.open).toLocaleString()}</td>
                          <td className="text-right px-2" style={{ color: RED }}>{Math.round(r.high).toLocaleString()}</td>
                          <td className="text-right px-2" style={{ color: BLUE }}>{Math.round(r.low).toLocaleString()}</td>
                          <td className="text-right px-2 font-bold">{Math.round(r.close).toLocaleString()}</td>
                          <td className="text-right px-2 font-bold"
                            style={{ color: (r.chg ?? 0) > 0 ? RED : (r.chg ?? 0) < 0 ? BLUE : "var(--text-muted)" }}>
                            {r.chg == null ? "-" : `${r.chg > 0 ? "+" : ""}${r.chg}%`}</td>
                          <td className="text-right px-2 text-[var(--text-secondary)]">{Math.round(r.volume).toLocaleString()}</td>
                          <td className="text-right px-3"
                            style={{ color: f ? (f.foreign > 0 ? RED : BLUE) : "var(--text-muted)" }}>
                            {f ? `${f.foreign > 0 ? "+" : ""}${Math.round(f.foreign / 1e8).toLocaleString()}억` : "—"}</td>
                          <td className="text-right px-2"
                            style={{ color: f ? (f.inst > 0 ? RED : BLUE) : "var(--text-muted)" }}>
                            {f ? `${f.inst > 0 ? "+" : ""}${Math.round(f.inst / 1e8).toLocaleString()}억` : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {drillDays.length > 0 && (
                  <div className="mt-2 text-[10.5px]">
                    <b style={{ color: "#6a1b9a" }}>
                      🔎 {t("초 단위까지 파고들기 — 기록된 날만 가능:", "drill to the second - recorded days only:")}
                    </b>
                    {drillDays.map((d2) => (
                      <button key={d2} onClick={() => pullDrill(d2, "hours")}
                        className="ml-1 px-1.5 py-0.5 rounded border text-[10px] font-bold"
                        style={drill?.day === d2 ? { background: "#6a1b9a", color: "#fff", borderColor: "#6a1b9a" }
                                                 : { borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                        {d2.slice(4, 6)}-{d2.slice(6)}
                      </button>
                    ))}
                  </div>
                )}
                {drill && (
                  <div className="mt-2 rounded-lg border p-2" style={{ borderColor: "#6a1b9a" }}>
                    <div className="flex items-center gap-2 text-[10.5px] font-bold mb-1"
                      style={{ color: "#6a1b9a" }}>
                      <button onClick={() => pullDrill(drill.day, "hours")}
                        className="underline decoration-dotted">{drill.day.slice(4, 6)}-{drill.day.slice(6)}</button>
                      {drill.level !== "hours" && (
                        <>
                          <span>›</span>
                          <button onClick={() => pullDrill(drill.day, "minutes", drill.hour)}
                            className="underline decoration-dotted">{drill.hour}{t("시", "h")}</button>
                        </>
                      )}
                      {drill.level === "seconds" && (<><span>›</span><span>{drill.hour}:{drill.minute}</span></>)}
                      <span className="text-[var(--text-muted)] font-normal">
                        {drill.level === "hours" ? t("· 시간을 누르면 분 단위로", "· click an hour for its minutes")
                          : drill.level === "minutes" ? t("· 분을 누르면 초 단위로", "· click a minute for its seconds")
                          : t("· 초 단위 — 실제 체결 그대로", "· seconds - the executions as they happened")}
                      </span>
                      {drillBusy && <span style={{ color: "#e65100" }}>⏳</span>}
                    </div>
                    <div className="overflow-y-auto" style={{ maxHeight: 260 }}>
                      <table className="w-full text-[10.5px] tabular-nums">
                        <thead><tr className="text-[9.5px] text-[var(--text-muted)] sticky top-0"
                          style={{ background: "var(--bg-elevated)" }}>
                          <th className="text-left px-2 py-0.5">{t("시각", "time")}</th>
                          <th className="text-right px-2">{t("시가", "open")}</th>
                          <th className="text-right px-2">{t("고가", "high")}</th>
                          <th className="text-right px-2">{t("저가", "low")}</th>
                          <th className="text-right px-2">{t("종가", "close")}</th>
                          <th className="text-right px-2">{t("등락", "±")}</th>
                          <th className="text-right px-2">{t("거래량", "volume")}</th>
                          <th className="text-right px-2">{t("체결수", "deals")}</th>
                        </tr></thead>
                        <tbody>
                          {drill.rows.map((r) => (
                            <tr key={r.key}
                              onClick={() => {
                                if (drill.level === "hours") pullDrill(drill.day, "minutes", r.key);
                                else if (drill.level === "minutes") pullDrill(drill.day, "seconds", r.key.slice(0, 2), r.key.slice(2));
                              }}
                              className={"border-t border-[var(--border-default)]/30 " +
                                (drill.level !== "seconds" ? "cursor-pointer hover:bg-[var(--bg-elevated)]" : "")}>
                              <td className="px-2 py-0.5 font-bold"
                                style={{ color: drill.level !== "seconds" ? "#6a1b9a" : "var(--text-secondary)" }}>
                                {drill.level !== "seconds" ? "▸ " : ""}{r.t}</td>
                              <td className="text-right px-2">{Math.round(r.open).toLocaleString()}</td>
                              <td className="text-right px-2" style={{ color: RED }}>{Math.round(r.high).toLocaleString()}</td>
                              <td className="text-right px-2" style={{ color: BLUE }}>{Math.round(r.low).toLocaleString()}</td>
                              <td className="text-right px-2 font-bold">{Math.round(r.close).toLocaleString()}</td>
                              <td className="text-right px-2 font-bold"
                                style={{ color: r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-muted)" }}>
                                {r.chg == null ? "-" : `${r.chg > 0 ? "▲+" : r.chg < 0 ? "▼" : ""}${r.chg}%`}</td>
                              <td className="text-right px-2 text-[var(--text-secondary)]">{r.vol.toLocaleString()}</td>
                              <td className="text-right px-2 text-[var(--text-muted)]">{r.n.toLocaleString()}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                <div className="text-[10px] text-[var(--text-muted)] mt-2">
                  {raw.flow_latest
                    ? t(`수급 자료 최신일: ${raw.flow_latest} — 이 날짜 이후의 외국인/기관 칸은 비어 있습니다.`,
                        `flow data goes up to ${raw.flow_latest} - foreign/inst cells after that date are empty.`)
                    : t("이 종목은 수급 자료가 없습니다.", "no flow data for this stock.")}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <p className="mt-3 text-[10.5px] text-[var(--text-muted)] leading-relaxed">
        {t("이 화면은 아직 매매를 하지 않습니다 — 진짜 시장의 데이터를 인공 데이터와 같은 방식으로 보여주는 단계입니다. 규칙을 여기에 붙이기 전에, 같은 캔들·같은 호가·같은 체결이 맞는지 먼저 눈으로 확인하십시오.",
           "this page does not trade yet - it puts real market data in the same shape as the artificial one. Before any rule is attached here, check by eye that the candles, the book and the executions are what you expect.")}
      </p>
    </div>
  );
}
