"""
Quantitative sentiment signal fetcher for Lock 2.

Collects analyst consensus, short interest, recent analyst actions,
and news titles from yfinance. All fields fail gracefully — never raises.
"""
from datetime import datetime, timedelta

import yfinance as yf
from loguru import logger


def _analyst_label(mean: float) -> str:
    if mean <= 1.5:
        return "Strong Buy"
    if mean <= 2.5:
        return "Buy"
    if mean <= 3.5:
        return "Hold"
    if mean <= 4.5:
        return "Underperform"
    return "Sell"


def fetch_market_signals(ticker: str) -> dict:
    """
    Fetch quantitative sentiment signals for a ticker from yfinance.

    Returns a dict with:
      company_name           str — display name (e.g. "Apple Inc.")
      short_pct_float        float | None — e.g. 0.085 = 8.5% of float short
      short_ratio            float | None — days to cover at average volume
      analyst_mean           float | None — 1.0=Strong Buy … 5.0=Sell
      analyst_label          str  | None — human label for analyst_mean
      analyst_count          int          — number of analyst opinions
      price_target_mean      float | None — consensus price target
      current_price          float | None — last price
      upside_pct             float | None — (target/current - 1) * 100
      recent_analyst_actions list[dict]   — grade changes last 14 days
      news_titles            list[str]    — up to 10 recent news headlines
    """
    result: dict = {
        "company_name":           ticker,
        "short_pct_float":        None,
        "short_ratio":            None,
        "analyst_mean":           None,
        "analyst_label":          None,
        "analyst_count":          0,
        "price_target_mean":      None,
        "current_price":          None,
        "upside_pct":             None,
        "recent_analyst_actions": [],
        "news_titles":            [],
    }

    try:
        t    = yf.Ticker(ticker)
        info = t.info

        result["company_name"]      = info.get("longName", ticker)
        result["short_pct_float"]   = info.get("shortPercentOfFloat")
        result["short_ratio"]       = info.get("shortRatio")
        result["price_target_mean"] = info.get("targetMeanPrice")
        result["current_price"]     = info.get("currentPrice")

        mean = info.get("recommendationMean")
        if mean is not None:
            result["analyst_mean"]  = mean
            result["analyst_label"] = _analyst_label(mean)
        result["analyst_count"] = int(info.get("numberOfAnalystOpinions", 0) or 0)

        if result["price_target_mean"] and result["current_price"]:
            result["upside_pct"] = round(
                (result["price_target_mean"] / result["current_price"] - 1) * 100, 1
            )

    except Exception as e:
        logger.warning(f"fetcher_sentiment [{ticker}]: info error — {e}")

    try:
        t        = yf.Ticker(ticker)
        upgrades = t.upgrades_downgrades
        if upgrades is not None and not upgrades.empty:
            cutoff  = datetime.now() - timedelta(days=14)
            recent  = upgrades[upgrades.index >= cutoff]
            actions = []
            for idx, row in recent.iterrows():
                target = row.get("currentPriceTarget")
                actions.append({
                    "date":   idx.strftime("%Y-%m-%d"),
                    "firm":   str(row.get("Firm", "")),
                    "from":   str(row.get("FromGrade", "")),
                    "to":     str(row.get("ToGrade", "")),
                    "action": str(row.get("Action", "")),
                    "target": float(target) if target and target == target else None,
                })
            result["recent_analyst_actions"] = actions

    except Exception as e:
        logger.warning(f"fetcher_sentiment [{ticker}]: upgrades error — {e}")

    try:
        t    = yf.Ticker(ticker)
        news = t.news or []
        result["news_titles"] = [
            n.get("content", {}).get("title") or n.get("title", "")
            for n in news[:10]
            if n.get("content", {}).get("title") or n.get("title")
        ]

    except Exception as e:
        logger.warning(f"fetcher_sentiment [{ticker}]: news error — {e}")

    return result
