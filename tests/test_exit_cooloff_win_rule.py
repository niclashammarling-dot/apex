"""The win window is its own L1 rule (phase 3, Niclas 2026-10-31: "egen regel låter mer konsekvent").
db.get_recently_exited_*tickers return (exit_cooloff, exit_cooloff_win); the runners mark each skip with the rule
whose window holds it, and history before the split stays under exit_cooloff."""
from datetime import datetime, timedelta, timezone

import pytest

from backend import db
from backend.gate import lock_codes
from backend.gate.gate_runner import _pre_chain_states

NOW = datetime.now(timezone.utc)


def _ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


@pytest.fixture
def exits(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "exits.db")
    db.init_db()
    rows = [("TPW", "WIN", "TP", 100), ("TSLW", "WIN", "TSL", 100), ("SLL", "LOSS", "SL", 10),
            ("TIMEX", "EXPIRED", "TIME", 10), ("REGW", "WIN", "REGIME", 10),
            ("OLDTP", "WIN", "TP", 200), ("OLDSL", "LOSS", "SL", 30),
            ("BOTH", "WIN", "TP", 100), ("BOTH", "LOSS", "SL", 5)]
    conn = db.get_db()
    for table, extra in (("trades", ("action", "BUY")), ("live_trades", ("alpaca_order_id", "x"))):
        for i, (t, out, why, h) in enumerate(rows):
            conn.execute(f"INSERT INTO {table} (timestamp, ticker, sector, {extra[0]}, outcome, exit_reason, "
                         "exited_at) VALUES (?, ?, 'Energy', ?, ?, ?, ?)",
                         (_ago(h + 1), t, f"{extra[1]}{i}", out, why, _ago(h)))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("fn", [db.get_recently_exited_tickers, db.get_recently_exited_live_tickers])
def test_the_windows_split_into_the_two_rules(exits, fn):
    other, won = fn(sl_hours=24, tp_hours=168)
    assert won == {"TPW", "TSLW", "BOTH"}
    assert other == {"SLL", "TIMEX", "REGW", "BOTH"}


def test_a_skip_in_the_win_window_stops_on_exit_cooloff_win():
    checks = {"position_open": set(), "cooloff": set(), "exit_cooloff": set(), "exit_cooloff_win": {"TPW"}}
    states = _pre_chain_states("TPW", checks)
    assert lock_codes.stop_of(states) == (1, "exit_cooloff_win")
    assert lock_codes.stop_code(1, "exit_cooloff_win") in lock_codes.SKIP_RULES


def test_both_windows_stop_on_exit_cooloff_first_in_the_register():
    checks = {"exit_cooloff": {"BOTH"}, "exit_cooloff_win": {"BOTH"}}
    assert lock_codes.stop_of(_pre_chain_states("BOTH", checks)) == (1, "exit_cooloff")


def test_each_rule_carries_one_window_key():
    assert lock_codes.rule_for("exit_cooloff").config_keys == ("exit_cooloff_hours",)
    assert lock_codes.rule_for("exit_cooloff_win").config_keys == ("tp_cooloff_hours",)
