"""
Ticker config manager — reads/writes data/tickers.json at runtime.

Stores per-sector ticker lists and ETF symbols. Falls back to SECTORS
in config.py if the JSON file doesn't exist.

Usage:
    from backend.ticker_config import get_sectors, set_sector_tickers
"""
import json
from pathlib import Path
from loguru import logger

_CONFIG_PATH = Path(__file__).parent.parent / "data" / "tickers.json"


def get_sectors() -> dict:
    """Return effective sector config — JSON file if present, else config.py defaults."""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                stored = json.load(f)
            # Merge with defaults so any new sectors added to config.py appear
            from backend.config import SECTORS as defaults
            merged = dict(defaults)
            merged.update(stored)
            return merged
        except Exception as e:
            logger.warning(f"ticker_config: failed to read {_CONFIG_PATH} — using defaults: {e}")
    from backend.config import SECTORS
    return dict(SECTORS)


def set_sector_tickers(sector: str, tickers: list[str]) -> dict:
    """
    Replace the ticker list for a sector. ETF is preserved.
    Returns the full updated sectors dict.
    """
    current = get_sectors()
    if sector not in current:
        raise ValueError(f"Unknown sector: {sector}")
    # Validate tickers — uppercase, 1–5 letters
    import re
    pattern = re.compile(r"^[A-Z]{1,5}$")
    clean = []
    for t in tickers:
        t = t.upper().strip()
        if not pattern.match(t):
            raise ValueError(f"Invalid ticker: '{t}'")
        clean.append(t)
    current[sector] = {**current[sector], "tickers": clean}
    _save(current)
    return current


def add_ticker(sector: str, ticker: str) -> dict:
    """Add a ticker to a sector if not already present."""
    import re
    ticker = ticker.upper().strip()
    if not re.compile(r"^[A-Z]{1,5}$").match(ticker):
        raise ValueError(f"Invalid ticker: '{ticker}'")
    current = get_sectors()
    if sector not in current:
        raise ValueError(f"Unknown sector: {sector}")
    tickers = list(current[sector]["tickers"])
    if ticker not in tickers:
        tickers.append(ticker)
        current[sector] = {**current[sector], "tickers": tickers}
        _save(current)
    return current


def remove_ticker(sector: str, ticker: str) -> dict:
    """Remove a ticker from a sector."""
    ticker  = ticker.upper().strip()
    current = get_sectors()
    if sector not in current:
        raise ValueError(f"Unknown sector: {sector}")
    tickers = [t for t in current[sector]["tickers"] if t != ticker]
    if len(tickers) < 1:
        raise ValueError("Each sector must have at least one ticker")
    current[sector] = {**current[sector], "tickers": tickers}
    _save(current)
    return current


def _save(sectors: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(sectors, f, indent=2)
    logger.info(f"ticker_config: saved to {_CONFIG_PATH}")
