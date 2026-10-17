"""
engine_fast regime layer, PRODUCTION_GAP rows 2, 5, 20, 21 under regime_layer=True
(2026-10-17). Synthetic OHLCV from tests/test_engine_flag_off_parity.py — these test
the wiring (each row reaches the decision it reaches live), not market behaviour.
"""
import ast
from datetime import timedelta
from pathlib import Path

import pytest

import backend.backtest.engine_fast as ef
import backend.regime.regime_bayes as rbm
from backend import db
from backend.regime.regime_bayes import _lr_ipo_cluster
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def pre():
    p = synthetic_precomputed()
    p["regime_timeline"] = ef.build_regime_timeline(p)
    return p


def _run(pre, **kw):
    return ef.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_neutral_reweighting_is_the_cached_score(pre):
    """Row 2's re-weighting reproduces signal_score exactly under the neutral bucket."""
    cache = pre["signal_cache"]
    assert len(cache) > 500
    for entry in cache.values():
        assert ef._regime_score(entry["components"], "neutral") == entry["signal_score"]
    some = next(iter(cache.values()))["components"]
    assert ef._regime_score(some, "bull") != ef._regime_score(some, "bear") or some[:4] == (some[0],) * 4


def test_timeline_reads_the_regime_from_the_previous_session(pre):
    tl = pre["regime_timeline"]
    days = sorted(tl["days"])
    assert days == [d.isoformat() for d in pre["trading_days"]]
    assert tl["warmup_sessions"] >= ef.REGIME_WARMUP_MIN_SESSIONS
    for ds in days:
        assert tl["days"][ds]["regime_from"] < ds
    for a, b in zip(days, days[1:]):
        assert tl["days"][b]["regime_from"] == a          # t's regime is t−1's step
    assert {v["bucket"] for v in tl["days"].values()} <= {"bull", "neutral", "bear"}


def test_timeline_build_writes_no_production_state():
    def rows():
        conn = db.get_db()
        n = conn.execute("SELECT COUNT(*) FROM sector_posterior_history").fetchone()[0]
        conn.close()
        return n
    before_rows = rows()
    files = {p: (p.stat().st_mtime_ns if p.exists() else None)
             for p in (rbm.RESULT_CACHE_PATH, rbm.SIGNAL_TRACE_PATH)}
    ef.build_regime_timeline(synthetic_precomputed())
    assert rows() == before_rows
    assert {p: (p.stat().st_mtime_ns if p.exists() else None) for p in files} == files


def test_short_lookback_refuses_instead_of_running_cold():
    p = synthetic_precomputed()
    first = p["trading_days"][0]
    p["raw_data"] = p["raw_data"][p["raw_data"].index.date >= first - timedelta(days=60)]
    with pytest.raises(ValueError, match="warm-up"):
        ef.build_regime_timeline(p)


def _forced(pre, **day):
    """A copy of pre whose timeline is the same fixed regime every day."""
    p = dict(pre)
    p["regime_timeline"] = {"warmup_sessions": 99, "days": {
        d.isoformat(): {"regime_from": None, "bucket": "neutral",
                        "allocation": {"Technology": 0.6, "Energy": 0.4, "Healthcare": 0.0},
                        "caps": {}, **day}
        for d in pre["trading_days"]}}
    return p


def test_row5_sectors_without_allocation_never_enter(pre):
    off = _run(pre)
    assert any(t["sector"] == "Healthcare" for t in off["trade_log"])         # control
    on  = _run(_forced(pre), regime_layer=True)
    assert on["total_trades"] > 0
    assert not any(t["sector"] == "Healthcare" for t in on["trade_log"])


def test_row21_caps_bind(pre):
    loose = _run(_forced(pre, caps={"Technology": 0.9, "Energy": 0.9}), regime_layer=True)
    tight = _run(_forced(pre, caps={"Technology": 0.001, "Energy": 0.9}), regime_layer=True)
    assert any(t["sector"] == "Technology" for t in loose["trade_log"])       # control
    assert not any(t["sector"] == "Technology" for t in tight["trade_log"])


