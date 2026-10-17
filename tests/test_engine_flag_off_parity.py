"""
engine_fast with regime_layer off is the parent engine (2026-10-17, engine port).

The port touched the signal cache (components added), candidate selection, sizing
and the sector-cap test. With the flag off every one of those must behave as
before: the golden below was written by this harness with cf3db17's engine_fast.py
copied in, and the ported engine must reproduce it exactly. Synthetic OHLCV
(seeded random walk, no network) — it tests that the engine's arithmetic is
unchanged, not anything about markets. CHECK 76 layer 3 covers the real cache.

Regenerate only on a deliberate flag-off behaviour change:
APEX_WRITE_ENGINE_GOLDEN=1 venv/bin/python -m pytest tests/test_engine_flag_off_parity.py
"""
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import backend.backtest.engine_fast as ef
from backend.backtest.exit_rules import build_ohlc_cache

GOLDEN = Path(__file__).parent / "fixtures" / "engine_flag_off_golden_cf3db17.json"

UNIVERSE = {
    "Technology": (["AAA", "AAB", "AAC", "AAD"], "XTK", 0.0012),
    "Energy":     (["BBA", "BBB", "BBC", "BBD"], "XEN", -0.0004),
    "Healthcare": (["CCA", "CCB", "CCC", "CCD"], "XHC", 0.0004),
}
START, END = date(2025, 3, 3), date(2025, 6, 27)


def synthetic_raw(seed: int = 17) -> pd.DataFrame:
    rng   = np.random.default_rng(seed)
    index = pd.bdate_range("2024-09-02", END.isoformat())
    cols  = {}

    def bars(drift, vol, base):
        ret   = rng.normal(drift, vol, len(index))
        close = base * np.exp(np.cumsum(ret))
        opn   = close * np.exp(rng.normal(0, vol / 3, len(index)))
        high  = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, len(index))))
        low   = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, len(index))))
        volu  = rng.integers(800_000, 1_600_000, len(index)).astype(float)
        return {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volu}

    for sector, (tickers, etf, drift) in UNIVERSE.items():
        for t in tickers + [etf]:
            for k, v in bars(drift, 0.018, 50 + 10 * rng.random()).items():
                cols[(t, k)] = v
    for t, drift, vol, base in (("SPY", 0.0004, 0.010, 400.0), ("^VIX", 0.0, 0.04, 18.0)):
        for k, v in bars(drift, vol, base).items():
            cols[(t, k)] = v
    return pd.DataFrame(cols, index=index)


def synthetic_precomputed() -> dict:
    """precompute()'s cache builders over the synthetic frame (no download, no pickle)."""
    raw_data      = synthetic_raw()
    ticker_sector = {t: s for s, (ts, _, _) in UNIVERSE.items() for t in ts}
    sector_etf    = {s: e for s, (_, e, _) in UNIVERSE.items()}
    etf_tickers   = list(sector_etf.values())
    all_tickers   = ["SPY", "^VIX"] + etf_tickers + list(ticker_sector)
    trading_days  = ef._trading_days(START, END, raw_data)
    spy_cache     = ef._build_spy_cache(raw_data, trading_days)
    etf_cache     = ef._build_etf_cache(raw_data, etf_tickers, trading_days)
    return {
        "raw_data":      raw_data,
        "ticker_sector": ticker_sector,
        "sector_etf":    sector_etf,
        "trading_days":  trading_days,
        "price_cache":   ef._build_price_cache(raw_data, all_tickers, trading_days),
        "ohlc_cache":    build_ohlc_cache(raw_data, all_tickers),
        "spy_cache":     spy_cache,
        "etf_cache":     etf_cache,
        "vix_cache":     ef._build_vix_cache(raw_data, trading_days),
        "rs_cache":      ef._build_rs_cache(raw_data, ticker_sector, sector_etf, trading_days),
        "signal_cache":  ef._build_signal_cache(raw_data, ticker_sector, sector_etf,
                                                spy_cache, etf_cache, trading_days),
    }


RUN_KW = dict(sl_cooldown_days=5, tp_cooldown_days=0, lock1_threshold=0.45, max_positions=6)


def _summary(res: dict) -> dict:
    keep = ("final_balance", "total_return_pct", "max_drawdown", "total_trades", "win_rate")
    return json.loads(json.dumps({**{k: res[k] for k in keep}, "trade_log": res["trade_log"]}))


def test_flag_off_reproduces_the_parent_engine():
    pre = synthetic_precomputed()
    out = _summary(ef.run(START.isoformat(), END.isoformat(), precomputed=pre, **RUN_KW))
    if os.environ.get("APEX_WRITE_ENGINE_GOLDEN"):
        GOLDEN.write_text(json.dumps(out, indent=1, sort_keys=True))
        pytest.skip("golden written")
    golden = json.loads(GOLDEN.read_text())
    assert out["total_trades"] == golden["total_trades"] > 10      # the window trades
    assert out == golden
