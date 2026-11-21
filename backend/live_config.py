"""
Live config manager — reads/writes data/live_config.json at runtime.

This lets the Promote feature update live thresholds without restarting
the server or editing .env.

One value per live fact (M12, R090 a ruled 2026-11-17, built 2026-11-21): the file is the only source. A missing or
unreadable file raises LiveConfigUnavailable: live entries halt with the cause on the gate_cycles row, a CRITICAL
mail goes out once per cause, open positions keep their broker legs and stored levels, and nothing is written over
the file (the operator restores it). Until 2026-11-21 a missing file traded silently on config.py defaults and a
corrupt one was written over with them; 11 of 27 keys differ from the file (starting balance 100000 vs 10000, stop
0.04 vs 0.06, TP 0.12 vs 0.06; vault raw/notes/2026-11/2026-11-16-apex-live-facts-with-more-than-one-value.md).
A key the file lacks is a KeyError at the reader that asks for it, never a default.

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


class LiveConfigUnavailable(RuntimeError):
    """data/live_config.json is missing or unreadable (R090 a): live entries halt until it is restored."""


_alerted: set[str] = set()


def _unavailable(cause: str) -> LiveConfigUnavailable:
    """Log every time, mail once per cause per process (the gate asks every cycle)."""
    logger.error(f"live_config: {cause} — live entries halt until the file is restored (R090 a); nothing written")
    if cause not in _alerted:
        _alerted.add(cause)
        try:
            from backend.alerts import alert_live_config_unavailable
            alert_live_config_unavailable(cause)
        except Exception as e:
            logger.warning(f"live_config: alert not sent — {e}")
    return LiveConfigUnavailable(cause)


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
    """The live config: the file's values for the runtime keys (one source per fact; module docstring).
    Raises LiveConfigUnavailable when the file is missing or unreadable."""
    if not _CONFIG_PATH.exists():
        raise _unavailable(f"{_CONFIG_PATH.name} missing")
    try:
        with open(_CONFIG_PATH) as f:
            stored = _upgraded(json.load(f))
    except Exception as e:
        raise _unavailable(f"{_CONFIG_PATH.name} unreadable: {str(e)[:80]}")
    return {k: stored[k] for k in _KEYS if k in stored}


def set_live_config(updates: dict, source: str = "settings") -> dict:
    """Write updates to live_config.json atomically. Returns the full resulting config."""
    current = get_live_config()
    updates = _upgraded(updates)
    for k in _KEYS:
        if k in updates:
            current[k] = updates[k]
    _write_atomic(current, source)
    logger.info(f"live_config: saved to {_CONFIG_PATH}")
    return current


def ensure_config_exists() -> None:
    """At startup: a missing live config is reported (log, CRITICAL mail), never created from defaults (R090 a)."""
    if not _CONFIG_PATH.exists():
        _unavailable(f"{_CONFIG_PATH.name} missing")      # the same cause as the gate's: one mail


def _write_atomic(cfg: dict, source: str = "unknown") -> None:
    """Write config to a temp file then rename — prevents corrupt files on crash. Each key the write changes gets a
    config_changes row (2026-11-07, Job 2); a failed record is logged and never blocks the write."""
    try:
        old = json.loads(_CONFIG_PATH.read_text()) if _CONFIG_PATH.exists() else None
    except Exception:
        old = None                                   # unreadable (the corrupt-reset path): every key is new
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, _CONFIG_PATH)
    except Exception:
        os.unlink(tmp)
        raise
    try:
        from backend.db import record_config_changes
        record_config_changes(_CONFIG_PATH.name, source, old, cfg)
    except Exception as e:
        logger.error(f"live_config: config change not recorded — {e}")


# Settings that must never be promoted from demo — account-size specific.
# starting_balance: demo=$2k, live=$100k — completely different denominator
# (l0_portfolio_loss_cap_max left both config files on 2026-11-21, R111 a: the cap is L0's share of equity in both
#  books, so no dollar cap is configured to leak.)
_PROMOTE_EXCLUDE = {"starting_balance"}


def demo_thresholds() -> dict:
    """Return promotable demo gate thresholds (excludes account-size-specific keys)."""
    from backend.demo_config import get_demo_config
    cfg = get_demo_config()
    return {k: v for k, v in cfg.items() if k not in _PROMOTE_EXCLUDE}
