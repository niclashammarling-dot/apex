"""Finding 5 (2026-10-31): the conftest Yahoo guard refuses a real yfinance call and leaves other hosts alone.
Matched by class name: pytest loads conftest as its own module, so an import of tests.conftest is another class."""
import pytest


def _refused(e) -> bool:
    return type(e.value).__name__ == "YahooReachedInTest"


def test_a_yfinance_history_call_is_refused(_never_reach_yahoo):
    import yfinance as yf
    with pytest.raises(BaseException) as e:
        yf.Ticker("SPY").history(period="5d", raise_errors=True)
    assert _refused(e) and _never_reach_yahoo                    # recorded, so a swallowed refusal still fails
    _never_reach_yahoo.clear()


def test_a_direct_yahoo_request_is_refused(_never_reach_yahoo):
    import curl_cffi.requests as cr
    with pytest.raises(BaseException) as e:
        cr.Session().get("https://query1.finance.yahoo.com/v8/finance/chart/SPY")
    assert _refused(e)
    _never_reach_yahoo.clear()


def test_other_hosts_pass_through_to_the_real_request(_never_reach_yahoo):
    import curl_cffi.requests as cr
    with pytest.raises(Exception) as e:
        cr.Session().get("http://127.0.0.1:9/", timeout=2)       # nothing listens on port 9: refused, not guarded
    assert not _refused(e) and not _never_reach_yahoo
