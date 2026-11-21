"""CHECK 101 — L5 sub-check error share per runner and session (2026-11-21, shipped with the options source switch).

Founding case: an Alpaca chain without lastTradeDate made unusual_calls error on 102 of 102 tickers in the 11-20 probe.
Both controls from the INDEX scope: a session where every row's unusual_calls carries "no lastTradeDate" is CRITICAL;
a session at 5 % errors (the live worst session 10-20..11-20) is not.
"""
import json
from datetime import date

UCV_ERR = {"pass": False, "error": True,
           "reason": "options chain has no lastTradeDate: today's volume cannot be told from earlier days'"}
UCV_OK = {"pass": True, "cv_ratio": 2.4, "reason": "call/put vol 2.4x today (unusual)"}
OTHER = {"relative_strength": {"pass": True}, "put_call_ratio": {"pass": False}, "volume_accumulation": {"pass": True}}


def _row(runner, ts, ucv):
    return (runner, ts, json.dumps({**OTHER, "unusual_calls": ucv}))


def _run101(monkeypatch, rows, today=date(2026, 11, 23)):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check101(rows=rows, today=today)
    return [(f[2], f[3], f[4]) for f in core.findings if f[0] == 101]


def test_every_row_erroring_in_a_session_is_critical(monkeypatch):
    rows = [_row("live", f"2026-11-23T{15 + i}:00:00+00:00", UCV_ERR) for i in range(6)]
    out = _run101(monkeypatch, rows)
    crit = [o for o in out if o[0] == "CRITICAL"]
    assert len(crit) == 1 and crit[0][1].endswith("unusual_calls")
    assert crit[0][2].startswith("live 2026-11-23: unusual_calls errored on 6 of 6 L5 rows (100%)")


def test_a_five_percent_session_is_not_critical(monkeypatch):
    rows = [_row("live", f"2026-11-23T15:{i:02d}:00+00:00", UCV_ERR if i == 0 else UCV_OK) for i in range(20)]
    out = _run101(monkeypatch, rows)
    assert [o[0] for o in out] == ["INFO"] and "live 2026-11-23 unusual_calls 1/20 (5%)" in out[0][2]


def test_runners_and_sessions_are_judged_apart(monkeypatch):
    rows = ([_row("demo", f"2026-11-23T15:0{i}:00+00:00", UCV_ERR) for i in range(5)]
            + [_row("live", f"2026-11-23T15:0{i}:00+00:00", UCV_OK) for i in range(5)]
            + [_row("live", f"2026-11-10T15:0{i}:00+00:00", UCV_ERR) for i in range(5)])   # before the window
    crit = [o for o in _run101(monkeypatch, rows) if o[0] == "CRITICAL"]
    assert len(crit) == 1 and crit[0][2].startswith("demo 2026-11-23")


def test_too_few_rows_are_not_judged(monkeypatch):
    rows = [_row("live", f"2026-11-23T15:0{i}:00+00:00", UCV_ERR) for i in range(4)]
    out = _run101(monkeypatch, rows)
    assert [o[0] for o in out] == ["INFO"] and "no session with enough rows" in out[0][2]


def test_the_session_is_the_new_york_date():
    from audit.checks_gate import l5_error_shares
    s = l5_error_shares([("live", "2026-11-24T01:30:00+00:00", json.dumps({"unusual_calls": UCV_ERR})),
                         ("live", "2026-11-23T15:00:00Z", "not json"), ("live", "2026-11-23T15:00:00Z", None)])
    assert s == {("live", "2026-11-23", "unusual_calls"): (1, 1)}


def test_the_database_path_reads_both_tables(tmp_path, monkeypatch):
    import sqlite3
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    for t in ("live_gate_history", "demo_gate_history"):
        conn.execute(f"CREATE TABLE {t} (timestamp TEXT, l5_positioning_checks TEXT)")
    conn.executemany("INSERT INTO demo_gate_history VALUES (?, ?)",
                     [(f"2026-11-23T15:0{i}:00+00:00", json.dumps({"unusual_calls": UCV_ERR})) for i in range(5)])
    conn.commit(); conn.close()
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check101(today=date(2026, 11, 23))
    assert [f[2] for f in core.findings if f[0] == 101] == ["CRITICAL", "INFO"]


def test_check101_runs_in_the_gate_module():
    import inspect
    from audit import checks_gate as cg
    assert "check101()" in inspect.getsource(cg.run)
