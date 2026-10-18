"""The profit-read runner's own logic (2026-10-17): incumbent read from live config,
RND per-day counts, fold slicing. The engine and metrics have their own tests."""
from unittest.mock import patch

import pandas as pd

from backend.backtest import profit_read_2026_10 as pr
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed

LIVE = {"lock1_threshold": 0.65, "take_profit_pct": 0.06, "stop_loss_pct": 0.06,
        "profit_lock_trigger_pct": 0.04, "profit_lock_trail_pct": 0.01, "max_positions": 9,
        "max_hold_days": 25, "vix_threshold": 30, "max_sector_exposure": 0.3,
        "exit_cooloff_hours": 24, "tp_cooloff_hours": 168, "macro_pre_event_penalty": 0.05,
        "etf_negative_penalty": 0.15, "etf_negative_floor": -1}


def test_incumbent_is_read_from_live_config():
    with patch("backend.live_config.get_live_config", return_value=LIVE):
        inc = pr.incumbent_from_live()
    assert inc["time_stop_days"] == 25 and inc["max_sector_exposure"] == 0.3
    assert (inc["sl_cooldown_days"], inc["tp_cooldown_days"]) == (1, 7)
    assert inc["lock1_threshold"] == 0.65 and inc["max_positions"] == 9
    assert inc["macro_pre_event_penalty"] == 0.05                        # row 10, from live (10-17)
    assert (inc["etf_negative_penalty"], inc["etf_negative_floor"]) == (0.15, -1)   # row 11 (10-18)
    assert pr.RUNS["R0"]["sl_cooldown_days"] == 5 and "sl_cooldown_days" not in pr.RUNS["R1"]


def test_entries_per_day_counts_by_entry_date():
    log = [{"entry_date": "2025-03-03"}, {"entry_date": "2025-03-03"}, {"entry_date": "2025-03-05"}]
    assert pr.entries_per_day(log) == {"2025-03-03": 2, "2025-03-05": 1}


def test_fold_metrics_slice_the_full_run():
    from backend.backtest.engine_fast import run
    pre = synthetic_precomputed()
    res = run(START.isoformat(), END.isoformat(), precomputed=pre, **RUN_KW)
    spy = pre["raw_data"]["SPY"]["Close"]
    with patch.object(pr, "FOLDS", {"A": ("2025-03-03", "2025-04-30"), "B": ("2025-05-01", "2025-06-27")}):
        f = pr.fold_metrics(res, spy, 0.0)
    total = (1 + f["A"]["total_return"]) * (1 + f["B"]["total_return"]) - 1
    assert abs(total - (res["final_balance"] / 10_000 - 1)) < 1e-9          # slices compose to the run
    assert f["A"]["trades"] + f["B"]["trades"] == sum(1 for t in res["trade_log"] if t.get("pnl") is not None)


def test_every_result_names_the_rows_it_does_not_model():
    import inspect
    src = inspect.getsource(pr.main)
    assert '"gaps": open_rows(' in src and "describe(" in src
