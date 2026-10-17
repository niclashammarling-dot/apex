"""
Profit-read metrics (2026-10-17), pre-registered in the vault note
raw/notes/2026-10/2026-10-16-apex-profit-read-preregistration.md: alpha against SPY
with Newey–West errors (primary), Calmar (the regime layer's own test), expectancy
per trade with a 95 % CI, mean deployed fraction, and exposure-matched SPY.

Pure functions over an engine result (equity_curve, trade_log) and price series;
nothing here runs the engine.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def daily_returns(equity_curve: list[dict]) -> pd.Series:
    """Daily simple returns of the engine's equity curve, indexed by date. The first
    point is the start balance; NaN balances (none since the 2026-10-17 holiday fix)
    are dropped, not filled."""
    s = pd.Series({pd.Timestamp(p["date"]): p["balance"] for p in equity_curve}).sort_index()
    s = s[~s.index.duplicated(keep="last")].dropna()
    return s.pct_change().dropna()


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns).cumprod()
    peak   = wealth.cummax()
    return float(((peak - wealth) / peak).max()) if len(wealth) else 0.0


def cagr(returns: pd.Series) -> float:
    if len(returns) == 0:
        return 0.0
    growth = float((1 + returns).prod())
    years  = len(returns) / TRADING_DAYS
    return growth ** (1 / years) - 1 if growth > 0 else -1.0


def calmar(returns: pd.Series) -> float | None:
    dd = max_drawdown(returns)
    return cagr(returns) / dd if dd > 0 else None


def newey_west_lag(n: int) -> int:
    """The usual automatic lag: floor(4 · (T/100)^(2/9))."""
    return int(math.floor(4 * (n / 100) ** (2 / 9))) if n > 0 else 0


def alpha_beta(strategy: pd.Series, market: pd.Series, rf: pd.Series | float = 0.0) -> dict:
    """OLS of excess returns: r − rf = α + β (r_m − rf) + ε, Newey–West (Bartlett)
    standard errors. α is reported daily and annualised (× 252)."""
    df = pd.concat({"s": strategy, "m": market}, axis=1, join="inner").dropna()
    if isinstance(rf, pd.Series):
        df["rf"] = rf.reindex(df.index).ffill().fillna(0.0)
    else:
        df["rf"] = float(rf)
    y = (df["s"] - df["rf"]).to_numpy()
    x = (df["m"] - df["rf"]).to_numpy()
    n = len(y)
    if n < 3:
        return {"n": n, "alpha_daily": None, "alpha_annual": None, "beta": None, "t_alpha": None, "t_beta": None}
    X = np.column_stack([np.ones(n), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    coef = XtX_inv @ X.T @ y
    u = y - X @ coef
    L = newey_west_lag(n)
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for lag in range(1, L + 1):
        w = 1 - lag / (L + 1)
        G = Xu[lag:].T @ Xu[:-lag]
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.diag(cov))
    return {
        "n": n, "nw_lag": L,
        "alpha_daily": float(coef[0]), "alpha_annual": float(coef[0] * TRADING_DAYS),
        "beta": float(coef[1]),
        "t_alpha": float(coef[0] / se[0]) if se[0] > 0 else None,
        "t_beta": float(coef[1] / se[1]) if se[1] > 0 else None,
    }


def expectancy(trade_log: list[dict]) -> dict:
    """Mean P&L per closed trade ($ and %), with a normal-approximation 95 % CI."""
    pnl = np.array([t["pnl"] for t in trade_log if t.get("pnl") is not None], dtype=float)
    pct = np.array([t["pnl_pct"] for t in trade_log if t.get("pnl_pct") is not None], dtype=float)
    out = {"n": int(len(pnl))}
    for name, a in (("usd", pnl), ("pct", pct)):
        if len(a) >= 2:
            m, se = float(a.mean()), float(a.std(ddof=1) / math.sqrt(len(a)))
            out[name] = {"mean": m, "ci95": [m - 1.96 * se, m + 1.96 * se]}
        else:
            out[name] = {"mean": float(a.mean()) if len(a) else None, "ci95": None}
    return out


def deployed_fraction(trade_log: list[dict], equity_curve: list[dict]) -> pd.Series:
    """Per day: capital in open positions (entry amount) / equity. A trade counts from
    its entry date up to, not including, its exit date."""
    eq = pd.Series({pd.Timestamp(p["date"]): p["balance"] for p in equity_curve}).sort_index()
    eq = eq[~eq.index.duplicated(keep="last")].dropna()
    dep = pd.Series(0.0, index=eq.index)
    for t in trade_log:
        e0 = pd.Timestamp(t["entry_date"])
        e1 = pd.Timestamp(t["exit_date"]) if t.get("exit_date") else eq.index[-1] + pd.Timedelta(days=1)
        dep[(dep.index >= e0) & (dep.index < e1)] += t["amount"]
    return (dep / eq).clip(lower=0.0)


def spy_em_returns(spy_close: pd.Series, days: pd.DatetimeIndex, fraction: float,
                   cash_daily: pd.Series | float = 0.0) -> pd.Series:
    """Exposure-matched SPY: `fraction` in SPY, the rest in cash at `cash_daily`
    (0 % = the paper-cash assumption; a T-bill series for the variant)."""
    r = spy_close.dropna().pct_change().reindex(days).fillna(0.0)
    c = cash_daily.reindex(days).ffill().fillna(0.0) if isinstance(cash_daily, pd.Series) else float(cash_daily)
    return fraction * r + (1 - fraction) * c


def summary(result: dict, spy_close: pd.Series, rf_daily: pd.Series | float = 0.0) -> dict:
    """Every pre-registered metric for one engine run."""
    r   = daily_returns(result["equity_curve"])
    m   = spy_close.dropna().pct_change().reindex(r.index)
    dep = deployed_fraction(result["trade_log"], result["equity_curve"])
    closed = [t for t in result["trade_log"] if t.get("pnl") is not None]
    wins   = [t for t in closed if t["pnl"] > 0]
    loss   = [t for t in closed if t["pnl"] <= 0]
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return {
        "total_return": float((1 + r).prod() - 1) if len(r) else 0.0,
        "cagr": cagr(r), "max_drawdown": max_drawdown(r), "calmar": calmar(r),
        "trades": len(closed), "expectancy": expectancy(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_win_pct": float(np.mean([t["pnl_pct"] for t in wins])) if wins else None,
        "avg_loss_pct": float(np.mean([t["pnl_pct"] for t in loss])) if loss else None,
        "mean_deployed": float(dep.mean()) if len(dep) else 0.0,
        "sharpe": float(r.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None,
        "vs_spy": alpha_beta(r, m, rf_daily),
    }


def window(result_returns: pd.Series, start: str | date, end: str | date) -> pd.Series:
    return result_returns[(result_returns.index >= pd.Timestamp(start)) & (result_returns.index <= pd.Timestamp(end))]
