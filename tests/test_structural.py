"""
Structural integrity tests — assert that key component boundaries stay in sync.

These catch config/code drift that unit tests miss: mismatched key sets,
naming divergences, and context shape gaps between demo and live pipelines.
Run automatically on Saturdays via the scheduler; also part of CI.
"""
from backend.maintenance import (
    check_config_parity,
    check_context_parity,
    check_exit_behavior_parity,
    check_gate_insert_fields,
    check_promote_exclusions,
    check_sector_names,
)


def test_config_key_parity():
    """demo_config and live_config must expose identical keys."""
    assert check_config_parity() == []


def test_sector_etf_names_match_config():
    """lock_leading.SECTOR_ETF must map every sector in config.SECTORS, with exact names."""
    assert check_sector_names() == []


def test_lock3_context_parity():
    """Demo and live gate runners must build identical risk_limits keys for Lock 3."""
    assert check_context_parity() == []


def test_promote_exclusions():
    """starting_balance and daily_loss_cap must never appear in the promotable set."""
    assert check_promote_exclusions() == []


def test_exit_behavior_parity():
    """Exit config keys used in wallet.py must all be present in live_trades_tracker.py."""
    assert check_exit_behavior_parity() == []


def test_gate_insert_fields():
    """
    Every gate history schema column must appear in the corresponding INSERT SQL.
    Call-site drift is caught at runtime by _assert_insert_fields in db.py.
    """
    from backend.db import init_db
    init_db()  # ensure tables exist in the test DB
    assert check_gate_insert_fields() == []
