"""
Tests for lock4_leading._check_volume_accumulation.

Covers: accumulation pass, distribution fail, neutral fail, all-up-days edge case,
insufficient history, empty data, and the evaluate() integration path.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from backend.gate.lock4_leading import (
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

@patch("backend.gate.lock4_leading.yf.download")
def test_accumulation_pass(mock_dl):
    """60%+ volume on up days → ratio > 1.2 → pass."""
    # 20 alternating days: 10 up (large vol), 10 down (small vol) → ratio = 3.0
    closes  = [100.0] + [101.0, 99.0] * 10
    volumes = [1_000_000] + [3_000_000, 1_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "NVDA")

    result = _check_volume_accumulation("NVDA")
    assert result["pass"] is True
    assert result["ratio"] > VOL_ACCUM_THRESHOLD


@patch("backend.gate.lock4_leading.yf.download")
def test_distribution_fail(mock_dl):
    """Heavy volume on down days → ratio < 1.2 → fail."""
    closes  = [100.0] + [99.0, 101.0] * 10
    volumes = [1_000_000] + [3_000_000, 1_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "NFLX")

    result = _check_volume_accumulation("NFLX")
    assert result["pass"] is False
    assert result["ratio"] < VOL_ACCUM_THRESHOLD


@patch("backend.gate.lock4_leading.yf.download")
def test_neutral_fail(mock_dl):
    """Roughly equal volume on up/down days → ratio near 1.0 → fail."""
    closes  = [100.0] + [101.0, 99.0] * 10
    volumes = [1_000_000] + [2_000_000, 2_000_000] * 10
    mock_dl.return_value = _make_price_data(closes[:21], volumes[:21], "MCD")

    result = _check_volume_accumulation("MCD")
    assert result["pass"] is False
    assert result["ratio"] < VOL_ACCUM_THRESHOLD


@patch("backend.gate.lock4_leading.yf.download")
def test_all_up_days_passes(mock_dl):
    """All up days (zero down volume) → ratio = inf → pass."""
    closes  = list(range(100, 122))        # 22 strictly rising values
    volumes = [1_000_000] * 22
    mock_dl.return_value = _make_price_data(closes, volumes, "AAPL")

    result = _check_volume_accumulation("AAPL")
    assert result["pass"] is True
    assert result["down_vol"] == 0


@patch("backend.gate.lock4_leading.yf.download")
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


@patch("backend.gate.lock4_leading.yf.download")
def test_insufficient_history_fails(mock_dl):
    """Fewer than 20 trading days → fail with reason."""
    closes  = [100.0, 101.0, 99.0]
    volumes = [1_000_000, 2_000_000, 1_500_000]
    mock_dl.return_value = _make_price_data(closes, volumes, "AMD")

    result = _check_volume_accumulation("AMD")
    assert result["pass"] is False
    assert "only" in result["reason"]


@patch("backend.gate.lock4_leading.yf.download")
def test_empty_data_fails(mock_dl):
    """Empty DataFrame → fail gracefully."""
    mock_dl.return_value = pd.DataFrame()

    result = _check_volume_accumulation("MSFT")
    assert result["pass"] is False
    assert "empty download" in result["reason"]


# ── Integration: evaluate() includes volume_accumulation ─────────────────────

@patch("backend.gate.lock4_leading._check_volume_accumulation")
@patch("backend.gate.lock4_leading._check_unusual_call_volume")
@patch("backend.gate.lock4_leading._check_put_call_ratio")
@patch("backend.gate.lock4_leading._check_relative_strength")
def test_evaluate_volume_accumulation_is_wired(mock_rs, mock_pcr, mock_ucv, mock_va):
    """volume_accumulation participates in the evaluate() sub-check loop."""
    mock_rs.return_value  = {"pass": True,  "reason": "rs ok"}
    mock_pcr.return_value = {"pass": True,  "reason": "pcr ok"}
    mock_ucv.return_value = {"pass": False, "reason": "ucv fail"}
    mock_va.return_value  = {"pass": True,  "reason": "accumulation ok"}

    result = evaluate("NVDA", "Technology")
    assert mock_va.called
    assert "volume_accumulation" in result.data["checks"]
    assert result.passed  # 3/4 checks pass, min_pass=2


@patch("backend.gate.lock4_leading._check_volume_accumulation")
@patch("backend.gate.lock4_leading._check_unusual_call_volume")
@patch("backend.gate.lock4_leading._check_put_call_ratio")
@patch("backend.gate.lock4_leading._check_relative_strength")
def test_insider_cluster_not_present(mock_rs, mock_pcr, mock_ucv, mock_va):
    """insider_cluster must not appear in evaluate() output."""
    for m in (mock_rs, mock_pcr, mock_ucv, mock_va):
        m.return_value = {"pass": True, "reason": "ok"}

    result = evaluate("AAPL", "Technology")
    assert "insider_cluster" not in result.data["checks"]
    assert "volume_accumulation" in result.data["checks"]


@patch("backend.gate.lock4_leading._check_volume_accumulation")
@patch("backend.gate.lock4_leading._check_unusual_call_volume")
@patch("backend.gate.lock4_leading._check_put_call_ratio")
@patch("backend.gate.lock4_leading._check_relative_strength")
def test_volume_accumulation_score_contribution(mock_rs, mock_pcr, mock_ucv, mock_va):
    """volume_accumulation contributes 0.25 to score when it passes."""
    mock_rs.return_value  = {"pass": True,  "reason": "ok"}
    mock_pcr.return_value = {"pass": False, "reason": "fail"}
    mock_ucv.return_value = {"pass": False, "reason": "fail"}
    mock_va.return_value  = {"pass": True,  "reason": "ok"}

    result = evaluate("GS", "Financials")
    assert result.passed          # 2/4, min_pass=2
    assert abs(result.score - 0.5) < 0.001  # 2 * 0.25
