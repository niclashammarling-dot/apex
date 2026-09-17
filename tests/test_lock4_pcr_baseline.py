"""
Lock 4 per-ticker PCR baseline (pcr_baseline.py), its wiring into
_check_put_call_ratio / evaluate_chain, and CHECK 80's branches.
"""
import json
import sqlite3
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.gate import lock4_leading as l4
from backend.gate import pcr_baseline as pb


def _rows(spec: dict[str, list[float]]) -> list[dict]:
    return [{"ticker": t, "date": f"2026-06-{i+1:02d}", "pcr": v}
            for t, xs in spec.items() for i, v in enumerate(xs)]


@pytest.fixture(autouse=True)
def _fresh_cache():
    pb._cache = {"date": None, "pooled": None, "pooled_n": 0, "by_ticker": {}}
    yield
    pb._cache = {"date": None, "pooled": None, "pooled_n": 0, "by_ticker": {}}


# ── estimator ─────────────────────────────────────────────────────────────────

def test_partial_pooling_shrinks_by_n_toward_pooled_p25():
    spec = {"HI": [2.0] * 30, "LO": [0.2] * 30, "NEW": [0.9, 0.9, 0.9]}
    with patch("backend.db.get_pcr_history", return_value=_rows(spec)):
        pooled = np.percentile([v for xs in spec.values() for v in xs], pb.PCR_DESIGN_PCT)
        hi, new = pb.pcr_threshold_for("HI"), pb.pcr_threshold_for("NEW")
    k = pb.PCR_SHRINK_K
    assert hi["n_obs"] == 30 and hi["shrink_w"] == round(30 / (30 + k), 3)
    assert hi["threshold"] == pytest.approx(30 / (30 + k) * 2.0 + k / (30 + k) * pooled, abs=1e-3)
    assert new["threshold"] == pytest.approx(3 / (3 + k) * 0.9 + k / (3 + k) * pooled, abs=1e-3)
    # no cliff: n=3 is neither trusted fully nor discarded
    assert 0 < new["shrink_w"] < hi["shrink_w"] < 1


def test_unknown_ticker_gets_pooled_p25_with_zero_weight():
    with patch("backend.db.get_pcr_history", return_value=_rows({"A": [0.5] * 10, "B": [1.5] * 10})):
        r = pb.pcr_threshold_for("ZZZ")
    assert r["n_obs"] == 0 and r["shrink_w"] == 0.0
    assert r["threshold"] == r["pooled_p25"]


def test_empty_table_yields_no_baseline():
    with patch("backend.db.get_pcr_history", return_value=[]):
        assert pb.pcr_threshold_for("A") is None


def test_baseline_is_built_once_per_day():
    with patch("backend.db.get_pcr_history", return_value=_rows({"A": [0.5] * 5})) as m:
        pb.pcr_threshold_for("A")
        pb.pcr_threshold_for("A")
        assert m.call_count == 1
        pb.get_baseline(refresh=True)
        assert m.call_count == 2


# ── gate check ────────────────────────────────────────────────────────────────

def _chains(call_oi: int, put_oi: int):
    return [(pd.DataFrame({"openInterest": [call_oi]}), pd.DataFrame({"openInterest": [put_oi]}))]


def test_pooled_scalar_mode_is_unchanged():
    r = l4._check_put_call_ratio(_chains(100, 80), ticker="A", mode="pooled_scalar")
    assert r["pass"] is True and r["threshold"] == l4.PCR_THRESHOLD
    assert r["threshold_mode"] == "pooled_scalar" and "own_p25" not in r


def test_per_ticker_mode_uses_the_ticker_threshold():
    with patch("backend.db.get_pcr_history", return_value=_rows({"A": [1.5] * 30, "B": [0.3] * 30})):
        # pc = 1.2: fails the pooled scalar, passes A's own P25 (≈1.5 shrunk toward pooled)
        r = l4._check_put_call_ratio(_chains(100, 120), ticker="A", mode="per_ticker_p25")
    assert r["threshold_mode"] == "per_ticker_p25"
    assert r["pass"] is True and r["threshold"] > l4.PCR_THRESHOLD
    assert r["n_obs"] == 30 and r["own_p25"] == 1.5
    assert "[per_ticker_p25]" in r["reason"]


def test_per_ticker_mode_falls_back_when_no_baseline_and_says_so():
    with patch("backend.db.get_pcr_history", return_value=[]):
        r = l4._check_put_call_ratio(_chains(100, 80), ticker="A", mode="per_ticker_p25")
    assert r["threshold_mode"] == "pooled_scalar_fallback"
    assert r["threshold"] == l4.PCR_THRESHOLD and r["pass"] is True


def test_chain_passes_cfg_mode_to_lock4():
    from backend.gate.types import LockResult
    with patch("backend.gate.chain.lock1_evaluate", return_value=LockResult.pass_(lock_id=1, score=0.9, reason="", data={})), \
         patch("backend.gate.chain.lock2_evaluate", return_value=LockResult.pass_(lock_id=2, score=0.9, reason="", data={})), \
         patch("backend.gate.chain.lock3_evaluate", return_value=LockResult.pass_(lock_id=3, score=0.9, reason="", data={})), \
         patch("backend.gate.chain.lock4_evaluate", return_value=LockResult.fail(lock_id=4, reason="x", data={})) as m4:
        from backend.gate.chain import evaluate_chain
        evaluate_chain("A", "Energy", 0.9, {}, {"lock4_pcr_mode": "per_ticker_p25"})
    assert m4.call_args.kwargs["pcr_mode"] == "per_ticker_p25"


