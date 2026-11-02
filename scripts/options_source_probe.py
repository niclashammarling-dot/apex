"""Read-only session-time probe: Alpaca options data against yfinance for the L5 options checks.

Vault scope: raw/notes/2026-10/2026-10-31-apex-saturday-scopes.md § 2 (choose (a), (b) or (c)).
For each universe ticker, back to back in the same minute:
  yfinance  — production's _fetch_options_chains (first two listed expiries)
  Alpaca    — contracts endpoint (open_interest) and the indicative-feed chain snapshot
              (raw, so dailyBar survives: alpaca-py's OptionsSnapshot model drops it)
for the same two expiries. Both sides go through production's _check_put_call_ratio and
_check_unusual_call_volume, so the comparison is what L5 would decide on each source.

Writes nothing to the DB. Usage: python -u scripts/options_source_probe.py OUT.json [TICKER ...]
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

from backend.config import ALPACA_API_KEY, ALPACA_BASE_URL, ALPACA_SECRET_KEY
from backend.gate.lock4_leading import (
    _check_put_call_ratio,
    _check_unusual_call_volume,
    _fetch_options_chains,
)
from backend.ticker_config import get_sectors


def _alpaca_contracts(tc: TradingClient, ticker: str, expiries: list[str]) -> list[dict]:
    out, token = [], None
    while True:
        req = GetOptionContractsRequest(underlying_symbols=[ticker], expiration_date_gte=min(expiries),
                                        expiration_date_lte=max(expiries), limit=10000, page_token=token)
        resp = tc.get_option_contracts(req)
        for c in resp.option_contracts or []:
            if str(c.expiration_date) in expiries:
                out.append({"symbol": c.symbol, "expiry": str(c.expiration_date), "type": c.type.value,
                            "oi": int(c.open_interest) if c.open_interest is not None else None,
                            "oi_date": str(c.open_interest_date) if c.open_interest_date else None})
        token = resp.next_page_token
        if not token:
            return out


def _alpaca_snapshots(dc: OptionHistoricalDataClient, ticker: str, expiries: list[str]) -> dict:
    raw = dc.get_option_chain(OptionChainRequest(underlying_symbol=ticker, feed=OptionsFeed.INDICATIVE,
                                                 expiration_date_gte=min(expiries),
                                                 expiration_date_lte=max(expiries)))
    return raw if isinstance(raw, dict) else {}


def _frames(contracts: list[dict], snaps: dict, expiry: str, today: str):
    # dailyBar is the contract's last traded day, which can be weeks old: only today's counts as volume
    rows = []
    for c in contracts:
        if c["expiry"] != expiry:
            continue
        bar = (snaps.get(c["symbol"]) or {}).get("dailyBar") or {}
        vol = bar.get("v") if bar.get("t", "")[:10] == today else None
        rows.append({"type": c["type"], "openInterest": c["oi"], "volume": vol})
    df = pd.DataFrame(rows, columns=["type", "openInterest", "volume"])
    calls = df[df["type"] == "call"].astype({"openInterest": "float", "volume": "float"})
    puts = df[df["type"] == "put"].astype({"openInterest": "float", "volume": "float"})
    return calls, puts


def _yf_today(chains: list[tuple], today: str) -> list[tuple]:
    # yfinance's volume is the contract's last traded day's: keep it only where that day is today
    out = []
    for calls, puts, exp in chains:
        pair = []
        for df in (calls, puts):
            d = pd.to_datetime(df["lastTradeDate"], utc=True).dt.tz_convert("America/New_York").dt.date.astype(str)
            df = df.copy()
            df.loc[d != today, "volume"] = None
            pair.append(df)
        out.append((*pair, exp))
    return out


def _today_volume_by_symbol(yf_today: list[tuple], snaps: dict, today: str) -> dict:
    yv = {r.contractSymbol: r.volume for c, p, _ in yf_today for r in pd.concat([c, p]).itertuples()
          if pd.notna(r.volume) and r.volume > 0}
    av = {sym: s["dailyBar"]["v"] for sym, s in snaps.items()
          if s.get("dailyBar", {}).get("t", "")[:10] == today and s["dailyBar"].get("v")}
    both = sorted(set(yv) & set(av))
    return {"yf_only": len(set(yv) - set(av)), "alpaca_only": len(set(av) - set(yv)), "both": len(both),
            "vol_yf_both": int(sum(yv[k] for k in both)), "vol_alpaca_both": int(sum(av[k] for k in both)),
            "vol_yf_total": int(sum(yv.values())), "vol_alpaca_total": int(sum(av.values()))}


def probe(ticker: str, tc, dc) -> dict:
    rec = {"ticker": ticker, "t_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        yf_chains = _fetch_options_chains(ticker)
    except Exception as e:
        rec["yf_error"] = str(e)[:120]
        return rec
    if not yf_chains:
        rec["yf_error"] = "no listed expiries"
        return rec
    expiries = [str(exp) for *_, exp in yf_chains]
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    yf_today = _yf_today(yf_chains, today)
    rec["expiries"] = expiries
    rec["yf"] = {
        "n_contracts": sum(len(c) + len(p) for c, p, _ in yf_chains),
        "n_with_oi": sum(int((c["openInterest"].fillna(0) > 0).sum() + (p["openInterest"].fillna(0) > 0).sum())
                         for c, p, _ in yf_chains),
        "n_with_volume": sum(int((c["volume"].fillna(0) > 0).sum() + (p["volume"].fillna(0) > 0).sum())
                             for c, p, _ in yf_chains),
        "pcr": _check_put_call_ratio(yf_chains, ticker=ticker),
        "ucv": _check_unusual_call_volume(yf_chains),
        "ucv_today": _check_unusual_call_volume(yf_today),
        "oi_by_symbol": {r.contractSymbol: (None if pd.isna(r.openInterest) else int(r.openInterest))
                         for c, p, _ in yf_chains for r in pd.concat([c, p]).itertuples()},
    }
    rec["t_alpaca_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        contracts = _alpaca_contracts(tc, ticker, expiries)
        snaps = _alpaca_snapshots(dc, ticker, expiries)
    except Exception as e:
        rec["alpaca_error"] = str(e)[:200]
        return rec
    al_chains = [(*_frames(contracts, snaps, exp, today), exp) for exp in expiries]
    with_bar = [s for s in snaps.values() if s.get("dailyBar", {}).get("t", "")[:10] == today]
    rec["alpaca"] = {
        "n_contracts": len(contracts),
        "n_with_oi": sum(1 for c in contracts if c["oi"]),
        "oi_dates": sorted({c["oi_date"] for c in contracts if c["oi_date"]}),
        "n_snapshots": len(snaps),
        "n_snap_with_dailybar": sum(1 for s in snaps.values() if s.get("dailyBar")),
        "n_with_volume": sum(1 for s in with_bar if (s["dailyBar"].get("v") or 0) > 0),
        "n_dailybar_stale": sum(1 for s in snaps.values() if s.get("dailyBar") and s not in with_bar),
        "pcr": _check_put_call_ratio(al_chains, ticker=ticker),
        "ucv": _check_unusual_call_volume(al_chains),
        "oi_by_symbol": {c["symbol"]: c["oi"] for c in contracts},
    }
    rec["volume_match"] = _today_volume_by_symbol(yf_today, snaps, today)
    if snaps:
        sym = next(iter(snaps))
        rec["alpaca"]["sample_snapshot"] = {sym: snaps[sym]}
    return rec


def main() -> None:
    out_path = sys.argv[1]
    tickers = sys.argv[2:] or sorted({t for cfg in get_sectors().values() for t in cfg["tickers"]})
    tc = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper="paper-api" in ALPACA_BASE_URL)
    dc = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, raw_data=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    results = []
    for t in tickers:
        rec = probe(t, tc, dc)
        results.append(rec)
        y, a = rec.get("yf", {}), rec.get("alpaca", {})
        print(f"{t:6} yf pcr={y.get('pcr', {}).get('pc_ratio')} vol={y.get('n_with_volume')}/{y.get('n_contracts')} | "
              f"al pcr={a.get('pcr', {}).get('pc_ratio')} oi={a.get('n_with_oi')}/{a.get('n_contracts')} "
              f"vol_today={a.get('n_with_volume')}/{a.get('n_snapshots')} stale_bar={a.get('n_dailybar_stale')} "
              f"| ucv yf={y.get('ucv', {}).get('cv_ratio')} yf_today={y.get('ucv_today', {}).get('cv_ratio')} "
              f"al={a.get('ucv', {}).get('cv_ratio')} | match={rec.get('volume_match')} "
              f"{rec.get('yf_error') or rec.get('alpaca_error') or ''}", flush=True)
        time.sleep(0.5)
    with open(out_path, "w") as f:
        json.dump({"producer": {"repo": "apex", "commit": commit, "script": "scripts/options_source_probe.py",
                                "run": " ".join(["python -u scripts/options_source_probe.py"] + sys.argv[1:]),
                                "why": "raw/notes/2026-10/2026-10-31-apex-saturday-scopes.md § 2"},
                   "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "results": results}, f, indent=1, default=str)


if __name__ == "__main__":
    main()
