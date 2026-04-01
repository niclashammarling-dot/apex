"""
Demo config manager — reads/writes data/demo_config.json at runtime.

Same pattern as live_config.py. Falls back to config.py defaults if the
JSON file doesn't exist yet.

Usage:
    from backend.demo_config import get_demo_config, set_demo_config
"""
import json
import os
import tempfile
from pathlib import Path
from loguru import logger

_CONFIG_PATH = Path(__file__).parent.parent / "data" / "demo_config.json"

_KEYS = [
    "lock1_threshold",
    "lock2_sentiment_min",
    "lock3_confidence_min",
    "take_profit_pct",
    "stop_loss_pct",
    "trailing_stop_pct",
    "max_positions",
    "max_position_size",
    "daily_loss_cap",
    "max_hold_days",
    "vix_threshold",
    "macro_event_blackout_days",
    "macro_earnings_blackout_days",
    "gate_cooloff_hours",
    "max_sector_exposure",
    "lock_leading_min_pass",
    "starting_balance",
    "max_drawdown_pct",
]


def _defaults() -> dict:
    from backend.config import (
        LOCK1_THRESHOLD, LOCK2_SENTIMENT_MIN, LOCK3_CONFIDENCE_MIN,
        TAKE_PROFIT_PCT, STOP_LOSS_PCT,
        MAX_POSITIONS, MAX_POSITION_SIZE, DAILY_LOSS_CAP, TIME_STOP_DAYS,
        MACRO_VIX_THRESHOLD, MACRO_EVENT_BLACKOUT_DAYS, MACRO_EARNINGS_BLACKOUT_DAYS, GATE_COOLOFF_HOURS,
        MAX_SECTOR_EXPOSURE, LOCK_LEADING_MIN_PASS, LOCK3_MAX_DRAWDOWN_PCT, STARTING_BALANCE,
    )
    return {
        "lock1_threshold":              LOCK1_THRESHOLD,
        "lock2_sentiment_min":          LOCK2_SENTIMENT_MIN,
        "lock3_confidence_min":         LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":              TAKE_PROFIT_PCT,
        "stop_loss_pct":                STOP_LOSS_PCT,
        "trailing_stop_pct":            None,
        "max_positions":                MAX_POSITIONS,
        "max_position_size":            MAX_POSITION_SIZE,
        "daily_loss_cap":               DAILY_LOSS_CAP,
        "max_hold_days":                TIME_STOP_DAYS,
        "vix_threshold":                MACRO_VIX_THRESHOLD,
        "macro_event_blackout_days":    MACRO_EVENT_BLACKOUT_DAYS,
        "macro_earnings_blackout_days": MACRO_EARNINGS_BLACKOUT_DAYS,
        "gate_cooloff_hours":           GATE_COOLOFF_HOURS,
        "max_sector_exposure":          MAX_SECTOR_EXPOSURE,
        "lock_leading_min_pass":        LOCK_LEADING_MIN_PASS,
        "starting_balance":             STARTING_BALANCE,
        "max_drawdown_pct":             LOCK3_MAX_DRAWDOWN_PCT,
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
            logger.error(f"demo_config: corrupt config file — resetting to defaults: {e}")
            _write_atomic(_defaults())
            try:
                from backend.alerts import alert_config_corrupted
                alert_config_corrupted("demo_config", str(e))
            except Exception:
                pass
    return _defaults()


def set_demo_config(updates: dict) -> dict:
    """Write updates to demo_config.json atomically. Returns the full resulting config."""
    current = get_demo_config()
    for k in _KEYS:
        if k in updates:
            current[k] = updates[k]
    _write_atomic(current)
    logger.info(f"demo_config: saved to {_CONFIG_PATH}")
    return current


def ensure_config_exists() -> None:
    """Materialize defaults to disk if the config file doesn't exist yet."""
    if not _CONFIG_PATH.exists():
        _write_atomic(_defaults())
        logger.info(f"demo_config: created default config at {_CONFIG_PATH}")


def _write_atomic(cfg: dict) -> None:
    """Write config to a temp file then rename — prevents corrupt files on crash."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, _CONFIG_PATH)
    except Exception:
        os.unlink(tmp)
        raise
