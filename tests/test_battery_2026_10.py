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
    assert v.pop("_flips") == []                          # nothing read, nothing flipped
    for name, verdict in v.items():
        assert ("blocked" in verdict) != ("incomplete" in verdict), name
    assert (tmp_path / "verdicts" / "profit_read.json").exists()


def test_every_verdict_names_its_producer_for_the_vault_gate(tmp_path):
    import json
    from backend.backtest.battery_2026_10 import SCRIPT
    bt.read_all(bt.build_cells(), bt.Store(tmp_path), tmp_path, "abc123", ["out", "--read"])
    repo = Path(__file__).resolve().parent.parent
    assert (repo / SCRIPT).exists()
    for f in (tmp_path / "verdicts").iterdir():
        if f.name == "_flips.json":
            continue
        p = json.loads(f.read_text())["producer"]
        assert p["repo"] == "apex" and p["commit"] == "abc123" and p["script"] == SCRIPT
        assert p["run"].endswith("out --read") and p["why"].startswith("raw/notes/2026-10/") and " " not in p["why"]


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


# ── engine_fast.run honours its window on a precompute (2026-10-19) ───────────

def test_run_on_a_precompute_slices_to_its_window_and_refuses_outside(runner):
    pre = synthetic_precomputed()
    days = pre["trading_days"]
    a, b = days[10], days[40]
    res = ef.run(a.isoformat(), b.isoformat(), precomputed=pre, **RUN_KW)
    assert res["equity_curve"][0]["date"] == a.isoformat()
    assert all(a.isoformat() <= t["entry_date"] <= b.isoformat() for t in res["trade_log"])
    assert max(p["date"] for p in res["equity_curve"]) <= b.isoformat()
    sliced = ef.run(a.isoformat(), b.isoformat(),
                    precomputed={**pre, "trading_days": [d for d in days if a <= d <= b]}, **RUN_KW)
    assert res["trade_log"] == sliced["trade_log"]               # the same as slicing by hand
    with pytest.raises(ValueError, match="outside the precompute"):
        ef.run(a.isoformat(), "2099-01-01", precomputed=pre, **RUN_KW)
    with pytest.raises(ValueError, match="outside the precompute"):
        ef.run("2025-03-03", b.isoformat(), precomputed={**pre, "window": (a.isoformat(), b.isoformat())}, **RUN_KW)


def test_engine_sharpe_slices_the_same_way():
    from backend.backtest import engine_sharpe as es
    pre = synthetic_precomputed()
    days = pre["trading_days"]
    a, b = days[10], days[40]
    res = es.run(a.isoformat(), b.isoformat(), precomputed=pre)
    assert all(a.isoformat() <= t["entry_date"] <= b.isoformat() for t in res["trade_log"])
    with pytest.raises(ValueError, match="outside the precompute"):
        es.run(a.isoformat(), "2099-01-01", precomputed=pre)


# ── Fix reverts (7C attribution; 2026-10-19) ──────────────────────────────────

from tests.test_engine_flag_off_parity import GOLDEN, END, START, _summary   # noqa: E402


def _ef(pre, **kw):
    return ef.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_every_fix_reverted_is_the_parent_engine():
    # Positive control for the attribution set: with all seven reverts the engine reproduces
    # the cf3db17 golden, without the macro monkeypatch the golden test needs.
    import json
    revert = {k: v for _, kw in bt.FIXES.values() for k, v in kw.items()}
    assert _summary(_ef(synthetic_precomputed(), **revert)) == json.loads(GOLDEN.read_text())


def test_macro_revert_enters_on_hard_block_days():
    from backend.macro_calendar import macro_status
    pre = synthetic_precomputed()
    hard = {d.isoformat() for d in pre["trading_days"] if macro_status(d)[0] == "hard_block"}
    assert not {t["entry_date"] for t in _ef(pre)["trade_log"]} & hard
    assert {t["entry_date"] for t in _ef(pre, macro_blackout=False)["trade_log"]} & hard


def test_vix_revert_lets_the_threshold_day_through():
    pre = synthetic_precomputed()
    pre = {**pre, "vix_cache": {d.isoformat(): 30.0 for d in pre["trading_days"]}}
    assert _ef(pre, vix_threshold=30.0)["total_trades"] == 0
    assert _ef(pre, vix_threshold=30.0, vix_block_inclusive=False)["total_trades"] > 0


