"""gate_cycles: one row per gate cycle, whether or not it writes a gate row (2026-10-04).

Quiet cycles (no Lock 1 candidates, all skipped) and live halts wrote no gate
row, so CHECK 80/85, /api/ops/window and the cycle watch undercounted quiet
days, and CHECK 83 could not see a quiet second scheduler. From
GATE_CYCLES_FROM the readers count scheduler rows in gate_cycles only.
"""
import sqlite3
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from backend.gate.cycle import GateCycle, run_recorded
from tests.test_gate_runners import _account, _demo_patches, _live_patches, _signal

T0 = "2026-10-05T13:41:00.000000+00:00"      # a past start: rows' own clocks cannot equal it


def _cycle():
    return GateCycle("run_gate", "scheduler", T0)


def _row(job):
    from backend.db import get_db
    with get_db() as conn:
        return conn.execute("SELECT trigger, finished_at, outcome, reason FROM gate_cycles WHERE job = ? "
                            "ORDER BY id DESC LIMIT 1", (job,)).fetchone()


# ── the recorder ────────────────────────────────────────────────────────────────

def test_recorded_cycle_writes_trigger_outcome_and_reason():
    from backend import db
    db.init_db()

    def run(cycle):
        cycle.reason = "no_candidates"
        return ["r"]
    assert run_recorded("t_rec_ok", "scheduler", run) == ["r"]      # run()'s value passes through
    trigger, finished, outcome, reason = _row("t_rec_ok")
    assert (trigger, outcome, reason) == ("scheduler", "ok", "no_candidates") and finished


def test_recorded_cycle_error_is_recorded_and_reraised():
    from backend import db
    db.init_db()

    def run(cycle):
        cycle.reason = "evaluated"
        raise RuntimeError("alpaca down")
    with pytest.raises(RuntimeError):
        run_recorded("t_rec_err", "manual", run)
    trigger, _, outcome, reason = _row("t_rec_err")
    assert trigger == "manual" and outcome.startswith("error: RuntimeError('alpaca down'") and reason == "evaluated"


def test_row_exists_while_the_cycle_runs():
    """Inserted at start, closed at the end: a cycle killed mid-run still leaves its start."""
    from backend import db
    db.init_db()
    seen = []
    run_recorded("t_rec_mid", "scheduler", lambda cycle: seen.append(_row("t_rec_mid")) or [])
    assert seen[0] is not None and seen[0][1] is None and seen[0][2] is None    # open: no finish, no outcome


def test_finish_failure_never_stops_the_cycle(monkeypatch):
    from backend import db
    db.init_db()

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "finish_gate_cycle", broken)
    assert run_recorded("t_rec_fin", "scheduler", lambda cycle: [2]) == [2]
    assert _row("t_rec_fin")[2] is None                     # start kept, end missing


def test_record_failure_never_stops_the_cycle(monkeypatch):
    from backend import db

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "insert_gate_cycle", broken)
    assert run_recorded("t_rec_locked", "scheduler", lambda cycle: [1]) == [1]


# ── the runners: reason per exit, one clock per cycle ──────────────────────────

def _demo(cycle, **kw):
    from backend.gate import gate_runner
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _demo_patches(**kw)]
        stack.enter_context(patch("backend.gate.gate_runner._persist_multiplier_stats"))
        return gate_runner.run(cycle=cycle), mocks


def _live(cycle, **kw):
    from backend.gate import gate_runner_live
    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _live_patches(**kw)]
        return gate_runner_live.run(cycle=cycle), mocks


@pytest.mark.parametrize("kw, reason", [
    ({"candidates": []}, "no_candidates"),
    ({"candidates": [_signal("NVDA")], "open_tickers": {"NVDA"}}, "all_skipped"),
    ({"candidates": [_signal("NVDA")]}, "evaluated"),
])
def test_demo_reason(kw, reason):
    c = _cycle()
    _demo(c, **kw)
    assert c.reason == reason


def test_demo_every_evaluation_raised_is_not_a_quiet_cycle():
    c = _cycle()
    with patch("backend.gate.gate_runner._evaluate", side_effect=RuntimeError("x")):
        _demo(c, candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)])
    assert c.reason == "all_raised"


