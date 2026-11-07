"""VIX gate at the threshold blocks, as live (2026-10-17).
Live L1 ELIGIBILITY B1 blocks at `vix >= l1_eligibility_vix_max` (backend/gate/l1_eligibility.py);
both engines used `>`, so a VIX close exactly at the threshold entered in the
backtest and was blocked live."""
import inspect

from backend.backtest import engine, engine_fast
from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed


def test_engine_fast_blocks_at_the_threshold():
    pre = synthetic_precomputed()
    pre["vix_cache"] = {d: 20.0 for d in pre["vix_cache"]}
    run = lambda thr: engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre,
                                      **{**RUN_KW, "l1_eligibility_vix_max": thr})
    assert run(20.01)["total_trades"] > 10          # positive control: just above, the window trades
    assert run(20.0)["total_trades"] == 0           # at the threshold: blocked, as live


def test_engine_slow_uses_the_same_comparison():
    src = inspect.getsource(engine.run)
    assert "_vix_on(raw_data, today) >= l1_eligibility_vix_max" in src
    assert "> l1_eligibility_vix_max" not in src.replace(">= l1_eligibility_vix_max", "")
