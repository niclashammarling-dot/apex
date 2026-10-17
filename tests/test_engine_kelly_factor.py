"""engine_fast `kelly_factor` (H-V1, 2026-10-17): a per-day factor replaces half-Kelly's 0.50."""
from backend.backtest import engine_fast
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _run(pre, **kw):
    return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_factor_half_everywhere_is_the_baseline_and_lower_factor_sizes_down():
    pre = synthetic_precomputed()
    base = _run(pre)
    half = {d.isoformat(): 0.5 for d in pre["trading_days"]}
    assert _run(pre, kelly_factor=half)["trade_log"] == base["trade_log"]        # positive control
    assert _run(pre, kelly_factor={})["trade_log"] == base["trade_log"]          # missing day -> 0.50
    low = _run(pre, kelly_factor={d: 0.25 for d in half})
    a = {(t["ticker"], t["entry_date"]): t["amount"] for t in base["trade_log"]}
    b = {(t["ticker"], t["entry_date"]): t["amount"] for t in low["trade_log"]}
    first = min(a, key=lambda k: k[1])
    assert b[first] < a[first] * 0.6                                              # first entry sized at about half
