-- ============================================================================
--  ML stock-prediction pipeline — Supabase schema
--  Two layers:  RAW (one table per source, append-only, point-in-time)
--               →  GOLD: stock_features_daily  (one row per ticker per day)
--               →  model_registry / model_predictions (what the agent reads)
--
--  Keys are always (ticker, date) or (ticker, ts) so sources can be joined.
--  Re-running collectors is idempotent (ON CONFLICT upsert).
--  Safe to run multiple times: every object uses IF NOT EXISTS.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- RAW LAYER (bronze) — each source in its own native shape
-- ---------------------------------------------------------------------------

-- Daily OHLCV history (FinanceDataReader / Kiwoom historical). The backbone.
CREATE TABLE IF NOT EXISTS raw_daily_prices (
    ticker      TEXT        NOT NULL,
    date        DATE        NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      BIGINT,
    change_pct  NUMERIC,                       -- daily % change if source provides
    source      TEXT        DEFAULT 'fdr',
    ingested_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_raw_daily_prices_date ON raw_daily_prices (date);

-- Intraday 1-minute bars (rt_kiwoom real-time collector). The speed edge.
CREATE TABLE IF NOT EXISTS raw_intraday_bars (
    ticker      TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,          -- minute bucket (KST-aware)
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      BIGINT,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, ts)
);
CREATE INDEX IF NOT EXISTS idx_raw_intraday_ts ON raw_intraday_bars (ts);

-- News + sentiment (Naver, Newspaper, our Breaking gather). Many rows/day.
CREATE TABLE IF NOT EXISTS raw_news (
    id          BIGSERIAL   PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,          -- published time (point-in-time!)
    ticker      TEXT,                          -- nullable: market-wide if NULL
    source      TEXT,
    url         TEXT,
    title       TEXT,
    snippet     TEXT,
    sentiment   NUMERIC,                       -- -1..+1
    relevance   NUMERIC,                       -- 0..1
    raw         JSONB,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (url, ticker)
);
CREATE INDEX IF NOT EXISTS idx_raw_news_ticker_ts ON raw_news (ticker, ts);

-- YouTube transcripts + sentiment (sparse).
CREATE TABLE IF NOT EXISTS raw_youtube (
    id          BIGSERIAL   PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    video_id    TEXT,
    channel     TEXT,
    title       TEXT,
    transcript  TEXT,
    sentiment   NUMERIC,
    tickers     TEXT[],                        -- tickers mentioned
    views       BIGINT,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (video_id)
);

-- DART official disclosures (highest-signal company events). Optional, high value.
CREATE TABLE IF NOT EXISTS raw_disclosures (
    id          BIGSERIAL   PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    ticker      TEXT,
    rcept_no    TEXT,                          -- DART receipt no
    type        TEXT,                          -- 실적/유상증자/M&A/계약 ...
    title       TEXT,
    detail      JSONB,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (rcept_no)
);
CREATE INDEX IF NOT EXISTS idx_raw_disclosures_ticker_ts ON raw_disclosures (ticker, ts);

-- ---------------------------------------------------------------------------
-- GOLD LAYER — the unified feature table the ML model trains on.
--   One row per (ticker, date). Built by build_features.py joining the raw
--   tables down to a common daily grain. Point-in-time: every feature uses
--   ONLY data available on/ before `date`; labels look FORWARD.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_features_daily (
    ticker          TEXT  NOT NULL,
    date            DATE  NOT NULL,

    -- price context
    close           NUMERIC,
    volume          BIGINT,

    -- returns
    ret_1d          NUMERIC,
    ret_5d          NUMERIC,
    ret_20d         NUMERIC,
    gap_open        NUMERIC,                   -- overnight gap

    -- trend
    sma_5           NUMERIC,
    sma_20          NUMERIC,
    sma_60          NUMERIC,
    ema_12          NUMERIC,
    ema_26          NUMERIC,
    macd            NUMERIC,
    macd_signal     NUMERIC,
    adx_14          NUMERIC,
    price_vs_sma20  NUMERIC,                   -- close/sma20 - 1

    -- momentum
    rsi_14          NUMERIC,
    stoch_k         NUMERIC,                   -- paper 1: most predictive feature
    stoch_d         NUMERIC,
    roc_10          NUMERIC,

    -- volatility
    bb_upper        NUMERIC,
    bb_lower        NUMERIC,
    bb_pct          NUMERIC,                   -- position within Bollinger band
    atr_14          NUMERIC,
    realized_vol_20 NUMERIC,                   -- 20d annualized realized vol

    -- volume
    volume_z_20     NUMERIC,                   -- volume z-score vs 20d
    obv             NUMERIC,

    -- fundamental (slow-moving)
    per             NUMERIC,
    pbr             NUMERIC,
    market_cap      NUMERIC,
    div_yield       NUMERIC,

    -- sentiment / attention (OUR EDGE — the papers lacked these)
    news_sentiment_mean NUMERIC,
    news_count          INTEGER DEFAULT 0,     -- buzz
    news_sentiment_std  NUMERIC,               -- disagreement
    naver_buzz          NUMERIC,               -- search trend proxy
    youtube_sentiment   NUMERIC,
    breaking_severity   INTEGER,               -- from our breaking engine (0-10)
    disclosure_flag     INTEGER DEFAULT 0,     -- 1 if a DART filing that day

    -- LABELS (forward-looking targets)
    fwd_ret_1d      NUMERIC,
    fwd_ret_5d      NUMERIC,
    fwd_ret_20d     NUMERIC,
    label_dir_1d    SMALLINT,                  -- 1 up / 0 flat / -1 down
    label_dir_5d    SMALLINT,
    label_dir_20d   SMALLINT,

    built_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_features_date ON stock_features_daily (date);

-- ---------------------------------------------------------------------------
-- MODEL REGISTRY — the best model chosen per (ticker, horizon) by the bake-off
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    ticker        TEXT      NOT NULL,
    horizon       SMALLINT  NOT NULL,          -- 1 / 5 / 20 day
    model_name    TEXT,                        -- xgboost / lightgbm / svm_rbf ...
    metrics       JSONB,                       -- {accuracy, f1, up_recall, auc, backtest_return, baseline_acc}
    feature_list  JSONB,                       -- columns used
    model_blob    BYTEA,                       -- pickled model (or path if external)
    trained_at    TIMESTAMPTZ DEFAULT now(),
    is_active     BOOLEAN   DEFAULT true,
    PRIMARY KEY (ticker, horizon)
);

-- ---------------------------------------------------------------------------
-- MODEL PREDICTIONS — what the agent/reports READ (thin, no ML deps to read)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_predictions (
    id              BIGSERIAL   PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    as_of           DATE        NOT NULL,      -- prediction made for this date
    horizon         SMALLINT    NOT NULL,
    direction       TEXT,                      -- UP / DOWN / FLAT
    probability     NUMERIC,                   -- model prob of the predicted class
    expected_move_pct NUMERIC,                 -- estimated move (band midpoint)
    expected_low_pct  NUMERIC,
    expected_high_pct NUMERIC,
    confidence      TEXT,                      -- 높음/보통/낮음
    advice          TEXT,                      -- BUY / SELL / HOLD
    model_name      TEXT,
    backtest_acc    NUMERIC,                   -- honesty: shown next to advice
    reasoning       TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (ticker, as_of, horizon)
);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker_asof ON model_predictions (ticker, as_of);
