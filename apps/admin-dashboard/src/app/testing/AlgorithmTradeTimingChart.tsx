"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const BUY_COLOR = "#d32f2f";
const SELL_COLOR = "#1565c0";
const MAX_MARKER_DISTANCE_SECONDS = 10 * 60;

export type TradeTimingRecord = {
  ticker?: string | null;
  name: string;
  opened_at?: string | null;
  closed_at?: string | null;
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
  shape: "arrowUp" | "arrowDown";
  text: string;
};

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
          `/paper-desk/chart?code=${encodeURIComponent(selected.code)}&tf=5m`,
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
    api<ChartResponse>(`/paper-desk/chart?code=${encodeURIComponent(code)}&tf=5m`)
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
              "완료된 매수·매도 시점을 종목별 5분봉 위에서 확인하실 수 있습니다.",
              "Review completed entries and exits on each stock's five-minute candles.",
            )}
          </p>
        </div>
        {selected ? (
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="rounded-lg border px-2.5 py-1 text-[11px] font-bold text-[var(--text-secondary)] disabled:opacity-50"
            style={{ borderColor: "var(--border-default)" }}
          >
            {loading ? t("불러오는 중입니다", "Loading") : t("새로고침", "Refresh")}
          </button>
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
            "선택한 종목의 5분봉 데이터가 아직 없습니다.",
            "Five-minute candle data is not available for the selected stock yet.",
          )}
        </ChartState>
      ) : (
        <TimingCanvas bars={chartData.bars} trades={selectedTrades} lang={lang} />
      )}

      {selected ? (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-3 py-1.5 text-[10px] text-[var(--text-muted)]"
          style={{ borderColor: "var(--border-default)" }}
        >
          <span><b style={{ color: BUY_COLOR }}>▲ {t("매수", "Buy")}</b> {t("진입 시점입니다", "entry")}</span>
          <span><b style={{ color: SELL_COLOR }}>▼ {t("매도", "Sell")}</b> {t("청산 시점입니다", "exit")}</span>
          <span>{t("한국 표준시 기준이며 30초마다 갱신됩니다.", "KST · refreshes every 30 seconds.")}</span>
        </div>
      ) : null}
    </section>
  );
}

function TimingCanvas({
  bars,
  trades,
  lang,
}: {
  bars: ChartBar[];
  trades: TradeTimingRecord[];
  lang: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

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
        },
        rightPriceScale: { borderColor: "rgba(128,128,128,0.24)" },
      });
      const series = chart.addCandlestickSeries({
        upColor: BUY_COLOR,
        downColor: SELL_COLOR,
        borderUpColor: BUY_COLOR,
        borderDownColor: SELL_COLOR,
        wickUpColor: BUY_COLOR,
        wickDownColor: SELL_COLOR,
      });
      const validBars = bars
        .filter((bar) => Number.isFinite(bar.time) && Number.isFinite(bar.close))
        .toSorted((left, right) => left.time - right.time);
      series.setData(validBars as never);
      series.setMarkers(buildMarkers(validBars, trades, lang) as never);
      chart.timeScale().fitContent();
      cleanup = () => chart.remove();
    });

    return () => {
      active = false;
      cleanup();
    };
  }, [bars, trades, lang]);

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{ height: 360 }}
      role="img"
      aria-label={lang === "ko"
        ? "종목 5분봉과 알고리즘의 매수·매도 시점 차트"
        : "Five-minute candles with algorithm entry and exit markers"}
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

function buildMarkers(bars: ChartBar[], trades: TradeTimingRecord[], lang: string): MarkerInput[] {
  const barTimes = bars.map((bar) => bar.time);
  const markers: MarkerInput[] = [];
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
      markers.push({
        time: exitTime,
        position: "aboveBar",
        color: SELL_COLOR,
        shape: "arrowDown",
        text: lang === "ko" ? "매도" : "Sell",
      });
    }
  }
  return markers.toSorted((left, right) => left.time - right.time);
}

function parseServerTimestamp(value?: string | null): number | null {
  if (!value) return null;
  const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  const milliseconds = new Date(normalized).getTime();
  return Number.isFinite(milliseconds) ? Math.floor(milliseconds / 1000) : null;
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
