"""
data/options_alpaca.py — Alpaca options chains for L5 POSITIONING (2026-11-21, the 11-21 order's item 1).

Decision (vault INDEX "APEX options source", Niclas 2026-11-04: "bekräftar alpaca som primär. Jag tror det är ok
om den släpar en om det gäller sentiment signal"): Alpaca is the primary source for open interest and volume,
yfinance the fallback per field (backend/gate/l5_positioning._fetch_options_chains).

  open interest  the trading API's contracts endpoint (`open_interest`, `open_interest_date`). Measured in three
                 probe runs (11-02, 11-04, 11-20): dated one session behind on every ticker, about 46-48 % of
                 matched contracts identical to yfinance's undated figure.
  volume         the indicative-feed chain snapshot, read raw: alpaca-py's OptionsSnapshot model drops dailyBar.
                 dailyBar is the contract's last traded day, which can be weeks old, so its date goes in as
                 lastTradeDate, the column production's unusual_calls reads to keep only the session's trades
                 (l5_positioning._today_volume). Without it every Alpaca chain errors there (11-20 15:55 probe
                 run: 102 of 102).

Expiries: the two nearest expiration dates on or after today that have contracts, as yfinance's t.options[:2].
Returns frames shaped like yfinance's option_chain(): calls and puts with openInterest, volume, lastTradeDate.
Raises on any API failure; the caller decides the fallback.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

_HORIZON_DAYS = 60           # contracts are listed up to this far out when the two nearest expiries are chosen

_trading = None
_data    = None


def _clients():
    global _trading, _data
    if _trading is None or _data is None:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.trading.client import TradingClient
        from backend.config import ALPACA_API_KEY, ALPACA_BASE_URL, ALPACA_SECRET_KEY
        _trading = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper="paper-api" in ALPACA_BASE_URL)
        _data    = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, raw_data=True)
    return _trading, _data


def contracts(ticker: str, today: date, tc=None) -> list[dict]:
    """Every contract of `ticker` expiring within _HORIZON_DAYS: symbol, expiry, type, oi, oi_date."""
    from alpaca.trading.requests import GetOptionContractsRequest
    tc = tc or _clients()[0]
    out, token = [], None
    while True:
        req = GetOptionContractsRequest(underlying_symbols=[ticker], expiration_date_gte=today.isoformat(),
                                        expiration_date_lte=(today + timedelta(days=_HORIZON_DAYS)).isoformat(),
                                        limit=10000, page_token=token)
        resp = tc.get_option_contracts(req)
        for c in resp.option_contracts or []:
            out.append({"symbol": c.symbol, "expiry": str(c.expiration_date), "type": getattr(c.type, "value", c.type),
                        "oi": int(c.open_interest) if c.open_interest is not None else None,
                        "oi_date": str(c.open_interest_date) if c.open_interest_date else None})
        token = resp.next_page_token
        if not token:
            return out


def nearest_expiries(rows: list[dict], n: int = 2) -> list[str]:
    return sorted({r["expiry"] for r in rows})[:n]


def snapshots(ticker: str, expiries: list[str], dc=None) -> dict:
    """Raw chain snapshots keyed by contract symbol (dailyBar kept)."""
    from alpaca.data.enums import OptionsFeed
    from alpaca.data.requests import OptionChainRequest
    dc = dc or _clients()[1]
    raw = dc.get_option_chain(OptionChainRequest(underlying_symbol=ticker, feed=OptionsFeed.INDICATIVE,
                                                 expiration_date_gte=min(expiries),
                                                 expiration_date_lte=max(expiries)))
    return raw if isinstance(raw, dict) else {}


def frames(rows: list[dict], snaps: dict | None, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(calls, puts) for one expiry, yfinance-shaped. snaps None leaves volume and lastTradeDate empty (the caller
    takes volume from yfinance then)."""
    out = []
    for c in rows:
        if c["expiry"] != expiry:
            continue
        bar = ((snaps or {}).get(c["symbol"]) or {}).get("dailyBar") or {}
        out.append({"contractSymbol": c["symbol"], "type": c["type"], "openInterest": c["oi"],
                    "volume": bar.get("v"), "lastTradeDate": bar.get("t")})
    df = pd.DataFrame(out, columns=["contractSymbol", "type", "openInterest", "volume", "lastTradeDate"])
    df = df.astype({"openInterest": "float", "volume": "float"})
    return (df[df["type"] == "call"].reset_index(drop=True), df[df["type"] == "put"].reset_index(drop=True))


def oi_date(rows: list[dict], expiries: list[str]) -> str | None:
    """The newest open_interest_date among the chosen expiries' contracts (one session behind, measured)."""
    dates = [r["oi_date"] for r in rows if r["expiry"] in expiries and r.get("oi_date")]
    return max(dates) if dates else None
