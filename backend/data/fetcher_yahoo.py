"""
Yahoo Finance data fetcher.
Fetches OHLCV history per ticker and runs the Phase 2 signal engine.
"""
import math
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd
from loguru import logger

from backend.config import SECTORS, SPY_TICKER
from backend.signals import momentum, volume, ev_kelly, aggregator
from backend.db import get_rolling_win_rate


# --------------------------------------------------------------------------- #
#  SPY regime — fetched once per poll cycle, shared across all tickers         #
# --------------------------------------------------------------------------- #

def _spy_regime() -> float:
    """Returns +0.03 if SPY > 50d MA, -0.03 if below, 0.0 on error."""
    try:
        df = yf.Ticker(SPY_TICKER).history(period="70d", auto_adjust=True)
        if df.empty or len(df) < 50:
            return 0.0
        price = float(df["Close"].iloc[-1])
        ma50  = float(df["Close"].rolling(50).mean().iloc[-1])
        regime = 0.03 if price > ma50 else -0.03
        logger.debug(f"SPY regime: {'bullish' if regime > 0 else 'bearish'} (price={price:.2f}, MA50={ma50:.2f})")
        return regime
    except Exception as e:
        logger.warning(f"SPY regime fetch failed: {e}")
        return 0.0


def _etf_regime(etf_ticker: str) -> float:
    """
    Returns a signal multiplier based on whether the sector ETF is above its 20d MA.
    1.0 = ETF in uptrend (no penalty), 0.85 = ETF in downtrend (15% score penalty).
    """
    try:
        df = yf.Ticker(etf_ticker).history(period="30d", auto_adjust=True)
        if df.empty or len(df) < 20:
            return 1.0
        price = float(df["Close"].iloc[-1])
        ma20  = float(df["Close"].rolling(20).mean().iloc[-1])
        mult  = 1.0 if price >= ma20 else 0.85
        logger.debug(f"{etf_ticker} ETF regime: {'up' if mult == 1.0 else 'down'} (price={price:.2f}, MA20={ma20:.2f})")
        return mult
    except Exception as e:
        logger.warning(f"ETF regime fetch failed for {etf_ticker}: {e}")
        return 1.0


# --------------------------------------------------------------------------- #
#  Per-ticker signal computation                                                #
# --------------------------------------------------------------------------- #

def _score_ticker(symbol: str, sector: str, spy_regime: float, rolling_win_rate: float | None, etf_mult: float = 1.0) -> dict | None:
    try:
        df = yf.Ticker(symbol).history(period="60d", auto_adjust=True)
        if df.empty or len(df) < 21:
            logger.warning(f"{symbol}: insufficient history ({len(df)} rows)")
            return None

        mom = momentum.compute(df)
        vol = volume.compute(df)

        # ATR(14) as % of price — used for dynamic Kelly stop-loss floor
        atr_raw = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
        if mom["price"] and not math.isnan(atr_raw):
            atr_pct = float(atr_raw / mom["price"])
        else:
            atr_pct = 0.0

        # 60-day price range for Lock 3 context
        high_60d = float(df["Close"].max())
        low_60d  = float(df["Close"].min())

        evk = ev_kelly.compute(spy_regime, rolling_win_rate, atr_pct=atr_pct)
        score = aggregator.compute(mom["momentum_score"], vol["volume_score"], evk["ev_norm"])

        # Sector ETF regime: down-weight score if ETF is below its 20d MA
        score = round(min(max(score * etf_mult, 0.0), 1.0), 4)

        # Phase 6: scale position size by signal strength.
        # Signals near the threshold (0.65) get ~65% of base Kelly;
        # high-conviction signals (0.90+) get the full amount.
        kelly_scaled = round(evk["kelly_size"] * min(1.0, max(0.5, score)), 4)

        logger.info(
            f"{symbol} [{sector}] "
            f"score={score:.3f}  "
            f"mom={mom['momentum_score']:.3f}  "
            f"vol={vol['volume_score']:.3f}  "
            f"ev={evk['ev_norm']:.3f}  "
            f"rsi={mom['rsi']:.1f}  "
            f"p_win={evk['p_win']:.2f}  "
            f"atr={atr_pct:.3f}  "
            f"kelly={kelly_scaled:.4f}  "
            f"etf_mult={etf_mult:.2f}"
        )

        return {
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "ticker":         symbol,
            "sector":         sector,
            # momentum
            "price":          mom["price"],
            "ma20":           mom["ma20"],
            "rsi":            mom["rsi"],
            "momentum_score": mom["momentum_score"],
            # volume
            "volume":         vol["volume"],
            "avg_vol_30d":    vol["avg_vol_30d"],
            "volume_ratio":   vol["volume_ratio"],
            "volume_score":   vol["volume_score"],
            # ev/kelly
            "ev":             evk["ev"],
            "kelly_size":     kelly_scaled,
            "effective_sl":   evk["effective_sl"],
            "atr_pct":        evk["atr_pct"],
            # price range
            "high_60d":       round(high_60d, 4),
            "low_60d":        round(low_60d, 4),
            # aggregate
            "signal_score":   score,
        }

    except Exception as e:
        logger.error(f"{symbol}: scoring failed — {e}")
        return None


# --------------------------------------------------------------------------- #
#  Public API                                                                   #
# --------------------------------------------------------------------------- #

def fetch_sector(sector_name: str) -> list[dict]:
    """Fetch and score all tickers in a sector."""
    cfg              = SECTORS[sector_name]
    tickers          = cfg["tickers"]
    spy_regime       = _spy_regime()
    rolling_win_rate = get_rolling_win_rate()
    etf_mult         = _etf_regime(cfg["etf"])

    results = []
    for symbol in tickers:
        row = _score_ticker(symbol, sector_name, spy_regime, rolling_win_rate, etf_mult)
        if row:
            results.append(row)
    return results


def fetch_all_sectors() -> list[dict]:
    """Fetch every configured sector. Called by the scheduler."""
    # Fetch shared inputs once — not once per ticker
    spy_regime       = _spy_regime()
    rolling_win_rate = get_rolling_win_rate()

    all_signals = []
    for sector_name, cfg in SECTORS.items():
        etf_mult = _etf_regime(cfg["etf"])
        for symbol in cfg["tickers"]:
            row = _score_ticker(symbol, sector_name, spy_regime, rolling_win_rate, etf_mult)
            if row:
                all_signals.append(row)
    return all_signals
