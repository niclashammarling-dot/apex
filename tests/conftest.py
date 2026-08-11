"""
Test configuration — redirects all DB access to a temporary file, and blocks
every test from ever dispatching a real alert.

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