def test_demo_some_evaluations_raised():
    from backend.gate import gate_runner
    real = gate_runner._evaluate

    def flaky(signal, *a, **k):
        if signal["ticker"] == "AAPL":
            raise RuntimeError("x")
        return real(signal, *a, **k)
    c = _cycle()
    with patch("backend.gate.gate_runner._evaluate", side_effect=flaky):
        _demo(c, candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)])
    assert c.reason == "evaluated_some_raised"


@pytest.mark.parametrize("kw, reason", [
    ({"candidates": [], "live_enabled": False}, "disabled"),
    ({"candidates": [_signal("NVDA")], "unreconciled": [{"ticker": "HON"}]}, "halt_unreconciled"),
    ({"candidates": [_signal("NVDA")], "account": _account(trading_blocked=True)}, "account_blocked"),
    ({"candidates": []}, "no_candidates"),
    ({"candidates": [_signal("NVDA")], "open_tickers": {"NVDA"}}, "all_skipped"),
    ({"candidates": [_signal("NVDA")]}, "evaluated"),
])
def test_live_reason(kw, reason):
    c = GateCycle("run_live_gate", "scheduler", T0)
    _live(c, **kw)
    assert c.reason == reason


def test_rows_carry_the_cycle_start_and_their_own_timestamp():
    """One clock per cycle: the recorded start is every row's cycle_started_at, so
    gate_cycles and gate rows cannot disagree on the minute. Skip rows used to
    reuse that stamp as their timestamp too."""
    _, mocks = _demo(_cycle(), candidates=[_signal("NVDA", sid=1), _signal("AAPL", sid=2)],
                     open_tickers={"NVDA"})
    rows = [c.args[0] for c in mocks[8].call_args_list]              # insert_demo_gate_result
    assert [r["cycle_started_at"] for r in rows] == [T0, T0]
    skip = next(r for r in rows if r["gate_decision"] == "SKIPPED_OPEN")
    assert skip["timestamp"] != T0
    _, mocks = _live(GateCycle("run_live_gate", "scheduler", T0),
                     candidates=[_signal("NVDA", sid=1)], open_tickers={"NVDA"})
    row = mocks[9].call_args.args[0]                                 # insert_live_gate_result
    assert row["cycle_started_at"] == T0 and row["timestamp"] != T0


def test_a_direct_call_still_works_unrecorded():
    _, mocks = _demo(None, candidates=[_signal("NVDA")])
    assert mocks[8].call_args.args[0]["cycle_started_at"]


# ── the reader: source by date ─────────────────────────────────────────────────

D_OLD, D_NEW = "2026-10-02", "2026-10-06"


def _db(with_table=True):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE demo_gate_history (timestamp TEXT, cycle_started_at TEXT)")
    if with_table:
        conn.execute("CREATE TABLE gate_cycles (job TEXT, trigger TEXT, started_at TEXT)")
    return conn


def test_from_the_cutover_quiet_cycles_count_and_manual_ones_do_not():
    from audit.gate_cycles import cycle_starts
    conn = _db()
    conn.executemany("INSERT INTO gate_cycles VALUES (?, ?, ?)", [
        ("run_gate", "scheduler", f"{D_NEW}T13:41:00.1+00:00"),       # quiet: no gate row
        ("run_gate", "scheduler", f"{D_NEW}T14:01:59.9+00:00"),       # its rows land at 14:02
        ("run_gate", "manual", f"{D_NEW}T14:10:00+00:00"),
        ("run_live_gate", "scheduler", f"{D_NEW}T13:53:00+00:00"),    # the other book
    ])
    conn.execute("INSERT INTO demo_gate_history VALUES (?, ?)",
                 (f"{D_NEW}T14:02:30+00:00", f"{D_NEW}T14:02:30+00:00"))   # a row whose own minute differs
    assert cycle_starts(conn, "demo_gate_history", D_NEW) == {D_NEW: [f"{D_NEW}T13:41", f"{D_NEW}T14:01"]}