def _reentries_after(trades, reason, days):
    out = 0
    for t in trades:
        if t.get("exit_reason") != reason or not t.get("exit_date"):
            continue
        x = pd.Timestamp(t["exit_date"])
        out += sum(1 for u in trades if u["ticker"] == t["ticker"]
                   and x < pd.Timestamp(u["entry_date"]) < x + pd.Timedelta(days=days))   # day x+N is open
    return out


def test_cooloff_revert_sets_no_window_after_a_time_exit():
    pre = synthetic_precomputed()
    kw = dict(time_stop_days=3, sl_cooldown_days=20, tp_cooldown_days=0)
    fixed, old = _ef(pre, **kw)["trade_log"], _ef(pre, cooloff_per_exit=False, **kw)["trade_log"]
    assert any(t["exit_reason"] == "TIME" for t in fixed)
    assert _reentries_after(fixed, "TIME", 20) == 0
    assert _reentries_after(old, "TIME", 20) > 0


# ── RND keeps the reference's slots, not row 14's score bar (1C) ──────────────

def test_rnd_fills_the_reference_slots_above_the_roof_without_the_score_bar():
    pre = synthetic_precomputed()
    days = [d.isoformat() for d in pre["trading_days"]]
    rnd = {"seed": 3, "per_day": dict.fromkeys(days, 5), "open_cap": dict.fromkeys(days, 3)}
    # lock1 0.99: row 14's bar above the roof cannot be met by any score, so a score bar
    # would hold RND at one position; the reference's 3 slots are what it gets instead.
    res = _ef(pre, max_positions=1, lock1_threshold=0.99, overflow_quant_increment=None, random_entries=rnd)
    opened = bt.open_after_entries(res["trade_log"], days)
    assert max(opened.values()) == 3


def test_open_after_entries_counts_exits_before_entries():
    days = ["2025-01-01", "2025-01-02", "2025-01-03"]
    log = [{"entry_date": days[0], "exit_date": days[1]}, {"entry_date": days[1], "exit_date": None},
           {"entry_date": days[1], "exit_date": days[2]}]
    assert bt.open_after_entries(log, days) == {days[0]: 1, days[1]: 2, days[2]: 1}


# ── 1C and 6C readers ─────────────────────────────────────────────────────────

def _erec(e):
    return {"summary": {"expectancy": {"usd": {"mean": e}}}}


def test_signal_edge_needs_four_of_five_starts():
    s = FakeStore()
    for st in bt.RND_STARTS:
        ref = bt.unit("IS", bt.R2, st)
        s.put(ref, _erec(10.0 if st != 16 else 0.0))              # above at 4 of 5
        for u in bt.rnd_units(ref):
            s.put(u, {"seed": u.seed, "expectancy": {"usd": {"mean": u.seed / 200 * 5}}})
    r = bt._signal_edge(s, "IS", "R2", bt.R2)
    assert r["passed"] is True and r["starts_above"] == "4/5" and r["start0_as_preregistered"] is True
    s.put(bt.unit("IS", bt.R2, 12), _erec(0.0))
    assert bt._signal_edge(s, "IS", "R2", bt.R2)["passed"] is False


def test_etf_reader_needs_start0_and_eleven_of_twenty():
    s = FakeStore()
    e = lambda pf: {"engine": {"profit_factor": pf, "cagr": 0.05, "max_drawdown": 0.1}}
    for st in bt.starts():
        s.put(bt.unit("IS", bt.R2, st, etf_negative_penalty=None), e(1.0))
        for r in bt.ETF_RATES:
            for f in bt.ETF_FLOORS:
                good = (r == 0.15 and st < 11) or (r == 0.20 and 0 < st <= 15)
                s.put(bt.unit("IS", bt.R2, st, etf_negative_penalty=r, etf_negative_floor=f), e(1.05 if good else 1.0))
    v = bt.read_etf(s)
    assert v["grid"]["0.15"]["floors"]["-1.0"]["meets_starts"] == "11/20" and v["rules"][0]["passed"] is True
    assert v["grid"]["0.2"]["floors"]["-1.0"]["passes"] is False            # 15/20 but not at start 0
    assert "engine_fast" in v["read"] and "engine.py" in v["read"]


# ── Flips and their attribution (7C) ──────────────────────────────────────────

