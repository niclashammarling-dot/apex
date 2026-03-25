import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
XAI_API_KEY       = os.getenv("XAI_API_KEY", "")
NEWS_API_KEY      = os.getenv("NEWS_API_KEY", "")

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

# --- P(win) base rate (decoupled from signal score) ---
BASE_WIN_RATE        = 0.55
WIN_RATE_MIN_TRADES  = 20  # use rolling win rate only after this many closed trades

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
