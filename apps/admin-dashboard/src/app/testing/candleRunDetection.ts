export type DirectionalCandle = {
  time: number;
  open: number;
  close: number;
};

export type CandleRun = {
  direction: "up" | "down";
  bars: [DirectionalCandle, DirectionalCandle, DirectionalCandle];
};

type CandleDirection = CandleRun["direction"] | "flat";

const directionOf = (bar: DirectionalCandle): CandleDirection => {
  if (bar.close > bar.open) return "up";
  if (bar.close < bar.open) return "down";
  return "flat";
};

export function findFirstThreeCandleRuns<T extends DirectionalCandle>(bars: T[]): CandleRun[] {
  const runs: CandleRun[] = [];
  for (let index = 0; index <= bars.length - 3; index += 1) {
    const direction = directionOf(bars[index]);
    if (direction === "flat") continue;

    const previousDirection = index > 0 ? directionOf(bars[index - 1]) : "flat";
    if (previousDirection === direction) continue;
    if (
      directionOf(bars[index + 1]) !== direction
      || directionOf(bars[index + 2]) !== direction
    ) {
      continue;
    }

    runs.push({
      direction,
      bars: [bars[index], bars[index + 1], bars[index + 2]],
    });
  }
  return runs;
}
