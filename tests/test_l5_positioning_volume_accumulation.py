"""
Tests for l5_positioning._check_volume_accumulation.

Covers: accumulation pass, distribution fail, neutral fail, all-up-days edge case,
insufficient history, empty data, and the evaluate() integration path.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

@pytest.fixture(autouse=True)
def _no_options_fetch():
    """These tests are about the check loop, not the fetch. Found 2026-10-31 by the Finding 5 Yahoo guard: three of
    them fetched real option chains for NVDA, AAPL and GS on every run (the checks were mocked, the fetch was not)."""
    with patch("backend.gate.l5_positioning._fetch_options_chains", return_value=[]):
        yield


from backend.gate.l5_positioning import (
    _check_volume_accumulation,
    evaluate,
    VOL_ACCUM_THRESHOLD,
    VOL_ACCUM_WINDOW,
)


def _make_price_data(closes: list[float], volumes: list[int], ticker: str = "NVDA") -> pd.DataFrame:
    """Build a yf.download()-shaped DataFrame (multi-level columns, yfinance >= 0.2.x)."""
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="B")
    arrays = [["Close", "Volume"], [ticker, ticker]]
    cols = pd.MultiIndex.from_arrays(arrays)
    df = pd.DataFrame(
        list(zip(closes, volumes)),
        index=idx,
        columns=cols,
    )
    df.columns.names = ["Price", "Ticker"]
    return df


# ── Unit tests: _check_volume_accumulation ────────────────────────────────────

@patch("backend.gate.l5_positioning.yf.download")
def test_accumulation_pass(mock_dl):
    """60%+ volume on up days → ratio > 1.2 → pass."""
    # 20 alternating days: 10 up (large vol), 10 down (small vol) → ratio = 3.0
    closes  = [100.0] + [101.0, 99.0] * 10
    volumes = [1_000_000] + [3_000_000, 1_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "NVDA")

    result = _check_volume_accumulation("NVDA")
    assert result["pass"] is True
    assert result["ratio"] > VOL_ACCUM_THRESHOLD


@patch("backend.gate.l5_positioning.yf.download")
def test_distribution_fail(mock_dl):
    """Heavy volume on down days → ratio < 1.2 → fail."""
    closes  = [100.0] + [99.0, 101.0] * 10
    volumes = [1_000_000] + [3_000_000, 1_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "NFLX")

    result = _check_volume_accumulation("NFLX")
    assert result["pass"] is False
    assert result["ratio"] < VOL_ACCUM_THRESHOLD


@patch("backend.gate.l5_positioning.yf.download")
def test_neutral_fail(mock_dl):
    """Roughly equal volume on up/down days → ratio near 1.0 → fail."""
    closes  = [100.0] + [101.0, 99.0] * 10
    volumes = [1_000_000] + [2_000_000, 2_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "MCD")

    result = _check_volume_accumulation("MCD")
    assert result["pass"] is False
    assert result["ratio"] < VOL_ACCUM_THRESHOLD


@patch("backend.gate.l5_positioning.yf.download")
def test_all_up_days_passes(mock_dl):
    """All up days (zero down volume) → ratio = inf → pass."""
    closes  = list(range(100, 122))        # 22 strictly rising values
    volumes = [1_000_000] * 22
    mock_dl.return_value = _make_price_data(closes, volumes, "AAPL")

    result = _check_volume_accumulation("AAPL")
    assert result["pass"] is True
    assert result["down_vol"] == 0


@patch("backend.gate.l5_positioning.yf.download")
def test_at_threshold_boundary_fails(mock_dl):
    """Ratio exactly at threshold (not strictly greater) → fail."""
    # up_vol = 1.2, down_vol = 1.0 → ratio = 1.2 exactly → not > 1.2
    closes  = [100.0] + [101.0, 99.0] * 10
    up_v    = 12_000_000
    down_v  = 10_000_000
    volumes = [1_000_000] + [up_v, down_v] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "TEST")

    result = _check_volume_accumulation("TEST")
    # ratio = sum(up_v * 10) / sum(down_v * 10) = 1.2 exactly
    assert result["pass"] is False


@patch("backend.gate.l5_positioning.yf.download")
def test_insufficient_history_fails(mock_dl):
    """Fewer than 20 trading days → fail with reason."""
    closes  = [100.0, 101.0, 99.0]
    volumes = [1_000_000, 2_000_000, 1_500_000]
    mock_dl.return_value = _make_price_data(closes, volumes, "AMD")

    result = _check_volume_accumulation("AMD")
    assert result["pass"] is False
    assert "only" in result["reason"]


@patch("backend.gate.l5_positioning.yf.download")
def test_empty_data_fails(mock_dl):
    """Empty DataFrame → fail gracefully."""
    mock_dl.return_value = pd.DataFrame()

    result = _check_volume_accumulation("MSFT")
    assert result["pass"] is False
    assert "empty download" in result["reason"]


# ── Integration: evaluate() includes volume_accumulation ─────────────────────

@patch("backend.gate.l5_positioning._check_volume_accumulation")
@patch("backend.gate.l5_positioning._check_unusual_call_volume")
@patch("backend.gate.l5_positioning._check_put_call_ratio")
@patch("backend.gate.l5_positioning._check_relative_strength")
def test_evaluate_volume_accumulation_is_wired(mock_rs, mock_pcr, mock_ucv, mock_va):
    """volume_accumulation participates in the evaluate() sub-check loop."""
    mock_rs.return_value  = {"pass": True,  "reason": "rs ok"}
    mock_pcr.return_value = {"pass": True,  "reason": "pcr ok"}
    mock_ucv.return_value = {"pass": False, "reason": "ucv fail"}
    mock_va.return_value  = {"pass": True,  "reason": "accumulation ok"}

    result = evaluate("NVDA", "Technology")
    assert mock_va.called
    assert "volume_accumulation" in result.data["checks"]
    assert result.passed  # price group (RS) and options group pass


@patch("backend.gate.l5_positioning._check_volume_accumulation")
@patch("backend.gate.l5_positioning._check_unusual_call_volume")
@patch("backend.gate.l5_positioning._check_put_call_ratio")
@patch("backend.gate.l5_positioning._check_relative_strength")
def test_insider_cluster_not_present(mock_rs, mock_pcr, mock_ucv, mock_va):
    """insider_cluster must not appear in evaluate() output."""
    for m in (mock_rs, mock_pcr, mock_ucv, mock_va):
        m.return_value = {"pass": True, "reason": "ok"}

    result = evaluate("AAPL", "Technology")
    assert "insider_cluster" not in result.data["checks"]
    assert "volume_accumulation" in result.data["checks"]


@patch("backend.gate.l5_positioning._check_volume_accumulation")
@patch("backend.gate.l5_positioning._check_unusual_call_volume")
@patch("backend.gate.l5_positioning._check_put_call_ratio")
@patch("backend.gate.l5_positioning._check_relative_strength")
def test_volume_accumulation_score_contribution(mock_rs, mock_pcr, mock_ucv, mock_va):
    """volume_accumulation contributes 0.25 to score when it passes.

    Group constraint (d70ab53): price group and options group each require ≥1 pass.
    Setup: RS=pass (price group ok), PCR=pass (options group ok), UCv=fail, VA=pass.
    → 3/4 pass, both groups satisfied.
    """
    mock_rs.return_value  = {"pass": True,  "reason": "ok"}
    mock_pcr.return_value = {"pass": True,  "reason": "ok"}
    mock_ucv.return_value = {"pass": False, "reason": "fail"}
    mock_va.return_value  = {"pass": True,  "reason": "ok"}

    result = evaluate("GS", "Financials")
    assert result.passed          # 3/4, both groups pass
    assert abs(result.score - 0.75) < 0.001  # 3 * 0.25


def test_settings_and_logs_carry_no_min_pass():
    """lock_leading_min_pass was read by chain.py and passed into a parameter lock4 never used (since the
    two-group rule, 2026-06-03); Settings offered it as "Min Checks" and the gate log printed pass_count/min_pass.
    Retired 2026-10-28 (Niclas: "om inget läser min pass är det väl ingen poäng att spara"). The log names the
    two groups that decide instead."""
    import inspect
    from loguru import logger
    from backend.gate import gate_runner, gate_runner_live, lock_codes
    from backend.live_config import get_live_config
    from backend.demo_config import get_demo_config
    assert "min_pass" not in inspect.signature(evaluate).parameters
    assert "lock_leading_min_pass" not in get_live_config() and "lock_leading_min_pass" not in get_demo_config()
    assert "lock_leading_min_pass" not in lock_codes.config_labels()
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        for runner in (gate_runner, gate_runner_live):
            runner._log_summary("NVDA", {"outcome": "FILTERED_LEADING",
                                         "lock1": {"passed": True, "score": 0.7},
                                         "lock_leading": {"passed": False, "pass_count": 2,
                                                          "price_group_pass": True, "options_group_pass": False}})
    finally:
        logger.remove(sink)
    assert sum("LD=✗(price ✓ options ✗)" in l for l in lines) == 2, lines
