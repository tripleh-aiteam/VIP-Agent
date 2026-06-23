"""daily_run.py — the once-a-day pipeline that RETRAINS + re-predicts.

Runs OFF Render (on the user's PC / a worker), after the Korean market close. Each
run uses the newest day's data end-to-end:
    1. backfill_prices    — pull the latest daily bars (idempotent upsert)
    2. backfill_flows     — latest 외국인/기관 수급
    3. build_features     — recompute features incl. the new day
    4. market_features    — macro/regime
    5. flow_features      — 수급 features
    6. bakeoff (RETRAIN)  — re-pick the best algorithm per stock on the new data
    7. backtest --save    — refresh the economic edge that gates BUY/SELL
    8. predict            — write fresh BUY/SELL/HOLD (+ levels) to model_predictions

The model is NOT retrained every second — it's retrained ONCE per run (daily). The
bake-off uses a fixed random_state so a retrain on essentially-the-same data gives the
same winners (stable), only shifting as real new data accumulates. The orchestrator
(Render) just READS model_predictions; the heavy ML stays here.

Automatic daily scheduling: run `python ml/auto_retrain.py` once and leave the PC on
(it fires this pipeline every day at 16:30 KST), or schedule ml/daily_run.bat.

Flags:
    --no-retrain   skip the bake-off/backtest (just refresh data + predict, faster)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ml/
ROOT = HERE.parent                              # orchestrator-api/
HORIZON = "5"

DATA_STEPS = [
    ([sys.executable, str(HERE / "data" / "backfill_prices.py"), "--start", "2024-01-01"],
     "update prices"),
    ([sys.executable, str(HERE / "data" / "backfill_flows.py"), "--pages", "3"],
     "update 수급/investor flows"),
    ([sys.executable, str(HERE / "features" / "build_features.py")],
     "rebuild features"),
    ([sys.executable, str(HERE / "features" / "market_features.py")],
     "add market/macro features"),
    ([sys.executable, str(HERE / "features" / "flow_features.py")],
     "add 수급/flow features"),
]
RETRAIN_STEPS = [
    ([sys.executable, str(HERE / "models" / "bakeoff.py"), "--horizon", HORIZON],
     "RETRAIN best model per stock"),
    ([sys.executable, str(HERE / "models" / "backtest.py"), "--save"],
     "refresh economic edge (gates BUY/SELL)"),
]
PREDICT_STEP = [
    ([sys.executable, str(HERE / "models" / "predict.py"), "--horizon", HORIZON],
     "predict BUY/SELL/HOLD (+ levels/time)"),
]


def main() -> int:
    retrain = "--no-retrain" not in sys.argv
    steps = DATA_STEPS + (RETRAIN_STEPS if retrain else []) + PREDICT_STEP
    total = len(steps)
    for i, (cmd, label) in enumerate(steps, 1):
        print(f"\n===== daily_run {i}/{total}: {label} =====", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"[daily_run] STEP FAILED ({label}) rc={r.returncode}")
            return r.returncode
    print(f"\n[daily_run] DONE — {'retrained + ' if retrain else ''}predictions refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
