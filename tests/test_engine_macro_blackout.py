"""Rows 7 / 10 in engine_fast (2026-10-17): Lock 1 B2's macro blackout, as engine.py and live."""
from datetime import date

from backend.backtest import engine, engine_fast
from backend.macro_calendar import macro_status
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def _days(pre, status, window=1):
    return {d.isoformat() for d in pre["trading_days"]
            if macro_status(d, cpi_nfp_pre_days=window)[0] == status}


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
    assert engine._macro_status(date(2026, 9, 14), 1) == "hard_block"


def test_the_cpi_nfp_window_is_live_config_by_reference(monkeypatch):
    """P3 (Niclas 2026-10-28): one calendar for every book. The engines read live's
    macro_event_blackout_days, as lock1_eligibility does, not macro_status's own default."""
    pre = synthetic_precomputed()
    wide = _days(pre, "pre_event", window=2) - _days(pre, "pre_event")
    assert wide                                                       # the wider window adds eves
    entries = lambda **kw: {t["entry_date"] for t in _run(pre, macro_pre_event_penalty=0.99, **kw)["trade_log"]}
    assert entries(macro_event_blackout_days=1) & wide                # positive control: window 1 trades them
    assert not entries(macro_event_blackout_days=2) & wide
    import backend.live_config as live_config
    real = live_config.get_live_config
    monkeypatch.setattr(live_config, "get_live_config", lambda: {**real(), "macro_event_blackout_days": 2})
    assert entries() == entries(macro_event_blackout_days=2)          # None follows live config
    # engine.py downloads its data and runs nowhere offline; its wrapper takes the window as a required
    # argument, so a call that does not pass it fails, and run() resolves None the same way.
    import inspect
    assert inspect.signature(engine.run).parameters["macro_event_blackout_days"].default is None
    assert engine._macro_status(date(2026, 9, 9), 2) == "pre_event"   # CPI 09-11 in 2 d
    assert engine._macro_status(date(2026, 9, 9), 1) == "clear"
