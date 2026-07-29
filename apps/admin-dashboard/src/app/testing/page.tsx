"use client";

// 모의투자 (Paper Trading) LANDING — boss 2026-07-14: clicking the menu opens NOTHING
// by default, just the two ALGORITHMS with a short explanation; click one to enter.
//   Algorithm 1 = the 1-hour decision-engine trading (semi / manual / auto)
//   Algorithm 2 = the ripple scalper (small wins repeatedly; auto / manual+호가창)
import Link from "next/link";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const PURPLE = "#7b1fa2";
const TEAL = "#00838f";
const INDIGO = "#3949ab";

// boss 2026-07-21: Render (small box) runs ALGO 1 ONLY, so Algo 2 & 3 cards are hidden
// there (their DATA is untouched — still in the DB + the verdict board). LOCAL testing on
// the boss's strong PC shows all 3: set NEXT_PUBLIC_SHOW_ALGO_23=true in .env.local.
const SHOW_ALGO_23 = process.env.NEXT_PUBLIC_SHOW_ALGO_23 === "true";

export default function TestingIndex() {
  const { t } = useLanguage();
  return (
    <div className="max-w-[1000px] mx-auto px-4 py-8">
      <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">
        🧪 {t("모의투자 — 알고리즘을 선택하세요", "Paper Trading — pick an algorithm")}
      </h1>
      <p className="mt-1 text-[13px] text-[var(--text-muted)]">
        {SHOW_ALGO_23
          ? t("알고리즘들은 같은 모의계좌(같은 현금·같은 종목)를 씁니다. 성적은 따로 기록됩니다.",
              "The algorithms share the same paper account (same cash, same stocks). Records are kept separately.")
          : t("지금은 알고리즘 1만 테스트 중입니다. 알고리즘 2·3은 잠시 숨김(데이터는 그대로 보관).",
              "Testing Algorithm 1 only for now. Algorithms 2 & 3 are hidden for a bit (their data is kept).")}
      </p>

      <div className={`mt-6 grid gap-5 ${SHOW_ALGO_23 ? "md:grid-cols-2" : "md:grid-cols-1 max-w-[560px]"}`}>
        {/* ---- Algorithm 1 — the 1-hour engine ---- */}
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: RED, background: "rgba(211,47,47,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: RED }}>
            🧠 {t("알고리즘 1 — 1시간 엔진 매매", "Algorithm 1 — 1-hour engine trading")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("지금까지 쓰던 방식입니다. 결정 엔진(ML + 뉴스 + 차트 + 과거 패턴)이 매수 자리를 찾아 신호를 내고, 목표 +1%~ / 손절 −1% / 1시간(⚡초단타 20분) 계획으로 매매합니다. 판 전체 보호 가드 포함.",
               "The way we trade now. The decision engine (ML + news + chart + history patterns) finds the spot and signals; plans run +1%~ target / −1% stop / 1-hour (⚡quick 20-min). Universal sell-guard included.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/semi" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: RED }}>
              {t("반자동 (추천+내 손)", "Semi-Auto")}
            </Link>
            <Link prefetch={true} href="/testing/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: RED, borderColor: RED }}>
              {t("자동", "Auto")}
            </Link>
            <Link prefetch={true} href="/testing/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border text-[var(--text-secondary)]" style={{ borderColor: "var(--border-default)" }}>
              {t("수동", "Manual")}
            </Link>
          </div>
        </div>

        {/* ---- Algorithm 2 — the ripple scalper (HIDDEN for now, data kept) ---- */}
        {SHOW_ALGO_23 && (
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: PURPLE, background: "rgba(123,31,162,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: PURPLE }}>
            ⚡ {t("알고리즘 2 — 잔물결 초단타 (NEW)", "Algorithm 2 — ripple scalper (NEW)")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("1시간을 들고 있지 않습니다. 오르기 시작하면 사고, 조금 오르면 바로 팔고(기본 +0.4%), 다시 오르면 또 사고 — 작은 승리를 반복합니다. 사고 나서 내려가면 버티다가 −1%에서만 손절 후 다음 상승을 기다립니다. 수동 모드는 키움 호가창 + 차트 + 지정가 자동매도까지.",
               "No 1-hour holds. Buy when it starts rising, sell the small win (+0.4% default), buy again if it keeps rising — many small wins. After a buy, dips are held; only −1% cuts, then wait for the next upturn. Manual mode: Kiwoom order book + chart + auto-sell at your price.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/scalp/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: PURPLE }}>
              {t("자동 (기계가 반복)", "Auto")}
            </Link>
            <Link prefetch={true} href="/testing/scalp/semi" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: "#e65100", borderColor: "#e65100" }}>
              {t("반자동 (추천+내 손)", "Semi-Auto")}
            </Link>
            <Link prefetch={true} href="/testing/scalp/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: PURPLE, borderColor: PURPLE }}>
              {t("수동 (호가창+차트)", "Manual (order book)")}
            </Link>
          </div>
        </div>
        )}

        {/* ---- Algorithm 3 — the candle trader (HIDDEN for now, data kept) ---- */}
        {SHOW_ALGO_23 && (
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: TEAL, background: "rgba(0,131,143,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: TEAL }}>
            🕯️ {t("알고리즘 3 — 캔들 매매 (NEW)", "Algorithm 3 — candle trader (NEW)")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("1분봉 캔들만 봅니다. 3연속 양봉이 뜨면 매수, 3연속 음봉이 뜨면 매도합니다 (−1% 손절, 15:18 정리). 매수·매도 판단에 짝꿍 종목과 거래량도 함께 확인합니다. 알고리즘 2와 똑같은 3가지 모드.",
               "Watches only the 1-min candles. 3 up candles in a row → buy, 3 down in a row → sell (−1% stop, flat 15:18). It also checks the partner stock and volume when deciding. Same 3 modes as Algorithm 2.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/candle3/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: TEAL }}>
              {t("자동 (기계가 반복)", "Auto")}
            </Link>
            <Link prefetch={true} href="/testing/candle3/semi" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: "#e65100", borderColor: "#e65100" }}>
              {t("반자동 (추천+내 손)", "Semi-Auto")}
            </Link>
            <Link prefetch={true} href="/testing/candle3/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: TEAL, borderColor: TEAL }}>
              {t("수동", "Manual")}
            </Link>
          </div>
        </div>
        )}

        {/* ---- Algorithm 4 — the cross-check consensus trader (data kept) ---- */}
        {SHOW_ALGO_23 && (
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: INDIGO, background: "rgba(57,73,171,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: INDIGO }}>
            🔀 {t("교차검증 — 3개 알고리즘이 동의할 때만 (NEW)", "Cross-Check — 3 algorithms must agree (NEW)")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("스스로 판단하지 않습니다. 알고리즘 1(🤖 엔진) · 2(⚡ 잔물결) · 3(🕯️ 캔들)이 모두 매수에 동의할 때만 삽니다 (엄격 3/3, 또는 느슨 2/3+브레인). 파는 규칙은 항상 안전장치 우선: −손절 · 트레일 청산 · 합의 이탈 · 15:18 정리. 기존 3개는 그대로 두는 대조군 — 이건 4번째 경쟁자입니다.",
               "It has no opinion of its own. It buys only when Algorithm 1 (🤖 engine) · 2 (⚡ ripple) · 3 (🕯️ candle) all agree to buy (strict 3/3, or loose 2/3+brain). Exits always put safety first: −stop · trailing exit · lost-consensus · flat 15:18. The 3 stay untouched as the control group — this is the 4th competitor.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/crosscheck/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: INDIGO }}>
              {t("자동 (3개 동의 시 매매)", "Auto")}
            </Link>
            <Link prefetch={true} href="/testing/crosscheck/semi" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: "#e65100", borderColor: "#e65100" }}>
              {t("반자동 (추천+내 손)", "Semi-Auto")}
            </Link>
            <Link prefetch={true} href="/testing/crosscheck/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: INDIGO, borderColor: INDIGO }}>
              {t("수동 (호가창+차트)", "Manual (order book)")}
            </Link>
          </div>
        </div>
        )}

        {/* ---- 🧪 Proof Lab — boss 2026-07-29: PROVE Algo 3 trades exactly on the 3rd candle ---- */}
        {SHOW_ALGO_23 && (
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: "#e65100" }}>
            🧪 {t("증명 시뮬레이션 — 3번째에 사고파는가? (NEW)", "Proof Lab — trades on the 3rd candle? (NEW)")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("사장님 증명용. 실제 엔진 함수를 인공 데이터(함정 포함)와 키움 실데이터 양쪽에 돌려, 매수 화살표가 정확히 3번째 양봉·매도 화살표가 정확히 3번째 음봉에 찍히는지 + 체결가가 호가창의 best ask/bid인지 전 거래를 자동 검증합니다. 계좌는 건드리지 않습니다.",
               "For the boss. Runs the LIVE engine function on artificial data (with planted traps) AND real Kiwoom data; auto-verifies every trade: BUY arrow exactly on the 3rd red, SELL arrow exactly on the 3rd blue, fills = order-book best ask/bid. Touches no accounts.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/proof" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: "#e65100" }}>
              {t("증명 열기 (인공 + 실데이터)", "Open the proof (artificial + real)")}
            </Link>
          </div>
        </div>
        )}
      </div>

      <p className="mt-6 text-[11.5px] text-[var(--text-muted)]">
        {t("⚠️ 왕복 수수료+세금 0.23%가 실제처럼 붙습니다 — 초단타 목표는 0.4% 이상이어야 실수익이 남습니다.",
           "⚠️ Real-style round-trip fees+tax of 0.23% apply — scalp targets need ≥0.4% to net a profit.")}
      </p>
    </div>
  );
}
