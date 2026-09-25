"""
EXIT_PARITY.md test 1a — exit-level equivalence, both engines, in CI.

The full-run reproduction (1b: score 0.56542, 41 stop exits, 27 invisible) is a
local one-time certification — it needs the frozen parquet and it is a
compounding counterfactual, since exit timing frees slots and changes later
entries. What CI can hold is the per-trade rule: given a trade and the bars it
was open for, both engines must exit on the same day, at the same price, for the
same reason.

The fixture is those 41 trades and only their own bars, frozen from the
certified 2026-09-25 run. It is the reference, not a snapshot of current
behaviour — regenerating it to make a failing test pass defeats the point.

Consumer: .github/workflows/tests.yml, triggered on changes to the engine files
(EXIT_PARITY.md, "Consumers"). Deterministic inputs mean this belongs on a
trigger, not a schedule.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.backtest import engine as slow
from backend.backtest import engine_fast as fast
from backend.backtest.exit_rules import Bar, ParityStats

FIXTURE = Path(__file__).parent / "fixtures" / "exit_parity_cases.json"


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(FIXTURE.read_text())


def _trade(case: dict) -> dict:
    return {
        "ticker":       case["ticker"],
        "sector":       case["sector"],
        "entry_date":   case["entry_date"],
        "entry_price":  case["entry_price"],
        "shares":       case["shares"],
        "amount":       case["amount"],
        "signal_score": case["signal_score"],
    }


def _caches(case: dict):
    """(price_cache, ohlc_cache) for engine_fast, from the case's own bars."""
    price, ohlc = {}, {}
    for day, (o, h, l, c) in case["bars"].items():
        price[(case["ticker"], day)] = c
        ohlc[(case["ticker"], day)] = Bar(open=o, high=h, low=l, close=c)
    return price, ohlc


def _frame(case: dict) -> pd.DataFrame:
    """Minimal MultiIndex frame for engine.py's _price_on."""
    days = sorted(case["bars"])
    rows = [case["bars"][d] for d in days]
    return pd.DataFrame(
        rows,
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]),
        columns=pd.MultiIndex.from_product([[case["ticker"]], ["Open", "High", "Low", "Close"]]),
    )


def _replay(case: dict, params: dict, engine: str, stats: ParityStats | None = None):
    """Walk the case's bars in order; return the first exit record, or None."""
    trade = _trade(case)
    open_trades = [trade]
    price, ohlc = _caches(case)
    frame = _frame(case) if engine == "slow" else None

    # The engine checks exits at the TOP of each day and enters at that day's
    # close, so a trade entered on D is first exit-checked on D+1. Replaying
    # from D instead tests the entry bar against a stop the position did not
    # yet have — which reclassifies intraday breaches as gap-throughs, because
    # the entry day's OPEN is frequently below entry_price × (1 − sl).
    for day in sorted(case["bars"])[1:]:
        today = date.fromisoformat(day)
        args = (
            open_trades,
            frame if engine == "slow" else price,
            today, day,
            params["take_profit_pct"], params["stop_loss_pct"], params["time_stop_days"],
            params["trailing_stop_pct"], None, None,
        )
        fn = slow._check_exits if engine == "slow" else fast._check_exits_fast
        closed = fn(*args, ohlc_cache=ohlc, parity_stats=stats, ratchet_from_high=False)
        if closed:
            assert len(closed) == 1
            return closed[0]["record"]
        # The caller removes closed trades; nothing closed, so nothing to remove.
    return None


@pytest.mark.parametrize("engine", ["fast", "slow"])
def test_every_stop_exit_reproduces(data: dict, engine: str) -> None:
    """Exit day, fill price, reason and outcome, per trade, in both engines."""
    failures = []
    for case in data["cases"]:
        rec = _replay(case, data["params"], engine)
        want = case["expect"]
        if rec is None:
            failures.append(f"{case['ticker']} {case['entry_date']}: no exit produced")
            continue
        got = {
            "exit_date":   rec["exit_date"],
            "exit_price":  rec["exit_price"],
            "exit_reason": rec["exit_reason"],
            "outcome":     rec["outcome"],
        }
        if got != want:
            failures.append(f"{case['ticker']} {case['entry_date']}: want {want}, got {got}")
    assert not failures, f"{len(failures)}/{len(data['cases'])} in {engine}:\n" + "\n".join(failures[:10])


