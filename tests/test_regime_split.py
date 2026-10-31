"""
RegimeBayes split into a pure step() plus persist (2026-10-17, engine port step 1).

Before/after (ruled 2026-10-16): with frozen inputs, the parent update() and the
split update() produce identical output, compared exactly. The golden file was
written by this harness on the parent (cf3db17) before the split; the split must
reproduce it bit for bit. Window 09-16..10-15, inputs from the signal trace
(trace = history exactly there), OHLCV frozen once. See
tests/fixtures/regime_split/inputs.json "_doc" for provenance.

Regenerate the golden only on a deliberate behaviour change, never to make this
pass: APEX_WRITE_REGIME_GOLDEN=1 venv/bin/python -m pytest tests/test_regime_split.py
"""
import json
import os
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import backend.regime.regime_bayes as rbm
from backend import db
from backend.regime.regime_bayes import RegimeBayes, RegimeResult, SectorEntry

FIX    = Path(__file__).parent / "fixtures" / "regime_split"
GOLDEN = FIX / "golden_parent_cf3db17.json"


def _inputs():
    inp   = json.loads((FIX / "inputs.json").read_text())
    close = pd.read_parquet(FIX / "close_2026-06-10_10-15.parquet")
    return inp, close


def _clear_db():
    conn = db.get_db()
    conn.execute("DELETE FROM sector_posterior_history")
    conn.execute("DELETE FROM sector_posteriors")
    conn.commit()
    conn.close()


def _seeded_rb(inp):
    cfg = inp["sectors_cfg"]
    rb  = RegimeBayes(cfg, {s: c["etf"] for s, c in cfg.items()})
    uniform = 1.0 / len(cfg)
    rb._posteriors = {s: inp["seed_posteriors"].get(s, uniform) for s in cfg}
    lb = [SectorEntry(**e) for e in inp["seed_leaderboard"]]
    rb._last_result = RegimeResult(
        date="2026-09-15",
        leaderboard=lb,
        allocation={e.sector: e.allocation for e in lb},
        leader=min(lb, key=lambda e: e.rank).sector,
        qualifiers=[e.sector for e in lb if e.allocation > 0],
    )
    return rb


def _day_inputs(inp, close, d):
    target = date.fromisoformat(d)
    idx    = close.index.date
    raw    = close[(idx > target - timedelta(days=90)) & (idx <= target)]
    snaps  = {s: v["aggregate_score"] for s, v in inp["inputs"][d].items()}
    ipo    = {s: v["ipo_share"] for s, v in inp["inputs"][d].items()}
    return target, raw, snaps, ipo


