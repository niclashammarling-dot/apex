"""
Live config manager — reads/writes data/live_config.json at runtime.

This lets the Promote feature update live thresholds without restarting
the server or editing .env. Falls back to config.py defaults if the JSON
file doesn't exist yet.

Usage:
    from backend.live_config import get_live_config, set_live_config
"""
import json
from pathlib import Path
from loguru import logger

_CONFIG_PATH = Path(__file__).parent.parent / "data" / "live_config.json"

# Keys that can be overridden at runtime
_KEYS = [
    "lock1_threshold",
    "lock2_sentiment_min",
    "lock3_confidence_min",
    "take_profit_pct",
    "stop_loss_pct",
    "max_positions",
    "max_position_size",
    "daily_loss_cap",
    "max_hold_days",
    "vix_threshold",
    "macro_event_blackout_days",
    "macro_earnings_blackout_days",
    "gate_cooloff_hours",
]


def _defaults() -> dict:
    from backend.config import (
        LIVE_LOCK1_THRESHOLD, LIVE_LOCK2_SENTIMENT_MIN, LIVE_LOCK3_CONFIDENCE_MIN,
        LIVE_TAKE_PROFIT_PCT, LIVE_STOP_LOSS_PCT,
        LIVE_MAX_POSITIONS, LIVE_MAX_POSITION_SIZE, LIVE_DAILY_LOSS_CAP, TIME_STOP_DAYS,
        MACRO_VIX_THRESHOLD, MACRO_EVENT_BLACKOUT_DAYS, MACRO_EARNINGS_BLACKOUT_DAYS, GATE_COOLOFF_HOURS,
    )
    return {
        "lock1_threshold":              LIVE_LOCK1_THRESHOLD,
        "lock2_sentiment_min":          LIVE_LOCK2_SENTIMENT_MIN,
        "lock3_confidence_min":         LIVE_LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":              LIVE_TAKE_PROFIT_PCT,
        "stop_loss_pct":                LIVE_STOP_LOSS_PCT,
        "max_positions":                LIVE_MAX_POSITIONS,
        "max_position_size":            LIVE_MAX_POSITION_SIZE,
        "daily_loss_cap":               LIVE_DAILY_LOSS_CAP,
        "max_hold_days":                TIME_STOP_DAYS,
        "vix_threshold":                MACRO_VIX_THRESHOLD,
        "macro_event_blackout_days":    MACRO_EVENT_BLACKOUT_DAYS,
        "macro_earnings_blackout_days": MACRO_EARNINGS_BLACKOUT_DAYS,
        "gate_cooloff_hours":           GATE_COOLOFF_HOURS,
    }


def get_live_config() -> dict:
    """Return effective live config (JSON file if present, else .env defaults)."""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                stored = json.load(f)
            cfg = _defaults()
            cfg.update({k: stored[k] for k in _KEYS if k in stored})
            return cfg
        except Exception as e:
            logger.warning(f"live_config: failed to read {_CONFIG_PATH} — using defaults: {e}")
    return _defaults()


def set_live_config(updates: dict) -> dict:
    """
    Write updates to live_config.json. Only recognised keys are written.
    Returns the full resulting config.
    """
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = get_live_config()
    for k in _KEYS:
        if k in updates:
            current[k] = updates[k]
    with open(_CONFIG_PATH, "w") as f:
        json.dump(current, f, indent=2)
    logger.info(f"live_config: saved to {_CONFIG_PATH}")
    return current


def demo_thresholds() -> dict:
    """Return current demo gate thresholds for the Promote preview."""
    from backend.config import (
        LOCK1_THRESHOLD, LOCK2_SENTIMENT_MIN, LOCK3_CONFIDENCE_MIN,
        TAKE_PROFIT_PCT, STOP_LOSS_PCT,
        MAX_POSITIONS, MAX_POSITION_SIZE, DAILY_LOSS_CAP,
    )
    return {
        "lock1_threshold":      LOCK1_THRESHOLD,
        "lock2_sentiment_min":  LOCK2_SENTIMENT_MIN,
        "lock3_confidence_min": LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":      TAKE_PROFIT_PCT,
        "stop_loss_pct":        STOP_LOSS_PCT,
        "max_positions":        MAX_POSITIONS,
        "max_position_size":    MAX_POSITION_SIZE,
        "daily_loss_cap":       DAILY_LOSS_CAP,
    }
