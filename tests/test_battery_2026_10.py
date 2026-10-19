"""The rerun battery's own logic (2026-10-19): unit identity and sharing, start shifts on a
shared regime timeline, regime-constant patches, the H-V1 scaler, re-entry classing, the
readers' rules, and the run guard. Synthetic OHLCV from tests/test_engine_flag_off_parity.py;
the engine and metrics have their own tests. Nothing here runs the battery."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import backend.backtest.battery_2026_10 as bt
import backend.backtest.engine_fast as ef
import backend.regime.regime_bayes as rbm
from tests.test_engine_flag_off_parity import RUN_KW, synthetic_precomputed


# ── Units ─────────────────────────────────────────────────────────────────────

def test_a_unit_shared_by_cells_runs_once():
    cells = bt.build_cells()
    r2 = bt.unit("IS", bt.R2).key()
    holders = [c.name for c in cells.values() if any(u.key() == r2 for u in c.units)]
    assert {"profit_read", "noise_floor", "regime_recovery", "vix_scaler_hv1"} <= set(holders)
    allu = [u for c in cells.values() for u in c.units]
    assert len(bt.ordered(allu)) == len({u.key() for u in allu})
    # the 3B baseline and the cap 0.30 point are the same run as plain R2 only when the
    # override equals the incumbent; they are their own units otherwise, never merged by name
    assert bt.unit("IS", bt.R2, max_sector_exposure=0.30).key() != r2


def test_keys_separate_everything_that_changes_a_run():
    base = bt.unit("IS", bt.R2)
    variants = [bt.unit("OOS", bt.R2), bt.unit("IS", bt.R2, 1), bt.unit("IS", bt.R1),
                bt.unit("IS", bt.R2, regime={"POSTERIOR_CLAMP_FLOOR": 0.10}),
                bt.unit("IS", bt.R2, vix9d_kelly=True), bt.unit("IS", bt.R2, row2_neutral=True)]
    keys = {base.key()} | {v.key() for v in variants}
    assert len(keys) == 1 + len(variants)
    rnd = bt.rnd_units(base)
    assert len({u.key() for u in rnd}) == len(bt.RND_SEEDS)
    assert bt.rnd_units(bt.unit("IS", bt.R1))[0].key() != rnd[0].key()      # the reference is in the key


def test_rnd_units_come_after_their_reference():
    ref = bt.unit("IS", bt.R2)
    order = bt.ordered(bt.rnd_units(ref)[:3])                  # the reference is pulled in
    assert order[0].key() == ref.key() and all(u.rnd_ref is not None for u in order[1:])


def test_blocked_cells_say_why_and_have_no_units():
    cells = bt.build_cells()
    for name in ("check80_counterfactual", "position_size", "earnings_on_off"):
        assert cells[name].blocked and not cells[name].units


# ── Runner on synthetic data ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    pre = synthetic_precomputed()
    days = pre["trading_days"]
    spy = pre["raw_data"]["SPY"]["Close"].dropna()
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    r = bt.Runner(bt.Store(tmp_path_factory.mktemp("battery")), "deadbeef")
    r.pre["IS"] = pre
    r.market["IS"] = {"spy": spy, "rf": pd.Series(0.0, index=idx),
                      "vix9d": pd.Series(np.linspace(12, 30, len(idx)), index=idx)}
    r.inc = dict(RUN_KW)
    return r


def test_start_shift_slices_the_days_and_shares_one_timeline(runner):
    days = runner.pre["IS"]["trading_days"]
    a = runner.run(bt.unit("IS", bt.R2, 0))
    b = runner.run(bt.unit("IS", bt.R2, 5))
    assert a["first_day"] == days[0].isoformat() and b["first_day"] == days[5].isoformat()
    assert b["trading_days"] == [d.isoformat() for d in days[5:]]
    assert all(t["entry_date"] >= days[5].isoformat() for t in b["trade_log"])
    assert list(runner.timelines) == [("IS", ())]                # built once, on the full window
    assert sorted(b["regime_days"]) == [d.isoformat() for d in days]
    assert a["commit"] == "deadbeef" and isinstance(a["gaps"], list) and a["gaps"]
    assert set(a["spy_em"]) == {"cash0", "tbill"} and "spy_em" not in b


def test_regime_patch_builds_its_own_timeline_and_restores(runner, monkeypatch):
    seen = []
    real = ef.build_regime_timeline
    monkeypatch.setattr(ef, "build_regime_timeline", lambda p: (seen.append(rbm.POSTERIOR_CLAMP_FLOOR), real(p))[1])
    runner.run(bt.unit("IS", bt.R2, regime={"POSTERIOR_CLAMP_FLOOR": 0.10}))
    assert seen == [0.10]
    assert rbm.POSTERIOR_CLAMP_FLOOR == 0.05
    assert ("IS", (("POSTERIOR_CLAMP_FLOOR", 0.10),)) in runner.timelines


def test_rnd_unit_copies_its_reference_counts(runner):
    ref = bt.unit("IS", bt.R2)
    runner.store.put(ref, runner.run(ref))
    u = bt.rnd_units(ref)[0]
    rec = runner.run(u)
    assert rec["seed"] == 1 and {"alpha_tbill", "spy_em_calmar", "calmar", "expectancy"} <= set(rec)


def test_row2_neutral_scores_l2_with_neutral_weights(runner, monkeypatch):
    seen = set()
    real = ef._get_candidates_from_cache

    def spy(*a, **kw):
        seen.add(kw.get("regime_state"))
        return real(*a, **kw)
    monkeypatch.setattr(ef, "_get_candidates_from_cache", spy)
    pre = {**runner.pre["IS"], "regime_timeline": runner._timeline("IS", ())}
    ef.run("2025-03-03", "2025-06-27", precomputed=pre, **RUN_KW, regime_layer=True, row2_neutral=True)
    assert seen == {"neutral"}
    buckets = {v["bucket"] for v in pre["regime_timeline"]["days"].values()}
    seen.clear()
    ef.run("2025-03-03", "2025-06-27", precomputed=pre, **RUN_KW, regime_layer=True)
    assert seen == buckets


# ── H-V1 scaler ───────────────────────────────────────────────────────────────

def test_vix9d_factor_maps_the_63_session_rank_to_075_025():
    idx = pd.bdate_range("2025-01-01", periods=70)
    up = pd.Series(np.arange(70, dtype=float), index=idx)         # every day the window's highest
    days = [d.date().isoformat() for d in idx[62:]] + ["2099-01-01"]
    k, missing = bt.vix9d_kelly_factor(up, days)
    assert missing == 1 and all(v == pytest.approx(0.25) for v in k.values())
    k, _ = bt.vix9d_kelly_factor(-up, days)                         # every day the lowest
    assert all(v == pytest.approx(0.75) for v in k.values())
    flat = pd.Series(5.0, index=idx)
    k, _ = bt.vix9d_kelly_factor(flat, days)                        # ties sit at the mid-rank
    assert all(v == pytest.approx(0.5) for v in k.values())


# ── Re-entry classes (cooloff mechanism) ──────────────────────────────────────

def _t(ticker, entry, exit_, reason, pnl, pct=1.0):
    return {"ticker": ticker, "entry_date": entry, "exit_date": exit_, "exit_reason": reason,
            "pnl": pnl, "pnl_pct": pct}


def test_reentry_classes_by_last_exit_and_sessions_since():
    days = [f"2025-01-{d:02d}" for d in range(1, 32)] + [f"2025-02-{d:02d}" for d in range(1, 29)]
    trades = [
        _t("A", days[0], days[2], "TP", 10),       # win exit day 2
        _t("A", days[4], days[4], "SL", -5),       # re-entry 2 sessions later; own same-day exit not prior
        _t("A", days[4], days[9], "TIME", -1),     # same day as the SL exit: the 0 bin, after a loss
        _t("B", days[0], days[1], "TSL", -2),      # TSL with a loss is a loss
        _t("B", days[40], days[41], "TP", 3),      # 39 sessions after: fresh
        _t("C", days[10], days[12], "TIME", -1),
        _t("C", days[20], days[22], "TP", 1),      # 8 sessions after a time stop
    ]
    cls = bt._reentry_classes(trades, days)
    names = {k: [(t["ticker"], t["entry_date"]) for t in v] for k, v in cls.items()}
    assert names["win_1-3"] == [("A", days[4])]
    assert names["loss_0"] == [("A", days[4])]
    assert names["TIME_8-14"] == [("C", days[20])]
    assert ("B", days[40]) in names["fresh"] and ("A", days[0]) in names["fresh"]


def test_welch_and_bootstrap_zero_on_identical_inputs():
    w = bt.welch([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert w["diff"] == 0 and w["ci95"][0] < 0 < w["ci95"][1]
    r = pd.Series(np.random.default_rng(1).normal(0.0005, 0.01, 300), index=pd.bdate_range("2020-01-01", periods=300))
    bs = bt.block_bootstrap_calmar_diff(r, r, draws=50)
    assert bs["ci95"] == [0.0, 0.0]


# ── Readers ───────────────────────────────────────────────────────────────────

class FakeStore(bt.Store):
    def __init__(self):
        self.recs: dict[str, dict] = {}

    def has(self, u):
        return u.key() in self.recs

    def get(self, u):
        if u.key() not in self.recs:
            raise bt.MissingUnit(u)
        return self.recs[u.key()]

    def put(self, u, rec):
        self.recs[u.key()] = rec


def _rec(calmar, years=None, folds=None):
    return {"summary": {"calmar": calmar}, "years": {y: {"calmar": c} for y, c in (years or {}).items()},
            "folds": {f: {"calmar": c, "max_drawdown": 0.1} for f, c in (folds or {}).items()}}


def test_3b_reader_needs_six_of_eight_years_in_sample():
    s = FakeStore()
    ys = [str(y) for y in range(2019, 2027)]
    s.put(bt.unit("IS", bt.R2, row2_neutral=False), _rec(1.0, dict(zip(ys, [2, 2, 2, 2, 2, 0, 0, 0]))))
    s.put(bt.unit("IS", bt.R2, row2_neutral=True), _rec(0.9, dict.fromkeys(ys, 1)))
    oys = ["2015", "2016", "2017", "2018"]
    s.put(bt.unit("OOS", bt.R2, row2_neutral=False), _rec(0.5, dict(zip(oys, [2, 2, 0, 0]))))
    s.put(bt.unit("OOS", bt.R2, row2_neutral=True), _rec(0.4, dict.fromkeys(oys, 1)))
    v = bt.read_3b(s)
    assert v["IS"]["rules"][0]["passed"] is False and v["IS"]["rules"][0]["years"] == "5/8"
    assert v["OOS"]["rules"][0]["verdict"] == "switch-off stands, weak"


def test_cap_reader_neighbours_exclude_030_and_need_four_folds():
    s = FakeStore()
    win = dict.fromkeys(bt.CAP_FOLDS, 2.0)
    for b in (bt.R2, bt.R1):
        for c in bt.CAP_GRID:
            good = c in (0.25, 0.26, 0.29, 0.35)
            s.put(bt.unit("IS", b, max_sector_exposure=c),
                  _rec(1.2 if good else 0.8, folds=win if good else dict.fromkeys(bt.CAP_FOLDS, 0.0)))
        s.recs[bt.unit("IS", b, max_sector_exposure=0.30).key()] = _rec(1.0, folds=dict.fromkeys(bt.CAP_FOLDS, 1.0))
    for c in (0.25, 0.30):
        s.put(bt.unit("OOS", bt.R2, max_sector_exposure=c), _rec(0.1, {"2015": 1}))
        s.put(bt.unit("OOS", bt.R1, max_sector_exposure=c), _rec(0.1, {"2015": 1}))
    g = bt.read_sector_cap(s)["IS_R2"]["grid"]
    assert g["0.29"]["passes"] is False                   # neighbours 0.28 (bad) and 0.35
    assert g["0.25"]["passes"] is False                   # neighbour 0.20 is bad
    assert g["0.26"]["passes"] is False and g["0.26"]["neighbours_beat_030"] is False
    assert g["0.35"]["neighbours_beat_030"] is False      # 0.29 good, 0.40 bad


def test_a_reader_with_missing_units_reports_incomplete_never_a_pass(tmp_path):
    v = bt.read_all(bt.build_cells(), bt.Store(tmp_path), tmp_path)
    for name, verdict in v.items():
        assert ("blocked" in verdict) != ("incomplete" in verdict), name
    assert (tmp_path / "verdicts" / "profit_read.json").exists()


# ── Run guard ─────────────────────────────────────────────────────────────────

def test_run_is_refused_without_a_ruling_and_on_a_dirty_tree(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(bt.Runner, "run", lambda self, u: ran.append(u))
    assert bt.main([str(tmp_path), "--cells", "regime_weights_3b", "--run"]) == 2
    monkeypatch.setattr(bt, "git_state", lambda: ("abc", True))
    assert bt.main([str(tmp_path), "--cells", "regime_weights_3b", "--run", "--ruling", "ja"]) == 2
    assert ran == [] and not (tmp_path / "manifest.json").exists()


def test_plan_counts_without_running(tmp_path, capsys):
    assert bt.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert '"unique_units"' in out and not list(Path(tmp_path, "units").iterdir())
