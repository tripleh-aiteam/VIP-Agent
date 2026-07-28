import assert from "node:assert/strict";
import test from "node:test";

import { findFirstThreeCandleRuns } from "../src/app/testing/candleRunDetection.ts";

const bar = (time, open, close) => ({ time, open, close });

test("returns only the first three candles from a longer bullish run", () => {
  const runs = findFirstThreeCandleRuns([
    bar(1, 100, 101),
    bar(2, 101, 102),
    bar(3, 102, 103),
    bar(4, 103, 104),
    bar(5, 104, 105),
  ]);

  assert.deepEqual(runs, [{
    direction: "up",
    bars: [
      bar(1, 100, 101),
      bar(2, 101, 102),
      bar(3, 102, 103),
    ],
  }]);
});

test("detects bullish and bearish runs independently", () => {
  const runs = findFirstThreeCandleRuns([
    bar(1, 100, 101),
    bar(2, 101, 102),
    bar(3, 102, 103),
    bar(4, 103, 102),
    bar(5, 102, 101),
    bar(6, 101, 100),
    bar(7, 100, 99),
  ]);

  assert.deepEqual(
    runs.map((run) => ({
      direction: run.direction,
      times: run.bars.map((item) => item.time),
    })),
    [
      { direction: "up", times: [1, 2, 3] },
      { direction: "down", times: [4, 5, 6] },
    ],
  );
});

test("flat candles break a run and two-candle runs are ignored", () => {
  const runs = findFirstThreeCandleRuns([
    bar(1, 100, 101),
    bar(2, 101, 102),
    bar(3, 102, 102),
    bar(4, 102, 101),
    bar(5, 101, 100),
    bar(6, 100, 100),
  ]);

  assert.deepEqual(runs, []);
});
