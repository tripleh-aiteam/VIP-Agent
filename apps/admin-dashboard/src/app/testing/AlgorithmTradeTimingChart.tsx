"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { LogicalRange } from "lightweight-charts";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import { findFirstThreeCandleRuns } from "./candleRunDetection";

const BUY_COLOR = "#d32f2f";
const SELL_COLOR = "#1565c0";
const MAX_MARKER_DISTANCE_SECONDS = 10 * 60;
const CHART_TIMEFRAME = "1m";
const KOREAN_STOCK_CODE_PATTERN = /^\d{6}$/;
const KST_TIME_ZONE = "Asia/Seoul";
const RISING_RUN_COLOR = "#dc2626";
const FALLING_RUN_COLOR = "#2563eb";

const KST_TICK_FORMATTERS = {
  ko: new Intl.DateTimeFormat("ko-KR", {
    timeZone: KST_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }),
  en: new Intl.DateTimeFormat("en-GB", {
    timeZone: KST_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }),
};

const KST_CROSSHAIR_FORMATTERS = {
  ko: new Intl.DateTimeFormat("ko-KR", {
    timeZone: KST_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }),
  en: new Intl.DateTimeFormat("en-GB", {
    timeZone: KST_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }),
};

export type TradeTimingRecord = {
  ticker?: string | null;
  name: string;
  opened_at?: string | null;
  closed_at?: string | null;
  entry?: number | null;
  exit_price?: number | null;
  net_pct?: number | null;
  realized_pnl_pct?: number | null;
};

type ChartBar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

type ChartResponse = {
  code: string;
  tf: string;
  bars: ChartBar[];
};

type SymbolOption = {
  code: string;
  name: string;
  trades: number;
};

export type TradeTimingSymbol = {
  code: string;
  name: string;
};

type MarkerInput = {
  time: number;
  position: "belowBar" | "aboveBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
};

type CandleRunMode = "off" | "up" | "down" | "both";

