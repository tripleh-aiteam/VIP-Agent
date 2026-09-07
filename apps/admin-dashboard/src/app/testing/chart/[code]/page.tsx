"use client";
/* 📈 THE CHATBOT'S ONE-CLICK CHART (boss 2026-09-07: "inside the chatbot we
   can see proof — one click and we should see the 1-minute, 1-day, 1-week,
   1-month, 3-month, 6-month chart including the volume numbers"). Candles +
   volume bars + the volume figures, six windows, one page. Minute bars come
   from our own Kiwoom tape; daily bars from the official record. */
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useLanguage } from "@/components/i18n";
import { API } from "../../../../components/api";

type Bar = { t: string; o?: number | null; h?: number | null; l?: number | null;
             c?: number | null; v?: number | null };
type Payload = { ok: boolean; mode: string; code: string; bars: Bar[] };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

export default function ChartPage() {
  const { t } = useLanguage();
  const params = useParams<{ code: string }>();
  const sp = useSearchParams();
  const code = String(params?.code || "");
  const name = sp?.get("n") || code;
  const [tf, setTf] = useState("day");
  const [data, setData] = useState<Payload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const TFS: { key: string; mode: string; ko: string; en: string }[] = [
    { key: "min1", mode: "min", ko: "1분 (최근 60분)", en: "1 min (last 60m)" },
    { key: "day", mode: "min", ko: "1일 (오늘 분봉)", en: "1 day (today, 1-min)" },
    { key: "week", mode: "week", ko: "1주", en: "1 week" },
    { key: "month", mode: "month", ko: "1개월", en: "1 month" },
    { key: "month3", mode: "month3", ko: "3개월", en: "3 months" },
    { key: "month6", mode: "month6", ko: "6개월", en: "6 months" },
  ];
  const cur = TFS.find((x) => x.key === tf) || TFS[1];

  useEffect(() => {
    let dead = false;
    const pull = async () => {
      try {
        const r = await fetch(`${API}/approval/chart/${code}?mode=${cur.mode}`, { cache: "no-store" });
        const j = await r.json();
        if (!dead) { setData(j); setErr(j?.ok ? null : "no data"); }
      } catch { if (!dead) setErr("서버 연결 중…"); }
    };
    pull();
    const iv = setInterval(pull, cur.mode === "min" ? 10000 : 60000);
    return () => { dead = true; clearInterval(iv); };
  }, [code, cur.mode]);

  let bars = (data?.bars || []).filter((b) => b.h != null && b.l != null);
  if (tf === "min1") bars = bars.slice(-60);

  const Wd = 900, H = 300, VH = 90, pad = 6;
  const hi = bars.length ? Math.max(...bars.map((b) => b.h as number)) : 0;
  const lo = bars.length ? Math.min(...bars.map((b) => b.l as number)) : 0;
  const vmax = bars.length ? Math.max(1, ...bars.map((b) => Number(b.v || 0))) : 1;
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (v - lo) / Math.max(1e-9, hi - lo));
  const bw = bars.length ? (Wd - 2 * pad) / bars.length : 1;
  const totV = bars.reduce((a, b) => a + Number(b.v || 0), 0);
  const last = bars[bars.length - 1];
  const first = bars[0];
  const chg = last?.c && first?.o ? ((last.c / first.o - 1) * 100) : null;

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "16px 14px 50px" }}>
      <div style={{ fontSize: 12, marginBottom: 8, display: "flex", gap: 12 }}>
        <a href="/testing/approve" style={{ color: "#2e7d32" }}>{t("← 메뉴3 실시간 모니터링", "← Menu 3 Real Time Monitoring")}</a>
      </div>
      <h1 style={{ fontSize: 19, fontWeight: 800, margin: "0 0 8px" }}>
        📈 {name} ({code}) — {t(cur.ko, cur.en)}
      </h1>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {TFS.map((x) => (
          <button key={x.key} onClick={() => setTf(x.key)}
            style={{ padding: "6px 12px", borderRadius: 8, fontSize: 12.5, fontWeight: 700,
                     cursor: "pointer",
                     border: `1.5px solid ${tf === x.key ? "#2e7d32" : "rgba(128,128,128,0.4)"}`,
                     background: tf === x.key ? "rgba(46,125,50,0.12)" : "transparent",
                     color: tf === x.key ? "#2e7d32" : "inherit" }}>
            {t(x.ko, x.en)}</button>))}
      </div>

      {/* the volume NUMBERS he asked for, not only the bars */}
      {bars.length > 0 && (
        <div style={{ fontSize: 12.5, marginBottom: 8, display: "flex", gap: 16, flexWrap: "wrap" }}>
          <span>{t("현재/종가", "last")}: <b>{W(last?.c)}</b>
            {chg != null && <b style={{ marginLeft: 5, color: chg >= 0 ? "#e53935" : "#1e88e5" }}>
              ({chg >= 0 ? "+" : ""}{chg.toFixed(2)}%)</b>}</span>
          <span>{t("기간 최고", "high")}: <b>{W(hi)}</b></span>
          <span>{t("기간 최저", "low")}: <b>{W(lo)}</b></span>
          <span>📊 {t("기간 총 거래량", "total volume")}: <b>{totV.toLocaleString()}{t("주", " sh")}</b></span>
          <span>{t("마지막 봉 거래량", "last bar volume")}: <b>{Number(last?.v || 0).toLocaleString()}{t("주", " sh")}</b></span>
        </div>)}

      {err && !bars.length && <div style={{ fontSize: 13, color: "#c62828" }}>{err}</div>}
      {!data && !err && <div style={{ fontSize: 13, opacity: 0.6 }}>{t("불러오는 중…", "loading…")}</div>}

      {bars.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid rgba(128,128,128,0.3)",
                      borderRadius: 10, padding: 8 }}>
          <svg width={Wd} height={H + VH + 18} style={{ display: "block" }}>
            {/* candles — red up / blue down, like every desk here */}
            {bars.map((b, i) => {
              const x = pad + i * bw + bw / 2;
              const up = (b.c ?? 0) >= (b.o ?? 0);
              const col = up ? "#e53935" : "#1e88e5";
              return (
                <g key={i}>
                  <line x1={x} x2={x} y1={y(b.h as number)} y2={y(b.l as number)}
                        stroke={col} strokeWidth={1} />
                  <rect x={x - Math.max(1, bw * 0.32)} width={Math.max(2, bw * 0.64)}
                        y={y(Math.max(b.o ?? 0, b.c ?? 0))}
                        height={Math.max(1, Math.abs(y(b.o ?? 0) - y(b.c ?? 0)))}
                        fill={col} />
                  <title>{`${b.t}  O ${W(b.o)} H ${W(b.h)} L ${W(b.l)} C ${W(b.c)}  V ${Number(b.v || 0).toLocaleString()}`}</title>
                </g>);
            })}
            {/* volume bars with their own scale */}
            {bars.map((b, i) => {
              const x = pad + i * bw + bw / 2;
              const vh = (Number(b.v || 0) / vmax) * (VH - 4);
              const up = (b.c ?? 0) >= (b.o ?? 0);
              return (
                <g key={`v${i}`}>
                  <rect x={x - Math.max(1, bw * 0.32)} width={Math.max(2, bw * 0.64)}
                        y={H + 14 + (VH - 4 - vh)} height={Math.max(1, vh)}
                        fill={up ? "rgba(229,57,53,0.55)" : "rgba(30,136,229,0.55)"} />
                  <title>{`${b.t}  ${t("거래량", "volume")} ${Number(b.v || 0).toLocaleString()}`}</title>
                </g>);
            })}
            <text x={pad} y={H + 11} fontSize={10} fill="currentColor" opacity={0.6}>
              📊 {t("거래량", "volume")} (max {vmax.toLocaleString()})</text>
            {/* first / last time labels */}
            <text x={pad} y={H + VH + 16} fontSize={10} fill="currentColor" opacity={0.6}>{first?.t}</text>
            <text x={Wd - pad} y={H + VH + 16} fontSize={10} fill="currentColor" opacity={0.6}
                  textAnchor="end">{last?.t}</text>
          </svg>
        </div>)}
      <p style={{ fontSize: 11, opacity: 0.55, marginTop: 8 }}>
        {t("분봉 = 우리 서버가 받는 키움 실시간 체결 데이터 · 일봉 = 공식 일별 시세. 봉 위에 마우스를 올리면 시가·고가·저가·종가·거래량이 보입니다.",
           "Minute bars = our own Kiwoom live tick tape · daily bars = the official daily record. Hover a bar for O/H/L/C and volume.")}
      </p>
    </div>
  );
}
