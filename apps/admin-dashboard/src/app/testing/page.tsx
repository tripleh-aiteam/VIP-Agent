"use client";

// 모의투자 (Paper Trading) LANDING — boss 2026-07-14: clicking the menu opens NOTHING
// by default, just the two ALGORITHMS with a short explanation; click one to enter.
//   Algorithm 1 = the 1-hour decision-engine trading (semi / manual / auto)
//   Algorithm 2 = the ripple scalper (small wins repeatedly; auto / manual+호가창)
import Link from "next/link";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const PURPLE = "#7b1fa2";

export default function TestingIndex() {
  const { t } = useLanguage();
  return (
    <div className="max-w-[1000px] mx-auto px-4 py-8">
      <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">
        🧪 {t("모의투자 — 알고리즘을 선택하세요", "Paper Trading — pick an algorithm")}
      </h1>
      <p className="mt-1 text-[13px] text-[var(--text-muted)]">
        {t("두 알고리즘은 같은 모의계좌(같은 현금·같은 종목)를 씁니다. 성적은 따로 기록됩니다.",
           "Both algorithms share the same paper account (same cash, same stocks). Records are kept separately.")}
      </p>

      <div className="mt-6 grid md:grid-cols-2 gap-5">
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
            <Link href="/testing/semi" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: RED }}>
              {t("반자동 (추천+내 손)", "Semi-Auto")}
            </Link>
            <Link href="/testing/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: RED, borderColor: RED }}>
              {t("자동", "Auto")}
            </Link>
            <Link href="/testing/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border text-[var(--text-secondary)]" style={{ borderColor: "var(--border-default)" }}>
              {t("수동", "Manual")}
            </Link>
          </div>
        </div>

        {/* ---- Algorithm 2 — the ripple scalper ---- */}
        <div className="rounded-2xl border-2 p-5" style={{ borderColor: PURPLE, background: "rgba(123,31,162,0.04)" }}>
          <div className="text-[18px] font-extrabold" style={{ color: PURPLE }}>
            ⚡ {t("알고리즘 2 — 잔물결 초단타 (NEW)", "Algorithm 2 — ripple scalper (NEW)")}
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {t("1시간을 들고 있지 않습니다. 오르기 시작하면 사고, 조금 오르면 바로 팔고(기본 +0.4%), 다시 오르면 또 사고 — 작은 승리를 반복합니다. 사고 나서 내려가면 버티다가 −1%에서만 손절 후 다음 상승을 기다립니다. 수동 모드는 키움 호가창 + 차트 + 지정가 자동매도까지.",
               "No 1-hour holds. Buy when it starts rising, sell the small win (+0.4% default), buy again if it keeps rising — many small wins. After a buy, dips are held; only −1% cuts, then wait for the next upturn. Manual mode: Kiwoom order book + chart + auto-sell at your price.")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/testing/scalp/auto" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl text-white" style={{ background: PURPLE }}>
              {t("자동 (기계가 반복)", "Auto")}
            </Link>
            <Link href="/testing/scalp/manual" className="text-[13.5px] font-extrabold px-4 py-2 rounded-xl border" style={{ color: PURPLE, borderColor: PURPLE }}>
              {t("수동 (호가창+차트)", "Manual (order book)")}
            </Link>
          </div>
        </div>
      </div>

      <p className="mt-6 text-[11.5px] text-[var(--text-muted)]">
        {t("⚠️ 왕복 수수료+세금 0.23%가 실제처럼 붙습니다 — 초단타 목표는 0.4% 이상이어야 실수익이 남습니다.",
           "⚠️ Real-style round-trip fees+tax of 0.23% apply — scalp targets need ≥0.4% to net a profit.")}
      </p>
    </div>
  );
}
