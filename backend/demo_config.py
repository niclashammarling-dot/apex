"""
Demo config manager — reads/writes data/demo_config.json at runtime.

Same pattern as live_config.py. Falls back to config.py defaults if the
JSON file doesn't exist yet.

Usage:
    from backend.demo_config import get_demo_config, set_demo_config
"""
import json
from pathlib import Path
from loguru import logger

_CONFIG_PATH = Path(__file__).parent.parent / "data" / "demo_config.json"

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
    "max_sector_exposure",
]


def _defaults() -> dict:
    from backend.config import (
        LOCK1_THRESHOLD, LOCK2_SENTIMENT_MIN, LOCK3_CONFIDENCE_MIN,
        TAKE_PROFIT_PCT, STOP_LOSS_PCT,
        MAX_POSITIONS, MAX_POSITION_SIZE, DAILY_LOSS_CAP, TIME_STOP_DAYS,
        MACRO_VIX_THRESHOLD, MACRO_EVENT_BLACKOUT_DAYS, MACRO_EARNINGS_BLACKOUT_DAYS, GATE_COOLOFF_HOURS,
        MAX_SECTOR_EXPOSURE,
    )
    return {
        "lock1_threshold":              LOCK1_THRESHOLD,
        "lock2_sentiment_min":          LOCK2_SENTIMENT_MIN,
        "lock3_confidence_min":         LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":              TAKE_PROFIT_PCT,
        "stop_loss_pct":                STOP_LOSS_PCT,
        "max_positions":                MAX_POSITIONS,
        "max_position_size":            MAX_POSITION_SIZE,
        "daily_loss_cap":               DAILY_LOSS_CAP,
        "max_hold_days":                TIME_STOP_DAYS,
        "vix_threshold":                MACRO_VIX_THRESHOLD,
        "macro_event_blackout_days":    MACRO_EVENT_BLACKOUT_DAYS,
        "macro_earnings_blackout_days": MACRO_EARNINGS_BLACKOUT_DAYS,
        "gate_cooloff_hours":           GATE_COOLOFF_HOURS,
        "max_sector_exposure":          MAX_SECTOR_EXPOSURE,
    }


def get_demo_config() -> dict:
    """Return effective demo config (JSON file if present, else config.py defaults)."""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                stored = json.load(f)
            cfg = _defaults()
            cfg.update({k: stored[k] for k in _KEYS if k in stored})
            return cfg
        except Exception as e:
            logger.warning(f"demo_config: failed to read {_CONFIG_PATH} — using defaults: {e}")
    return _defaults()


def set_demo_config(updates: dict) -> dict:
    """Write updates to demo_config.json. Returns the full resulting config."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = get_demo_config()
    for k in _KEYS:
        if k in updates:
            current[k] = updates[k]
    with open(_CONFIG_PATH, "w") as f:
        json.dump(current, f, indent=2)
    logger.info(f"demo_config: saved to {_CONFIG_PATH}")
    return current
