"""leaderboard.py — average each algorithm's walk-forward metrics across ALL
stocks, so we can rank the 11 algorithms head-to-head (not just the winners).

Usage: python ml/models/leaderboard.py --horizon 5
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))      # ml/models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/
from bakeoff import bakeoff_ticker  # noqa: E402
from _db import get_conn            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM stock_features_daily ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]

    agg = defaultdict(lambda: {"acc": [], "f1": [], "up": [], "base": [], "beat": 0, "win": 0})
    n = 0
    for t in tickers:
        res = bakeoff_ticker(conn, t, args.horizon, False)
        if not res:
            continue
        n += 1
        base = res["baseline"]
        for algo, m in res["all"].items():
            a = agg[algo]
            a["acc"].append(m["accuracy"]); a["f1"].append(m["f1_macro"])
            a["up"].append(m["up_recall"]); a["base"].append(base)
            if m["accuracy"] > base:
                a["beat"] += 1
        agg[res["best"]]["win"] += 1
    conn.close()

    rows = []
    for algo, d in agg.items():
        acc = float(np.mean(d["acc"])); edge = acc - float(np.mean(d["base"]))
        rows.append((algo, acc, float(np.mean(d["f1"])), float(np.mean(d["up"])),
                     edge, d["beat"], d["win"]))
    rows.sort(key=lambda r: -r[1])

    print(f"\n=== ALGORITHM LEADERBOARD (horizon {args.horizon}d, {n} stocks, walk-forward avg) ===")
    print(f"{'rank':<5}{'algorithm':<16}{'avg_acc':>9}{'avg_f1':>9}{'up_recall':>11}"
          f"{'avg_edge':>10}{'beat/'+str(n):>9}{'wins':>7}")
    print("-" * 76)
    for i, (algo, acc, f1, up, edge, beat, win) in enumerate(rows, 1):
        print(f"{i:<5}{algo:<16}{acc:>9.3f}{f1:>9.3f}{up:>11.3f}{edge:>+10.3f}{beat:>6}/{n:<2}{win:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
