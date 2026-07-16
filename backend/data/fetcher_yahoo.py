"""
Yahoo Finance data fetcher.
Fetches OHLCV history per ticker and runs the Phase 2 signal engine.
"""
import math
from datetime import datetime, timezone

import yfinance as yf
from loguru import logger

from backend.config import EXCLUDED_SECTORS, SPY_TICKER
from backend.db import get_rolling_win_rate
from backend.regime.regime_bayes import load_regime_state
from backend.signals import (
    aggregator,
    ev_kelly,
    momentum,
    relative_strength,
    trend,
    volume,
)
from backend.ticker_config import get_sectors

# --------------------------------------------------------------------------- #
#  Consecutive data-gap tracker (process-lifetime; resets on success)          #
# --------------------------------------------------------------------------- #

_HISTORY_FAIL_THRESHOLD = 3
_history_fail_count: dict[str, int] = {}


# --------------------------------------------------------------------------- #
#  SPY regime — fetched once per poll cycle, shared across all tickers         #
# --------------------------------------------------------------------------- #

def _spy_data() -> dict:
    """
    Fetch SPY once per cycle.  Returns:
      regime        — +0.03 if SPY > MA50, -0.03 below, 0.0 on error
      return_20d    — 20-day price return (for relative-strength scoring)
    """
    try:
        df = yf.Ticker(SPY_TICKER).history(period="70d", auto_adjust=True)
        if df.empty or len(df) < 50:
            return {"regime": 0.0, "return_20d": 0.0}
        close  = df["Close"].dropna()
        price  = float(close.iloc[-1])
        ma50   = float(close.rolling(50).mean().iloc[-1])
        regime = 0.03 if price > ma50 else -0.03
        ret20  = float(close.iloc[-1] / close.iloc[-20] - 1) if len(close) >= 20 else 0.0
        logger.debug(
            f"SPY regime: {'bullish' if regime > 0 else 'bearish'} "
            f"(price={price:.2f}, MA50={ma50:.2f}, 20d_ret={ret20:.3f})"
        )
        return {"regime": regime, "return_20d": ret20}
    except Exception as e:
        logger.warning(f"SPY data fetch failed: {e}")
        return {"regime": 0.0, "return_20d": 0.0}


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

