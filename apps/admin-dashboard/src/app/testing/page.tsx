"use client";

// 모의투자 (Paper Trading) LANDING — slimmed at the boss's order 2026-08-13
// ("our app is heavy, remove these; if we need later we can recreate"):
// the legacy Strategy-Lab algorithms (1-hour engine w/ ML, ripple scalper,
// candle trader, Cross-Check) and the two artificial labs are UNLINKED here.
// Their code and data remain in the repo/DB — see REMOVED_FEATURES.md for the
// full recreation recipe, including the standing promise about ML buy/sell
// prediction. The Live Kiwoom Desk IS the product now.
import Link from "next/link";
import { useLanguage } from "@/components/i18n";

export default function TestingIndex() {
  const { t } = useLanguage();
  return (
    <div className="max-w-[1000px] mx-auto px-4 py-8">
      <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">
        🧪 {t("모의투자", "Paper Trading")}
      </h1>
      <p className="mt-1 text-[13px] text-[var(--text-muted)]">
        {t("실시간 키움 데스크가 회장님의 알고리즘 1·2와 예전 규칙을 진짜 시장에서 병렬 매매합니다.",
           "The Live Kiwoom Desk trades the boss's Algorithms 1 & 2 and the old rule in parallel, on the real market.")}
      </p>

      <div className="mt-6 grid gap-5 md:grid-cols-1 max-w-[560px]">
        {/* ---- 📡 Live Kiwoom Desk — the product ---- */}
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: "#00838f", background: "rgba(0,131,143,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: "#00838f" }}>
            📡 {t("실시간 키움 데스크 — 진짜 시장", "Live Kiwoom Desk — the real market")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("회장님의 두 알고리즘이 결투 중입니다 — 같은 4개의 문(급락·아침·상승·바닥반등), 같은 수량, 같은 −1.5% 리셋에 단 하나의 차이: 알고리즘1은 +1%마다 50%씩, 알고리즘2는 10%씩 수확합니다. 예전 규칙(3연속 상승)도 나란히 달립니다. 모든 매수·매도는 1분봉이 시점을, 호가창이 가격을 정하며, 매매 하나하나가 차트 화살표로 증명됩니다.",
               "The boss's two algorithms are in a duel — same four doors (dip, morning, climb, rebound), same sizes, same −1.5% reset, one difference: Algorithm 1 harvests 50% per +1%, Algorithm 2 harvests 10%. The old rule (3 rises) runs alongside. The 1-minute chart decides timing, the order book decides prices, and every trade proves itself with chart arrows.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/live" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: "#00838f" }}>
              {t("실시간 데스크 열기", "Open the live desk")}
            </Link>
          </div>
        </div>

        {/* ---- 🎯 Checklist Reco Desk — the 100-item picks, trading live (boss 2026-08-24) ---- */}
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: "#e65100" }}>
            🎯 {t("체크리스트 추천 데스크 — 100문항이 고른 종목", "Checklist Reco Desk — chosen by the 100 items")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("매일 아침 100문항 체크리스트가 후보 전체를 채점해 상위 5종목을 뽑고, 그 종목들이 같은 알고리즘 1·2·3으로 실전 매매됩니다. 이 화면은 추천 5종목만 모아서 봅니다 — 추천이 실제로 사고파는 것을 매매 화살표로 확인하세요. 내 6종목과 병렬로 돌아갑니다 (둘 다 기본 ON).",
               "Every morning the 100-item checklist scores every candidate and picks the top five, and those five trade live on the same Algorithms 1·2·3. This view shows just the recommended five — watch the recommendation actually buy and sell, proven by the chart arrows. Runs in parallel with my six (both ON by default).")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/reco" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: "#e65100" }}>
              {t("추천 데스크 열기", "Open the reco desk")}
            </Link>
          </div>
        </div>

        {/* ---- 🤝 Menu 3 — Semi-Auto Approval Desk (boss 2026-09-02: "tomorrow I
             wanna trade with both — menu 2 automatic without asking, menu 3 takes
             our approve then will do it") ---- */}
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: "#2e7d32", background: "rgba(46,125,50,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: "#2e7d32" }}>
            🖥 {t("실시간 모니터링", "Real Time Monitoring")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("메뉴2가 자동으로 매매하는 동안, 여기서는 같은 두뇌(100문항·1년 역사 데이터·호가창·거래량·뉴스)가 종목·가격·수량까지 제안만 하고 — 승인 버튼을 눌러야만 실행됩니다. 10개 방(고정 6 + 오늘의 추천 4)을 클릭하면 에이전트가 실제로 검사하는 과정이 그대로 보입니다. 팝업의 이유를 읽고 ✅ 승인 또는 취소 — 매매마다 사람의 손가락이 들어갑니다.",
               "While Menu 2 trades automatically, here the same brain (100 items, 1-year history, order book, volume, news) only SUGGESTS — company, price and share count — and nothing executes until you press Approve. Click any of the 10 rooms (the six + today's top 4) to watch the agent actually working. Read the popup's reasons, then ✅ Approve or Cancel — a human finger on every trade.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link prefetch={true} href="/testing/approve" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: "#2e7d32" }}>
              {t("실시간 모니터링 열기", "Open Real Time Monitoring")}
            </Link>
          </div>
        </div>

        {/* 🎭 Demo Theater card removed at the boss's order (2026-08-27:
            "remove Demo Theater - only keep Live Order Room in menu 1, 2").
            The real-trade 🎞 replay lives inside each menu's Order Room. */}
      </div>

      <p className="mt-6 text-[11.5px] text-[var(--text-muted)]">
        {t("⚠️ 왕복 수수료+세금 0.23%가 실제처럼 붙습니다. 예전 알고리즘(1시간 엔진·잔물결·캔들·교차검증)과 실험실들은 2026-08-13 회장님 지시로 숨김 — 코드·데이터는 보존 (REMOVED_FEATURES.md).",
           "⚠️ Real-style round-trip fees+tax of 0.23% apply. The legacy algorithms (1-hour engine, ripple, candle, Cross-Check) and the labs were hidden on 2026-08-13 at the boss's order — code & data preserved (REMOVED_FEATURES.md).")}
      </p>
    </div>
  );
}