def _history_rows(d):
    conn = db.get_db()
    rows = conn.execute("SELECT sector, posterior FROM sector_posterior_history WHERE date = ?",
                        (d,)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _run_window():
    """update() over the window through the public API; the same code path on parent and split."""
    _clear_db()
    inp, close = _inputs()
    rb  = _seeded_rb(inp)
    out = []
    for d in inp["days"]:
        res = rb.update(*_day_inputs(inp, close, d))
        out.append({"result": asdict(res), "posteriors": dict(rb._posteriors),
                    "history": _history_rows(d)})
    return json.loads(json.dumps(out))


def test_split_reproduces_the_parent_exactly():
    out = _run_window()
    if os.environ.get("APEX_WRITE_REGIME_GOLDEN"):
        GOLDEN.write_text(json.dumps(out, indent=1, sort_keys=True))
        pytest.skip("golden written")
    golden = json.loads(GOLDEN.read_text())
    assert len(out) == len(golden) == 22
    for got, want in zip(out, golden):
        assert got == want, f"diverged on {want['result']['date']}"


def test_positive_control_a_decay_change_breaks_the_comparison(monkeypatch):
    """The comparison can fail. 1e-6 did not: posteriors are rounded to 4 decimals (_apply_signals), which absorbs it (first run, 2026-10-17)."""
    monkeypatch.setattr(rbm, "POSTERIOR_DECAY", rbm.POSTERIOR_DECAY + 1e-3)
    golden = json.loads(GOLDEN.read_text())
    assert _run_window() != golden


def _db_rows():
    conn = db.get_db()
    n = (conn.execute("SELECT COUNT(*) FROM sector_posterior_history").fetchone()[0],
         conn.execute("SELECT COUNT(*) FROM sector_posteriors").fetchone()[0])
    conn.close()
    return n


def test_step_chained_over_the_window_equals_update_and_writes_nothing():
    """step() is the engine's entry point: chained by hand it must give update()'s
    results (golden) while the DB, the result cache, the trace and the instance stay
    untouched."""
    _clear_db()
    inp, close = _inputs()
    rb    = _seeded_rb(inp)
    state = rbm.RegimeState(posteriors=dict(rb._posteriors), last_result=rb._last_result)
    own   = (dict(rb._posteriors), rb._last_result)
    files = {p: (p.stat().st_mtime_ns if p.exists() else None)
             for p in (rbm.RESULT_CACHE_PATH, rbm.SIGNAL_TRACE_PATH)}
    golden = json.loads(GOLDEN.read_text())
    for d, want in zip(inp["days"], golden):
        res, state, entries = rb.step(state, *_day_inputs(inp, close, d))
        assert json.loads(json.dumps(asdict(res))) == want["result"], d
        assert json.loads(json.dumps(state.posteriors)) == want["posteriors"], d
        assert len(entries) == 11
    assert _db_rows() == (0, 0)
    assert {p: (p.stat().st_mtime_ns if p.exists() else None) for p in files} == files
    assert (rb._posteriors, rb._last_result) == own


def test_unpersisted_instance_reads_nothing_and_refuses_update(monkeypatch):
    _clear_db()
    def boom(*a, **k):
        raise AssertionError("persisted=False must not read production state")
    monkeypatch.setattr(RegimeBayes, "_load_posteriors", boom)
    monkeypatch.setattr(RegimeBayes, "_load_result", boom)
    inp, close = _inputs()
    cfg = inp["sectors_cfg"]
    rb  = RegimeBayes(cfg, {s: c["etf"] for s, c in cfg.items()}, persisted=False)
    assert rb._posteriors == {s: 1.0 / len(cfg) for s in cfg}      # live cold start, 1/15
    assert rb.last_result() is None
    with pytest.raises(RuntimeError, match="step\\(\\)-only"):
        rb.update(*_day_inputs(inp, close, inp["days"][0]))
    assert _db_rows() == (0, 0)
    res, state, _ = rb.step(rbm.RegimeState.cold(cfg), *_day_inputs(inp, close, inp["days"][0]))
    assert state.last_result is res and len(res.leaderboard) == 11


@pytest.mark.parametrize("n", [10, 11, 12, 15])
def test_decay_is_a_two_state_markov_step(n):
    """decayed = d·p + (1 − d)/n is π' = a·π + (1 − b)(1 − π) with b = 1 − (1 − d)/n,
    a = d + 1 − b: stationary point 1/n, the asymmetry the module docstring states."""
    d = rbm.POSTERIOR_DECAY
    b = 1 - (1 - d) / n
    a = d + 1 - b
    for p in (0.05, 0.0909, 0.26, 0.5, 0.95):
        assert abs((d * p + (1 - d) / n) - (a * p + (1 - b) * (1 - p))) < 1e-12
    assert abs((1 - b) / (2 - a - b) - 1 / n) < 1e-12
    if n == 11:
        assert (round(a, 3), round(b, 3)) == (0.909, 0.991)
        assert (round(1 / (1 - a)), round(1 / (1 - b))) == (11, 110)


def test_transition_priors_are_gone_and_persisted_is_keyword_only():
    # Removed 2026-10-31 (prework finding 1): the fallback they fed was never reached. A caller still passing
    # the old third positional argument must fail rather than set persisted={} (falsy: step-only, silently).
    import inspect
    sig = inspect.signature(RegimeBayes.__init__)
    assert "transition_priors" not in sig.parameters
    assert sig.parameters["persisted"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        RegimeBayes({}, {}, {})
    assert not hasattr(rbm, "build_transition_priors")
