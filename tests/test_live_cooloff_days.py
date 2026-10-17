"""Both engines take their re-entry cooloffs from live config by default (2026-10-17,
PRODUCTION_GAP row 23). Before, both defaulted to 5 / 0 days against live's 24 h / 168 h."""
import inspect
from unittest.mock import patch

from backend.backtest import engine, engine_fast
from backend.backtest.live_shape import live_cooloff_days


def test_hours_round_up_to_days():
    with patch("backend.live_config.get_live_config",
               return_value={"exit_cooloff_hours": 24, "tp_cooloff_hours": 168}):
        assert live_cooloff_days() == (1, 7)
    with patch("backend.live_config.get_live_config",
               return_value={"exit_cooloff_hours": 30, "tp_cooloff_hours": 0}):
        assert live_cooloff_days() == (2, 0)


def test_both_engines_default_to_the_live_resolver():
    for mod in (engine, engine_fast):
        params = inspect.signature(mod.run).parameters
        assert params["sl_cooldown_days"].default is None, mod.__name__
        assert params["tp_cooldown_days"].default is None, mod.__name__
        assert "live_cooloff_days()" in inspect.getsource(mod.run), mod.__name__
