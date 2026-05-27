import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
XAI_API_KEY       = os.getenv("XAI_API_KEY", "")
NEWS_API_KEY      = os.getenv("NEWS_API_KEY", "")

# --- Alpaca (live trading) ---
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
# Paper trading URL: https://paper-api.alpaca.markets
# Live trading URL:  https://api.alpaca.markets
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
# Master kill switch — set LIVE_ENABLED=true in .env to allow real order placement
LIVE_ENABLED      = os.getenv("LIVE_ENABLED", "false").lower() == "true"

# --- Signal thresholds ---
LOCK1_THRESHOLD       = 0.70
LOCK2_SENTIMENT_MIN   = 0.1
LOCK3_CONFIDENCE_MIN  = 0.60

# Sectors excluded from entry evaluation — no validated momentum edge.
# Determined by 2021-2026 threshold sweep (2026-05-07): PF < 1.0 across all
# viable threshold levels; structurally incompatible with momentum-based signals.
# Lock 1 (Eligibility) blocks these before any further evaluation.
EXCLUDED_SECTORS: dict[str, str] = {
    "Financials":      "Rate-driven sector; momentum signal has no structural validity; PF 0.46 at baseline, no crossover at any threshold",
    "Utilities":       "Rate-driven sector; PF never crosses 1.0 across 0.55–0.80 sweep; incompatible with momentum model. Anti-correlation confirmed: post≥0.50 PF 0.690 (2026-05-18 conditioned sweep) — same defensive rotation mechanism as ConsumerStaples.",
    "ConsumerStaples": "Anti-correlated with regime posterior: post≥0.60 PF 0.554 (2026-05-17 conditioned sweep). High posterior = defensive rotation = momentum entry conditions worst. Exclusion revalidated. KO exception: PF 2.025 at post≥0.50 (4 trades), manual-only.",
    "Metals":          "PF never crosses 1.0 at any threshold (0.55–0.75 sweep, isolated backtest, 2026-05-18). Best: PF 0.81 at 0.55. Commodity-driven momentum not captured by APEX signal stack. ETF: XME.",
}

# Minimum Lock 2 signal score floors per sector, applied above the calibrated
# threshold when calibration landed below a sweep-validated edge threshold.
SECTOR_THRESHOLD_FLOORS: dict[str, float] = {
    "ConsumerDisc": 0.75,  # Portfolio sweep floor; isolated sweep (AMZN excluded) at 0.75 → 9 trades (unreadable). Floor unvalidated — stay at global 0.70 until N accumulates. 2026-05-22.
    # Defense: floor removed 2026-05-18. Portfolio sweep PF 2.76 at 0.75 was contaminated.
    # Isolated sweep: 0.75 → 22 trades PF 3.504 (noise; cliff to 0 at 0.80); 0.70 → 80 trades PF 1.692.
    # Per-ticker drag from NOC (0.634) and GD (0.728) at baseline; LMT (6.696) and RTX (2.249) carry it.
    # Best stable edge is 0.65 (PF 1.923, 257 trades) but below-baseline floors require separate mechanism.
}

# --- Polling intervals (minutes) ---
POLL_INTERVAL_SECTORS = 15
GATE_INTERVAL         = 20
EXIT_CHECK_INTERVAL   = 5
GROK_CACHE_TTL        = 60  # minutes

# --- Wallet ---
STARTING_BALANCE      = 2_000.0
MAX_POSITIONS         = 4
MAX_SECTOR_EXPOSURE   = 0.25
MAX_POSITION_SIZE     = 0.15  # max 15% of balance per trade (~$300 on $2k)
DAILY_LOSS_CAP        = 100.0

# --- Exit conditions ---
TAKE_PROFIT_PCT         = 0.06
STOP_LOSS_PCT           = 0.05

# Realized payoffs from 2021-2026 backtest (clean sector mix, 2026-05-07).
# Used as Kelly B inputs in place of nominal TP/SL — actual exit quality
# exceeds config thresholds because daily close prices overshoot TP/undershoot SL.
AVG_WIN_PCT   = 0.077   # realized avg win  (nominal TP = 0.06)
AVG_LOSS_PCT  = 0.057   # realized avg loss (nominal SL = 0.05)
TIME_STOP_DAYS          = 40
PROFIT_LOCK_TRIGGER_PCT = 0.04   # peak gain that activates tighter TSL — sweep-validated 2026-05-27
PROFIT_LOCK_TRAIL_PCT   = 0.01   # drawdown from peak once profit-lock activates — sweep-validated 2026-05-27

# --- Macro filter (Lock 1.5) ---
MACRO_VIX_THRESHOLD           = 30.0
MACRO_EVENT_BLACKOUT_DAYS     = 1     # days before/after FOMC, CPI, NFP
MACRO_EARNINGS_BLACKOUT_DAYS  = 3     # days before a ticker's earnings
GATE_COOLOFF_HOURS            = 4     # hours before re-evaluating a ticker that failed L2/L3/MACRO

