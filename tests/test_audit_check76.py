"""CHECK 76 — engine parity, static layers (CI-safe; layer 3 needs the host Parquet cache)."""
from audit import checks_code as cc
from audit._audit_core import REPO


def test_run_signature_divergence_is_exactly_the_accepted_set():
    slow = cc._check76_run_params(REPO / "backend/backtest/engine.py")
    fast = cc._check76_run_params(REPO / "backend/backtest/engine_fast.py")
    assert slow - fast == cc.CHECK76_ACCEPTED_SLOW_ONLY
    assert fast - slow == cc.CHECK76_ACCEPTED_FAST_ONLY


def test_documented_absent_symbols_are_absent_from_fast_engine():
    import re
    src = (REPO / "backend/backtest/engine_fast.py").read_text()
    present = [row for row, pat in cc.CHECK76_ABSENT_FROM_FAST.items() if re.search(pat, src)]
    assert present == [], f"PRODUCTION_GAP.md rows now stale: {present}"


def test_signal_cache_key_changes_with_signal_code(tmp_path, monkeypatch):
    from backend.backtest import engine_fast as ef
    from datetime import date
    k1 = ef._signal_cache_key(["A", "B"], date(2026, 1, 1), date(2026, 2, 1))
    monkeypatch.setattr(ef, "_signal_code_hash", lambda: "deadbeef")
    k2 = ef._signal_cache_key(["A", "B"], date(2026, 1, 1), date(2026, 2, 1))
    assert k1 != k2


def test_spy_return_20d_matches_production_definition():
    """Production: close[-1] / close[-20] - 1 (19 periods). engine_fast must agree."""
    import pandas as pd
    from datetime import date
    from backend.backtest import engine_fast as ef
    from backend.config import SPY_TICKER
    idx = pd.bdate_range("2026-01-01", periods=80)
    closes = pd.Series(range(100, 180), index=idx, dtype=float)
    raw = pd.concat({SPY_TICKER: pd.DataFrame({"Close": closes})}, axis=1)
    d = idx[-1].date()
    cache = ef._build_spy_cache(raw, [d])
    expected = float(closes.iloc[-1] / closes.iloc[-20] - 1)
    assert abs(cache[d.isoformat()]["return_20d"] - expected) < 1e-12


def test_slow_engine_etf_regime_ignores_nan_rows():
    import numpy as np
    import pandas as pd
    from datetime import date
    from backend.backtest import engine as e
    idx = pd.bdate_range("2026-01-01", periods=40)
    closes = pd.Series(np.linspace(100, 140, 40), index=idx)   # steady uptrend
    closes.iloc[-3] = np.nan                                    # one union-index hole
    raw = pd.concat({"XLK": pd.DataFrame({"Close": closes})}, axis=1)
    assert e._etf_regime_on(raw, "XLK", idx[-1].date()) == 1.0
