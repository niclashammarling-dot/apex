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
    "l3_score_threshold_min",
    "l4_sentiment_grok_min",
    "l6_review_claude_min",
    "take_profit_pct",
    "stop_loss_pct",
    "l2_risk_max_positions_count",
    "l2_risk_overflow_step",
    "max_position_size",
    "l0_portfolio_loss_cap_max",
    "max_hold_days",
    "l1_eligibility_vix_max",
    "l1_eligibility_macro_event_days",
    "l2_risk_earnings_days",
    "l1_eligibility_cooloff_hours",
    "l1_eligibility_exit_cooloff_hours",
    "l1_eligibility_exit_cooloff_win_hours",
    "l2_risk_sector_cap_max",
    "l5_positioning_leading_mode",
    "starting_balance",
    "l0_portfolio_drawdown_guard_max",
    "profit_lock_trigger_pct",
    "profit_lock_trail_pct",
    "l2_risk_etf_floor",
    "l2_risk_etf_penalty",
    "l2_risk_earnings_near_days",
    "l2_risk_earnings_near_penalty",
    "l1_eligibility_macro_pre_event_penalty",
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
        LIVE_LOCK4_PCR_MODE,
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
        "l3_score_threshold_min":              LIVE_LOCK1_THRESHOLD,
        "l4_sentiment_grok_min":          LIVE_LOCK2_SENTIMENT_MIN,
        "l6_review_claude_min":         LIVE_LOCK3_CONFIDENCE_MIN,
        "take_profit_pct":              LIVE_TAKE_PROFIT_PCT,
        "stop_loss_pct":                LIVE_STOP_LOSS_PCT,
        "profit_lock_trigger_pct":      PROFIT_LOCK_TRIGGER_PCT,
        "profit_lock_trail_pct":        PROFIT_LOCK_TRAIL_PCT,
        "l2_risk_max_positions_count":                LIVE_MAX_POSITIONS,
        "l2_risk_overflow_step":     OVERFLOW_QUANT_INCREMENT,
        "max_position_size":            LIVE_MAX_POSITION_SIZE,
        "l0_portfolio_loss_cap_max":               LIVE_DAILY_LOSS_CAP,
        "max_hold_days":                TIME_STOP_DAYS,
        "l1_eligibility_vix_max":                MACRO_VIX_THRESHOLD,
        "l1_eligibility_macro_event_days":    MACRO_EVENT_BLACKOUT_DAYS,
        "l2_risk_earnings_days": MACRO_EARNINGS_BLACKOUT_DAYS,
        "l2_risk_earnings_near_days":     MACRO_EARNINGS_NEAR_DAYS,
        "l2_risk_earnings_near_penalty":  MACRO_EARNINGS_NEAR_PENALTY,
        "l1_eligibility_macro_pre_event_penalty":      MACRO_PRE_EVENT_PENALTY,
        "l1_eligibility_cooloff_hours":           GATE_COOLOFF_HOURS,
        "l1_eligibility_exit_cooloff_hours":           EXIT_COOLOFF_HOURS,
        "l1_eligibility_exit_cooloff_win_hours":             TP_COOLOFF_HOURS,
        "l2_risk_sector_cap_max":          LIVE_MAX_SECTOR_EXPOSURE,
        "l5_positioning_leading_mode":               LIVE_LOCK4_PCR_MODE,
        "starting_balance":             LIVE_STARTING_BALANCE,
        "l0_portfolio_drawdown_guard_max":             LOCK3_MAX_DRAWDOWN_PCT,
        "l2_risk_etf_floor":           ETF_NEGATIVE_FLOOR,
        "l2_risk_etf_penalty":         ETF_NEGATIVE_PENALTY,
    }


_warned_legacy = False


def _upgraded(stored: dict) -> dict:
    """The stored or posted keys under their derived names (phase 3 part a: read the old name for one release, write
    the new one). The first fallback is logged once per process."""
    global _warned_legacy
    from backend.gate.lock_codes import has_legacy_keys, upgrade_keys
    if has_legacy_keys(stored) and not _warned_legacy:
        _warned_legacy = True
        logger.warning("live_config: pre-phase-3 key names read and upgraded; the next save writes the derived names")
    return upgrade_keys(stored)


def get_live_config() -> dict:
    """Return effective live config (JSON file if present, else .env defaults)."""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                stored = _upgraded(json.load(f))
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
    updates = _upgraded(updates)
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
# l0_portfolio_loss_cap_max:   absolute dollars calibrated to account size; demo=$100 (read by the demo wallet);
#                   live is not read by the gate since 2026-10-24 (L0 Portfolio: 5 % of equity)
_PROMOTE_EXCLUDE = {"starting_balance", "l0_portfolio_loss_cap_max"}


def demo_thresholds() -> dict:
    """Return promotable demo gate thresholds (excludes account-size-specific keys)."""
    from backend.demo_config import get_demo_config
    cfg = get_demo_config()
    return {k: v for k, v in cfg.items() if k not in _PROMOTE_EXCLUDE}