def _score_ticker(
    symbol: str,
    sector: str,
    spy_regime: float,
    spy_return_20d: float,
    rolling_win_rate: float | None,
    etf_mult: float = 1.0,
    poll_ts: str | None = None,
    regime_state: str = "neutral",
) -> dict | None:
    try:
        df = yf.Ticker(symbol).history(period="90d", auto_adjust=True)
        # Need 55 bars: MA50(50) + a few extra to avoid edge NaNs
        if df.empty or len(df) < 55:
            rows = len(df)
            _history_fail_count[symbol] = _history_fail_count.get(symbol, 0) + 1
            consecutive = _history_fail_count[symbol]
            logger.warning(f"{symbol}: insufficient history ({rows} rows) — consecutive failures: {consecutive}")
            if consecutive == _HISTORY_FAIL_THRESHOLD:
                from backend.alerts import alert_ticker_data_gap
                alert_ticker_data_gap(symbol, sector, consecutive, rows)
            return None

        mom = momentum.compute(df)
        if mom.get("rsi_blocked"):
            logger.debug(f"{symbol}: RSI {mom['rsi']:.1f} >= 80 — blocked")
            return None
        vol = volume.compute(df)
        trd = trend.compute(df)
        rs  = relative_strength.compute(df, spy_return_20d)

        # ATR(14) as % of price — used for dynamic Kelly stop-loss floor
        atr_raw = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
        if mom["price"] and not math.isnan(atr_raw):
            atr_pct = float(atr_raw / mom["price"])
        else:
            atr_pct = 0.0

        # 90-day price range for Lock 3 context
        high_60d = float(df["Close"].max())
        low_60d  = float(df["Close"].min())

        evk = ev_kelly.compute(spy_regime, rolling_win_rate, atr_pct=atr_pct)
        score = aggregator.compute(
            mom["momentum_score"],
            vol["volume_score"],
            trd["trend_score"],
            rs["rs_score"],
            regime_state=regime_state,
        )

        # Sector ETF regime: down-weight score if ETF is below its 20d MA
        score = round(min(max(score * etf_mult, 0.0), 1.0), 4)

        # Scale position size by signal strength.
        # Signals near the threshold get ~65% of base Kelly;
        # high-conviction signals (0.90+) get the full amount.
        kelly_scaled = round(evk["kelly_size"] * min(1.0, max(0.5, score)), 4)

        logger.info(
            f"{symbol} [{sector}] "
            f"score={score:.3f}  "
            f"mom={mom['momentum_score']:.3f}  "
            f"vol={vol['volume_score']:.3f}  "
            f"ev={evk['ev_norm']:.3f}  "
            f"trend={trd['trend_score']:.3f}  "
            f"rs={rs['rs_score']:.3f}  "
            f"rsi={mom['rsi']:.1f}  "
            f"p_win={evk['p_win']:.2f}  "
            f"atr={atr_pct:.3f}  "
            f"kelly={kelly_scaled:.4f}  "
            f"etf_mult={etf_mult:.2f}"
        )

        _history_fail_count.pop(symbol, None)
        return {
            "timestamp":         poll_ts or datetime.now(timezone.utc).isoformat(),
            "ticker":            symbol,
            "sector":            sector,
            # momentum
            "price":             mom["price"],
            "ma20":              mom["ma20"],
            "rsi":               mom["rsi"],
            "momentum_score":    mom["momentum_score"],
            # volume
            "volume":            vol["volume"],
            "avg_vol_30d":       vol["avg_vol_30d"],
            "volume_ratio":      vol["volume_ratio"],
            "volume_score":      vol["volume_score"],
            # ev/kelly
            "ev":                evk["ev"],
            "kelly_size":        kelly_scaled,
            "effective_sl":      evk["effective_sl"],
            "atr_pct":           evk["atr_pct"],
            # trend (MACD + MA50)
            "trend_score":       trd["trend_score"],
            "macd_hist":         trd["macd_hist"],
            "ma50":              trd["ma50"],
            # relative strength vs SPY
            "rs_score":          rs["rs_score"],
            "ticker_return_20d": rs["ticker_return_20d"],
            "excess_return":     rs["excess_return"],
            # price range
            "high_60d":          round(high_60d, 4),
            "low_60d":           round(low_60d, 4),
            # aggregate
            "signal_score":      score,
        }

    except Exception as e:
        logger.error(f"{symbol}: scoring failed — {e}")
        return None


# --------------------------------------------------------------------------- #
#  Public API                                                                   #
# --------------------------------------------------------------------------- #

def fetch_sector(sector_name: str) -> list[dict]:
    """Fetch and score all tickers in a sector."""
    cfg              = get_sectors()[sector_name]
    tickers          = cfg["tickers"]
    spy              = _spy_data()
    rolling_win_rate = get_rolling_win_rate()
    etf_mult         = _etf_regime(cfg["etf"])
    poll_ts          = datetime.now(timezone.utc).isoformat()
    regime_state     = load_regime_state()

    results = []
    for symbol in tickers:
        row = _score_ticker(
            symbol, sector_name,
            spy["regime"], spy["return_20d"],
            rolling_win_rate, etf_mult,
            poll_ts=poll_ts,
            regime_state=regime_state,
        )
        if row:
            results.append(row)
    return results


def fetch_all_sectors() -> list[dict]:
    """Fetch every configured sector. Called by the scheduler."""
    # Shared inputs and timestamp generated once — all tickers in this cycle
    # share the same poll_ts so trend comparisons work correctly.
    spy              = _spy_data()
    rolling_win_rate = get_rolling_win_rate()
    poll_ts          = datetime.now(timezone.utc).isoformat()
    regime_state     = load_regime_state()

    all_signals = []
    for sector_name, cfg in get_sectors().items():
        if sector_name in EXCLUDED_SECTORS:
            continue
        etf_mult = _etf_regime(cfg["etf"])
        for symbol in cfg["tickers"]:
            row = _score_ticker(
                symbol, sector_name,
                spy["regime"], spy["return_20d"],
                rolling_win_rate, etf_mult,
                poll_ts=poll_ts,
                regime_state=regime_state,
            )
            if row:
                all_signals.append(row)
    return all_signals
