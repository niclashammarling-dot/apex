"""A test's config dict completed with the values the removed .get() fallbacks gave (R150 b, 2026-11-28).

Until 2026-11-28 the money-path readers read the config through .get(key, fallback), so a test could hand the chain,
the runners, L0 or the exit pass a dict with only the keys it cared about, and the readers filled the rest. The readers
now subscript (a live file that lacks a key is unavailable, vault
raw/notes/2026-11/2026-11-28-apex-get-config-readers-money-path-scope.md). complete() gives an older test's partial dict
the value each removed fallback gave, so it still tests what it tested: the explicit fallbacks as written; None where
the reader treated None as off (profit lock, time stop); where None went on to an evaluator's DEMO_ default, that
default; "0000" where the canonical sample's start fell back to every live trade. A new test writes its keys out.
"""
from backend import config

REMOVED_FALLBACKS = {
    "l1_eligibility_cooloff_hours":           4,                                       # gate_runner(_live) .get(…, 4)
    "l1_eligibility_exit_cooloff_hours":      24,
    "l1_eligibility_exit_cooloff_win_hours":  168,
    "l2_risk_overflow_step":                  0.05,
    "l1_eligibility_vix_max":                 25.0,                                    # l1_eligibility: 25.0 if None
    "l1_eligibility_macro_pre_event_penalty": 0.05,
    "l1_eligibility_macro_event_days":        1,
    "l2_risk_earnings_days":                  config.L2_RISK_EARNINGS_DAYS,           # l2_risk: config.* if None
    "l2_risk_earnings_near_days":             config.L2_RISK_EARNINGS_NEAR_DAYS,
    "l2_risk_earnings_near_penalty":          config.L2_RISK_EARNINGS_NEAR_PENALTY,
    "l2_risk_etf_floor":                      -1.0,
    "l3_score_threshold_min":                 config.DEMO_L3_SCORE_THRESHOLD_MIN,     # None → l3_score's default
    "l4_sentiment_grok_min":                  config.DEMO_L4_SENTIMENT_GROK_MIN,      # None → l4_sentiment's default
    "l6_review_claude_min":                   config.DEMO_L6_REVIEW_CLAUDE_MIN,       # None → l6_review's default
    "l5_positioning_leading_mode":            "pooled_scalar",
    "l0_portfolio_drawdown_guard_max":        0.20,
    "l2_risk_sector_cap_max":                 config.DEMO_L2_RISK_SECTOR_CAP_MAX,     # demo's .get(…, DEMO_…)
    "l2_risk_max_positions_count":            config.DEMO_L2_RISK_MAX_POSITIONS_COUNT,
    "max_position_size":                      config.DEMO_MAX_POSITION_SIZE,
    "profit_lock_trigger_pct":                None,                                    # None: the ratchet off
    "profit_lock_trail_pct":                  None,
    "max_hold_days":                          None,                                    # None: the time stop off
    "live_account_since":                     "0000",                                  # live_sample_since: or "0000"
}

# Read by subscript before 2026-11-28 (M12, 3d79130): every test that reaches them already writes them, so complete()
# gives them nothing. Every runtime key is in exactly one of the two (tests/test_live_config_complete.py), so a key
# added to _KEYS has to be placed here before a test can lean on a value it does not state.
NO_FALLBACK = {"take_profit_pct", "stop_loss_pct", "starting_balance", "l2_risk_etf_penalty"}


def complete(cfg: dict | None = None, **overrides) -> dict:
    """The removed fallbacks, then the test's own keys, then overrides."""
    return {**REMOVED_FALLBACKS, **(cfg or {}), **overrides}
