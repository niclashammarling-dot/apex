"""engine_fast candidate ledger (2026-10-24): off by default, never changes the trades, one row per
Lock 1 candidate per day before penalties, every entered trade has its ledger row."""
from backend.backtest import engine_fast
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _run(pre, **kw):
    return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_ledger_never_changes_the_trades():
    pre = synthetic_precomputed()
    base = _run(pre)
    led: list = []
    with_ledger = _run(pre, candidate_ledger=led)
    assert with_ledger["trade_log"] == base["trade_log"] and with_ledger["equity_curve"] == base["equity_curve"]
    assert led                                                                    # the fixture has candidates


def test_rows_are_lock1_candidates_with_their_fields_and_cover_every_entry():
    pre = synthetic_precomputed()
    led: list = []
    res = _run(pre, candidate_ledger=led)
    keys = {"date", "ticker", "sector", "score", "threshold", "macro_day", "vix", "etf_ret",
            "earnings_near", "held", "cooloff"}
    assert all(keys <= set(r) for r in led)
    assert all(r["score"] >= r["threshold"] - 1e-9 for r in led)                  # passed Lock 1's threshold
    rows = {(r["date"], r["ticker"]) for r in led}
    assert all((t["entry_date"], t["ticker"]) in rows for t in res["trade_log"])   # every entry was a candidate
    assert len(rows) == len(led)                                                  # one row per candidate per day


def test_random_entry_runs_write_no_rows():
    pre = synthetic_precomputed()
    led: list = []
    _run(pre, candidate_ledger=led, random_entries={"seed": 1, "per_day": {}, "open_cap": {}})
    assert led == []