ALL_IN = {"Technology": 0.5, "Energy": 0.3, "Healthcare": 0.2}


def test_row20_multiplier_is_the_only_difference_when_everything_else_is_neutral(pre, monkeypatch):
    """All sectors allocated, neutral bucket, no caps (→ the flat cap): the flag-on run
    differs from flag-off only through the size multiplier — and with the multiplier
    stubbed to {} it is the flag-off run exactly."""
    off  = _run(pre)
    mult = _run(_forced(pre, allocation=ALL_IN), regime_layer=True)
    assert mult["trade_log"] != off["trade_log"]
    monkeypatch.setattr(rbm.RegimeBayes, "size_multipliers", staticmethod(lambda a, q: {}))
    assert _run(_forced(pre, allocation=ALL_IN), regime_layer=True)["trade_log"] == off["trade_log"]


def test_row2_bucket_moves_scores(pre):
    bull = _run(_forced(pre, bucket="bull"), regime_layer=True)
    bear = _run(_forced(pre, bucket="bear"), regime_layer=True)
    assert bull["trade_log"] != bear["trade_log"]


def test_flag_on_full_run_differs_from_flag_off(pre):
    on, off = _run(pre, regime_layer=True), _run(pre)
    assert on["trade_log"] != off["trade_log"]


def test_ipo_neutral_share_is_lr_one_for_any_sector_count():
    assert _lr_ipo_cluster(ef.REGIME_IPO_NEUTRAL_SHARE) == 1.0
    assert _lr_ipo_cluster(1 / 10) != 1.0          # why 1/n is not used


def test_nothing_under_backtest_imports_ipo_sentiment():
    """Never call IpoSentiment from the engine (10-16 pre-work finding 6): it may write
    data/ipo_sentiment_cache.json, and the engine has no historical EDGAR feed."""
    offenders = []
    for path in (REPO / "backend" / "backtest").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Name):
                names = [node.id]
            if any("ipo_sentiment" in n or n == "IpoSentiment" for n in names):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, offenders


def test_ipo_guard_scanner_finds_a_known_import():
    """Positive control for the scan above: scheduler.py imports IpoSentiment."""
    tree = ast.parse((REPO / "backend" / "scheduler.py").read_text())
    assert any(isinstance(n, ast.ImportFrom) and any(a.name == "IpoSentiment" for a in n.names)
               for n in ast.walk(tree))


def test_rnd_draws_k_entries_per_day_from_the_unthresholded_pool(pre):
    ref = _run(pre, regime_layer=True)
    per_day: dict[str, int] = {}
    for t in ref["trade_log"]:
        per_day[t["entry_date"]] = per_day.get(t["entry_date"], 0) + 1
    a  = _run(pre, regime_layer=True, random_entries={"seed": 1, "per_day": per_day})
    a2 = _run(pre, regime_layer=True, random_entries={"seed": 1, "per_day": per_day})
    b  = _run(pre, regime_layer=True, random_entries={"seed": 2, "per_day": per_day})
    assert a["trade_log"] == a2["trade_log"]                      # seeded
    assert a["trade_log"] != b["trade_log"]                       # seeds differ
    got: dict[str, int] = {}
    for t in a["trade_log"]:
        got[t["entry_date"]] = got.get(t["entry_date"], 0) + 1
    assert all(got.get(d, 0) <= k for d, k in per_day.items())    # never more than the reference
    assert set(got) <= set(per_day)                               # only on reference entry days
    assert sum(got.values()) >= 0.8 * sum(per_day.values())       # capacity rarely binds
    # Below-threshold tickers enter: the pool is not the signal's candidate set.
    scores = [t["signal_score"] for t in a["trade_log"]]
    assert min(scores) < RUN_KW["lock1_threshold"]
    assert _run(pre, regime_layer=True, random_entries={"seed": 1, "per_day": {}})["total_trades"] == 0
