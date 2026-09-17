"""
Test configuration — redirects all DB access and every known runtime
data/ file read/write to a temp directory, and blocks every test from
ever dispatching a real alert.

Must use pytest_configure (not a fixture) so the path is set before
test modules are imported. test_wallet.py calls init_db() and imports
DB_PATH at module level during collection, both of which must see the
temp path rather than the production data/apex.db.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def pytest_configure(config):
    """Redirect DB to a temp file before any test modules are imported."""
    import backend.db as db_module
    tmp = tempfile.mkdtemp(prefix="apex_test_")
    db_module.DB_PATH = Path(tmp) / "apex_test.db"
    # Give the temp DB its schema here, not per test file. Found 2026-09-16:
    # two test_gate_runners.py tests failed on `no such table: alert_latches`
    # when the file ran alone (-k, single file) and passed in full-suite order
    # because an earlier file's init_db() had created the table — so a green
    # full suite was partly an ordering artifact. init_db() is idempotent
    # (CREATE IF NOT EXISTS; ADD COLUMN failures swallowed), so the per-file
    # calls that remain are harmless.
    db_module.init_db()

    # Same reasoning, one level later: regime_bayes.py's two JSON/JSONL
    # runtime outputs were git-tracked (not gitignored like apex.db), which
    # let test runs silently write real-shaped content to production paths
    # under data/ — indistinguishable in `git status` from genuine live
    # accumulation, and the reason regime_signal_trace.jsonl sat pinned at
    # its 2026-07-16 committed content through 5+ weeks of live daily
    # writes (each session's routine "clean up test pollution" reflex
    # erasing it before anyone diffed first). Untracking+gitignoring closes
    # the destroy-by-git-checkout path; this closes the write-in-the-first-
    # place path. See 2026-08-16 CHECKS.md trace-gap entry.
    import backend.regime.regime_bayes as regime_module
    regime_module.RESULT_CACHE_PATH = Path(tmp) / "regime_result_cache.json"
    regime_module.SIGNAL_TRACE_PATH = Path(tmp) / "regime_signal_trace.jsonl"

    # Same pattern, remainder of the class (2026-09-10): confirmed directly
    # this session — three separate test runs against the un-redirected
    # data/ paths below wrote real content to tracked, live-consumed files
    # (bayesian_multiplier_stats.json feeds live sizing), caught only by
    # `git status` after the fact, not by anything in the test harness.
    # Each read/write site references its module attribute at call time
    # (confirmed by inspection — not a function-default captured at import),
    # same as RESULT_CACHE_PATH/SIGNAL_TRACE_PATH above, so reassigning here
    # redirects every call site with no code change in the modules themselves.
    import backend.gate.gate_runner as gate_runner_module
    gate_runner_module._MULTIPLIER_STATS_PATH = Path(tmp) / "bayesian_multiplier_stats.json"

    import backend.regime.ipo_sentiment as ipo_sentiment_module
    ipo_sentiment_module.CACHE_PATH   = Path(tmp) / "ipo_sentiment_cache.json"
    ipo_sentiment_module.HISTORY_PATH = Path(tmp) / "ipo_sentiment_history.json"

    import backend.weekly_report as weekly_report_module
    weekly_report_module._SENT_MARKER = Path(tmp) / "weekly_report_sent.txt"

    import backend.ticker_threshold_calibration as calibration_module
    calibration_module._CALIBRATION_MARKER = Path(tmp) / "calibration_done.txt"


@pytest.fixture(autouse=True, scope="session")
def _never_send_real_alerts():
    """
    Blanket safety net at the transport layer, not the call-site layer.
    DB isolation (above) has existed since this file's creation; alert
    isolation did not — every test that exercises a code path ending in
    backend.alerts._dispatch() relied on that specific test mocking the
    specific alert_*() function it expected to be called, one level above
    the transport. That's fine for tests written with the alert in mind; it
    is silently absent for any test that happens to hit an alert-dispatching
    branch it wasn't written to anticipate.

    Confirmed 2026-08-11: adding alert_data_quality_divergence() to
    gate_runner_live's daily-loss-cap path caused two pre-existing
    test_gate_runners.py tests (test_daily_loss_cap_exceeded_returns_empty,
    test_daily_loss_at_cap_returns_empty) — written before that function
    existed, with no reason to mock it — to fall through to a real,
    unmocked alert_data_quality_divergence() call and dispatch real SMTP
    email on every full-suite run. Four identical "Data-Quality Halt"
    emails reached Niclas's real inbox from two full-suite test runs.

    Patched at the transport functions (_send_slack/_send_email), not at
    _dispatch, so _dispatch's real routing/logging logic still runs and is
    still exercised by tests that assert on it — only the network I/O is
    replaced.
    """
    with patch("backend.alerts._send_slack", return_value=False), \
         patch("backend.alerts._send_email", return_value=False):
        yield
