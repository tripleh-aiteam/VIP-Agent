"""auto_retrain.py — fire the daily retrain+predict pipeline automatically.

The model should retrain ONCE a day (not every second), on the latest data. Run this
once and leave the PC on; it sleeps until the next run time, executes daily_run.py
(retrain → backtest → predict), then sleeps to the next day. No Windows Task Scheduler
needed — it IS the scheduler.

    python ml/auto_retrain.py                 # daily at 16:30 KST (after market close)
    python ml/auto_retrain.py --at 17:00      # custom time (24h KST)
    python ml/auto_retrain.py --now           # run once immediately, then daily
    python ml/auto_retrain.py --no-retrain    # only refresh data + predict each day

Idempotent + safe to restart. Logs each run's start/finish + the next fire time.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))


def _run(extra: list[str]) -> int:
    cmd = [sys.executable, str(HERE / "daily_run.py"), *extra]
    print(f"[auto_retrain] {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST — starting daily_run",
          flush=True)
    rc = subprocess.run(cmd, cwd=str(HERE.parent)).returncode
    print(f"[auto_retrain] daily_run finished rc={rc}", flush=True)
    return rc


def _seconds_until(hh: int, mm: int) -> float:
    now = datetime.now(KST)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="16:30", help="daily run time, 24h KST (HH:MM)")
    ap.add_argument("--now", action="store_true", help="run once immediately first")
    ap.add_argument("--no-retrain", action="store_true", help="skip bake-off, just predict")
    args = ap.parse_args()
    hh, mm = (int(x) for x in args.at.split(":"))
    extra = ["--no-retrain"] if args.no_retrain else []

    print(f"[auto_retrain] daily {'data+predict' if args.no_retrain else 'RETRAIN+predict'} "
          f"at {args.at} KST. Leave this running.", flush=True)
    if args.now:
        _run(extra)

    while True:
        secs = _seconds_until(hh, mm)
        nxt = datetime.now(KST) + timedelta(seconds=secs)
        print(f"[auto_retrain] sleeping until {nxt:%Y-%m-%d %H:%M} KST "
              f"({secs/3600:.1f}h)", flush=True)
        time.sleep(secs)
        try:
            _run(extra)
        except Exception as e:                       # never let one failure kill the loop
            print(f"[auto_retrain] run error: {str(e)[:120]}", flush=True)
        time.sleep(90)                               # avoid double-fire within the minute


if __name__ == "__main__":
    sys.exit(main())
