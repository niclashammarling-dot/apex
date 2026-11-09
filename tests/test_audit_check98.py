"""CHECK 98 — NOISE_FLOOR valid (2026-11-09): stale floor warns, roster drift is INFO only (R016)."""
FLOOR = {"measured": "2026-11-08", "objective_hash": "9961f0f0", "threshold": 0.19,
         "roster": {"n_tickers": 103, "hash": "6fa09571"}}


def _run98(monkeypatch, **kw):
    from audit import _audit_core as core
    from audit import checks_code as cc
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cc, "flag", core.flag)
    cc.check98(**kw)
    return [(f[2], f[4]) for f in core.findings if f[0] == 98]


def test_stale_floor_warns(monkeypatch):
    out = _run98(monkeypatch, valid=False, floor=FLOOR, current_roster=FLOOR["roster"])
    assert len(out) == 1 and out[0][0] == "WARNING" and "stale" in out[0][1]


def test_valid_floor_on_its_roster_is_clean(monkeypatch):
    assert _run98(monkeypatch, valid=True, floor=FLOOR, current_roster=dict(FLOOR["roster"])) == []


def test_roster_change_is_info_not_invalid(monkeypatch):
    out = _run98(monkeypatch, valid=True, floor=FLOOR, current_roster={"n_tickers": 104, "hash": "00000000"})
    assert out == [("INFO", out[0][1])] and "R016" in out[0][1]


def test_the_committed_floor_is_valid_now():
    from backend.backtest.optimizer import noise_floor_valid
    assert noise_floor_valid(), "NOISE_FLOOR no longer matches the optimizer: re-measure, then update the block"
