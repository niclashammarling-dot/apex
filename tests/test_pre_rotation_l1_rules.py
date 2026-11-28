"""Pre-rotation candidates take L1's rules before the chain (2026-11-28; scoped as lock labels phase 4a M5, landed on
its own ahead of 4a, riktning Niclas the same day). Vault raw/notes/2026-11/2026-11-28-apex-pre-rotation-entered-past-a-
failed-exit-cooloff-win.md. Each test fails on the parent (968ba2b)."""
from contextlib import ExitStack
from unittest.mock import patch

from backend.gate import lock_codes


# ── M5: pre-rotation candidates take L1's rules before the chain ───────────────────────────────────────────────
# Founding case: since phase 3 split exit_cooloff_win (10-31) the pre-rotation filter checked exited_tickers only, so a
# TP/TSL winner in its 168 h window reached L3-L6 with the rule failed (SPG 11-17: L6 approved six times; only the
# sector cap stopped the entry). Pre-rotation also skipped the live blocklist (audit finding 6).

def _watch(sector):
    return {"available": True, "watching": [{"sector": sector}], "likely_next": []}


def _run_live_with(won=frozenset(), blocklist=None, **kw):
    from tests.test_gate_runners import _live_patches
    from backend.gate import gate_runner_live
    patches = _live_patches(**kw)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in patches]
        st.enter_context(patch("backend.gate.gate_runner_live.get_recently_exited_live_tickers",
                               return_value=(set(), set(won))))
        if blocklist is not None:
            st.enter_context(patch.dict("backend.config.LIVE_TICKER_BLOCKLIST", blocklist, clear=True))
        gate_runner_live.run()
    return mocks


def _chained(mocks, idx):
    return [c.args[0] for c in mocks[idx].call_args_list]


def test_live_pre_rotation_winner_in_its_cooloff_stops_before_the_chain():
    from tests.test_gate_runners import _signal
    mocks = _run_live_with(won={"HON"}, candidates=[_signal("HON", sector="Industrials"), _signal("XOM", sector="Energy")],
                           forecast=_watch("Industrials"))
    assert "HON" not in _chained(mocks, 11) and "XOM" in _chained(mocks, 11)
    rows = [c.args[0] for c in mocks[9].call_args_list if c.args[0]["ticker"] == "HON"]
    assert rows and all(r["stop_code"] == lock_codes.stop_code(1, "exit_cooloff_win") for r in rows)
    assert all(lock_codes.rule_key(6, "claude") not in r["rule_states"] for r in rows)   # no L6 call spent


def test_live_pre_rotation_honours_the_blocklist():
    from tests.test_gate_runners import _signal
    mocks = _run_live_with(blocklist={"HON": "probe"},
                           candidates=[_signal("HON", sector="Industrials"), _signal("XOM", sector="Energy")],
                           forecast=_watch("Industrials"))
    assert "HON" not in _chained(mocks, 11)


def test_demo_pre_rotation_winner_in_its_cooloff_stops_before_the_chain():
    from tests.test_gate_runners import _demo_patches, _signal
    from backend.gate import gate_runner as gr
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _demo_patches(
            candidates=[_signal("HON", sector="Industrials"), _signal("XOM", sector="Energy")])]
        st.enter_context(patch("backend.sector_transitions.get_rotation_forecast", return_value=_watch("Industrials")))
        st.enter_context(patch("backend.gate.gate_runner.get_recently_exited_tickers", return_value=(set(), {"HON"})))
        gr.run()
    assert "HON" not in _chained(mocks, 9) and "XOM" in _chained(mocks, 9)
    rows = [c.args[0] for c in mocks[8].call_args_list if c.args[0]["ticker"] == "HON"]
    assert rows and all(r["stop_code"] == lock_codes.stop_code(1, "exit_cooloff_win") for r in rows)
