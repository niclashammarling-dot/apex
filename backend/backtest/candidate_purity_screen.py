"""
Ticker-addition purity screen — 20d/90d excess return vs. the candidate's own
sector ETF (not SPY).

Standalone pre-addition diagnostic, filed per the 2026-07-01 materials-sector-
diagnosis screen: any new ticker must show equity-factor responsiveness to its
sector before it's worth running through the full ticker-expansion-gate-audit
protocol (GICS classification, config.py/tickers.json wiring, backtest sweep).
Production rs_score (backend/signals/relative_strength.py) benchmarks against
SPY only — that's a market-beta screen, not a sector-factor screen, and it's
the wrong instrument for this question. This script exists so the sector-ETF
comparison doesn't have to be reconstructed by hand each expansion session.

Candidates that fail both windows against their own sector ETF never need the
full audit — cheap rejection before the expensive one.

Usage:
    cd /home/promenix/apex
    python -m backend.backtest.candidate_purity_screen
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf
from loguru import logger

# (ticker, target_sector, benchmark_etf) — benchmark is the sector's own ETF,
# not SPY. Cybersecurity uses CIBR as an interim proxy even though the sector
# isn't wired into config.py yet — the screen doesn't require production
# wiring, only a defensible sector-factor benchmark to compare against.
CANDIDATES: list[tuple[str, str, str]] = [
    ("AMZN",  "ConsumerDisc",   "XLY"),
    ("BKNG",  "ConsumerDisc",   "XLY"),
    ("ORLY",  "ConsumerDisc",   "XLY"),
    ("TJX",   "ConsumerDisc",   "XLY"),
    ("ETN",   "Industrials",    "XLI"),
    ("TT",    "Industrials",    "XLI"),
    ("PWR",   "Industrials",    "XLI"),
    ("AXON",  "Industrials",    "XLI"),
    ("HWM",   "Defense",        "ITA"),
    ("TDG",   "Defense",        "ITA"),
    ("ANET",  "Technology",     "XLK"),
    ("CDNS",  "Technology",     "XLK"),
    ("TSM",   "Semiconductors", "SOXX"),
    ("ODFL",  "Transportation", "IYT"),
    ("BSX",   "Healthcare",     "XLV"),
    ("CRWD",  "Cybersecurity*", "CIBR"),
    ("PANW",  "Cybersecurity*", "CIBR"),
    ("FTNT",  "Cybersecurity*", "CIBR"),
]

LOOKBACK_START = "2025-01-01"  # generous window; trims to available history per ticker


def _pct_return(close: pd.Series, n: int) -> float | None:
    close = close.dropna()
    if len(close) < n + 1:
        return None
    return float(close.iloc[-1] / close.iloc[-(n + 1)] - 1)


def run() -> list[dict]:
    tickers = sorted({t for t, _, _ in CANDIDATES} | {e for _, _, e in CANDIDATES})

    logger.info(f"Purity screen: downloading {len(tickers)} symbols …")
    raw = yf.download(
        tickers,
        start=LOOKBACK_START,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    if raw.empty:
        logger.error("Purity screen: download returned empty DataFrame")
        return []

    if not isinstance(raw.columns, pd.MultiIndex):
        raw.columns = pd.MultiIndex.from_product([raw.columns, [tickers[0]]])

    available = set(raw.columns.get_level_values(1))
    closes: dict[str, pd.Series] = {}
    for t in tickers:
        if t in available:
            closes[t] = raw[("Close", t)].dropna()

    results = []
    for ticker, sector, etf in CANDIDATES:
        if ticker not in closes or etf not in closes:
            results.append({
                "ticker": ticker, "sector": sector, "etf": etf,
                "r20": None, "etf_r20": None, "excess_20d": None,
                "r90": None, "etf_r90": None, "excess_90d": None,
                "verdict": "NO DATA",
            })
            continue

        r20, etf_r20 = _pct_return(closes[ticker], 20), _pct_return(closes[etf], 20)
        r90, etf_r90 = _pct_return(closes[ticker], 90), _pct_return(closes[etf], 90)

        excess_20d = (r20 - etf_r20) if r20 is not None and etf_r20 is not None else None
        excess_90d = (r90 - etf_r90) if r90 is not None and etf_r90 is not None else None

        if excess_20d is None or excess_90d is None:
            verdict = "INSUFFICIENT HISTORY"
        elif excess_20d < 0 and excess_90d < 0:
            verdict = "REJECT — negative excess both windows"
        elif excess_20d < 0 or excess_90d < 0:
            verdict = "MIXED — check one window"
        else:
            verdict = "PASS"

        results.append({
            "ticker": ticker, "sector": sector, "etf": etf,
            "r20": r20, "etf_r20": etf_r20, "excess_20d": excess_20d,
            "r90": r90, "etf_r90": etf_r90, "excess_90d": excess_90d,
            "verdict": verdict,
        })

    return results


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def main() -> None:
    results = run()
    header = f"{'Ticker':7s} {'Sector':16s} {'ETF':6s} {'20d exc':>9s} {'90d exc':>9s}  Verdict"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['ticker']:7s} {r['sector']:16s} {r['etf']:6s} "
            f"{_fmt_pct(r['excess_20d']):>9s} {_fmt_pct(r['excess_90d']):>9s}  {r['verdict']}"
        )

    rejects = [r for r in results if r["verdict"].startswith("REJECT")]
    if rejects:
        print(f"\n{len(rejects)} rejected before full audit: {', '.join(r['ticker'] for r in rejects)}")


if __name__ == "__main__":
    main()
