"""daily_run.py — the once-a-day pipeline that keeps predictions current.

Runs OFF Render (on the user's PC / a worker), after the Korean market close:
    1. backfill_prices.py   — pull the latest daily bars (idempotent upsert)
    2. build_features.py     — recompute features incl. the new day
    3. predict.py            — write fresh BUY/SELL/HOLD to model_predictions

The orchestrator (Render) then just READS the updated model_predictions.
Schedule with ml/daily_run.bat via Windows Task Scheduler (see that file).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ml/
ROOT = HERE.parent                              # orchestrator-api/

STEPS = [
    ([sys.executable, str(HERE / "data" / "backfill_prices.py"), "--start", "2024-01-01"],
     "1/3 update prices"),
    ([sys.executable, str(HERE / "features" / "build_features.py")],
     "2/4 rebuild features"),
    ([sys.executable, str(HERE / "features" / "market_features.py")],
     "3/4 add market/macro features"),
    ([sys.executable, str(HERE / "models" / "predict.py"), "--horizon", "5"],
     "4/4 predict BUY/SELL/HOLD"),
]


def main() -> int:
    for cmd, label in STEPS:
        print(f"\n===== daily_run: {label} =====", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"[daily_run] STEP FAILED ({label}) rc={r.returncode}")
            return r.returncode
    print("\n[daily_run] DONE — predictions refreshed in model_predictions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