def test_both_engines_agree_trade_for_trade(data: dict) -> None:
    """
    The two engines must not merely each match the fixture — they must match
    each other. One shared rule module makes this near-tautological today; the
    test exists so that reintroducing a per-engine copy is caught immediately.
    """
    for case in data["cases"]:
        a = _replay(case, data["params"], "fast")
        b = _replay(case, data["params"], "slow")
        assert a is not None and b is not None, case["ticker"]
        for field in ("exit_date", "exit_price", "exit_reason", "outcome"):
            assert a[field] == b[field], (
                f"{case['ticker']} {case['entry_date']} {field}: fast={a[field]} slow={b[field]}"
            )


def test_gap_and_intraday_split_matches(data: dict) -> None:
    """
    R2's buckets. A gap-through fills at the open and an intraday breach at the
    stop, so mixing them up is a fill-price error even when the exit day is
    right. `open == stop` exactly counts as a gap (`open <= stop`).
    """
    stats = ParityStats()
    for case in data["cases"]:
        _replay(case, data["params"], "fast", stats=stats)
    want = data["totals"]
    assert stats.intraday_sl  == want["intraday_sl"]
    assert stats.gap_sl       == want["gap_sl"]
    assert stats.intraday_tsl == want["intraday_tsl"]
    assert stats.gap_tsl      == want["gap_tsl"]
    assert stats.total_stop_exits == want["stop_exits"]


def test_both_touched_counter_is_reported(data: dict) -> None:
    """
    R3's exposure measurement. It is 0 on this window — no bar reached both the
    stop and the target — so the stop-wins tie-break never had to fire here.
    The assertion is on the frozen number, not on zero: if a future rule change
    starts breaking ties, this fails and the assumption gets re-argued rather
    than silently acquiring weight.
    """
    stats = ParityStats()
    for case in data["cases"]:
        _replay(case, data["params"], "fast", stats=stats)
    assert stats.both_touched == data["totals"]["both_touched"]
    assert stats.both_touched_tp_on_close == data["totals"]["both_touched_tp_on_close"]


def test_invisible_to_close_only_engine(data: dict) -> None:
    """
    The headline: exits the close-based SL branch would never have produced.
    This is the number the whole parity sequence exists for.
    """
    stats = ParityStats()
    for case in data["cases"]:
        _replay(case, data["params"], "fast", stats=stats)
    assert len(stats.missed_by_close) == data["totals"]["invisible_to_close"]


def test_close_only_arm_really_is_blind(data: dict) -> None:
    """
    Negative control. With no ohlc_cache the engines must fall back to
    close-based exits and MISS the invisible ones — otherwise the whole
    comparison is measuring nothing. Holds the guard closed before trusting it.
    """
    missed = 0
    for case in data["cases"]:
        trade = _trade(case)
        open_trades = [trade]
        price, _ = _caches(case)
        exited = False
        for day in sorted(case["bars"])[1:]:
            closed = fast._check_exits_fast(
                open_trades, price, date.fromisoformat(day), day,
                data["params"]["take_profit_pct"], data["params"]["stop_loss_pct"],
                data["params"]["time_stop_days"], data["params"]["trailing_stop_pct"],
                None, None, ohlc_cache=None,
            )
            if closed:
                exited = True
                if closed[0]["record"]["exit_date"] != case["expect"]["exit_date"]:
                    missed += 1
                break
        if not exited:
            missed += 1
    assert missed == data["totals"]["invisible_to_close"], (
        f"close-only arm diverged on {missed} cases, expected "
        f"{data['totals']['invisible_to_close']} — if this is 0 the intraday "
        f"path is not actually doing anything"
    )