class AnyStore(FakeStore):
    """A plausible record for every unit, so every reader runs end to end."""
    def get(self, u):
        if u.key() in self.recs:
            return self.recs[u.key()]
        if u.seed is not None:
            return {"seed": u.seed, "expectancy": {"usd": {"mean": 0.0}}, "alpha_tbill": 0.0,
                    "calmar": 0.5, "spy_em_calmar": 1.0}
        idx = pd.bdate_range("2019-01-02", periods=300)
        walk = np.cumprod(1 + np.random.default_rng(0).normal(0.0004, 0.01, len(idx)))
        eq = [{"date": d.date().isoformat(), "balance": 10_000 * w} for d, w in zip(idx, walk)]
        yrs = {str(y): {"calmar": 1.0, "max_drawdown": 0.1, "total_return": 0.1} for y in range(2015, 2027)}
        folds = {f: {"calmar": 1.0, "max_drawdown": 0.1, "total_return": 0.1}
                 for w in bt.EVENT_FOLDS.values() for f in w}
        return {"summary": {"calmar": 1.0, "expectancy": {"usd": {"mean": 1.0}}, "total_return": 0.1,
                            "vs_spy_tbill": {"alpha_annual": 0.0, "t_alpha": 0.0}},
                "spy_em": {"tbill": {"calmar": 2.0}}, "years": yrs, "folds": folds, "equity_curve": eq,
                "engine": {"profit_factor": 1.0, "cagr": 0.05, "max_drawdown": 0.1, "total_trades": 1}, "trade_log": [],
                "trading_days": [], "regime_days": {}}


def test_every_archived_rule_resolves_on_its_reader(tmp_path):
    v = bt.read_all(bt.build_cells(), AnyStore(), tmp_path)
    for cell, what, get, then, commit, src in bt.ARCHIVED:
        assert "incomplete" not in v[cell], cell
        now = bt._archived_value(get, v[cell])                    # KeyError = a renamed rule id
        assert isinstance(now, (bool, str)), (cell, what)
        assert src.startswith(bt.VAULT_NOTES)
    flipped = {(f["cell"], f["rule"]) for f in v["_flips"]}
    assert ("profit_read", "IS signal edge R2") in flipped        # R 1.0 above RND 0.0: archived fail flips
    assert all(f["use"] is False and f["status"].startswith("unexplained") for f in v["_flips"])


def _toy_cell(rule_value):
    """A cell whose one rule is `rule_value(record)` on its start-0 unit."""
    u = bt.unit("IS", bt.R2)
    return bt.Cell("toy", bt.VAULT_NOTES + "toy.md", [u],
                   lambda st: {"rules": [bt._rule("toy", rule_value(st.get(u)), rid="t")]})


def test_explain_names_the_one_fix_whose_revert_restores_the_archive(monkeypatch):
    monkeypatch.setattr(bt, "ARCHIVED", [("toy", "toy rule", "t", False, None, bt.VAULT_NOTES + "toy.md")])
    monkeypatch.setattr(bt, "FIXES", {"A": ("a", {"fa": False}), "B": ("b", {"fb": False})})
    s = FakeStore()
    u = bt.unit("IS", bt.R2)
    t = lambda n: [{"ticker": f"T{i}", "entry_date": "2025-01-01", "exit_date": "2025-01-05", "exit_reason": "TP"}
                   for i in range(n)]
    s.put(u, {"v": True, "trade_log": t(3)})
    s.put(bt._ablate(u, {"fa": False}), {"v": False, "trade_log": t(2)})     # A restores, logs differ
    s.put(bt._ablate(u, {"fb": False}), {"v": True, "trade_log": t(3)})
    cell = _toy_cell(lambda r: r["v"])
    verdict = cell.read(s)
    assert bt.flips({"toy": verdict})[0]["use"] is False
    res = bt.explain(cell, s, verdict, lambda a, b: False)
    e = res["toy:toy rule"]
    assert e["status"] == "explained" and e["fix"] == "A"
    assert e["tried"]["A"]["diff"][u.key()]["only_fixed"] == 1
    assert bt.flips({"toy": verdict}, res)[0]["use"] is True
    # B also restoring makes it ambiguous; a fix already in the archive commit is not tried
    s.put(bt._ablate(u, {"fb": False}), {"v": False, "trade_log": t(1)})
    assert bt.explain(cell, s, verdict, lambda a, b: False)["toy:toy rule"]["status"].startswith("unexplained: several")
    monkeypatch.setattr(bt, "ARCHIVED", [("toy", "toy rule", "t", False, "arch", bt.VAULT_NOTES + "toy.md")])
    only_a = bt.explain(cell, s, verdict, lambda fix, arch: fix == "b")["toy:toy rule"]
    assert only_a["status"] == "explained" and set(only_a["tried"]) == {"A"}


