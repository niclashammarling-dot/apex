"""
Pins the exit_confidence filter across all four consumers (2026-08-18).
Zero of this had test coverage before merge review flagged it: four
separate sites reading the same column, no error and no crash if any one
disagreed with the others — exactly the "plausible numbers that are quietly
wrong" class this week's whole investigation was about.

Inserts one confirmed row (a real trade, closed normally — exit_confidence
defaults to 'confirmed', never touched by close_live_trade) and one
unverified row (the RECONCILIATION shape: same close_live_trade path, then
backfilled to 'unverified' the way the real 2026-07-07 batch was). Asserts
each site's aggregate moves by exactly the confirmed row's contribution,
not both — delta-based rather than absolute-count, since these DB-level
functions aggregate the whole table and this test module's own fixture
runs multiple times against the same test-session DB.
"""
from unittest.mock import patch

import pytest

from backend.db import (
    close_live_trade,
    get_db,
    get_live_equity_curve,
    init_db,
    insert_live_trade,
)

init_db()


def _insert_confirmed_and_unverified(tag: str):
    """tag disambiguates ticker names across test invocations within one run."""
    confirmed_id = insert_live_trade({
        "timestamp": "2026-08-01T00:00:00+00:00", "ticker": f"C{tag}",
        "sector": "Technology", "alpaca_order_id": f"conf-order-{tag}",
        "entry_price": 100.0, "qty": 1.0, "notional": 100.0,
        "tp_price": 106.0, "sl_price": 94.0,
    })
    close_live_trade(
        trade_id=confirmed_id, exit_price=106.0, pnl=6.0,
        outcome="WIN", exit_reason="TP",
        exited_at="2026-08-02T00:00:00+00:00",
    )

    unverified_id = insert_live_trade({
        "timestamp": "2026-08-01T00:00:00+00:00", "ticker": f"U{tag}",
        "sector": "Technology", "alpaca_order_id": f"unver-order-{tag}",
        "entry_price": 200.0, "qty": 1.0, "notional": 200.0,
        "tp_price": 212.0, "sl_price": 188.0,
    })
    close_live_trade(
        trade_id=unverified_id, exit_price=212.0, pnl=12.0,
        outcome="WIN", exit_reason="RECONCILIATION",
        exited_at="2026-08-03T00:00:00+00:00",
    )
    # Real backfill mechanism, not a hand-set flag — same UPDATE init_db() runs.
    conn = get_db()
    try:
        conn.execute(
            "UPDATE live_trades SET exit_confidence = 'unverified' "
            "WHERE exit_reason = 'RECONCILIATION' AND exit_confidence != 'unverified'"
        )
        conn.commit()
    finally:
        conn.close()
    return confirmed_id, unverified_id


class TestExitConfidenceFiltering:
    def test_new_row_defaults_confirmed(self):
        from backend.db import get_all_live_trades
        confirmed_id, unverified_id = _insert_confirmed_and_unverified("A")
        rows = {r["id"]: r for r in get_all_live_trades()}
        assert rows[confirmed_id]["exit_confidence"] == "confirmed"
        assert rows[unverified_id]["exit_confidence"] == "unverified"

    def test_get_live_equity_curve_excludes_unverified(self):
        before = get_live_equity_curve()[-2]["balance"]  # last non-mtm point pre-insert
        _insert_confirmed_and_unverified("B")
        points = get_live_equity_curve()
        closed_points = [p for p in points if p["ts"] != "Start" and not p.get("mtm")]
        after = closed_points[-1]["balance"]
        # Must move by exactly the confirmed row's $6, not $6+$12=$18.
        assert round(after - before, 2) == 6.0

    def test_get_live_equity_curve_excludes_unverified_with_since(self):
        before_pts = get_live_equity_curve(since="2026-01-01")
        before = [p for p in before_pts if p["ts"] != "Start" and not p.get("mtm")][-1]["balance"]
        _insert_confirmed_and_unverified("C")
        after_pts = get_live_equity_curve(since="2026-01-01")
        after = [p for p in after_pts if p["ts"] != "Start" and not p.get("mtm")][-1]["balance"]
        assert round(after - before, 2) == 6.0

    def test_live_compare_excludes_unverified(self):
        from backend.routers.live_router import compare_performance

        fake_account = {"equity": "10000", "day_pnl": 0.0, "day_pnl_pct": 0.0}
        with patch("backend.routers.live_router.LIVE_ENABLED", True), \
             patch("backend.brokers.alpaca.get_account", return_value=fake_account):
            before = compare_performance()["live"]["realized_pnl"]
            _insert_confirmed_and_unverified("D")
            after = compare_performance()["live"]["realized_pnl"]
        assert round(after - before, 2) == 6.0

    def test_weekly_report_excludes_unverified(self):
        from backend.weekly_report import _live_stats

        before = _live_stats(since="2026-01-01", until="2099-01-01")["realized_pnl"]
        _insert_confirmed_and_unverified("E")
        after = _live_stats(since="2026-01-01", until="2099-01-01")["realized_pnl"]
        assert round(after - before, 2) == 6.0