export default function AlgorithmTradeTimingChart({
  trades,
  symbols = [],
  algorithmLabel,
  accent,
}: {
  trades: TradeTimingRecord[];
  symbols?: TradeTimingSymbol[];
  algorithmLabel: string;
  accent: string;
}) {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const options = useMemo(() => buildSymbolOptions(trades, symbols), [trades, symbols]);
  const [requestedCode, setRequestedCode] = useState<string | null>(null);
  const selected = options.find((item) => item.code === requestedCode) ?? options[0] ?? null;
  const selectedTrades = useMemo(
    () => (selected ? trades.filter((trade) => trade.ticker === selected.code) : []),
    [selected, trades],
  );
  const [chartData, setChartData] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candleRunMode, setCandleRunMode] = useState<CandleRunMode>("off");
  const refreshToken = useRef(0);

  useEffect(() => {
    if (!selected) {
      setChartData(null);
      setError(null);
      return;
    }
    let active = true;
    const load = async () => {
      const token = ++refreshToken.current;
      setLoading(true);
      setError(null);
      try {
        const result = await api<ChartResponse>(
          `/paper-desk/chart?code=${encodeURIComponent(selected.code)}&tf=${CHART_TIMEFRAME}`,
        );
        if (active && token === refreshToken.current) setChartData(result);
      } catch {
        if (active && token === refreshToken.current) {
          setError(t(
            "캔들 차트를 불러오지 못했습니다. 거래 기록은 아래에서 계속 확인하실 수 있습니다.",
            "The candle chart could not be loaded. You can continue reviewing the trade history below.",
          ));
        }
      } finally {
        if (active && token === refreshToken.current) setLoading(false);
      }
    };
    void load();
    const interval = window.setInterval(load, 30_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [selected?.code, lang]);

  const refresh = () => {
    if (!selected) return;
    const code = selected.code;
    const token = ++refreshToken.current;
    setLoading(true);
    setError(null);
    api<ChartResponse>(`/paper-desk/chart?code=${encodeURIComponent(code)}&tf=${CHART_TIMEFRAME}`)
      .then((result) => {
        if (token === refreshToken.current) setChartData(result);
      })
      .catch(() => {
        if (token === refreshToken.current) {
          setError(t(
            "캔들 차트를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
            "The candle chart could not be loaded. Please try again shortly.",
          ));
        }
      })
      .finally(() => {
        if (token === refreshToken.current) setLoading(false);
      });
  };

  return (
    <section
      className="mt-4 overflow-hidden rounded-xl border bg-[var(--bg-primary)]"
      style={{ borderColor: accent }}
      aria-label={t(`${algorithmLabel} 매매 타이밍 차트`, `${algorithmLabel} trade timing chart`)}
    >
      <div
        className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5"
        style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}
      >
        <div className="min-w-[220px] flex-1">
          <div className="text-[13.5px] font-extrabold" style={{ color: accent }}>
            {t("매매 타이밍 차트", "Trade Timing Chart")}
          </div>
          <p className="mt-0.5 text-[10.5px] text-[var(--text-muted)]">
            {t(
              "보유 종목과 완료 거래의 매수·매도 시점을 종목별 1분봉에서 확인하실 수 있습니다.",
              "Review held stocks and completed entries and exits on each stock's one-minute candles.",
            )}
          </p>
        </div>
        {selected ? (
          <>
            <label className="flex items-center gap-1.5 text-[10.5px] font-bold text-[var(--text-secondary)]">
              <span>{t("3연속 캔들", "3-candle runs")}</span>
              <select
                value={candleRunMode}
                onChange={(event) => setCandleRunMode(event.target.value as CandleRunMode)}
                className="rounded-lg border bg-[var(--bg-primary)] px-2 py-1 text-[11px] font-bold outline-none"
                style={{ borderColor: "var(--border-default)" }}
                aria-label={t("3연속 캔들 표시 옵션", "Three-candle run display option")}
              >
                <option value="off">{t("끄기", "Off")}</option>
                <option value="up">{t("상승", "Rising")}</option>
                <option value="down">{t("하락", "Falling")}</option>
                <option value="both">{t("모두", "Both")}</option>
              </select>
            </label>
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              className="rounded-lg border px-2.5 py-1 text-[11px] font-bold text-[var(--text-secondary)] disabled:opacity-50"
              style={{ borderColor: "var(--border-default)" }}
            >
              {loading ? t("불러오는 중입니다", "Loading") : t("새로고침", "Refresh")}
            </button>
          </>
        ) : null}
      </div>

      {options.length > 0 ? (
        <div
          className="flex gap-1.5 overflow-x-auto border-b px-3 py-2"
          style={{ borderColor: "var(--border-default)" }}
          role="group"
          aria-label={t("거래 종목을 선택해 주세요", "Select a traded stock")}
        >
          {options.map((option) => {
            const active = selected?.code === option.code;
            return (
              <button
                key={option.code}
                type="button"
                aria-pressed={active}
                onClick={() => setRequestedCode(option.code)}
                className="shrink-0 rounded-lg border px-2.5 py-1.5 text-left transition-colors"
                style={active
                  ? { borderColor: accent, background: `${accent}12`, color: accent }
                  : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}
              >
                <span className="block text-[11.5px] font-extrabold">{option.name}</span>
                <span className="block text-[9.5px] opacity-70">
                  {option.code} · {t(`${option.trades}회전`, `${option.trades} trips`)}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}

      {!selected ? (
        <ChartState>
          {t(
            "차트에 표시할 완료 매매 기록이 아직 없습니다.",
            "There are no completed trades to display on the chart yet.",
          )}
        </ChartState>
      ) : error ? (
        <ChartState tone="error">{error}</ChartState>
      ) : !chartData?.bars.length && loading ? (
        <ChartState>{t("캔들 차트를 불러오고 있습니다.", "Loading the candle chart.")}</ChartState>
      ) : !chartData?.bars.length ? (
        <ChartState>
          {t(
            "선택한 종목의 1분봉 데이터가 아직 없습니다.",
            "One-minute candle data is not available for the selected stock yet.",
          )}
        </ChartState>
      ) : (
        <TimingCanvas
          key={chartData.code}
          bars={chartData.bars}
          trades={selectedTrades}
          lang={lang}
          isKoreanStock={isKoreanStockCode(chartData.code)}
          candleRunMode={candleRunMode}
        />
      )}

      {selected ? (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-3 py-1.5 text-[10px] text-[var(--text-muted)]"
          style={{ borderColor: "var(--border-default)" }}
        >
          <span><b style={{ color: BUY_COLOR }}>▲ {t("매수", "Buy")}</b> {t("진입 시점입니다", "entry")}</span>
          <span>
            <b style={{ color: SELL_COLOR }}>▼ {t("매도", "Sell")}</b>{" "}
            {t("청산 시점과 실현손익률입니다", "exit with realized return")}
          </span>
          <span>{t("한국 표준시 기준이며 30초마다 갱신됩니다.", "KST · refreshes every 30 seconds.")}</span>
          <span>{t(
            "휠로 확대·축소하고 드래그로 이동하며, 더블클릭하면 초기화됩니다.",
            "Wheel to zoom, drag to pan, and double-click to reset.",
          )}</span>
        </div>
      ) : null}
    </section>
  );
}

function TimingCanvas({
  bars,
  trades,
  lang,
  isKoreanStock,
  candleRunMode,
}: {
  bars: ChartBar[];
  trades: TradeTimingRecord[];
  lang: string;
  isKoreanStock: boolean;
  candleRunMode: CandleRunMode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const visibleRangeRef = useRef<LogicalRange | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return;
    let active = true;
    let cleanup = () => {};

    void import("lightweight-charts").then((lw) => {
      if (!active || !containerRef.current) return;
      containerRef.current.replaceChildren();
      const dark = document.documentElement.classList.contains("dark");
      const chart = lw.createChart(containerRef.current, {
        height: 360,
        autoSize: true,
        layout: {
          background: { color: "transparent" },
          textColor: dark ? "#a8b0bd" : "#5f6b7a",
        },
        grid: {
          vertLines: { color: "rgba(128,128,128,0.10)" },
          horzLines: { color: "rgba(128,128,128,0.10)" },
        },
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          borderColor: "rgba(128,128,128,0.24)",
          rightBarStaysOnScroll: true,
          ...(isKoreanStock
            ? { tickMarkFormatter: (time: unknown) => formatKstTime(time, lang, "tick") }
            : {}),
        },
        rightPriceScale: { borderColor: "rgba(128,128,128,0.24)" },
        handleScroll: {
          mouseWheel: false,
          pressedMouseMove: true,
          horzTouchDrag: true,
          vertTouchDrag: false,
        },
        handleScale: {
          mouseWheel: true,
          pinch: true,
          axisPressedMouseMove: { time: true, price: true },
          axisDoubleClickReset: { time: true, price: true },
        },
        ...(isKoreanStock
          ? {
              localization: {
                timeFormatter: (time: unknown) => formatKstTime(time, lang, "crosshair"),
              },
            }
          : {}),
      });
      const series = chart.addCandlestickSeries({
        upColor: BUY_COLOR,
        downColor: SELL_COLOR,
        borderUpColor: BUY_COLOR,
        borderDownColor: SELL_COLOR,
        wickUpColor: BUY_COLOR,
        wickDownColor: SELL_COLOR,
        ...(isKoreanStock
          ? {
              priceFormat: {
                type: "price" as const,
                precision: 0,
                minMove: 1,
              },
            }
          : {}),
      });
      const validBars = bars
        .filter((bar) => Number.isFinite(bar.time) && Number.isFinite(bar.close))
        .toSorted((left, right) => left.time - right.time);
      series.setData(validBars as never);
      series.setMarkers(buildMarkers(validBars, trades, lang, candleRunMode) as never);
      if (visibleRangeRef.current) {
        chart.timeScale().setVisibleLogicalRange(visibleRangeRef.current);
      } else {
        chart.timeScale().fitContent();
      }
      cleanup = () => {
        visibleRangeRef.current = chart.timeScale().getVisibleLogicalRange();
        chart.remove();
      };
    });

    return () => {
      active = false;
      cleanup();
    };
  }, [bars, trades, lang, isKoreanStock, candleRunMode]);

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{ height: 360 }}
      role="img"
      aria-label={lang === "ko"
        ? "종목 1분봉과 알고리즘의 매수·매도 시점 차트"
        : "One-minute candles with algorithm entry and exit markers"}
    />
  );
}

function ChartState({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "error";
}) {
  return (
    <div
      className="flex min-h-[220px] items-center justify-center px-4 text-center text-[12px]"
      style={{ color: tone === "error" ? "var(--error)" : "var(--text-muted)" }}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

function buildSymbolOptions(
  trades: TradeTimingRecord[],
  trackedSymbols: TradeTimingSymbol[],
): SymbolOption[] {
  const symbolOptions = new Map<string, SymbolOption>();
  for (const symbol of trackedSymbols) {
    const code = symbol.code.trim();
    if (!code) continue;
    symbolOptions.set(code, { code, name: symbol.name || code, trades: 0 });
  }
  for (const trade of trades) {
    const code = trade.ticker?.trim();
    if (!code) continue;
    const current = symbolOptions.get(code);
    symbolOptions.set(code, {
      code,
      name: trade.name || code,
      trades: (current?.trades ?? 0) + 1,
    });
  }
  return Array.from(symbolOptions.values());
}

function buildMarkers(
  bars: ChartBar[],
  trades: TradeTimingRecord[],
  lang: string,
  candleRunMode: CandleRunMode,
): MarkerInput[] {
  const barTimes = bars.map((bar) => bar.time);
  const markers = buildCandleRunMarkers(bars, candleRunMode, lang);
  for (const trade of trades) {
    const openedAt = parseServerTimestamp(trade.opened_at);
    const closedAt = parseServerTimestamp(trade.closed_at);
    const entryTime = openedAt === null ? null : nearestBarTime(barTimes, openedAt);
    const exitTime = closedAt === null ? null : nearestBarTime(barTimes, closedAt);
    if (entryTime !== null) {
      markers.push({
        time: entryTime,
        position: "belowBar",
        color: BUY_COLOR,
        shape: "arrowUp",
        text: lang === "ko" ? "매수" : "Buy",
      });
    }
    if (exitTime !== null) {
      const returnPct = getRealizedReturnPct(trade);
      const sellLabel = lang === "ko" ? "매도" : "Sell";
      markers.push({
        time: exitTime,
        position: "aboveBar",
        color: SELL_COLOR,
        shape: "arrowDown",
        text: returnPct === null ? sellLabel : `${sellLabel} ${formatReturnPct(returnPct)}`,
      });
    }
  }
  return markers.toSorted((left, right) => left.time - right.time);
}

function buildCandleRunMarkers(
  bars: ChartBar[],
  mode: CandleRunMode,
  lang: string,
): MarkerInput[] {
  if (mode === "off") return [];

  const markers: MarkerInput[] = [];
  for (const run of findFirstThreeCandleRuns(bars)) {
    if (mode !== "both" && mode !== run.direction) continue;

    const isRising = run.direction === "up";
    for (let index = 0; index < run.bars.length; index += 1) {
      markers.push({
        time: run.bars[index].time,
        position: isRising ? "belowBar" : "aboveBar",
        color: isRising ? RISING_RUN_COLOR : FALLING_RUN_COLOR,
        shape: "circle",
        text: index === 2
          ? (lang === "ko"
              ? `3연속 ${isRising ? "상승" : "하락"}`
              : `3 ${isRising ? "rising" : "falling"}`)
          : "",
      });
    }
  }
  return markers;
}

function parseServerTimestamp(value?: string | null): number | null {
  if (!value) return null;
  const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  const milliseconds = new Date(normalized).getTime();
  return Number.isFinite(milliseconds) ? Math.floor(milliseconds / 1000) : null;
}

function isKoreanStockCode(code: string): boolean {
  return KOREAN_STOCK_CODE_PATTERN.test(code.trim());
}

function getRealizedReturnPct(trade: TradeTimingRecord): number | null {
  const reportedReturn = trade.net_pct ?? trade.realized_pnl_pct;
  if (reportedReturn != null && Number.isFinite(reportedReturn)) return reportedReturn;

  const entryPrice = trade.entry;
  const exitPrice = trade.exit_price;
  if (
    entryPrice == null
    || exitPrice == null
    || !Number.isFinite(entryPrice)
    || !Number.isFinite(exitPrice)
    || entryPrice <= 0
  ) {
    return null;
  }
  return ((exitPrice - entryPrice) / entryPrice) * 100;
}

function formatReturnPct(value: number): string {
  const rounded = Math.abs(value) < 0.005 ? 0 : value;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}%`;
}

function formatKstTime(
  time: unknown,
  lang: string,
  display: "tick" | "crosshair",
): string {
  if (typeof time !== "number" || !Number.isFinite(time)) return "";
  const date = new Date(time * 1000);
  const language = lang === "ko" ? "ko" : "en";
  const formatter = display === "tick"
    ? KST_TICK_FORMATTERS[language]
    : KST_CROSSHAIR_FORMATTERS[language];
  return formatter.format(date);
}

function nearestBarTime(times: number[], target: number): number | null {
  if (times.length === 0) return null;
  let low = 0;
  let high = times.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (times[middle] === target) return times[middle];
    if (times[middle] < target) low = middle + 1;
    else high = middle - 1;
  }
  const candidates = [times[Math.max(0, high)], times[Math.min(times.length - 1, low)]];
  const nearest = candidates.reduce((best, candidate) => (
    Math.abs(candidate - target) < Math.abs(best - target) ? candidate : best
  ));
  return Math.abs(nearest - target) <= MAX_MARKER_DISTANCE_SECONDS ? nearest : null;
}
