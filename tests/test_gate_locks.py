"""
Gate lock tests — covers edge cases for all five locks:
  - lock1_quant
  - lock_macro
  - lock2_sentiment
  - lock_leading
  - lock3_claude

All external I/O (yfinance, httpx, OpenAI, EDGAR, RSS) is mocked so
tests run offline and deterministically without API keys.
"""
import json
import threading
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _signal(score: float = 0.6) -> dict:
    return {"signal_score": score, "ticker": "AAPL", "sector": "Technology"}


def _cfg(**overrides) -> dict:
    base = {
        "lock1_threshold":               0.5,
        "lock2_sentiment_min":           0.2,
        "lock3_confidence_min":          0.6,
        "lock_leading_min_pass":         2,
        "vix_threshold":                 25.0,
        "macro_event_blackout_days":     1,
        "macro_earnings_blackout_days":  3,
        "take_profit_pct":               0.08,
        "stop_loss_pct":                 0.04,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Lock 1 — Quantitative
# ─────────────────────────────────────────────────────────────────────────────

class TestLock1Quant:
    from backend.gate import lock1_quant as _mod

    def test_pass_above_threshold(self):
        result = self._mod.evaluate(_signal(0.65), threshold=0.5)
        assert result["passed"] is True
        assert result["score"] == 0.65

    def test_fail_below_threshold(self):
        result = self._mod.evaluate(_signal(0.40), threshold=0.5)
        assert result["passed"] is False
        assert "0.400" in result["reason"]

    def test_exact_threshold_passes(self):
        # score == threshold should pass (>=)
        result = self._mod.evaluate(_signal(0.5), threshold=0.5)
        assert result["passed"] is True

    def test_just_below_threshold_fails(self):
        result = self._mod.evaluate(_signal(0.4999), threshold=0.5)
        assert result["passed"] is False

    def test_missing_score_treated_as_zero(self):
        result = self._mod.evaluate({}, threshold=0.5)
        assert result["passed"] is False
        assert result["score"] == 0.0

    def test_zero_threshold_always_passes(self):
        result = self._mod.evaluate(_signal(0.0), threshold=0.0)
        assert result["passed"] is True

    def test_perfect_score(self):
        result = self._mod.evaluate(_signal(1.0), threshold=0.5)
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_uses_config_default_when_no_threshold(self):
        with patch("backend.gate.lock1_quant.LOCK1_THRESHOLD", 0.55):
            result = self._mod.evaluate(_signal(0.56))
            assert result["passed"] is True
            assert result["threshold"] == 0.55


# ─────────────────────────────────────────────────────────────────────────────
# Lock Macro
# ─────────────────────────────────────────────────────────────────────────────

class TestLockMacro:
    from backend.gate import lock_macro as _mod

    def _evaluate(self, vix=15.0, today=None, earnings_date=None, **cfg_overrides):
        cfg = _cfg(**cfg_overrides)
        with patch.object(self._mod, "_get_vix", return_value=vix), \
             patch.object(self._mod, "_get_earnings_date", return_value=earnings_date), \
             patch("backend.gate.lock_macro.date") as mock_date:
            mock_date.today.return_value = today or date(2026, 3, 28)  # Saturday — no events
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            return self._mod.evaluate("AAPL", cfg)

    def test_pass_normal_conditions(self):
        result = self._evaluate(vix=15.0, today=date(2026, 3, 28))
        assert result["passed"] is True

    def test_vix_at_threshold_blocks(self):
        result = self._evaluate(vix=25.0, vix_threshold=25.0)
        assert result["passed"] is False
        assert "VIX" in result["reason"]

    def test_vix_just_below_threshold_passes(self):
        result = self._evaluate(vix=24.9, vix_threshold=25.0)
        assert result["passed"] is True

    def test_vix_none_does_not_block(self):
        # Fail-open: if VIX unavailable, don't block
        result = self._evaluate(vix=None)
        assert result["passed"] is True
        assert result["vix"] is None

    def test_fomc_date_blocks(self):
        # 2026-01-28 is an FOMC date in the calendar
        result = self._evaluate(today=date(2026, 1, 28), vix=15.0,
                                macro_event_blackout_days=1)
        assert result["passed"] is False
        assert "FOMC" in result["reason"]

    def test_day_before_fomc_blocks_within_blackout(self):
        result = self._evaluate(today=date(2026, 1, 27), vix=15.0,
                                macro_event_blackout_days=1)
        assert result["passed"] is False

    def test_two_days_before_fomc_passes_with_blackout_1(self):
        result = self._evaluate(today=date(2026, 1, 26), vix=15.0,
                                macro_event_blackout_days=1)
        assert result["passed"] is True

    def test_cpi_date_blocks(self):
        # 2026-01-14 is a CPI date
        result = self._evaluate(today=date(2026, 1, 14), vix=15.0,
                                macro_event_blackout_days=1)
        assert result["passed"] is False
        assert "CPI" in result["reason"]

    def test_earnings_within_blackout_blocks(self):
        earnings = date(2026, 3, 30)  # 2 days away
        result = self._evaluate(today=date(2026, 3, 28), vix=15.0,
                                earnings_date=earnings,
                                macro_earnings_blackout_days=3)
        assert result["passed"] is False
        assert "earnings" in result["reason"]

    def test_earnings_outside_blackout_passes(self):
        earnings = date(2026, 4, 10)  # 13 days away
        result = self._evaluate(today=date(2026, 3, 28), vix=15.0,
                                earnings_date=earnings,
                                macro_earnings_blackout_days=3)
        assert result["passed"] is True

    def test_earnings_none_does_not_block(self):
        result = self._evaluate(today=date(2026, 3, 28), vix=15.0,
                                earnings_date=None)
        assert result["passed"] is True

    def test_vix_blocks_before_earnings_check(self):
        # VIX > threshold + earnings close — VIX is checked first
        earnings = date(2026, 3, 29)
        result = self._evaluate(vix=30.0, vix_threshold=25.0,
                                earnings_date=earnings,
                                macro_earnings_blackout_days=3)
        assert result["passed"] is False
        assert "VIX" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# Lock 2 — Sentiment (Grok)
# ─────────────────────────────────────────────────────────────────────────────

class TestLock2Sentiment:
    from backend.gate import lock2_sentiment as _mod

    _EMPTY_SIGNALS = {
        "company_name": "Test Corp", "short_pct_float": None, "short_ratio": None,
        "analyst_mean": None, "analyst_label": None, "analyst_count": 0,
        "price_target_mean": None, "current_price": None, "upside_pct": None,
        "recent_analyst_actions": [], "news_titles": [],
    }

    def setup_method(self):
        # Reset module-level state between tests
        self._mod._cache.clear()
        self._mod._cb_failures   = 0
        self._mod._cb_open_until = 0.0
        # Prevent real yfinance calls in all lock2 tests
        self._signals_patcher = patch(
            "backend.gate.lock2_sentiment.fetch_market_signals",
            return_value=self._EMPTY_SIGNALS,
        )
        self._signals_patcher.start()

    def teardown_method(self):
        self._signals_patcher.stop()

    def _grok_response(self, sentiment="positive", score=0.7,
                       conviction="high", themes=None, summary="Bullish") -> dict:
        return {
            "sentiment":  sentiment,
            "score":      score,
            "conviction": conviction,
            "key_themes": themes or ["momentum", "breakout", "institutional buying"],
            "summary":    summary,
        }

    def _mock_call(self, response: dict | None):
        return patch.object(self._mod, "_call_grok", return_value=response)

    def test_pass_high_score_high_conviction(self):
        with self._mock_call(self._grok_response(score=0.7, conviction="high")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is True
        assert result["score"] == 0.7

    def test_fail_score_below_min(self):
        with self._mock_call(self._grok_response(score=0.1, conviction="high")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is False
        assert "score" in result["reason"]

    def test_fail_low_conviction_even_with_good_score(self):
        with self._mock_call(self._grok_response(score=0.9, conviction="low")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is False
        assert "conviction" in result["reason"]

    def test_fail_when_grok_unavailable(self):
        with self._mock_call(None):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is False
        assert result["reason"] == "grok_unavailable"
        assert result["score"] is None

    def test_score_exactly_at_min_fails(self):
        # pass condition is score > min (strict)
        with self._mock_call(self._grok_response(score=0.2, conviction="high")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is False

    def test_score_just_above_min_passes(self):
        with self._mock_call(self._grok_response(score=0.201, conviction="medium")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is True

    def test_medium_conviction_passes(self):
        with self._mock_call(self._grok_response(score=0.5, conviction="medium")):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is True

    def test_negative_score_fails(self):
        with self._mock_call(self._grok_response(score=-0.5, conviction="high")):
            result = self._mod.evaluate("TSLA", sentiment_min=0.2)
        assert result["passed"] is False

    def test_cache_hit_skips_api(self):
        response = self._grok_response(score=0.8, conviction="high")
        # Warm cache manually
        self._mod._cache["MSFT"] = {
            "expires_at": time.time() + 3600,
            "result": response,
        }
        with patch.object(self._mod, "_call_grok") as mock_api:
            result = self._mod.evaluate("MSFT", sentiment_min=0.2)
            mock_api.assert_not_called()
        assert result["passed"] is True

    def test_expired_cache_refreshes(self):
        old_response = self._grok_response(score=0.1, conviction="low")
        new_response = self._grok_response(score=0.8, conviction="high")
        self._mod._cache["NVDA"] = {
            "expires_at": time.time() - 1,  # already expired
            "result": old_response,
        }
        with self._mock_call(new_response):
            result = self._mod.evaluate("NVDA", sentiment_min=0.2)
        assert result["passed"] is True  # new result used

    def test_circuit_breaker_opens_after_3_failures(self):
        # Mock at the httpx level so _record_failure() is actually called
        import httpx
        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500))
        with patch("backend.gate.lock2_sentiment.XAI_API_KEY", "fake-key"), \
             patch("backend.gate.lock2_sentiment.httpx.post", return_value=err_resp), \
             patch("backend.gate.lock2_sentiment.time.sleep"):  # skip retry waits
            for _ in range(3):
                self._mod.evaluate(f"TICKER{_}", sentiment_min=0.2)
        assert self._mod._cb_open_until > time.time()

    def test_circuit_breaker_open_fails_fast_without_network_call(self):
        self._mod._cb_open_until = time.time() + 300
        with patch("backend.gate.lock2_sentiment.httpx.post") as mock_post:
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
            mock_post.assert_not_called()
        assert result["passed"] is False

    def test_circuit_breaker_resets_on_success(self):
        # Must mock at httpx layer so _record_success() inside _call_grok is reached
        self._mod._cb_failures   = 2
        self._mod._cb_open_until = 0.0
        payload = json.dumps(self._grok_response(score=0.8, conviction="high"))
        http_resp = MagicMock()
        http_resp.raise_for_status.return_value = None
        http_resp.json.return_value = {
            "choices": [{"message": {"content": payload}}]
        }
        with patch("backend.gate.lock2_sentiment.XAI_API_KEY", "fake-key"), \
             patch("backend.gate.lock2_sentiment.httpx.post", return_value=http_resp):
            self._mod.evaluate("CBTEST", sentiment_min=0.2)
        assert self._mod._cb_failures == 0

    def test_no_api_key_fails_closed(self):
        with patch("backend.gate.lock2_sentiment.XAI_API_KEY", ""):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["passed"] is False

    def test_missing_score_field_defaults_zero(self):
        # Grok returns response missing 'score'
        with self._mock_call({"sentiment": "positive", "conviction": "high",
                              "key_themes": [], "summary": ""}):
            result = self._mod.evaluate("AAPL", sentiment_min=0.2)
        assert result["score"] == 0.0
        assert result["passed"] is False  # 0.0 <= 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Lock Leading
# ─────────────────────────────────────────────────────────────────────────────

class TestLockLeading:
    from backend.gate import lock_leading as _mod

    # ── Relative Strength ─────────────────────────────────────────────────────
    # yf.download returns a MultiIndex DataFrame; the code accesses ["Close"]
    # which yields a DataFrame with ticker columns. We mock at that level.

    def _close_df(self, ticker_prices, etf_prices, ticker="NVDA", etf="XLK"):
        """Return a mock yf.download() result where mock["Close"] = close DataFrame."""
        idx       = pd.date_range("2026-03-20", periods=len(ticker_prices), freq="B")
        close_df  = pd.DataFrame({ticker: ticker_prices, etf: etf_prices}, index=idx)
        mock_dl   = MagicMock()
        mock_dl.__getitem__ = MagicMock(side_effect=lambda k: close_df if k == "Close"
                                        else pd.DataFrame())
        # Also make .empty work correctly
        mock_dl.empty = False
        return mock_dl

    def _empty_download(self):
        mock_dl = MagicMock()
        mock_dl.__getitem__ = MagicMock(side_effect=KeyError("Close"))
        mock_dl.empty = True
        return mock_dl

    def test_rs_pass_ticker_outperforms(self):
        dl = self._close_df([100, 105], [100, 102])
        with patch("backend.gate.lock_leading.yf.download", return_value=dl):
            result = self._mod._check_relative_strength("NVDA", "Technology")
        assert result["pass"] is True
        assert result["ticker_return"] == pytest.approx(5.0)

    def test_rs_fail_etf_outperforms(self):
        dl = self._close_df([100, 101], [100, 105])
        with patch("backend.gate.lock_leading.yf.download", return_value=dl):
            result = self._mod._check_relative_strength("NVDA", "Technology")
        assert result["pass"] is False

    def test_rs_fail_exact_tie(self):
        # equal returns → ticker not strictly greater → fail
        dl = self._close_df([100, 105], [100, 105])
        with patch("backend.gate.lock_leading.yf.download", return_value=dl):
            result = self._mod._check_relative_strength("NVDA", "Technology")
        assert result["pass"] is False

    def test_rs_fail_no_etf_for_sector(self):
        result = self._mod._check_relative_strength("NVDA", "UnknownSector")
        assert result["pass"] is False
        assert "no ETF mapped" in result["reason"]

    def test_rs_fail_empty_dataframe(self):
        # ["Close"] succeeds but returns a 0-row DataFrame → data.empty → fail
        empty_close = pd.DataFrame(columns=["NVDA", "XLK"])
        mock_dl = MagicMock()
        mock_dl.__getitem__ = MagicMock(return_value=empty_close)
        with patch("backend.gate.lock_leading.yf.download", return_value=mock_dl):
            result = self._mod._check_relative_strength("NVDA", "Technology")
        assert result["pass"] is False
        assert "unavailable" in result["reason"]

    def test_rs_fail_insufficient_rows(self):
        dl = self._close_df([100], [100])  # only 1 row
        with patch("backend.gate.lock_leading.yf.download", return_value=dl):
            result = self._mod._check_relative_strength("NVDA", "Technology")
        assert result["pass"] is False
        assert "insufficient" in result["reason"]

    # ── Put/Call Ratio ────────────────────────────────────────────────────────

    def _mock_ticker(self, expiries=("2026-04-04",), call_oi=1000, put_oi=500,
                     call_vol=500, put_vol=200):
        chain = MagicMock()
        chain.calls = pd.DataFrame({
            "openInterest": [call_oi], "volume": [call_vol],
            "strike": [150.0], "impliedVolatility": [0.3],
        })
        chain.puts = pd.DataFrame({
            "openInterest": [put_oi], "volume": [put_vol],
            "strike": [150.0], "impliedVolatility": [0.3],
        })
        t = MagicMock()
        t.options = list(expiries)
        t.option_chain.return_value = chain
        return t

    def test_pc_pass_bullish_ratio(self):
        t = self._mock_ticker(call_oi=1000, put_oi=500)  # P/C = 0.5
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_put_call_ratio("NVDA")
        assert result["pass"] is True
        assert result["pc_ratio"] == pytest.approx(0.5)

    def test_pc_fail_ratio_at_threshold(self):
        # P/C = 0.7 exactly → not < 0.7 → fail
        t = self._mock_ticker(call_oi=1000, put_oi=700)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_put_call_ratio("NVDA")
        assert result["pass"] is False

    def test_pc_fail_bearish_ratio(self):
        t = self._mock_ticker(call_oi=500, put_oi=1500)  # P/C = 3.0
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_put_call_ratio("NVDA")
        assert result["pass"] is False

    def test_pc_fail_no_options(self):
        t = MagicMock()
        t.options = []
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_put_call_ratio("NVDA")
        assert result["pass"] is False
        assert "no options data" in result["reason"]

    def test_pc_fail_zero_open_interest(self):
        t = self._mock_ticker(call_oi=0, put_oi=0)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_put_call_ratio("NVDA")
        assert result["pass"] is False
        assert "zero open interest" in result["reason"]

    def test_pc_uses_two_nearest_expiries(self):
        """Verify option_chain is called for up to 2 expiries."""
        t = self._mock_ticker(expiries=("2026-04-04", "2026-04-11", "2026-04-18"),
                              call_oi=600, put_oi=300)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            self._mod._check_put_call_ratio("NVDA")
        assert t.option_chain.call_count == 2

    # ── Unusual Call Volume ───────────────────────────────────────────────────

    def test_uc_pass_2x_call_volume(self):
        t = self._mock_ticker(call_vol=2000, put_vol=1000)  # ratio = 2.0
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_unusual_call_volume("NVDA")
        assert result["pass"] is True
        assert result["cv_ratio"] == pytest.approx(2.0)

    def test_uc_fail_just_below_2x(self):
        t = self._mock_ticker(call_vol=1999, put_vol=1000)  # ratio = 1.999
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_unusual_call_volume("NVDA")
        assert result["pass"] is False

    def test_uc_fail_puts_dominate(self):
        t = self._mock_ticker(call_vol=200, put_vol=1000)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_unusual_call_volume("NVDA")
        assert result["pass"] is False

    def test_uc_fail_no_volume_today(self):
        t = self._mock_ticker(call_vol=0, put_vol=0)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_unusual_call_volume("NVDA")
        assert result["pass"] is False
        assert "no volume" in result["reason"]

    def test_uc_pass_when_zero_put_volume(self):
        # call_vol > 0, put_vol = 0 → ratio = call_vol (very high) → pass
        t = self._mock_ticker(call_vol=1000, put_vol=0)
        with patch("backend.gate.lock_leading.yf.Ticker", return_value=t):
            result = self._mod._check_unusual_call_volume("NVDA")
        assert result["pass"] is True

    # ── Insider Cluster ───────────────────────────────────────────────────────

    def _edgar_response(self, filers: list[str]) -> MagicMock:
        hits = [{"_source": {"entity_name": f}} for f in filers]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"hits": {"hits": hits}}
        return resp

    def test_insider_pass_two_distinct_filers(self):
        resp = self._edgar_response(["John CEO", "Jane CFO"])
        with patch("backend.gate.lock_leading.requests.get", return_value=resp):
            result = self._mod._check_insider_cluster("NVDA")
        assert result["pass"] is True
        assert result["count"] == 2

    def test_insider_fail_one_filer(self):
        resp = self._edgar_response(["John CEO"])
        with patch("backend.gate.lock_leading.requests.get", return_value=resp):
            result = self._mod._check_insider_cluster("NVDA")
        assert result["pass"] is False
        assert result["count"] == 1

    def test_insider_fail_zero_filers(self):
        resp = self._edgar_response([])
        with patch("backend.gate.lock_leading.requests.get", return_value=resp):
            result = self._mod._check_insider_cluster("NVDA")
        assert result["pass"] is False

    def test_insider_deduplicates_same_filer(self):
        # Same person filing multiple Form 4s counts as 1
        resp = self._edgar_response(["John CEO", "John CEO", "John CEO"])
        with patch("backend.gate.lock_leading.requests.get", return_value=resp):
            result = self._mod._check_insider_cluster("NVDA")
        assert result["pass"] is False
        assert result["count"] == 1

    def test_insider_fail_edgar_error(self):
        resp = MagicMock()
        resp.status_code = 503
        with patch("backend.gate.lock_leading.requests.get", return_value=resp):
            result = self._mod._check_insider_cluster("NVDA")
        assert result["pass"] is False
        assert "503" in result["reason"]

    def test_insider_fail_network_timeout(self):
        with patch("backend.gate.lock_leading.requests.get",
                   side_effect=Exception("timeout")):
            result = self._mod.evaluate("NVDA", "Technology")
        # evaluate() catches exceptions per check, insider should be False
        assert result["checks"]["insider_cluster"]["pass"] is False

    # ── evaluate() integration ────────────────────────────────────────────────

    def test_evaluate_passes_with_2_of_4(self):
        dl = self._close_df([100, 105], [100, 102])
        t  = self._mock_ticker(call_oi=1000, put_oi=500, call_vol=2000, put_vol=500)
        edgar_resp = self._edgar_response([])  # insider fails

        with patch("backend.gate.lock_leading.yf.download", return_value=dl), \
             patch("backend.gate.lock_leading.yf.Ticker", return_value=t), \
             patch("backend.gate.lock_leading.requests.get", return_value=edgar_resp):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=2)

        assert result["passed"] is True
        assert result["pass_count"] >= 2
        assert result["checks"]["relative_strength"]["pass"] is True

    def test_evaluate_fails_with_1_of_4(self):
        dl = self._close_df([100, 105], [100, 102])  # RS passes
        t  = self._mock_ticker(call_oi=500, put_oi=1500,   # PC fails
                               call_vol=200, put_vol=1000)  # UC fails
        edgar_resp = self._edgar_response([])  # insider fails

        with patch("backend.gate.lock_leading.yf.download", return_value=dl), \
             patch("backend.gate.lock_leading.yf.Ticker", return_value=t), \
             patch("backend.gate.lock_leading.requests.get", return_value=edgar_resp):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=2)

        assert result["passed"] is False
        assert result["pass_count"] == 1

    def test_evaluate_all_4_pass(self):
        dl = self._close_df([100, 110], [100, 102])
        t  = self._mock_ticker(call_oi=1000, put_oi=400, call_vol=3000, put_vol=500)
        edgar_resp = self._edgar_response(["CEO", "CFO"])

        with patch("backend.gate.lock_leading.yf.download", return_value=dl), \
             patch("backend.gate.lock_leading.yf.Ticker", return_value=t), \
             patch("backend.gate.lock_leading.requests.get", return_value=edgar_resp):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=2)

        assert result["pass_count"] == 4
        assert result["passed"] is True

    def test_evaluate_all_4_fail(self):
        dl = self._close_df([100, 98], [100, 102])  # RS fails
        t  = self._mock_ticker(call_oi=500, put_oi=2000, call_vol=100, put_vol=500)
        edgar_resp = self._edgar_response(["One person"])  # only 1 → fail

        with patch("backend.gate.lock_leading.yf.download", return_value=dl), \
             patch("backend.gate.lock_leading.yf.Ticker", return_value=t), \
             patch("backend.gate.lock_leading.requests.get", return_value=edgar_resp):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=2)

        assert result["passed"] is False
        assert result["pass_count"] == 0

    def test_evaluate_exception_in_check_does_not_raise(self):
        with patch("backend.gate.lock_leading.yf.download",
                   side_effect=RuntimeError("network error")), \
             patch("backend.gate.lock_leading.yf.Ticker",
                   side_effect=RuntimeError("network error")), \
             patch("backend.gate.lock_leading.requests.get",
                   side_effect=RuntimeError("network error")):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=2)

        assert result["passed"] is False
        assert all("error" in c["reason"] for c in result["checks"].values())

    def test_evaluate_custom_min_pass_1(self):
        dl = self._close_df([100, 105], [100, 102])  # RS passes
        t  = self._mock_ticker(call_oi=500, put_oi=2000, call_vol=100, put_vol=500)
        edgar_resp = self._edgar_response([])

        with patch("backend.gate.lock_leading.yf.download", return_value=dl), \
             patch("backend.gate.lock_leading.yf.Ticker", return_value=t), \
             patch("backend.gate.lock_leading.requests.get", return_value=edgar_resp):
            result = self._mod.evaluate("NVDA", "Technology", min_pass=1)

        assert result["passed"] is True  # 1/4 is enough


