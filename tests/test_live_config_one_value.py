"""One value per live fact (M12; R090 a ruled 2026-11-17, built 2026-11-21; vault
raw/notes/2026-11/2026-11-16-apex-live-facts-with-more-than-one-value.md).

Until 2026-11-21 a missing data/live_config.json traded silently on config.py defaults and a corrupt one was written
over with them: 11 of 27 keys differed from the file (starting balance 100000 vs 10000, stop 0.04 vs 0.06, TP 0.12
vs 0.06). Now the file is the only source: missing or unreadable halts live entries with the cause on the
gate_cycles row, nothing is written, and the frontend carries no numeric fallback for a live config key.
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import live_config as lc
from tests.book_cfg import live_cfg

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "live_config.json"
    monkeypatch.setattr(lc, "_CONFIG_PATH", p)
    monkeypatch.setattr(lc, "_alerted", set())
    return p


def test_a_missing_file_raises_and_writes_nothing(cfg_path):
    with patch("backend.alerts.alert_live_config_unavailable") as alert:
        with pytest.raises(lc.LiveConfigUnavailable, match="missing"):
            lc.get_live_config()
        with pytest.raises(lc.LiveConfigUnavailable):
            lc.get_live_config()
    assert not cfg_path.exists() and alert.call_count == 1          # mailed once per cause


def test_a_corrupt_file_is_left_as_it_is(cfg_path):
    cfg_path.write_text('{"stop_loss_pct": 0.06,')                   # truncated
    before = cfg_path.read_bytes()
    with patch("backend.alerts.alert_live_config_unavailable"):
        with pytest.raises(lc.LiveConfigUnavailable, match="unreadable"):
            lc.get_live_config()
    assert cfg_path.read_bytes() == before


def test_the_file_is_the_only_source(cfg_path):
    full = json.loads((ROOT / "data" / "live_config.json").read_text())
    cfg_path.write_text(json.dumps({**full, "not_a_key": 1}))
    assert lc.get_live_config() == {k: full[k] for k in lc._KEYS}
    # A key the file lacks has no default. Until 2026-11-28 this test wrote a two-key file and asserted it came back
    # as two keys, which pinned the gap R150 b closed: a file that lacks a runtime key is now unavailable
    # (tests/test_live_config_complete.py).


def test_startup_does_not_create_the_file_and_settings_cannot_write_one(cfg_path):
    with patch("backend.alerts.alert_live_config_unavailable") as alert:
        lc.ensure_config_exists()
        with pytest.raises(lc.LiveConfigUnavailable):
            lc.set_live_config({"stop_loss_pct": 0.05})
    assert not cfg_path.exists() and alert.call_count == 1


def test_the_live_gate_halts_with_the_cause_on_the_cycle(monkeypatch):
    from backend.gate import gate_runner_live as glr
    from backend.gate.cycle import GateCycle
    monkeypatch.setattr(glr, "LIVE_ENABLED", True)
    cycle = GateCycle.unrecorded()
    with patch("backend.live_config.get_live_config", side_effect=lc.LiveConfigUnavailable("live_config.json missing")):
        assert glr._run(cycle) == []
    assert cycle.reason == "live_config_unavailable: live_config.json missing"


def test_exits_keep_running_with_the_time_stop_off():
    from backend import live_trades_tracker as tr
    with patch("backend.live_config.get_live_config", side_effect=lc.LiveConfigUnavailable("x missing")):
        assert tr._exit_config() == {}                                  # time stop and ratchet off
    with patch("backend.live_config.get_live_config", return_value=live_cfg(max_hold_days=25)):
        assert tr._exit_config() == live_cfg(max_hold_days=25)


def test_settings_show_live_as_unavailable_not_defaults():
    from backend.routers import signals_router as sr
    with patch("backend.live_config.get_live_config", side_effect=lc.LiveConfigUnavailable("live_config.json missing")):
        out = sr.get_settings()
    assert out["live"] == {"unavailable": "live_config.json missing"}


def test_the_frontend_carries_no_numeric_fallback_for_a_live_config_key():
    keys = sorted(lc._KEYS, key=len, reverse=True)
    hits = []
    for f in (ROOT / "frontend/src").rglob("*.jsx"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"\?\?\s*-?\d", line) and any(re.search(rf"\b{k}\b", line) for k in keys):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert hits == [], hits
    terminal = (ROOT / "frontend/src/components/ApexTerminal.jsx").read_text()
    assert "25% SECTOR CAP" not in terminal                           # a fact written as a literal


def test_the_unavailable_alert_says_entries_halt_and_nothing_was_written():
    from backend import alerts
    sent = []
    with patch.object(alerts, "_dispatch", lambda title, body: sent.append((title, body))):
        alerts.alert_live_config_unavailable("live_config.json missing")
    title, body = sent[0]
    assert "live entries halted" in title and "not written over" in body and "live_config.json missing" in body


def test_the_ops_endpoint_reports_available_and_unavailable(cfg_path):
    from backend.routers import signals_router as sr
    with patch("backend.alerts.alert_live_config_unavailable"):
        down = sr.get_live_config_state()
    assert down["available"] is False and "missing" in down["cause"] and down["path"] == "data/live_config.json"
    full = json.loads((ROOT / "data" / "live_config.json").read_text())
    cfg_path.write_text(json.dumps({k: v for k, v in full.items() if k != "max_hold_days"}))
    with patch("backend.alerts.alert_live_config_unavailable"):
        gap = sr.get_live_config_state()                               # incomplete is unavailable (R150 b)
    assert gap["available"] is False and gap["cause"].endswith("has no value for max_hold_days")
    cfg_path.write_text(json.dumps(full))
    up = sr.get_live_config_state()
    assert up["available"] is True and up["cause"] is None and up["keys"] == len(lc._KEYS)


def test_the_test_tab_mounts_the_live_config_card():
    terminal = (ROOT / "frontend/src/components/ApexTerminal.jsx").read_text()
    assert "<LiveConfigPanel />" in terminal
    assert "/api/ops/live-config" in (ROOT / "frontend/src/components/LiveConfigPanel.jsx").read_text()
