"""A live config that lacks a runtime key is unavailable (R150 b, ruled 2026-11-28; M12's remainder).

Vault: raw/notes/2026-11/2026-11-28-apex-get-config-readers-money-path-scope.md. Until 2026-11-28 get_live_config()
returned the keys the file held, and the .get() readers on the money path fell to a second value without a word.
Measured on a synthetic copy of the live file with one key removed: L3's threshold for an uncalibrated sector became
DEMO_L3_SCORE_THRESHOLD_MIN 0.70 (file 0.65), L1's VIX maximum 25.0 (file 30), the exit pass's time stop went off with
no log line, and the canonical live sample started at "0000". Now a key absent or null makes the file unavailable on
R090 a's path (entries halt with the cause on gate_cycles, one CRITICAL mail per cause, exits on the legs with the time
stop and ratchet off), and every money-path reader subscripts.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend import live_config as lc

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "data" / "live_config.json"
# The money-path modules (gate, sizing, exits, L0) of both books: no config read through .get() (scope note § Build 4).
MONEY_PATH = ["backend/gate/chain.py", "backend/gate/l1_eligibility.py", "backend/gate/l2_risk.py",
              "backend/gate/gate_runner_live.py", "backend/live_trades_tracker.py", "backend/gate/gate_runner.py",
              "backend/wallet.py"]
CONFIG_GET = re.compile(r"\b(?:cfg|_cfg|live_cfg|demo_cfg|get_live_config\(\)|get_demo_config\(\))\.get\(")


def _full() -> dict:
    return json.loads(TRACKED.read_text())


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "live_config.json"
    monkeypatch.setattr(lc, "_CONFIG_PATH", p)
    monkeypatch.setattr(lc, "_alerted", set())
    return p


def _write_without(path: Path, key: str, null: bool = False) -> None:
    d = _full()
    if null:
        d[key] = None
    else:
        del d[key]
    path.write_text(json.dumps(d))


@pytest.mark.parametrize("null", [False, True], ids=["absent", "null"])
@pytest.mark.parametrize("key", lc._KEYS)
def test_a_runtime_key_absent_or_null_makes_the_file_unavailable(cfg_path, key, null):
    _write_without(cfg_path, key, null)
    before = cfg_path.read_bytes()
    with patch("backend.alerts.alert_live_config_unavailable") as alert:
        with pytest.raises(lc.LiveConfigUnavailable, match=rf"has no value for {key}$"):
            lc.get_live_config()
    assert alert.call_count == 1 and cfg_path.read_bytes() == before          # mailed, nothing written


def test_a_complete_file_gives_every_runtime_key(cfg_path):
    cfg_path.write_text(json.dumps({**_full(), "not_a_key": 1}))
    assert sorted(lc.get_live_config()) == sorted(lc._KEYS)


def test_startup_reports_an_incomplete_file_once_and_completes_nothing(cfg_path):
    _write_without(cfg_path, "max_hold_days")
    before = cfg_path.read_bytes()
    with patch("backend.alerts.alert_live_config_unavailable") as alert:
        lc.ensure_config_exists()
        with pytest.raises(lc.LiveConfigUnavailable):
            lc.get_live_config()                                               # the gate's read: same cause
    assert alert.call_count == 1 and cfg_path.read_bytes() == before


def test_the_live_gate_halts_on_an_incomplete_file_with_the_key_named(cfg_path, monkeypatch):
    from backend.gate import gate_runner_live as glr
    from backend.gate.cycle import GateCycle
    monkeypatch.setattr(glr, "LIVE_ENABLED", True)
    _write_without(cfg_path, "l3_score_threshold_min")
    cycle = GateCycle.unrecorded()
    with patch("backend.alerts.alert_live_config_unavailable"):
        assert glr._run(cycle) == []
    assert cycle.reason == "live_config_unavailable: live_config.json has no value for l3_score_threshold_min"


def test_the_exit_pass_on_an_incomplete_file_runs_with_time_stop_and_ratchet_off(cfg_path):
    from backend import live_trades_tracker as tr
    _write_without(cfg_path, "max_hold_days")
    with patch("backend.alerts.alert_live_config_unavailable"):
        assert tr._exit_config() == {}
    broker = MagicMock()
    trade = {"id": 1, "ticker": "X", "alpaca_order_id": "p", "price": 100.0, "signal_price": 100.0}
    tr._maybe_ratchet_bracket_sl(trade, 120.0, {}, broker)                    # +20 %: in territory if it were on
    broker.get_order_by_id.assert_not_called()


def test_the_canonical_sample_start_has_no_default(cfg_path):
    from backend import db
    cfg_path.write_text(json.dumps(_full()))
    assert db.live_sample_since() == _full()["live_account_since"]
    _write_without(cfg_path, "live_account_since")
    with patch("backend.alerts.alert_live_config_unavailable"):
        with pytest.raises(lc.LiveConfigUnavailable):
            db.live_sample_since()                                             # never "0000", every live trade


def test_the_live_equity_curve_starts_at_the_file_and_an_incomplete_file_is_named(cfg_path, monkeypatch):
    from fastapi import HTTPException
    from backend.routers import live_router
    monkeypatch.setattr(live_router, "LIVE_ENABLED", True)
    cfg_path.write_text(json.dumps(_full()))
    with patch("backend.brokers.alpaca.get_positions", return_value=[{"unrealized_pnl": 5.0}]), \
         patch("backend.db.get_live_equity_curve", return_value={"points": []}) as curve:
        assert live_router.live_equity() == {"points": []}
    curve.assert_called_once_with(unrealised_total=5.0, since=_full()["live_account_since"])
    _write_without(cfg_path, "live_account_since")
    with patch("backend.alerts.alert_live_config_unavailable"), \
         patch("backend.db.get_live_equity_curve") as curve:
        with pytest.raises(HTTPException) as e:
            live_router.live_equity()                                          # was since=None: every live trade
    assert "has no value for live_account_since" in e.value.detail and not curve.called


def test_the_tracked_live_config_gives_every_runtime_key():
    assert lc.missing_keys(lc._upgraded(_full())) == []
    planted = {k: v for k, v in _full().items() if k != "stop_loss_pct"}        # control: the predicate sees a gap
    assert lc.missing_keys(planted) == ["stop_loss_pct"]


def _config_gets(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if CONFIG_GET.search(line)]


def test_no_money_path_module_reads_a_config_key_through_get():
    hits = {m: _config_gets((ROOT / m).read_text()) for m in MONEY_PATH}
    assert {m: h for m, h in hits.items() if h} == {}
    assert _config_gets('    max_hold_days = cfg.get("max_hold_days")\n') != []          # planted control
    assert _config_gets('    x = get_live_config().get("live_account_since") or "0000"\n') != []
    assert _config_gets('    p = l1.data.get("earnings_near_penalty", 0.0)\n') == []    # a data dict, not a config


def test_the_test_helper_places_every_runtime_key():
    """tests/cfg_complete.py gives each removed fallback's value; a key must be either there or in NO_FALLBACK."""
    from backend import demo_config
    from tests.cfg_complete import NO_FALLBACK, REMOVED_FALLBACKS
    keys = set(lc._KEYS) | set(demo_config._KEYS)
    assert keys == set(REMOVED_FALLBACKS) | NO_FALLBACK and not set(REMOVED_FALLBACKS) & NO_FALLBACK