def test_before_the_cutover_gate_rows_are_the_source():
    from audit.gate_cycles import cycle_starts
    conn = _db()
    conn.execute("INSERT INTO demo_gate_history VALUES (?, ?)", (f"{D_OLD}T13:41:05+00:00", f"{D_OLD}T13:41:00+00:00"))
    conn.execute("INSERT INTO gate_cycles VALUES ('run_gate', 'scheduler', ?)", (f"{D_OLD}T14:01:00+00:00",))
    assert cycle_starts(conn, "demo_gate_history", D_OLD)[D_OLD] == [f"{D_OLD}T13:41"]


def test_a_dead_day_with_one_manual_run_reads_zero():
    """The reviewer's case against 'any day with table rows': the fallback would
    have counted the manual cycle's gate rows as one scheduled cycle."""
    from audit.gate_cycles import cycle_starts
    conn = _db()
    conn.execute("INSERT INTO gate_cycles VALUES ('run_gate', 'manual', ?)", (f"{D_NEW}T15:00:00+00:00",))
    conn.execute("INSERT INTO demo_gate_history VALUES (?, ?)", (f"{D_NEW}T15:00:20+00:00", f"{D_NEW}T15:00:00+00:00"))
    assert cycle_starts(conn, "demo_gate_history", D_NEW) == {}


def test_a_db_without_the_table_reads_gate_rows_throughout():
    from audit.gate_cycles import cycle_starts
    conn = _db(with_table=False)
    conn.execute("INSERT INTO demo_gate_history VALUES (?, ?)", (f"{D_NEW}T13:41:05+00:00", f"{D_NEW}T13:41:00+00:00"))
    assert cycle_starts(conn, "demo_gate_history", D_NEW)[D_NEW] == [f"{D_NEW}T13:41"]


# ── CHECK 83 and 85 on recorded cycles ─────────────────────────────────────────

def _audit_repo(tmp_path, monkeypatch, cycles, rows=()):
    """cycles: (job, trigger, started_at); rows: (table, timestamp). GATE_CYCLES_FROM
    is pinned three days back so today's fixture reads as post-cutover."""
    from audit import _audit_core as core
    from audit import checks_gate as cg
    from audit import gate_cycles as gc
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    for t in ("live_gate_history", "demo_gate_history"):
        conn.execute(f"CREATE TABLE {t} (timestamp TEXT, ticker TEXT, cycle_started_at TEXT)")
    conn.execute("CREATE TABLE gate_cycles (job TEXT, trigger TEXT, started_at TEXT)")
    conn.executemany("INSERT INTO gate_cycles VALUES (?, ?, ?)", cycles)
    for t, ts in rows:
        conn.execute(f"INSERT INTO {t} VALUES (?, 'X', ?)", (ts, ts))
    conn.commit(); conn.close()
    monkeypatch.setattr(gc, "GATE_CYCLES_FROM", (date.today() - timedelta(days=3)).isoformat())
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    return cg, core


def test_check83_sees_a_quiet_second_scheduler(tmp_path, monkeypatch):
    d = date.today().isoformat()
    cycles = [("run_gate", "scheduler", f"{d}T{m}:00+00:00") for m in ("14:01", "14:04", "14:21", "14:24")]
    cg, core = _audit_repo(tmp_path, monkeypatch, cycles)                 # no gate rows at all
    cg.check83()
    alarms = [f[4] for f in core.findings if f[0] == 83 and f[2] == "CRITICAL"]
    assert len(alarms) == 1 and alarms[0].startswith("demo:") and "2 off-phase" in alarms[0]
    info = next(f[4] for f in core.findings if f[0] == 83 and f[2] == "INFO")
    assert "demo 3 pair(s), 3 from gate_cycles" in info


def test_check83_ignores_a_manual_run_between_scheduled_cycles(tmp_path, monkeypatch):
    d = date.today().isoformat()
    cycles = [("run_gate", "scheduler", f"{d}T{m}:00+00:00") for m in ("14:01", "14:21", "14:41")]
    cycles.append(("run_gate", "manual", f"{d}T14:05:00+00:00"))
    cg, core = _audit_repo(tmp_path, monkeypatch, cycles,
                           rows=[("demo_gate_history", f"{d}T14:05:30+00:00")])   # the manual cycle's row
    cg.check83()
    assert [f for f in core.findings if f[0] == 83 and f[2] != "INFO"] == []


