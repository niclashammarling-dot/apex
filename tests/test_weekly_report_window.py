"""
Pins the half-open window semantics added to weekly_report.py's six since/until
functions (2026-08-25). Before this fix, every one of them took `since` with no
upper bound — correct only because the weekly cron fires once, right at week
close, so "now" was an implicit correct ceiling. Any manual or recovery re-run
of a past week silently absorbed everything after it, including into the
recalibration trigger at send_weekly_report():802 (live threshold changes, not
just a report number).

This test pins the boundary itself: a row timestamped at exactly `since` must
land in that week; a row timestamped at exactly `until` must land in the next
week, not both and not neither. `_live_stats` stands in for all six — they
share the identical `>= since AND < until` shape (verified by inspection,
not re-tested six times here).
"""
from backend.db import close_live_trade, init_db, insert_live_trade
from backend.weekly_report import _live_stats

init_db()

SINCE = "2026-08-24T00:00:00+00:00"   # a Monday 00:00 UTC
UNTIL = "2026-08-31T00:00:00+00:00"   # SINCE + 7 days, next week's SINCE


def _insert_closed(tag: str, exited_at: str, pnl: float):
    trade_id = insert_live_trade({
        "timestamp": "2026-08-20T00:00:00+00:00", "ticker": f"W{tag}",
        "sector": "Technology", "alpaca_order_id": f"win-order-{tag}",
        "entry_price": 100.0, "qty": 1.0, "notional": 100.0,
        "tp_price": 110.0, "sl_price": 90.0,
    })
    close_live_trade(
        trade_id=trade_id, exit_price=100.0 + pnl, pnl=pnl,
        outcome="WIN", exit_reason="TP", exited_at=exited_at,
    )
    return trade_id


class TestWeeklyReportWindowBoundary:
    def test_row_at_since_boundary_included_in_that_week(self):
        before = _live_stats(SINCE, UNTIL)["realized_pnl"]
        _insert_closed("since-edge", SINCE, 7.0)
        after = _live_stats(SINCE, UNTIL)["realized_pnl"]
        assert round(after - before, 2) == 7.0

    def test_row_at_until_boundary_excluded_from_that_week(self):
        before = _live_stats(SINCE, UNTIL)["realized_pnl"]
        _insert_closed("until-edge", UNTIL, 9.0)
        after = _live_stats(SINCE, UNTIL)["realized_pnl"]
        assert round(after - before, 2) == 0.0

    def test_row_at_until_boundary_lands_in_next_week_not_both(self):
        # Same row as the previous test's insert (test order within a class
        # is source order in pytest, so it already exists) — but assert
        # independently via a fresh insert so this test stands alone too.
        next_until = "2026-09-07T00:00:00+00:00"
        before = _live_stats(UNTIL, next_until)["realized_pnl"]
        _insert_closed("until-edge-next", UNTIL, 11.0)
        after = _live_stats(UNTIL, next_until)["realized_pnl"]
        assert round(after - before, 2) == 11.0

    def test_boundary_row_never_double_counted_across_adjacent_weeks(self):
        next_until = "2026-09-07T00:00:00+00:00"
        before_this = _live_stats(SINCE, UNTIL)["realized_pnl"]
        before_next = _live_stats(UNTIL, next_until)["realized_pnl"]
        _insert_closed("no-double-count", UNTIL, 13.0)
        after_this = _live_stats(SINCE, UNTIL)["realized_pnl"]
        after_next = _live_stats(UNTIL, next_until)["realized_pnl"]
        # The row's $13 must land in exactly one window — next week's, per
        # the half-open boundary — not both and not neither.
        assert round(after_this - before_this, 2) == 0.0
        assert round(after_next - before_next, 2) == 13.0