# ─────────────────────────────────────────────────────────────────────────────
# Lock 3 — Claude primary, GPT-4o fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestLock3Claude:
    from backend.gate import lock3_claude as _mod

    def setup_method(self):
        # Reset module-level clients between tests
        self._mod._anthropic_client = None
        self._mod._openai_client    = None

    def _context(self, **overrides) -> dict:
        ctx = {
            "ticker": "NVDA", "sector": "Technology",
            "sentiment_score": 0.75, "sentiment_volume": "high",
            "sentiment_themes": ["momentum", "AI", "earnings beat"],
            "sentiment_summary": "Strong bullish sentiment across X/Twitter.",
            "leading_pass_count": 3,
            "leading_checks": {
                "relative_strength": True, "put_call_ratio": True,
                "unusual_calls": True, "insider_cluster": False,
            },
            "wallet_balance": 10000, "open_positions": 1, "sector_exposure": {},
            "risk_limits": {
                "starting_balance": 10000, "max_positions": 4,
                "max_sector_exposure": 0.25, "max_position_size": 0.15,
                "daily_loss_cap": 500, "max_drawdown_pct": 0.20,
            },
        }
        ctx.update(overrides)
        return ctx

    def _claude_client(self, decision="BUY", confidence=0.85, position=0.1,
                       reasoning="Strong setup, low portfolio risk."):
        payload = json.dumps({
            "decision":          decision,
            "confidence":        confidence,
            "position_size_pct": position,
            "reasoning":         reasoning,
        })
        client = MagicMock()
        resp   = MagicMock()
        resp.content[0].text = payload
        client.messages.create.return_value = resp
        return client

    def _openai_client(self, decision="BUY", confidence=0.85, position=0.1,
                       reasoning="GPT-4o fallback reasoning."):
        payload = json.dumps({
            "decision":          decision,
            "confidence":        confidence,
            "position_size_pct": position,
            "reasoning":         reasoning,
        })
        client = MagicMock()
        resp   = MagicMock()
        resp.choices[0].message.content = payload
        client.chat.completions.create.return_value = resp
        return client

    # ── Primary (Claude) path ─────────────────────────────────────────────────

    def test_pass_buy_above_confidence_min(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client()):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is True
        assert result["decision"] == "BUY"
        assert result["confidence"] == pytest.approx(0.85)
        assert result["model"] == self._mod._CLAUDE_MODEL

    def test_fail_buy_below_confidence_min(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(confidence=0.5)):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False
        assert "confidence" in result["reason"]

    def test_fail_hold_decision(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(decision="HOLD")):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False
        assert result["decision"] == "HOLD"

    def test_fail_sell_decision(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(decision="SELL")):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False

    def test_confidence_exactly_at_min_passes(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(confidence=0.6)):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is True

    def test_confidence_just_below_min_fails(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(confidence=0.5999)):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False

    def test_lowercase_decision_normalised(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(decision="buy")):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["decision"] == "BUY"
        assert result["passed"] is True

    def test_position_size_returned(self):
        with patch.object(self._mod, "_get_anthropic", return_value=self._claude_client(position=0.15)):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["position_size_pct"] == pytest.approx(0.15)

    def test_missing_decision_field_defaults_hold(self):
        client = MagicMock()
        resp   = MagicMock()
        resp.content[0].text = json.dumps({"confidence": 0.9, "position_size_pct": 0.1,
                                           "reasoning": "Good setup"})
        client.messages.create.return_value = resp
        with patch.object(self._mod, "_get_anthropic", return_value=client):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["decision"] == "HOLD"
        assert result["passed"] is False

    def test_missing_confidence_defaults_zero_fails(self):
        client = MagicMock()
        resp   = MagicMock()
        resp.content[0].text = json.dumps({"decision": "BUY", "position_size_pct": 0.1,
                                           "reasoning": "Good"})
        client.messages.create.return_value = resp
        with patch.object(self._mod, "_get_anthropic", return_value=client):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["confidence"] == 0.0
        assert result["passed"] is False

    # ── Fallback (GPT-4o) path ────────────────────────────────────────────────

    def test_claude_api_error_falls_back_to_openai(self):
        claude = MagicMock()
        claude.messages.create.side_effect = Exception("API timeout")
        with (patch.object(self._mod, "_get_anthropic", return_value=claude),
              patch.object(self._mod, "_get_openai",    return_value=self._openai_client())):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is True
        assert result["model"] == self._mod._GPT_MODEL

    def test_claude_json_error_falls_back_to_openai(self):
        claude = MagicMock()
        resp   = MagicMock()
        resp.content[0].text = "not valid json {"
        claude.messages.create.return_value = resp
        with (patch.object(self._mod, "_get_anthropic", return_value=claude),
              patch.object(self._mod, "_get_openai",    return_value=self._openai_client())):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is True
        assert result["model"] == self._mod._GPT_MODEL

    def test_no_anthropic_key_falls_back_to_openai(self):
        with (patch("backend.gate.lock3_claude.ANTHROPIC_API_KEY", ""),
              patch.object(self._mod, "_get_openai", return_value=self._openai_client())):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is True
        assert result["model"] == self._mod._GPT_MODEL

    def test_both_providers_fail_closes(self):
        claude = MagicMock()
        claude.messages.create.side_effect = Exception("Claude down")
        openai = MagicMock()
        openai.chat.completions.create.side_effect = Exception("OpenAI down")
        with (patch.object(self._mod, "_get_anthropic", return_value=claude),
              patch.object(self._mod, "_get_openai",    return_value=openai)):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False
        assert result["reason"] == "both_providers_failed"

    def test_no_keys_at_all_fails_closed(self):
        with (patch("backend.gate.lock3_claude.ANTHROPIC_API_KEY", ""),
              patch("backend.gate.lock3_claude.OPENAI_API_KEY",    "")):
            result = self._mod.evaluate(self._context(), confidence_min=0.6)
        assert result["passed"] is False
        assert result["reason"] == "both_providers_failed"