def test_demo_and_live_defaults_are_demo_first():
    from backend.demo_config import _defaults as demo
    from backend.live_config import _defaults as live
    assert demo()["lock4_pcr_mode"] == "per_ticker_p25"
    assert live()["lock4_pcr_mode"] == "pooled_scalar"
    from backend.live_config import _PROMOTE_EXCLUDE
    assert "lock4_pcr_mode" not in _PROMOTE_EXCLUDE   # promote carries it, deliberately


# ── CHECK 80 ──────────────────────────────────────────────────────────────────

def _c80_db(path, base, post, hist):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE demo_gate_history (id INTEGER PRIMARY KEY, timestamp TEXT, ticker TEXT, lock_leading_checks TEXT)")
    conn.execute("CREATE TABLE lock4_pcr_history (ticker TEXT, pcr REAL, is_dislocation INTEGER DEFAULT 0)")
    for t, passed in base:
        conn.execute("INSERT INTO demo_gate_history (timestamp, ticker, lock_leading_checks) VALUES (?, ?, ?)",
                     ("2026-08-01T14:00:00", t, json.dumps({"put_call_ratio": {"pass": passed, "pc_ratio": 0.7}})))
    for t, passed, mode in post:
        conn.execute("INSERT INTO demo_gate_history (timestamp, ticker, lock_leading_checks) VALUES (?, ?, ?)",
                     ("2026-09-18T14:00:00", t, json.dumps({"put_call_ratio": {"pass": passed, "pc_ratio": 0.7, "threshold_mode": mode}})))
    conn.executemany("INSERT INTO lock4_pcr_history (ticker, pcr) VALUES (?, ?)", hist)
    conn.commit()
    conn.close()


def _run80(tmp_path, monkeypatch, base, post, hist):
    from audit import checks_gate as cg
    from audit import _audit_core as core
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _c80_db(repo / "data/apex.db", base, post, hist)
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check80()
    return [(f[2], f[4]) for f in core.findings if f[0] == 80]


HIST = [("BLK", 1.5)] * 20 + [("OPN", 0.3)] * 20   # BLK: own P25 > 0.85; OPN: median < 0.85


def test_check80_reports_accumulating_when_thin(tmp_path, monkeypatch):
    out = _run80(tmp_path, monkeypatch, [("OPN", True), ("BLK", False)], [("OPN", True, "per_ticker_p25")], HIST)
    assert len(out) == 1 and out[0][0] == "INFO" and "accumulating" in out[0][1]
    assert "1/100" in out[0][1]


def test_check80_counts_fallback_rows_as_warning(tmp_path, monkeypatch):
    out = _run80(tmp_path, monkeypatch, [], [("OPN", True, "pooled_scalar_fallback")] * 3, HIST)
    assert any(s == "WARNING" and "3 demo Lock 4 row(s)" in f for s, f in out)


def test_check80_warns_when_composition_does_not_rotate(tmp_path, monkeypatch):
    base = [("OPN", True)] * 25 + [("BLK", False)] * 75          # 25% baseline
    # 240 rows, 30% blocked-class evaluations, 30 passes all from OPN: rate halves (12.5%), no rotation
    post = [("OPN", True, "per_ticker_p25")] * 30 + [("OPN", False, "per_ticker_p25")] * 138 \
         + [("BLK", False, "per_ticker_p25")] * 72
    out = _run80(tmp_path, monkeypatch, base, post, HIST)
    assert len(out) == 1 and out[0][0] == "WARNING"
    assert "composition did not rotate" in out[0][1]
    assert "estimator is not delivering" not in out[0][1]


def test_check80_warns_when_rate_does_not_move(tmp_path, monkeypatch):
    base = [("OPN", True)] * 25 + [("BLK", False)] * 75
    post = [("OPN", True, "per_ticker_p25")] * 15 + [("BLK", True, "per_ticker_p25")] * 15 \
         + [("OPN", False, "per_ticker_p25")] * 45 + [("BLK", False, "per_ticker_p25")] * 45   # 25%, rotated
    out = _run80(tmp_path, monkeypatch, base, post, HIST)
    assert out[0][0] == "WARNING" and "estimator is not delivering" in out[0][1]


def test_check80_clean_read_is_info_with_numbers(tmp_path, monkeypatch):
    base = [("OPN", True)] * 25 + [("BLK", False)] * 75
    post = [("OPN", True, "per_ticker_p25")] * 20 + [("BLK", True, "per_ticker_p25")] * 12 \
         + [("OPN", False, "per_ticker_p25")] * 100 + [("BLK", False, "per_ticker_p25")] * 100   # 13.8%, rotated
    out = _run80(tmp_path, monkeypatch, base, post, HIST)
    assert len(out) == 1 and out[0][0] == "INFO"
    assert "32/232 = 13.8%" in out[0][1] and "blocked-under-0.85 names: 38% of passes" in out[0][1]
