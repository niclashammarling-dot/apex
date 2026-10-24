"""Profit-read metrics (2026-10-17): each against a value known by construction."""
import math

import numpy as np
import pandas as pd

from backend.backtest import profit_metrics as pm

IDX = pd.bdate_range("2024-01-02", periods=500)


def test_alpha_beta_recover_a_known_model():
    rng = np.random.default_rng(3)
    m = pd.Series(rng.normal(0.0004, 0.01, len(IDX)), index=IDX)
    s = 0.0002 + 0.6 * m + pd.Series(rng.normal(0, 0.002, len(IDX)), index=IDX)
    ab = pm.alpha_beta(s, m)
    assert abs(ab["beta"] - 0.6) < 0.02
    assert abs(ab["alpha_daily"] - 0.0002) < 0.0002
    assert ab["t_alpha"] > 1.0 and ab["nw_lag"] == pm.newey_west_lag(len(IDX))
    zero = pm.alpha_beta(0.6 * m, m)                      # control: no alpha
    assert abs(zero["alpha_daily"]) < 1e-12


def test_newey_west_widens_errors_under_autocorrelation():
    rng = np.random.default_rng(5)
    m = pd.Series(rng.normal(0, 0.01, len(IDX)), index=IDX)
    e = np.zeros(len(IDX))
    shocks = rng.normal(0, 0.002, len(IDX))
    for i in range(1, len(IDX)):
        e[i] = 0.8 * e[i - 1] + shocks[i]
    s = 0.0001 + 0.5 * m + pd.Series(e, index=IDX)
    ab = pm.alpha_beta(s, m)
    X = np.column_stack([np.ones(len(IDX)), m.to_numpy()])
    u = s.to_numpy() - X @ np.linalg.lstsq(X, s.to_numpy(), rcond=None)[0]
    ols_se = math.sqrt((u @ u / (len(u) - 2)) * np.linalg.inv(X.T @ X)[0, 0])
    assert abs(ab["alpha_daily"] / ab["t_alpha"]) > 1.5 * ols_se


def test_drawdown_cagr_calmar_on_a_known_path():
    r = pd.Series([0.10, -0.50, 0.20], index=IDX[:3])      # wealth 1.1 → 0.55 → 0.66
    assert math.isclose(pm.max_drawdown(r), 0.5)
    g = 1.1 * 0.5 * 1.2
    assert math.isclose(pm.cagr(r), g ** (252 / 3) - 1)
    assert math.isclose(pm.calmar(r), pm.cagr(r) / 0.5)


def test_expectancy_and_ci():
    trades = [{"pnl": p, "pnl_pct": p / 1000} for p in (10.0, -5.0, 20.0, -5.0)]
    e = pm.expectancy(trades)
    assert e["n"] == 4 and math.isclose(e["usd"]["mean"], 5.0)
    se = np.std([10, -5, 20, -5], ddof=1) / 2
    assert np.allclose(e["usd"]["ci95"], [5 - 1.96 * se, 5 + 1.96 * se])


def test_deployed_fraction_counts_entry_to_exit():
    eq = [{"date": str(d.date()), "balance": 1000.0} for d in IDX[:5]]
    trades = [{"entry_date": str(IDX[1].date()), "exit_date": str(IDX[3].date()), "amount": 250.0}]
    dep = pm.deployed_fraction(trades, eq)
    assert list(dep) == [0.0, 0.25, 0.25, 0.0, 0.0]


def test_spy_em_mixes_spy_and_cash():
    spy = pd.Series([100.0, 110.0, 99.0], index=IDX[:3])
    r = pm.spy_em_returns(spy, IDX[1:3], 0.4, cash_daily=0.001)
    assert np.allclose(r.to_numpy(), [0.4 * 0.1 + 0.6 * 0.001, 0.4 * -0.1 + 0.6 * 0.001])
