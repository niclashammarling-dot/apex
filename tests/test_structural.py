"""
Structural integrity tests — assert that key component boundaries stay in sync.

These catch config/code drift that unit tests miss: mismatched key sets,
naming divergences, and context shape gaps between demo and live pipelines.
Run automatically on Saturdays via the scheduler; also part of CI.
"""
from backend.maintenance import check_config_parity, check_sector_names, check_context_parity


def test_config_key_parity():
    """demo_config and live_config must expose identical keys."""
    assert check_config_parity() == []


def test_sector_etf_names_match_config():
    """lock_leading.SECTOR_ETF must map every sector in config.SECTORS, with exact names."""
    assert check_sector_names() == []


def test_lock3_context_parity():
    """Demo and live gate runners must build identical risk_limits keys for Lock 3."""
    assert check_context_parity() == []
