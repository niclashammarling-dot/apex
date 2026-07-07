"""
Live config manager — reads/writes data/live_config.json at runtime.

This lets the Promote feature update live thresholds without restarting
the server or editing .env. Falls back to config.py defaults if the JSON
file doesn't exist yet.

Usage:
    from backend.live_config import get_live_config, set_live_config
"""
import json
import os
import tempfile
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
    "overflow_quant_increment",
    "max_position_size",
    "daily_loss_cap",
    "max_hold_days",
    "vix_threshold",
    "macro_event_blackout_days",
    "macro_earnings_blackout_days",
    "gate_cooloff_hours",
    "exit_cooloff_hours",
    "tp_cooloff_hours",
    "max_sector_exposure",
    "lock_leading_min_pass",
    "starting_balance",
    "max_drawdown_pct",
    "profit_lock_trigger_pct",
    "profit_lock_trail_pct",
    "etf_negative_floor",
    "etf_negative_penalty",
    "macro_earnings_near_days",
    "macro_earnings_near_penalty",
    "macro_pre_event_penalty",
    "live_account_since",
]


def _defaults() -> dict:
    from backend.config import (
        EXIT_COOLOFF_HOURS,
        GATE_COOLOFF_HOURS,
        TP_COOLOFF_HOURS,
        LIVE_DAILY_LOSS_CAP,
        LIVE_LOCK1_THRESHOLD,
        LIVE_LOCK2_SENTIMENT_MIN,
        LIVE_LOCK3_CONFIDENCE_MIN,
        LIVE_MAX_POSITION_SIZE,
        LIVE_MAX_POSITIONS,
        LIVE_MAX_SECTOR_EXPOSURE,
        LIVE_STARTING_BALANCE,
        LIVE_STOP_LOSS_PCT,
        LIVE_TAKE_PROFIT_PCT,
        LOCK3_MAX_DRAWDOWN_PCT,
        LOCK_LEADING_MIN_PASS,
        MACRO_EARNINGS_BLACKOUT_DAYS,
        MACRO_EARNINGS_NEAR_DAYS,
        MACRO_EARNINGS_NEAR_PENALTY,
        MACRO_EVENT_BLACKOUT_DAYS,
        MACRO_PRE_EVENT_PENALTY,
        MACRO_VIX_THRESHOLD,
        ETF_NEGATIVE_FLOOR,
        ETF_NEGATIVE_PENALTY,
        OVERFLOW_QUANT_INCREMENT,
        PROFIT_LOCK_TRAIL_PCT,
        PROFIT_LOCK_TRIGGER_PCT,
        TIME_STOP_DAYS,
    )
    return {
        "lock1_threshold":              LIVE_LOCK1_THRESHOLD,
        "lock2_sentiment_min":          LIVE_LOCK2_SENTIMENT_MIN,
        "lock3_confidence_min":         LIVE_LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":              LIVE_TAKE_PROFIT_PCT,
        "stop_loss_pct":                LIVE_STOP_LOSS_PCT,
        "profit_lock_trigger_pct":      PROFIT_LOCK_TRIGGER_PCT,
        "profit_lock_trail_pct":        PROFIT_LOCK_TRAIL_PCT,
        "max_positions":                LIVE_MAX_POSITIONS,
        "overflow_quant_increment":     OVERFLOW_QUANT_INCREMENT,
        "max_position_size":            LIVE_MAX_POSITION_SIZE,
        "daily_loss_cap":               LIVE_DAILY_LOSS_CAP,
        "max_hold_days":                TIME_STOP_DAYS,
        "vix_threshold":                MACRO_VIX_THRESHOLD,
        "macro_event_blackout_days":    MACRO_EVENT_BLACKOUT_DAYS,
        "macro_earnings_blackout_days": MACRO_EARNINGS_BLACKOUT_DAYS,
        "macro_earnings_near_days":     MACRO_EARNINGS_NEAR_DAYS,
        "macro_earnings_near_penalty":  MACRO_EARNINGS_NEAR_PENALTY,
        "macro_pre_event_penalty":      MACRO_PRE_EVENT_PENALTY,
        "gate_cooloff_hours":           GATE_COOLOFF_HOURS,
        "exit_cooloff_hours":           EXIT_COOLOFF_HOURS,
        "tp_cooloff_hours":             TP_COOLOFF_HOURS,
        "max_sector_exposure":          LIVE_MAX_SECTOR_EXPOSURE,
        "lock_leading_min_pass":        LOCK_LEADING_MIN_PASS,
        "starting_balance":             LIVE_STARTING_BALANCE,
        "max_drawdown_pct":             LOCK3_MAX_DRAWDOWN_PCT,
        "etf_negative_floor":           ETF_NEGATIVE_FLOOR,
        "etf_negative_penalty":         ETF_NEGATIVE_PENALTY,
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
            logger.error(f"live_config: corrupt config file — resetting to defaults: {e}")
            _write_atomic(_defaults())
            try:
                from backend.alerts import alert_config_corrupted
                alert_config_corrupted("live_config", str(e))
            except Exception:
                pass
    return _defaults()


def set_live_config(updates: dict) -> dict:
    """Write updates to live_config.json atomically. Returns the full resulting config."""
    current = get_live_config()
    for k in _KEYS:
        if k in updates:
            current[k] = updates[k]
    _write_atomic(current)
    logger.info(f"live_config: saved to {_CONFIG_PATH}")
    return current


def ensure_config_exists() -> None:
    """Materialize defaults to disk if the config file doesn't exist yet."""
    if not _CONFIG_PATH.exists():
        _write_atomic(_defaults())
        logger.info(f"live_config: created default config at {_CONFIG_PATH}")


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


# Settings that must never be promoted from demo — account-size specific.
# starting_balance: demo=$2k, live=$100k — completely different denominator
# daily_loss_cap:   absolute dollars calibrated to account size; demo=$100, live=$1000
_PROMOTE_EXCLUDE = {"starting_balance", "daily_loss_cap"}


def demo_thresholds() -> dict:
    """Return promotable demo gate thresholds (excludes account-size-specific keys)."""
    from backend.demo_config import get_demo_config
    cfg = get_demo_config()
    return {k: v for k, v in cfg.items() if k not in _PROMOTE_EXCLUDE}
