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
      </div>

      <p className="mt-6 text-[11.5px] text-[var(--text-muted)]">
        {t("⚠️ 왕복 수수료+세금 0.23%가 실제처럼 붙습니다. 예전 알고리즘(1시간 엔진·잔물결·캔들·교차검증)과 실험실들은 2026-08-13 회장님 지시로 숨김 — 코드·데이터는 보존 (REMOVED_FEATURES.md).",
           "⚠️ Real-style round-trip fees+tax of 0.23% apply. The legacy algorithms (1-hour engine, ripple, candle, Cross-Check) and the labs were hidden on 2026-08-13 at the boss's order — code & data preserved (REMOVED_FEATURES.md).")}
      </p>
    </div>
  );
}
