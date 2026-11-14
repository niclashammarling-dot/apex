"""
The host suite sees CI's LIVE_ENABLED (2026-11-14). The host's .env sets it true and CI has no .env; three
regime-exit tests passed on the host and failed in CI 328/329, named by the run's annotations. conftest pins
the variable before backend.config is imported.
"""
import backend.config as config_module
import backend.live_trades_tracker as ltt


def test_live_enabled_is_cis_value_under_pytest():
    assert config_module.LIVE_ENABLED is False
    assert ltt.LIVE_ENABLED is False
