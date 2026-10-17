"""Rows 7 / 10 in engine_fast (2026-10-17): Lock 1 B2's macro blackout, as engine.py and live."""
from datetime import date

from backend.backtest import engine, engine_fast
from backend.macro_calendar import macro_status
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _days(pre, status):
    return {d.isoformat() for d in pre["trading_days"] if macro_status(d)[0] == status}


def _run(pre, **kw):
    return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_no_entry_on_a_hard_block_day_and_the_window_has_some():
    pre = synthetic_precomputed()
    hard = _days(pre, "hard_block")
    assert {"2025-03-17", "2025-03-19"} <= hard                     # FOMC 03-19, two days before
    res = _run(pre)
    assert res["total_trades"] > 10
    assert not {t["entry_date"] for t in res["trade_log"]} & hard


def test_pre_event_penalty_and_flag():
    pre = synthetic_precomputed()
    pre_ev = _days(pre, "pre_event")
    assert pre_ev
    base = {t["entry_date"] for t in _run(pre)["trade_log"]}
    assert base & pre_ev                                             # positive control: entries happen on eves
    assert not {t["entry_date"] for t in _run(pre, macro_pre_event_penalty=0.99)["trade_log"]} & pre_ev
    assert not {t["entry_date"] for t in _run(pre, macro_hard_block=True)["trade_log"]} & pre_ev


def test_shared_rule_precedence_and_engine_wrapper():
    assert macro_status(date(2026, 9, 14))[0] == "hard_block"        # FOMC 09-16 in 2 d
    assert macro_status(date(2026, 9, 10)) == ("pre_event", "CPI on 2026-09-11 (in 1d)")
    assert macro_status(date(2026, 9, 17))[0] == "clear"              # day after FOMC
    # a day that is both a CPI eve and inside an FOMC window is a hard block
    assert macro_status(date(2026, 12, 7))[0] == "hard_block"         # FOMC 12-09; 12-10 CPI
    assert engine._macro_status(date(2026, 9, 14)) == "hard_block"