def test_explain_needs_the_trade_logs_to_move(monkeypatch):
    monkeypatch.setattr(bt, "ARCHIVED", [("toy", "toy rule", "t", False, None, bt.VAULT_NOTES + "toy.md")])
    monkeypatch.setattr(bt, "FIXES", {"A": ("a", {"fa": False})})
    s = FakeStore()
    u = bt.unit("IS", bt.R2)
    log = [{"ticker": "T", "entry_date": "2025-01-01", "exit_date": "2025-01-05", "exit_reason": "TP"}]
    s.put(u, {"v": True, "trade_log": log})
    s.put(bt._ablate(u, {"fa": False}), {"v": False, "trade_log": log})       # outcome back, same trades
    cell = _toy_cell(lambda r: r["v"])
    e = bt.explain(cell, s, cell.read(s), lambda a, b: False)["toy:toy rule"]
    assert e["status"].startswith("unexplained: no single fix")


def test_the_etf_cell_does_not_try_its_own_penalty_as_an_attribution():
    cell = bt.build_cells()["etf_penalty"]
    fx = bt.applicable_fixes(cell, None, lambda a, b: False)
    assert "etf_penalty_row11 (6e22f5a)" not in fx and len(fx) == len(bt.FIXES) - 1


def test_fix_commits_are_in_this_branch_and_after_the_archive():
    for name, (c, _) in bt.FIXES.items():
        assert bt.git_is_ancestor(c, "HEAD"), name
        assert not bt.git_is_ancestor(c, "824150a"), name


# ── Block length (Politis & White 2004, corrected 2009) ───────────────────────

def test_pw_block_length_recovers_the_papers_ar1_optimum():
    # Patton, Politis & White (2009) Table 1: AR(1) rho 0.7, N 800 -> b_opt,CB 20.83; Table 3:
    # the estimate's mean ratio to it is 0.951. 200 draws here; the mean ratio sits in 0.8..1.1.
    rng = np.random.default_rng(7)
    ratios = []
    for _ in range(200):
        z = rng.normal(size=1000)
        x = np.zeros(1000)
        for t in range(1, 1000):
            x[t] = 0.7 * x[t - 1] + z[t]
        ratios.append(bt.pw_block_length(x[200:])["circular"] / 20.83)
    assert 0.8 < float(np.mean(ratios)) < 1.1
    white = bt.pw_block_length(rng.normal(size=1950))      # no dependence: a short block
    assert white["circular"] < 5 and white["b_max"] == 133


def test_portfolio_kelly_is_excess_mean_over_variance():
    idx = pd.bdate_range("2020-01-01", periods=2520)
    r = pd.Series(np.random.default_rng(3).normal(0.10 / 252, 0.20 / np.sqrt(252), len(idx)), index=idx)
    k = bt.portfolio_kelly(r, pd.Series(0.02 / 252, index=idx))
    assert k["kelly_rf"] == pytest.approx((k["m"] - 0.02) / k["s"] ** 2)
    assert k["r"] == pytest.approx(0.02) and abs(k["s"] - 0.20) < 0.01


def test_macro_gate_needs_sixteen_paired_starts_and_the_oos_sign():
    s = FakeStore()
    rec = lambda c: {"summary": {"calmar": c}, "engine": {"total_trades": 1}, "years": {"2020": {"calmar": c}}}
    for w in bt.WINDOWS:
        for b in (bt.R2, bt.R1):
            for st in bt.starts():
                s.put(bt.unit(w, b, st), rec(1.0))
                s.put(bt.unit(w, b, st, **bt.MACRO_OFF), rec(1.1 if (w == "OOS" or st < 16) else 0.9))
    v = bt.read_macro_gate(s)
    assert v["R2"]["IS"]["off_over_on_starts"] == 16 and v["rules"][0]["passed"] is True
    assert v["rules"][1]["passed"] is False
    s.put(bt.unit("IS", bt.R2, 15, **bt.MACRO_OFF), rec(0.9))           # 15/20: no longer robust
    assert bt.read_macro_gate(s)["rules"][0]["passed"] is False
