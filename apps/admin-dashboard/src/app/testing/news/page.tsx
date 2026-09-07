"use client";
/* 뉴스 영향 분석 (boss 2026-09-07: "we have to analyze news also — any keyword
   based news can not effect, we should analyze which news can effect or not
   effect, so please first build this thing, then I will check, then you will
   implement it to our case").

   Two columns matter on this page and they are independent:
     · 시장의 답 — what the tape did after the story broke, market move removed
     · 읽은 결과 — what the model said BEFORE looking at the price
   The scoreboard at the top grades the second against the first. Nothing here
   touches trading; it reads the stored news and the stored tape. */
import { useCallback, useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "../../../components/api";

type React9 = {
  ok: boolean; at?: string; measured_from?: string; pre_open?: boolean;
  base?: number; gap?: number | null; gap_ab?: number | null;
  m15?: number | null; ab15?: number | null; ab30?: number | null;
  ab60?: number | null; to_close?: number | null; ab_close?: number | null;
  vol_x?: number | null; note?: string };
type Judge = { move?: string; dir?: string; kind?: string; new?: boolean; why?: string };
type Row = {
  code: string; name: string; ts: string; title: string; outlets: number;
  n_titles?: number; stamp?: string; why?: string; link?: string;
  react: React9; moved: boolean; move_pct?: number | null; dir_ok?: boolean;
  repeat?: boolean; attribution?: string; shares_open_with?: number;
  shares_window_with?: number; judge?: Judge };
type Payload = {
  ok: boolean; day?: string; days?: string[]; stories?: number; moved?: number;
  reading?: string; generated?: string; rows?: Row[]; error?: string;
  shown?: number; matched?: number;
  score?: { judged: number; said_move: number; of_those_moved: number;
            said_none: number; of_those_quiet: number;
            precision: number | null; quiet_rate: number | null } | null };

const pct = (v?: number | null, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;

export default function NewsImpactPage() {
  const base = API.replace(/\/$/, "");
  const { t } = useLanguage();
  const [day, setDay] = useState("");
  const [reading, setReading] = useState(true);
  const [movedOnly, setMovedOnly] = useState(false);
  const [data, setData] = useState<Payload | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    setBusy(true);
    const q = new URLSearchParams({ judge: reading ? "1" : "0", top: "40" });
    if (day) q.set("day", day);
    if (movedOnly) q.set("moved_only", "1");
    fetch(`${base}/news-impact?${q.toString()}`)
      .then((r) => r.json())
      .then((d: Payload) => { setData(d); if (!day && d.day) setDay(d.day); })
      .catch(() => setData({ ok: false, error: "load failed" }))
      .finally(() => setBusy(false));
  }, [base, day, reading, movedOnly]);

  useEffect(() => { load(); }, [load]);
  // while the reading is being prepared, come back for it
  useEffect(() => {
    if (data?.reading !== "computing") return;
    const id = setTimeout(load, 6000);
    return () => clearTimeout(id);
  }, [data?.reading, load]);

  const rows = data?.rows || [];
  const sc = data?.score;

  return (
    <div style={{ padding: 18, maxWidth: 1180, margin: "0 auto" }}>
      <h2 style={{ fontSize: 19, fontWeight: 900, marginBottom: 2 }}>
        📰 {t("뉴스 영향 분석 — 어떤 뉴스가 가격을 움직이는가",
              "News impact — which news can actually move the price")}</h2>
      <div style={{ fontSize: 12.5, opacity: 0.75, marginBottom: 12, lineHeight: 1.6 }}>
        {t("왼쪽은 시장의 답(테이프가 실제로 한 일, 시장 전체의 움직임을 뺀 값), 오른쪽은 가격을 보기 전에 모델이 읽은 결과입니다. 위의 점수판이 오른쪽을 왼쪽으로 채점합니다. 이 화면은 매매에 전혀 관여하지 않습니다.",
           "Left is the market's answer (what the tape did, with the market's own move removed); right is what the model said BEFORE seeing the price. The scoreboard grades the right against the left. This page does not touch trading.")}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <select value={day} onChange={(e) => setDay(e.target.value)}
                style={{ fontSize: 13, padding: "5px 8px", borderRadius: 7 }}>
          {(data?.days || []).map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <label style={{ fontSize: 12.5, display: "flex", gap: 5, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={reading} onChange={(e) => setReading(e.target.checked)} />
          {t("모델의 읽기 포함", "include the model's reading")}
        </label>
        <label style={{ fontSize: 12.5, display: "flex", gap: 5, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={movedOnly} onChange={(e) => setMovedOnly(e.target.checked)} />
          {t("가격이 움직인 것만", "only the ones the price answered")}
        </label>
        <button onClick={load} disabled={busy}
                style={{ fontSize: 12.5, padding: "5px 12px", borderRadius: 7, cursor: "pointer",
                         border: "1px solid rgba(128,128,128,0.45)", background: "transparent",
                         color: "inherit", fontWeight: 700 }}>
          {busy ? t("불러오는 중…", "loading…") : t("새로고침", "refresh")}</button>
        {data?.generated && <span style={{ fontSize: 11, opacity: 0.6 }}>{data.generated}</span>}
      </div>

      {/* the scoreboard */}
      <div style={{ display: "grid", gap: 10, marginBottom: 14,
                    gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))" }}>
        <Card title={t("오늘의 뉴스", "stories today")}
              big={`${data?.stories ?? "—"}`}
              sub={t("중복 기사를 하나의 사건으로 묶은 수", "duplicate headlines folded into one story each")} />
        <Card title={t("가격이 답한 뉴스", "the price answered")}
              big={`${data?.moved ?? "—"}`}
              sub={data?.stories ? t(`전체의 ${Math.round(100 * (data.moved || 0) / data.stories)}%`,
                                     `${Math.round(100 * (data.moved || 0) / data.stories)}% of them`) : ""} />
        <Card title={t("“움직인다”고 읽은 것", "said it WOULD move")}
              big={sc ? `${sc.of_those_moved}/${sc.said_move}` : (data?.reading === "computing" ? "…" : "—")}
              sub={sc?.precision != null ? t(`적중률 ${Math.round(sc.precision * 100)}%`,
                                             `${Math.round(sc.precision * 100)}% right`) : ""} />
        <Card title={t("“안 움직인다”고 읽은 것", "said it would NOT move")}
              big={sc ? `${sc.of_those_quiet}/${sc.said_none}` : (data?.reading === "computing" ? "…" : "—")}
              sub={sc?.quiet_rate != null ? t(`적중률 ${Math.round(sc.quiet_rate * 100)}%`,
                                              `${Math.round(sc.quiet_rate * 100)}% right`) : ""} />
      </div>
      {data?.reading === "computing" && (
        <div style={{ fontSize: 12.5, opacity: 0.75, marginBottom: 10 }}>
          ⏳ {t("모델이 상위 기사를 읽는 중입니다 — 측정값은 이미 아래에 있습니다.",
                "the model is reading the top stories — the measurements are already below.")}</div>)}

      <div style={{ display: "grid", gap: 7 }}>
        {rows.map((r, i) => {
          const rc = r.react || ({} as React9);
          const mv = r.move_pct;
          const up = (mv || 0) >= 0;
          return (
            <div key={i} style={{ border: "1px solid rgba(128,128,128,0.3)", borderRadius: 9,
                                  padding: "9px 11px",
                                  background: r.moved ? "rgba(230,168,23,0.07)" : "transparent" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                <b style={{ fontSize: 13 }}>{r.name}</b>
                <span style={{ fontSize: 11, opacity: 0.65 }}>{String(r.ts).slice(11, 16)}</span>
                <span style={{ fontSize: 10.5, padding: "1px 6px", borderRadius: 8,
                               background: "rgba(128,128,128,0.18)" }}>
                  {t(`${r.outlets}개 매체`, `${r.outlets} outlets`)}</span>
                {r.repeat && <span style={{ fontSize: 10.5, padding: "1px 6px", borderRadius: 8,
                                            background: "#6a1b9a", color: "#fff" }}>
                  {t("지난 3일 안에 이미 나온 이야기", "already ran in the last 3 days")}</span>}
                {r.stamp && <span style={{ fontSize: 10.5, padding: "1px 6px", borderRadius: 8,
                                           background: r.stamp === "호재" ? "#2e7d32"
                                                     : r.stamp === "위험" ? "#c62828" : "#555",
                                           color: "#fff" }}>{r.stamp}</span>}
                {r.moved
                  ? <span style={{ fontSize: 10.5, fontWeight: 800, padding: "1px 7px", borderRadius: 8,
                                   background: up ? "#c62828" : "#1565c0", color: "#fff" }}>
                      {t("가격이 답했다 ", "the price answered ")}{pct(mv)}</span>
                  : (r.attribution && r.attribution !== "owns the open" && r.attribution !== "owns the window")
                    ? <span style={{ fontSize: 10.5, padding: "1px 7px", borderRadius: 8,
                                     background: "rgba(128,128,128,0.25)" }}>
                        {t(`같은 시간대 다른 뉴스와 겹침 ${pct(mv)} — 누구 덕인지 알 수 없음`,
                           `shared the window ${pct(mv)} — attribution unclear`)}</span>
                    : <span style={{ fontSize: 10.5, opacity: 0.6 }}>{t("반응 없음", "no reaction")}</span>}
                {r.attribution && <span style={{ fontSize: 10, opacity: 0.6 }}>({r.attribution})</span>}
              </div>
              <div style={{ fontSize: 12.5, marginTop: 4, cursor: "pointer" }}
                   onClick={() => setOpen(open === `${i}` ? null : `${i}`)}>
                {r.title}</div>
              <div style={{ display: "flex", gap: 12, marginTop: 5, fontSize: 11, opacity: 0.85,
                            flexWrap: "wrap" }}>
                {rc.pre_open
                  ? <span>{t("시가 갭", "opening gap")} <b>{pct(rc.gap)}</b>
                      {rc.gap_ab != null && <> · {t("시장 제외", "market removed")} <b>{pct(rc.gap_ab)}</b></>}</span>
                  : <span>+15m <b>{pct(rc.ab15)}</b> · +30m <b>{pct(rc.ab30)}</b> · +60m <b>{pct(rc.ab60)}</b></span>}
                <span>{t("종가까지", "to close")} <b>{pct(rc.ab_close)}</b></span>
                {rc.vol_x != null && <span>{t("거래량", "volume")} <b>{rc.vol_x}×</b></span>}
                {r.judge?.move && (
                  <span style={{ color: r.judge.move === "없음" ? "#777" : "#e6a817", fontWeight: 700 }}>
                    {t("모델: ", "model: ")}{r.judge.move}
                    {r.judge.kind ? ` · ${r.judge.kind}` : ""}
                    {r.judge.new === false ? t(" · 새로운 사실 아님", " · not a new fact") : ""}</span>)}
              </div>
              {open === `${i}` && (
                <div style={{ marginTop: 6, padding: "7px 9px", borderRadius: 8, fontSize: 11.5,
                              background: "rgba(128,128,128,0.10)", lineHeight: 1.6 }}>
                  {r.judge?.why && <div>🧠 {r.judge.why}</div>}
                  {r.why && <div style={{ opacity: 0.8 }}>🏷 {r.why}</div>}
                  <div style={{ opacity: 0.75, marginTop: 3 }}>
                    {t("기준가", "measured from")} {rc.measured_from} · ₩{Math.round(rc.base || 0).toLocaleString()}
                    {r.n_titles ? ` · ${t(`같은 사건 기사 ${r.n_titles}건`, `${r.n_titles} headlines on this one story`)}` : ""}
                    {r.shares_open_with ? ` · ${t(`같은 개장을 공유한 다른 사건 ${r.shares_open_with}건`, `${r.shares_open_with} other stories share this open`)}` : ""}
                  </div>
                  {r.link && <a href={r.link} target="_blank" rel="noreferrer"
                                style={{ color: "#e6a817" }}>{t("기사 열기", "open the article")} ↗</a>}
                </div>)}
            </div>);
        })}
        {!!data?.matched && (data.matched > rows.length) && (
          <div style={{ fontSize: 11.5, opacity: 0.65, padding: "6px 2px" }}>
            {t(`반응이 큰 순서로 ${rows.length}건 표시 중 (전체 ${data.matched}건). 나머지는 모두 반응 없음입니다.`,
               `showing the ${rows.length} with the biggest reaction of ${data.matched}. The rest are all no-reaction.`)}</div>)}
        {!rows.length && !busy && (
          <div style={{ padding: 24, opacity: 0.7, fontSize: 13 }}>
            {data?.error || t("이 날짜에는 저장된 뉴스가 없습니다.", "no stored news for this day.")}</div>)}
      </div>
    </div>);
}

function Card({ title, big, sub }: { title: string; big: string; sub?: string }) {
  return (
    <div style={{ border: "1px solid rgba(128,128,128,0.3)", borderRadius: 10, padding: "10px 12px" }}>
      <div style={{ fontSize: 11.5, opacity: 0.7 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 900, lineHeight: 1.25 }}>{big}</div>
      {sub && <div style={{ fontSize: 11, opacity: 0.65 }}>{sub}</div>}
    </div>);
}
