"""predict.py — score the latest features with each stock's saved model and write
BUY/SELL/HOLD rows to model_predictions (which the orchestrator reads).

Honesty rules baked in:
  - A model with edge <= 0 (no proven out-of-sample edge) NEVER yields BUY/SELL —
    it is forced to HOLD. We only act where the model actually beats the baseline.
  - Every row carries backtest_acc + baseline so the advice is shown WITH its track
    record, never as a guarantee. Expected move is a 1-sigma estimate, marked 추정.

Usage:  python ml/models/predict.py --horizon 5
Runs OFF Render (needs sklearn/xgboost/lightgbm to unpickle). Writes to Supabase.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402

NAMES = {
    "012450": "한화에어로스페이스", "047810": "한국항공우주", "079550": "LIG넥스원",
    "064350": "현대로템", "272210": "한화시스템", "329180": "HD현대중공업",
    "009540": "HD한국조선해양", "010140": "삼성중공업", "042660": "한화오션",
    "005930": "삼성전자", "000660": "SK하이닉스", "042700": "한미반도체",
    "373220": "LG에너지솔루션", "006400": "삼성SDI", "005490": "POSCO홀딩스",
    "247540": "에코프로비엠", "005380": "현대차", "000270": "기아",
    "012330": "현대모비스", "034020": "두산에너빌리티", "052690": "한전기술",
    "015760": "한국전력", "035420": "NAVER", "035720": "카카오", "018260": "삼성SDS",
    "207940": "삼성바이오로직스", "068270": "셀트리온", "010950": "S-OIL",
    "078930": "GS", "096770": "SK이노베이션", "105560": "KB금융", "055550": "신한지주",
    "138040": "메리츠금융지주", "003490": "대한항공", "272450": "진에어",
    "039130": "하나투어", "069500": "KODEX 200",
}


def _conf(prob: float) -> str:
    if prob >= 0.45:
        return "높음"
    if prob >= 0.38:
        return "보통"
    return "낮음"


def _decide(direction: int, conf: str, edge: float) -> str:
    """Honest decision: only act when the model has real edge AND decent confidence."""
    if edge is None or edge <= 0.0:        # no proven edge -> never act
        return "HOLD"
    if conf == "낮음":
        return "HOLD"
    if direction == 1:
        return "BUY"
    if direction == -1:
        return "SELL"
    return "HOLD"


def predict_ticker(conn, ticker: str, horizon: int) -> dict | None:
    import joblib
    cur = conn.cursor()
    cur.execute("SELECT model_name, metrics, model_blob FROM model_registry "
                "WHERE ticker=%s AND horizon=%s AND is_active", (ticker, horizon))
    row = cur.fetchone()
    if not row:
        return None
    model_name, metrics, blob = row
    metrics = metrics if isinstance(metrics, dict) else json.loads(metrics)
    # joblib.load is safe here: the blob is OUR OWN model, trained by ml/models/
    # bakeoff.py and written to our own Supabase model_registry — not untrusted input.
    bundle = joblib.load(io.BytesIO(bytes(blob)))
    model, le, feats = bundle["model"], bundle["le"], bundle["features"]

    # latest feature row
    cols = ", ".join(feats)
    cur.execute(f"SELECT date, close, realized_vol_20, rsi_14, {cols} "
                f"FROM stock_features_daily WHERE ticker=%s ORDER BY date DESC LIMIT 1", (ticker,))
    r = cur.fetchone()
    if not r:
        return None
    as_of, close, rvol, rsi = r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0)
    x = r[4:]
    if any(v is None for v in x):
        return None
    X = np.array([[float(v) for v in x]])

    # predict
    try:
        proba = model.predict_proba(X)[0]
        cls_idx = int(np.argmax(proba))
        prob = float(proba[cls_idx])
        direction = int(le.inverse_transform([cls_idx])[0])
    except Exception:
        direction = int(le.inverse_transform(model.predict(X))[0])
        prob = 0.34

    conf = _conf(prob)
    edge = metrics.get("edge")
    advice = _decide(direction, conf, edge)

    # 1-sigma 5-day move estimate from the stock's own volatility (추정)
    band = (rvol / 100.0) / math.sqrt(252) * math.sqrt(horizon) * 100 if rvol else 2.0
    band = round(band, 2)
    exp_mid = round(direction * band / 2, 2)
    dlabel = {1: "▲상승", 0: "→중립", -1: "▼하락"}[direction]

    # buy/sell levels (추정)
    buy_px = sell_px = None
    if advice == "BUY":
        buy_px = round(close * (1 - 0.003 * band))      # slight dip entry
        sell_px = round(close * (1 + band / 100))       # +1 sigma target
    elif advice == "SELL":
        sell_px = round(close * (1 + 0.003 * band))
        buy_px = round(close * (1 - band / 100))

    reason = (f"{NAMES.get(ticker, ticker)} · {model_name} 모델 (백테스트 정확도 "
              f"{metrics.get('accuracy', 0)*100:.1f}%, 엣지 {edge:+.3f}) · 5일 방향 {dlabel} "
              f"(확률 {prob*100:.0f}%, 신뢰도 {conf}) · RSI {rsi:.0f} · 예상 변동폭 ±{band}%(추정)")

    return {
        "ticker": ticker, "as_of": as_of, "horizon": horizon, "direction": dlabel,
        "probability": round(prob, 4), "expected_move_pct": exp_mid,
        "expected_low_pct": -band, "expected_high_pct": band, "confidence": conf,
        "advice": advice, "model_name": model_name,
        "backtest_acc": metrics.get("accuracy"), "reasoning": reason,
        "buy_px": buy_px, "sell_px": sell_px, "edge": edge, "close": close,
    }


def _save(conn, p: dict):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_predictions (ticker,as_of,horizon,direction,probability,"
            "expected_move_pct,expected_low_pct,expected_high_pct,confidence,advice,"
            "model_name,backtest_acc,reasoning) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (ticker,as_of,horizon) DO UPDATE SET "
            "direction=EXCLUDED.direction, probability=EXCLUDED.probability, "
            "expected_move_pct=EXCLUDED.expected_move_pct, expected_low_pct=EXCLUDED.expected_low_pct, "
            "expected_high_pct=EXCLUDED.expected_high_pct, confidence=EXCLUDED.confidence, "
            "advice=EXCLUDED.advice, model_name=EXCLUDED.model_name, "
            "backtest_acc=EXCLUDED.backtest_acc, reasoning=EXCLUDED.reasoning, created_at=now()",
            (p["ticker"], p["as_of"], p["horizon"], p["direction"], p["probability"],
             p["expected_move_pct"], p["expected_low_pct"], p["expected_high_pct"],
             p["confidence"], p["advice"], p["model_name"], p["backtest_acc"], p["reasoning"]))
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--tickers", nargs="+")
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    if args.tickers:
        tickers = args.tickers
    else:
        cur.execute("SELECT ticker FROM model_registry WHERE horizon=%s AND is_active ORDER BY ticker",
                    (args.horizon,))
        tickers = [r[0] for r in cur.fetchall()]

    buys, sells, holds = [], [], []
    for t in tickers:
        try:
            p = predict_ticker(conn, t, args.horizon)
        except Exception as e:
            print(f"  {t} ERROR: {str(e)[:80]}"); continue
        if not p:
            continue
        _save(conn, p)
        (buys if p["advice"] == "BUY" else sells if p["advice"] == "SELL" else holds).append(p)

    conn.close()
    print(f"\n=== PREDICTIONS WRITTEN (horizon {args.horizon}d) ===")
    print(f"BUY {len(buys)} | SELL {len(sells)} | HOLD {len(holds)}\n")
    for p in sorted(buys, key=lambda x: -(x['edge'] or 0)):
        print(f"  BUY  {NAMES.get(p['ticker'],p['ticker']):<14} conf={p['confidence']} "
              f"edge={p['edge']:+.3f} 목표 {p['sell_px']}")
    for p in sorted(sells, key=lambda x: -(x['edge'] or 0)):
        print(f"  SELL {NAMES.get(p['ticker'],p['ticker']):<14} conf={p['confidence']} edge={p['edge']:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
