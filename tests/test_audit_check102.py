"""CHECK 102 — an entry past a failed rule (2026-11-28, with the pre-rotation fix scoped as phase 4a M5; learning step 2).

Founding case: live SPG 2026-11-17, a pre-rotation candidate in its exit_cooloff_win window reached L6 six times and was
approved; only the sector cap stopped the entry (the pre-rotation filter had not been ported when phase 3 split the
rule on 10-31). The positive control is that row as it would have been stored had the sector cap had room.
"""
import json
from datetime import date

FAILED_WIN = {"L1_ELIGIBILITY_REGIME": "pass", "L1_ELIGIBILITY_EXIT_COOLOFF_WIN": "fail", "L3_SCORE_THRESHOLD": "pass",
              "L6_REVIEW_CLAUDE": "pass", "L2_RISK_SECTOR_CAP": "pass"}
CLEAN = {k: "pass" for k in FAILED_WIN}


def _run102(monkeypatch, rows, today=date(2026, 11, 17)):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check102(rows=rows, today=today)
    return [(f[2], f[3], f[4]) for f in core.findings if f[0] == 102]


def test_an_entry_with_a_failed_rule_is_critical(monkeypatch):
    out = _run102(monkeypatch, [("live", "2026-11-17T17:08:00+00:00", "SPG", json.dumps(FAILED_WIN)),
                                ("live", "2026-11-17T17:28:00+00:00", "XOM", json.dumps(CLEAN))])
    crit = [o for o in out if o[0] == "CRITICAL"]
    assert len(crit) == 1 and crit[0][1] == "data/apex.db:live_gate_history"
    assert crit[0][2].startswith("live SPG 2026-11-17T17:08: executed with L1_ELIGIBILITY_EXIT_COOLOFF_WIN failed")
    assert any(o[0] == "INFO" and o[2].startswith("1 executed row(s) with a failed rule in 2") for o in out)


def test_clean_entries_and_old_hits_are_not_critical(monkeypatch):
    out = _run102(monkeypatch, [("demo", "2026-11-10T15:00:00+00:00", "SPG", json.dumps(FAILED_WIN)),
                                ("live", "2026-11-17T17:28:00+00:00", "XOM", json.dumps(CLEAN)),
                                ("live", "2026-11-17T17:30:00+00:00", "BAD", "not json")])
    assert not [o for o in out if o[0] == "CRITICAL"]                    # the demo hit is outside the 2-day window
    assert any(o[0] == "INFO" and o[2].startswith("1 executed row(s)") for o in out)


def test_check102_is_in_the_nightly_run():
    import inspect
    from audit import checks_gate as cg
    assert "check102()" in inspect.getsource(cg.run)
