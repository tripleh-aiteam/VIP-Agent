# Removed features — and how to bring them back

## 2026-08-13 — the legacy Strategy-Lab algorithms (boss's order: "our app is
## heavy, remove these; if we need later we can recreate")

Removed from the testing index (code kept in the repo, pages unlinked, engines
idle — nothing was deleted, so recreation is a matter of re-linking):

1. **🧠 Algorithm 1 — 1-hour engine trading** (decision engine: ML + news +
   chart + history patterns; +1% target / −1% stop / 1-hour plans).
2. **⚡ Algorithm 2 — ripple scalper** (+0.4% quick wins, −1% cut).
3. **🕯️ Algorithm 3 — candle trader** (3 up → buy, 3 down → sell).
4. **🔀 Cross-Check** (buys only when 1·2·3 agree).

The live Kiwoom desk (알고리즘1/알고리즘2/예전규칙 with the boss's law-book)
is NOT part of this removal — it is the product.

## THE STANDING PROMISE (boss, verbatim intent)

> "If I ask ML model implementation to predict buy or sell, you have to
> remember."

When the boss asks for an ML buy/sell prediction model again:
- The old per-stock ML machinery lives in `services/proof_ml.py`
  (features_at / features_at_v2, score, quantity, MARGIN) and
  `services/kiwoom_rules.py::kiwoom_ml_for` (per-day retraining with
  yesterday-only data, honest train/test split).
- The lesson learned on 08-10: NEVER train on another rule's target — labels
  must be the deployed exit's own outcomes on the deployed entry's signals.
- The natural v2: train on the drip law's episode outcomes (entry features →
  episode net %), gate entries with p > base_rate + margin, size with
  confidence, and A/B it against the bare algorithm exactly like the old
  ML-vs-plain paired rows.
