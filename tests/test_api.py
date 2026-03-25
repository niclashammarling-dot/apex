"""
Unit tests for API endpoint validation and rate limiting.
Run: cd ~/apex && .venv/bin/python tests/test_api.py
"""
import sys
import time
from collections import defaultdict
from unittest.mock import patch

results = []

def check(name, condition, detail=""):
    verdict = "PASS" if condition else "FAIL"
    results.append((name, verdict))
    icon = "✓" if condition else "✗"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {icon}  {name}{suffix}")


# ── TEST 1: Ticker validation ──────────────────────────────────────────────────
print("\nTEST 1: Ticker validation")

import re
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

# Router normalizes to uppercase before validation, so lowercase is accepted.
valid = ["AAPL", "GOOGL", "T", "BRK", "MSFT", "aapl", "msft"]
# These remain invalid even after .upper().strip()
invalid = ["", "TOOLONG", "AA PL", "A@PL", "123", "A" * 6, "'; DROP TABLE--"]

for t in valid:
    check(f"valid ticker accepted: '{t}'", bool(_TICKER_RE.match(t.upper().strip())))

for t in invalid:
    normalized = t.upper().strip()
    check(f"invalid ticker rejected: '{t}'", not bool(_TICKER_RE.match(normalized)))


# ── TEST 2: BacktestRequest pydantic validation ────────────────────────────────
print("\nTEST 2: BacktestRequest pydantic validation")

from pydantic import ValidationError
from backend.routers.signals_router import BacktestRequest

# Valid request
try:
    req = BacktestRequest(start_date="2024-01-01", end_date="2024-12-31")
    check("valid request accepted",          True)
    check("default balance = $10,000",       req.initial_balance == 10_000.0)
except ValidationError as e:
    check("valid request accepted",          False, str(e))

# Negative balance
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", initial_balance=50.0)
    check("balance < $100 rejected",         False, "should have raised")
except ValidationError:
    check("balance < $100 rejected",         True)

# TP out of range
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", take_profit_pct=2.0)
    check("take_profit_pct > 1.0 rejected",  False, "should have raised")
except ValidationError:
    check("take_profit_pct > 1.0 rejected",  True)

# SL out of range
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", stop_loss_pct=0.0)
    check("stop_loss_pct = 0 rejected",      False, "should have raised")
except ValidationError:
    check("stop_loss_pct = 0 rejected",      True)

# Negative time_stop_days
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", time_stop_days=-1)
    check("time_stop_days < 1 rejected",     False, "should have raised")
except ValidationError:
    check("time_stop_days < 1 rejected",     True)

# lock1_threshold out of range
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", lock1_threshold=1.5)
    check("lock1_threshold > 1.0 rejected",  False, "should have raised")
except ValidationError:
    check("lock1_threshold > 1.0 rejected",  True)

# max_entries_per_day < 1
try:
    BacktestRequest(start_date="2024-01-01", end_date="2024-12-31", max_entries_per_day=0)
    check("max_entries_per_day = 0 rejected", False, "should have raised")
except ValidationError:
    check("max_entries_per_day = 0 rejected", True)

# Valid overrides
try:
    req = BacktestRequest(
        start_date="2024-01-01", end_date="2024-12-31",
        take_profit_pct=0.08, stop_loss_pct=0.03,
        time_stop_days=10, lock1_threshold=0.72,
        max_entries_per_day=2, initial_balance=50_000.0,
    )
    check("valid override request accepted", True)
except ValidationError as e:
    check("valid override request accepted", False, str(e))


# ── TEST 3: Rate limiter ───────────────────────────────────────────────────────
print("\nTEST 3: Rate limiter")

from fastapi import HTTPException

# Import rate limiter internals
from backend.routers.signals_router import _rate_store, _rate_check, _RATE_LIMIT, _RATE_WINDOW

# Clear any state from previous test runs
_rate_store.clear()

# Should allow _RATE_LIMIT calls within the window
allowed = 0
for _ in range(_RATE_LIMIT):
    try:
        _rate_check("__test__")
        allowed += 1
    except HTTPException:
        break

check(f"allows {_RATE_LIMIT} calls within window", allowed == _RATE_LIMIT,
      f"allowed={allowed}")

# The next call should be rejected
try:
    _rate_check("__test__")
    check(f"({_RATE_LIMIT+1}th) call rejected", False, "should have raised 429")
except HTTPException as e:
    check(f"({_RATE_LIMIT+1}th) call rejected", e.status_code == 429,
          f"status={e.status_code}")

# Different key is independent
try:
    _rate_check("__test_other__")
    check("different key is independent",   True)
except HTTPException:
    check("different key is independent",   False)

# After window expires, calls are allowed again
_rate_store["__test_expire__"] = [time.time() - _RATE_WINDOW - 1]  # artificially expired
try:
    _rate_check("__test_expire__")
    check("expired entries don't count",    True)
except HTTPException:
    check("expired entries don't count",    False)

_rate_store.clear()


# ── TEST 4: Lock 2 circuit breaker ────────────────────────────────────────────
print("\nTEST 4: Lock 2 circuit breaker")

import backend.gate.lock2_sentiment as l2_mod

# Reset state
l2_mod._cb_failures   = 0
l2_mod._cb_open_until = 0.0

check("circuit starts closed",  not l2_mod._circuit_open())

# Simulate failures
for _ in range(l2_mod._CB_THRESHOLD):
    l2_mod._record_failure()

check("circuit opens after threshold failures", l2_mod._circuit_open())

# Success resets
l2_mod._record_success()
check("success resets circuit",  not l2_mod._circuit_open())
check("failure counter reset",   l2_mod._cb_failures == 0)

# Reset
l2_mod._cb_failures   = 0
l2_mod._cb_open_until = 0.0


# ── TEST 5: Lock 2 cache cleanup ──────────────────────────────────────────────
print("\nTEST 5: Lock 2 cache cleanup")

l2_mod._cache.clear()

# Add one expired and one live entry
l2_mod._cache["EXPIRED_TICKER"] = {"expires_at": time.time() - 10, "result": {}}
l2_mod._cache["LIVE_TICKER"]    = {"expires_at": time.time() + 3600, "result": {}}

l2_mod._cache_cleanup()

check("expired entry removed",   "EXPIRED_TICKER" not in l2_mod._cache)
check("live entry preserved",    "LIVE_TICKER"    in  l2_mod._cache)

l2_mod._cache.clear()


# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("─" * 45)
passed = sum(1 for _, v in results if v == "PASS")
failed = sum(1 for _, v in results if v == "FAIL")
print(f"Results: {passed} passed, {failed} failed  ({len(results)} total)")
if failed:
    print("Failed:")
    for name, v in results:
        if v == "FAIL":
            print(f"  ✗ {name}")
    sys.exit(1)
