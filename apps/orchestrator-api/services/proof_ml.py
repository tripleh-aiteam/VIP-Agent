"""🤖 proof_ml — a per-company model that FILTERS the entry signals of a rule.

The rule decides WHEN a signal exists. This decides whether that particular signal is
worth taking. It never invents a trade of its own, so every trade it allows is still a
trade the rule itself produced — which is what makes "rule" and "rule + ML" comparable.

FOUR RULES THIS MODULE OBEYS, because breaking any of them produces a number that looks
like skill and is not:

  1. ONE MODEL PER COMPANY. Pooling the three fake stocks let a model separate them by
     price scale and score 0.64 AUC while knowing nothing about timing (measured
     2026-08-03). Per stock, that collapsed to 0.48 / 0.29 / 0.60.
  2. FEATURES KNOWABLE AT THE SIGNAL. Every input is computed from bars at or before the
     signal bar. Nothing about the outcome, nothing after it.
  3. TIME-ORDERED SPLIT. Train on the earlier part of the tape, trade only the later part.
     A random split leaks the future: neighbouring signals overlap in time and share
     outcomes, which inflated the same measurement to 0.64 from a true 0.56.
  4. THE BASELINE IS MEASURED ON THE SAME WINDOW. "+ML" is only meaningful against the
     same rule, over the same bars, on the same signals — not against its all-day figure.

Logistic regression is deliberate: its per-feature contribution is a number you can put
on screen next to a trade and say "this is what it saw". A forest would score the same
and explain nothing.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("vip.proof_ml")

# Each feature is (key, Korean, English) — the labels travel with the model so the
# explanation on screen is written once, here, and never drifts from what was trained.
FEATURES: list[tuple[str, str, str]] = [
    ("last_move", "직전 봉 변동", "last bar's move"),
    ("push", "신호 구간 상승폭", "the push into the signal"),
    ("range", "최근 변동폭", "recent range"),
    ("pos_in_range", "구간 내 위치", "position in that range"),
    ("accel", "가속도", "acceleration"),
    ("streak_size", "연속 봉 평균 크기", "average size of the run's bars"),
    ("since_last", "직전 신호 이후 경과", "bars since the last signal"),
    ("vol_rel", "거래량 대비", "volume vs recent"),
]
KEYS = [f[0] for f in FEATURES]

MIN_TRAIN = 60          # below this the fit is noise dressed as a model
TRAIN_FRAC = 0.65       # the earlier 65% trains; trades are taken only in the later 35%
# The bar a signal must clear is the rule's OWN base rate, not a fixed 0.5. A rule that
# wins 38% of the time produces a model that almost never rates anything above 0.5, so a
# fixed threshold silently turned three variants into "never trade" — which is not a
# filter, it is an off switch. Above the base rate means "better than this rule's average
# signal", which is the actual question being asked. MARGIN keeps it from trading on noise.
MARGIN = 0.02

_cache: dict[tuple, dict] = {}
_CACHE_MAX = 64


def features_at(cl: list[float], vols: list[float], i: int, last_sig: int) -> list[float]:
    """The model's view of bar i — built ONLY from bars at or before i.

    `i` is the signal bar. cl[i] is its close, which is known the moment it closes, and
    that is the same instant the rule fires. Nothing here reaches past i.
    """
    w = cl[max(0, i - 6): i + 1]
    lo, hi = min(w), max(w)
    rng = (hi - lo) / cl[i] * 100 if cl[i] else 0.0
    r1 = (cl[i] / cl[i - 1] - 1) * 100 if i >= 1 and cl[i - 1] else 0.0
    r2 = (cl[i - 1] / cl[i - 2] - 1) * 100 if i >= 2 and cl[i - 2] else 0.0
    push = (cl[i] / cl[max(0, i - 3)] - 1) * 100 if cl[max(0, i - 3)] else 0.0
    v = vols[max(0, i - 9): i + 1] or [0.0]
    vm = sum(v) / len(v)
    return [
        r1,
        push,
        rng,
        (cl[i] - lo) / (hi - lo) if hi > lo else 0.5,
        r1 - r2,                                   # is the push accelerating or fading
        abs(push) / 3.0,
        float(min(i - last_sig, 500)) if last_sig >= 0 else 500.0,
        (vols[i] / vm) if vm else 1.0,
    ]


def train(samples: list[tuple[list[float], int]], key: tuple) -> dict[str, Any] | None:
    """Fit on the EARLIER portion of the signals and report skill on the later portion.

    `samples` must already be in time order. Returns None when there is not enough to
    fit honestly — a model refusing to exist is a better answer than one guessing.
    """
    hit = _cache.get(key)
    if hit is not None and hit.get("n") == len(samples):
        return hit["bundle"]
    if len(samples) < MIN_TRAIN:
        return None
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler
    except Exception as e:                                  # pragma: no cover
        logger.warning("proof_ml: sklearn unavailable (%s)", e)
        return None

    # `samples` arrives ALREADY embargoed by the caller: every label here was settled
    # before trading begins. The split inside is only to measure skill on held-out data.
    cut = int(len(samples) * TRAIN_FRAC)
    Xtr = np.array([s[0] for s in samples[:cut]], dtype=float)
    ytr = np.array([s[1] for s in samples[:cut]], dtype=int)
    Xte = np.array([s[0] for s in samples[cut:]], dtype=float)
    yte = np.array([s[1] for s in samples[cut:]], dtype=int)
    if len(set(ytr.tolist())) < 2 or len(Xte) < 15:
        return None

    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(max_iter=3000, C=0.5).fit(sc.transform(Xtr), ytr)
    auc = None
    if len(set(yte.tolist())) == 2:
        auc = float(roc_auc_score(yte, m.predict_proba(sc.transform(Xte))[:, 1]))

    bundle = {
        "model": m, "scaler": sc, "cut": cut,
        "auc": auc,                       # skill on data the fit never saw
        "n_train": int(cut), "n_test": int(len(samples) - cut),
        "base_rate": float(ytr.mean()),
        "coef": [float(x) for x in m.coef_[0]],
        "mean": [float(x) for x in sc.mean_], "scale": [float(x) for x in sc.scale_],
    }
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = {"n": len(samples), "bundle": bundle}
    return bundle


def features_at_v2(cl, vols, i, last_sig, times, up_run, ctx):
    """V2 (2026-08-06 night, boss's order: use the 5-year data): the 8 tick features
    plus 4 more from the tape (run length, bar speed, day-so-far move, range position)
    plus 6+1 DAILY-CONTEXT numbers from the long tables - yesterday's momentum, gap,
    SMA ratio and the foreign/institutional flow signs. Context is strictly from days
    BEFORE the traded day; nothing here reaches past bar i."""
    base = features_at(cl, vols, i, last_sig)

    def _sec(t):
        p = t.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2] if len(p) > 2 else 0)
    j = max(0, i - 5)
    bar_secs = (_sec(times[i]) - _sec(times[j])) / max(1, i - j) if times else 0.0
    extra = [float(up_run), float(min(bar_secs, 60.0)),
             (cl[i] / cl[0] - 1) * 100 if cl[0] else 0.0,
             (cl[i] / max(cl[max(0, i - 60):i + 1]) - 1) * 100]
    return base + extra + list(ctx or [0.0] * 7)


def train_v2(samples: list[tuple[list[float], int]], key: tuple):
    """Bake-off on v2 features: logistic vs gradient boosting, winner by AUC on the
    last 25% of samples (time-ordered, so the split is a real walk-forward). Returns a
    v2 bundle, or None when there is too little to fit honestly - and None means the
    caller falls back to the v1 recipe, never to guessing."""
    if len(samples) < 80:
        return None
    import numpy as np
    X = np.array([f for f, _y in samples], dtype=float)
    Y = np.array([y for _f, y in samples], dtype=int)
    if Y.sum() < 8 or (1 - Y).sum() < 8:
        return None
    cut = int(len(X) * 0.75)
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    # HYPERPARAMETER TUNING (boss 2026-08-06 night). Every candidate is scored on the
    # SAME time-ordered tail split - later samples judge earlier fits, mirroring live
    # trading - and only the winner is refit on everything. The grid is small on
    # purpose: with hundreds of samples a big grid finds noise, not skill.
    cands = [("logreg C=1.0", lambda: make_pipeline(StandardScaler(),
                 LogisticRegression(max_iter=500, C=1.0))),
             ("logreg C=0.1", lambda: make_pipeline(StandardScaler(),
                 LogisticRegression(max_iter=500, C=0.1))),
             ("gbdt 120x3 lr.05", lambda: GradientBoostingClassifier(
                 n_estimators=120, max_depth=3, learning_rate=0.05,
                 subsample=0.8, random_state=7)),
             ("gbdt 240x3 lr.03", lambda: GradientBoostingClassifier(
                 n_estimators=240, max_depth=3, learning_rate=0.03,
                 subsample=0.8, random_state=7)),
             ("gbdt 120x2 lr.10", lambda: GradientBoostingClassifier(
                 n_estimators=120, max_depth=2, learning_rate=0.10,
                 subsample=0.8, random_state=7)),
             ("gbdt 300x4 lr.02", lambda: GradientBoostingClassifier(
                 n_estimators=300, max_depth=4, learning_rate=0.02,
                 subsample=0.7, random_state=7))]
    best = None
    for algo, mk in cands:
        try:
            m = mk()
            m.fit(X[:cut], Y[:cut])
            pv = m.predict_proba(X[cut:])[:, 1]
            auc = (roc_auc_score(Y[cut:], pv) if len(set(Y[cut:])) > 1 else 0.5)
            acc = float(accuracy_score(Y[cut:], pv >= 0.5))
            if best is None or auc > best["auc"]:
                best = {"v2": True, "model": m, "algo": algo,
                        "auc": round(float(auc), 3),
                        "val_acc": round(acc, 3),
                        "base_rate": float(Y.mean()), "n_train": len(X),
                        "n_test": len(X) - cut, "_val_p": pv, "_val_y": Y[cut:]}
        except Exception:
            continue
    if best is None:
        return None
    # ── CALIBRATION (boss 2026-08-07: "75% is confidence, not a guarantee" - some
    # models claimed 66-80% and delivered 0-53%). The winner stays fitted on the
    # TRAIN portion, and a Platt layer learns on the VALIDATION portion how much its
    # confidence historically exaggerates: raw p -> honest p. The desk's gate and the
    # share sizing then act on claims the model can actually back. No calibrator when
    # the validation slice is one-sided - a layer fitted on nothing would be a new lie.
    pv, yv = best.pop("_val_p"), best.pop("_val_y")
    if len(set(yv)) > 1 and len(yv) >= 20:
        from sklearn.linear_model import LogisticRegression as _LR
        cal = _LR(max_iter=200)
        cal.fit(np.array(pv, dtype=float).reshape(-1, 1), np.array(yv, dtype=int))
        best["cal"] = cal
    return best


def score(bundle: dict, feats: list[float]) -> dict[str, Any]:
    """P(this signal wins), and WHY — the per-feature push behind that number.

    Contribution = standardised feature x its coefficient, which for logistic regression
    is exactly what moved the odds. Ranked by size, so the explanation on screen is the
    real reason and not a plausible-sounding one."""
    import numpy as np
    if bundle.get("v2"):
        # v2 bundles are sklearn pipelines fitted on raw v2 features - no hand-rolled
        # standardisation, and the per-feature "why" of the logistic path does not
        # apply; the evidence panel still gets p, bar and the share count
        p = float(bundle["model"].predict_proba(np.array([feats], dtype=float))[0][1])
        if bundle.get("cal") is not None:
            # the honesty layer: raw confidence corrected by its own track record
            p = float(bundle["cal"].predict_proba(np.array([[p]], dtype=float))[0][1])
        return {"p": p,
                "why": [{"key": "v2", "ko": "5년 데이터 모델(" + bundle.get("algo", "?") + ")",
                         "en": "5-yr data model (" + bundle.get("algo", "?") + ")",
                         "value": round(p, 4), "push": 0.0, "for": p >= 0.5}]}
    z = [(feats[j] - bundle["mean"][j]) / (bundle["scale"][j] or 1.0)
         for j in range(len(feats))]
    contrib = [z[j] * bundle["coef"][j] for j in range(len(feats))]
    p = float(bundle["model"].predict_proba(np.array([z], dtype=float))[0][1])
    order = sorted(range(len(contrib)), key=lambda j: -abs(contrib[j]))
    return {
        "p": p,
        "why": [{"key": KEYS[j], "ko": FEATURES[j][1], "en": FEATURES[j][2],
                 "value": round(feats[j], 4), "push": round(contrib[j], 4),
                 "for": contrib[j] > 0} for j in order[:3]],
    }

# ── HOW MANY SHARES: the model's confidence turned into a real position ────────────
# The boss asked for a share count he can DEFEND: "we should have a reason if someone
# asks why you bought 2k shares — then I will tell this is the ML prediction." So the
# number is a model output, not a setting, and it is reproducible from the model's own
# probability.
#
# RISK IS CAPPED BY PRICE, to his specification (2026-08-04). A thousand shares means
# something completely different on a ₩1,500,000 stock than on a ₩19,000 one, so the
# ceiling is set per price band and the model only ever chooses a fraction of it:
#
#     over ₩1,000,000   ->     10 shares max   (SK하이닉스: ₩15.6m at the cap)
#     over ₩100,000     ->    100 shares max   (삼성전자:   ₩23.9m at the cap)
#     below that        ->  1,000 shares max   (한화오션:   ₩87.0m at the cap)
#
# TEN TIMES FEWER SHARES FOR TEN TIMES THE PRICE (boss 2026-08-05). This brings the
# biggest position to 5.6x the smallest, down from 12.5x when every band was 1,000.
#
# The residual spread is INSIDE a band, not between them: 한화오션 at ₩87,000 and
# 시뮬중공업 at ₩19,150 both take 1,000 shares, which is ₩87m against ₩19m. No share-count
# rule can fix that - a band spanning ₩1 to ₩100,000 treats stocks 5x apart in price as
# equals. Setting the MONEY per trade and deriving the shares removes it exactly; he
# preferred to keep share counts, and this is much the better version of that.
#
# THE CHEAP BAND WAS 100,000 AND IT DROWNED EVERYTHING (boss 2026-08-05: "한화오션 is
# 100K so it decreases a lot our gain"). At 100,000 shares a ₩87,000 stock is ₩8.7bn of
# exposure - fifty-six times the SK하이닉스 cap - so one cheap stock decided every total
# on the board while the expensive one, where the edge actually lives, contributed almost
# nothing. 10,000 brings the three bands within about 5x of each other instead of 56x.
#
# ONE THING TO KEEP IN VIEW, said once and then left alone: quantity multiplies the
# result and cannot change its sign. Every rule here currently loses per trade, so a
# bigger position produces a bigger loss in exactly the same proportion. The value of
# this is that the size is now explainable and consistent - it is not a way to turn a
# losing rule into a winning one.
FLOOR_FRAC = 0.05        # the least confident accepted signal still takes 5% of the cap
FULL_EDGE = 0.10         # this much edge over the model's own bar earns the whole cap


def cap_for(price: float) -> int:
    """The most shares allowed at this price — the boss's risk bands."""
    if price > 1_000_000:
        return 10
    if price > 100_000:
        return 100
    return 1_000


def quantity(p: float, bar: float, price: float = 0.0) -> int:
    """Shares for a signal the model scored `p`, against its own acceptance bar.

    A signal only reaches here if p >= bar, so `edge` is never negative. The edge is
    scaled against FULL_EDGE rather than against 1.0, because `p` on accepted signals sits
    a few points above the bar and never near certainty — dividing by 1.0 would mean the
    model effectively always asked for the floor and the cap would be dead code.
    """
    if price <= 0:
        return 1
    edge = max(0.0, float(p) - float(bar))
    frac = min(1.0, max(FLOOR_FRAC, edge / FULL_EDGE))
    return max(1, int(round(cap_for(price) * frac)))