def test_check85_drops_the_quiet_cycle_clause_from_the_cutover(tmp_path, monkeypatch):
    from audit import gate_cycles as gc
    day = "2026-10-02"                                                    # a full Friday session
    cycles = [("run_gate", "scheduler", f"{day}T{13 + (41 + 20 * k) // 60}:{(41 + 20 * k) % 60:02d}:00+00:00")
              for k in range(8)]
    cg, core = _audit_repo(tmp_path, monkeypatch, cycles)
    monkeypatch.setattr(gc, "GATE_CYCLES_FROM", "2026-10-01")
    sqlite3.connect(tmp_path / "repo/data/apex.db").execute(
        "CREATE TABLE session_flags (date TEXT, kind TEXT, status TEXT, cause TEXT)").connection.commit()
    monkeypatch.setattr(cg, "_C85_SINCE", "2000-01-01")
    monkeypatch.setattr(cg, "_c82_session_dates", lambda n: [day])
    monkeypatch.setattr(cg, "_c82_open_day", lambda: None)
    cg.check85()
    out = [f[4] for f in core.findings if f[0] == 85]
    assert len(out) == 1 and "8/" in out[0] and "quiet cycles" not in out[0]
    monkeypatch.setattr(gc, "GATE_CYCLES_FROM", "2099-01-01")             # same day, before the cutover
    monkeypatch.setattr(core, "findings", [])
    cg.check85()
    assert "quiet cycles undercounted" in next(f[4] for f in core.findings if f[0] == 85)


# ── the surfaces: manual endpoints record, ops/window reports reasons ──────────

def test_manual_demo_gate_run_is_recorded_as_manual(monkeypatch):
    import backend.routers.signals_router as sr
    from backend import db
    from backend.gate import gate_runner
    db.init_db()
    seen = []

    def fake_run(cycle):
        seen.append(cycle)
        cycle.reason = "no_candidates"
        return []
    monkeypatch.setattr(gate_runner, "run", fake_run)
    monkeypatch.setattr(sr, "_rate_check", lambda *a: None)
    assert sr.run_gate(None)["evaluated"] == 0
    assert seen[0].trigger == "manual" and _row("run_gate")[0] == "manual"


def test_manual_live_gate_run_is_recorded_as_manual(monkeypatch):
    import backend.routers.live_router as lr
    from backend import db
    from backend.gate import gate_runner_live
    db.init_db()
    monkeypatch.setattr(gate_runner_live, "run", lambda cycle: [])
    monkeypatch.setattr(lr, "_require_live", lambda: None)
    lr.run_live_gate()
    assert _row("run_live_gate")[0] == "manual"


def test_ops_window_reports_cycle_reasons(tmp_path, monkeypatch):
    import backend.routers.signals_router as sr
    from backend.db import get_db
    monkeypatch.setattr(sr, "_LOG_DIR", tmp_path)
    d = sr.get_market_window(days=10)["sessions"][-1]["date"]
    with get_db() as conn:
        conn.execute("DELETE FROM gate_cycles WHERE substr(started_at, 1, 10) = ?", (d,))
        conn.executemany("INSERT INTO gate_cycles (job, trigger, started_at, outcome, reason) VALUES (?, ?, ?, 'ok', ?)", [
            ("run_gate", "scheduler", f"{d}T13:41:00+00:00", "no_candidates"),
            ("run_gate", "scheduler", f"{d}T14:01:00+00:00", "no_candidates"),
            ("run_live_gate", "scheduler", f"{d}T13:53:00+00:00", "halt_data_quality"),
            ("run_gate", "manual", f"{d}T14:10:00+00:00", "evaluated"),
        ])
        conn.commit()
    s = next(x for x in sr.get_market_window(days=10)["sessions"] if x["date"] == d)
    assert s["cycle_reasons"] == {"demo": {"no_candidates": 2}, "live": {"halt_data_quality": 1},
                                  "manual": {"demo": 1, "live": 0}}