# --- Lock Leading ---
LOCK_LEADING_MIN_PASS = 2      # checks that must pass (out of 4) to advance

# --- Portfolio roof overflow ---
# For each position slot beyond max_positions, the quant threshold multiplies by
# (1 + overflow_level * OVERFLOW_QUANT_INCREMENT).
# Position 6 = ×1.05, position 7 = ×1.10, etc. No hard cap — the escalating bar
# is self-limiting.
OVERFLOW_QUANT_INCREMENT = 0.05

# --- Lock 3 risk limits ---
LOCK3_MAX_DRAWDOWN_PCT = 0.20  # drawdown threshold passed to Claude in context

# --- P(win) base rate (decoupled from signal score) ---
BASE_WIN_RATE        = 0.55
WIN_RATE_MIN_TRADES  = 20  # use rolling win rate only after this many closed trades

# --- Live starting balance (used for drawdown calc in Lock 3 context) ---
LIVE_STARTING_BALANCE = float(os.getenv("LIVE_STARTING_BALANCE", "100000.0"))

# --- Live gate thresholds — defaults only. Runtime values live in data/live_config.json
# (written by the Promote feature). Import get_live_config() to read effective values.
LIVE_LOCK1_THRESHOLD      = float(os.getenv("LIVE_LOCK1_THRESHOLD", "0.65"))
LIVE_LOCK2_SENTIMENT_MIN  = float(os.getenv("LIVE_LOCK2_SENTIMENT_MIN", "0.2"))
LIVE_LOCK3_CONFIDENCE_MIN = float(os.getenv("LIVE_LOCK3_CONFIDENCE_MIN", "0.75"))

# --- Live wallet / risk controls ---
LIVE_MAX_POSITIONS       = int(os.getenv("LIVE_MAX_POSITIONS", "4"))
LIVE_MAX_SECTOR_EXPOSURE = float(os.getenv("LIVE_MAX_SECTOR_EXPOSURE", "0.20"))
LIVE_MAX_POSITION_SIZE   = float(os.getenv("LIVE_MAX_POSITION_SIZE", "0.10"))
LIVE_DAILY_LOSS_CAP      = float(os.getenv("LIVE_DAILY_LOSS_CAP", "200.0"))
LIVE_TAKE_PROFIT_PCT     = float(os.getenv("LIVE_TAKE_PROFIT_PCT", "0.12"))
LIVE_STOP_LOSS_PCT       = float(os.getenv("LIVE_STOP_LOSS_PCT", "0.04"))

# --- Sectors ---
SECTORS = {
    "Technology": {
        "etf": "XLK",
        "tickers": ["AAPL", "MSFT", "NVDA", "AMD", "V", "AVGO", "CRM", "ORCL", "ADBE", "NOW", "QCOM"],
    },
    "Healthcare": {
        "etf": "XLV",
        "tickers": ["JNJ", "PFE", "UNH", "MRNA", "LLY"],
    },
    "Energy": {
        "etf": "XLE",
        "tickers": ["EOG", "CVX", "HAL", "COP", "OXY"],
    },
    "Industrials": {
        "etf": "XLI",
        "tickers": ["CAT", "BA", "GE", "HON", "DE"],
    },
    "Financials": {
        "etf": "XLF",
        "tickers": ["JPM", "BAC", "GS", "BLK", "MS"],
    },
    "ConsumerDisc": {
        "etf": "XLY",
        "tickers": ["TSLA", "NKE", "MCD", "HD"],
    },
    "ConsumerStaples": {
        "etf": "XLP",
        "tickers": ["PG", "KO", "PEP", "WMT", "COST"],
    },
    "Communication": {
        "etf": "XLC",
        "tickers": ["GOOGL", "META", "NFLX", "DIS", "SPOT"],
    },
    "Utilities": {
        "etf": "XLU",
        "tickers": ["DUK", "SO", "AEP", "EXC", "NEE"],
    },
    "Materials": {
        "etf": "XLB",
        "tickers": ["LIN", "APD", "NEM", "FCX", "SHW"],
    },
    "RealEstate": {
        "etf": "XLRE",
        "tickers": ["PLD", "AMT", "EQIX", "SPG", "WELL", "DLR", "O", "VTR", "PSA"],
    },
    "Semiconductors": {
        "etf": "SOXX",
        "tickers": ["ASML", "AMAT", "LRCX", "KLAC", "MU"],
    },
    "Defense": {
        "etf": "ITA",
        "tickers": ["LMT", "RTX", "NOC", "GD", "HII"],
    },
    "Homebuilders": {
        "etf": "ITB",
        "tickers": ["DHI", "LEN", "PHM", "TOL", "NVR"],
    },
    "Transportation": {
        "etf": "IYT",
        "tickers": ["UNP", "CSX", "FDX", "UPS", "JBHT"],
    },
}

# SPY ticker used for market regime check in P(win) adjustment
SPY_TICKER = "SPY"

# Yahoo Finance RSS — primary news fallback when NewsAPI quota is exhausted
# Pattern: substitute {ticker} before use
RSS_URL_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
