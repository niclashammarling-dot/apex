"""CHECK 46 / 47 measure peak gain from the ratchet's own anchor (signal_price, else entry_price),
as live_trades_tracker._exit_anchor does. Found 2026-10-21: since the 10-13 fill capture moved
entry_price to the fill, CHECK 46 raised a false CRITICAL on COP 90 and EOG 92 (above the trigger
from the fill, below it from the anchor the ratchet uses)."""
import json
import sqlite3

from audit import _audit_core as core
from audit import checks_data as cd


def _db(tmp_path, rows):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "live_config.json").write_text(json.dumps(
        {"profit_lock_trigger_pct": 0.04, "profit_lock_trail_pct": 0.01}))
    con = sqlite3.connect(tmp_path / "data" / "apex.db")
    con.execute("CREATE TABLE live_trades (id INTEGER, ticker TEXT, alpaca_order_id TEXT, entry_price REAL, "
                "signal_price REAL, peak_price REAL, profit_lock_activated INTEGER, outcome TEXT, timestamp TEXT, "
                "exit_reason TEXT, exit_price REAL, exited_at TEXT)")
    con.executemany("INSERT INTO live_trades VALUES (?,?,?,?,?,?,?,'OPEN','2026-10-15T13:53:39+00:00',NULL,NULL,NULL)", rows)
    con.commit()
    con.close()


def _run(monkeypatch, tmp_path, check):
    monkeypatch.setattr(cd, "REPO", tmp_path)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cd, "flag", core.flag)
    check()
    return core.findings


def test_check46_reads_the_ratchet_anchor_not_the_fill(monkeypatch, tmp_path):
    _db(tmp_path, [
        (90, "COP", "p90", 141.26, 141.945, 147.33, 0),     # 4.3 % from the fill, 3.8 % from the anchor: quiet
        (91, "XYZ", "p91", 141.26, 141.945, 148.00, 0),     # 4.3 % from the anchor: the ratchet should have fired
        (93, "OLD", "p93", 100.00, None, 104.50, 0),        # no signal_price: the fill is the anchor
    ])
    out = _run(monkeypatch, tmp_path, cd.check46)
    hit = sorted(f[4].split(" ")[0] for f in out if f[0] == 46 and f[2] == "CRITICAL")
    assert hit == ["OLD", "XYZ"]
    assert any("peak_gain=4.3%" in f[4] for f in out if f[4].startswith("XYZ"))


def test_check47_open_subcheck_reads_the_anchor(monkeypatch, tmp_path):
    _db(tmp_path, [
        (1, "AAA", "p1", 100.0, 101.0, 108.5, 1),           # 8.5 % from the fill, 7.4 % from the anchor: quiet
        (2, "BBB", "p2", 100.0, 100.0, 108.5, 1),           # 8.5 % from the anchor: warns (> 2 x trigger)
    ])
    out = _run(monkeypatch, tmp_path, cd.check47)
    assert [f[4].split(" ")[0] for f in out if f[0] == 47 and f[2] == "WARNING"] == ["BBB"]
