import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
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
LOCK1_THRESHOLD       = 0.55
LOCK2_SENTIMENT_MIN   = 0.1
LOCK3_CONFIDENCE_MIN  = 0.60

# --- Polling intervals (minutes) ---
POLL_INTERVAL_SECTORS = 15
GATE_INTERVAL         = 20
EXIT_CHECK_INTERVAL   = 5
GROK_CACHE_TTL        = 60  # minutes

# --- Wallet ---
STARTING_BALANCE      = 10_000.0
MAX_POSITIONS         = 6
MAX_SECTOR_EXPOSURE   = 0.25
MAX_POSITION_SIZE     = 0.15  # max 15% of balance per trade (~$1500 on $10k)
DAILY_LOSS_CAP        = 500.0

# --- Exit conditions ---
TAKE_PROFIT_PCT = 0.15
STOP_LOSS_PCT   = 0.05
TIME_STOP_DAYS  = 5

# --- Macro filter (Lock 1.5) ---
MACRO_VIX_THRESHOLD           = 25.0
MACRO_EVENT_BLACKOUT_DAYS     = 1     # days before/after FOMC, CPI, NFP
MACRO_EARNINGS_BLACKOUT_DAYS  = 3     # days before a ticker's earnings
GATE_COOLOFF_HOURS            = 4     # hours before re-evaluating a ticker that failed L2/L3/MACRO

# --- P(win) base rate (decoupled from signal score) ---
BASE_WIN_RATE        = 0.55
WIN_RATE_MIN_TRADES  = 20  # use rolling win rate only after this many closed trades

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
        "tickers": ["AAPL", "MSFT", "NVDA", "META"],
    },
    "Healthcare": {
        "etf": "XLV",
        "tickers": ["JNJ", "PFE", "UNH", "MRNA"],
    },
    "Energy": {
        "etf": "XLE",
        "tickers": ["XOM", "CVX", "SLB", "NEE"],
    },
    "Industrials": {
        "etf": "XLI",
        "tickers": ["CAT", "BA", "GE", "HON"],
    },
    "Financials": {
        "etf": "XLF",
        "tickers": ["JPM", "BAC", "GS", "V"],
    },
    "ConsumerDisc": {
        "etf": "XLY",
        "tickers": ["AMZN", "TSLA", "NKE", "MCD"],
    },
    "ConsumerStaples": {
        "etf": "XLP",
        "tickers": ["PG", "KO", "PEP", "WMT"],
    },
    "Communication": {
        "etf": "XLC",
        "tickers": ["GOOGL", "NFLX", "DIS", "T"],
    },
    "Utilities": {
        "etf": "XLU",
        "tickers": ["DUK", "SO", "AEP", "EXC"],
    },
    "Materials": {
        "etf": "XLB",
        "tickers": ["LIN", "APD", "NEM", "FCX"],
    },
    "RealEstate": {
        "etf": "XLRE",
        "tickers": ["PLD", "AMT", "EQIX", "SPG"],
    },
}

# SPY ticker used for market regime check in P(win) adjustment
SPY_TICKER = "SPY"

# Yahoo Finance RSS — primary news fallback when NewsAPI quota is exhausted
# Pattern: substitute {ticker} before use
RSS_URL_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
