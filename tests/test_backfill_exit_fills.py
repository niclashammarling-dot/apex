"""
scripts/backfill_entry_fills.py --exits (2026-10-31): a closed row booked at one slice of a multi-slice sell
order takes the order's quantity-weighted fill. The TER shape: order 6025e192 filled 395.94 then 391.30;
row 70 was booked at 395.94 with the first slice's time.
"""
from backend.db import correct_exit_fill, get_db, init_db
from scripts.backfill_entry_fills import plan_exit_corrections

TER = {"id": 70, "ticker": "TER", "qty": 2.0, "entry_price": 377.95, "exit_price": 395.94,
       "exited_at": "2026-09-23T13:31:03.302004+00:00", "pnl": 35.98, "outcome": "WIN"}
SLICES = [{"ticker": "TER", "side": "sell", "qty": 1.0, "price": 391.30, "order_id": "6025e192",
           "filled_at": "2026-09-23T13:32:13.745827Z"},
          {"ticker": "TER", "side": "sell", "qty": 1.0, "price": 395.94, "order_id": "6025e192",
           "filled_at": "2026-09-23T13:31:03.302004Z"}]


def test_the_ter_row_takes_the_weighted_fill_and_the_last_slice_time():
    plan, skipped = plan_exit_corrections([TER], SLICES)
    assert skipped == [] and len(plan) == 1
    p = plan[0]
    assert (p["fill"], p["filled_at"], p["slices"]) == (393.62, "2026-09-23T13:32:13.745827+00:00", 2)
    assert p["new_pnl"] == round((393.62 - 377.95) * 2, 2)


def test_a_row_already_at_its_weighted_fill_is_not_planned():
    plan, skipped = plan_exit_corrections([{**TER, "exit_price": 393.62}], SLICES)
    assert plan == [] and skipped == []


def test_fills_short_of_the_row_qty_or_no_matching_order_are_skipped():
    plan, skipped = plan_exit_corrections([{**TER, "qty": 3.0}], SLICES)
    assert plan == [] and "sum to 2" in skipped[0]["reason"]
    plan, skipped = plan_exit_corrections([{**TER, "exited_at": "2026-09-24T10:00:00+00:00"}], SLICES)
    assert plan == [] and "0 sell orders" in skipped[0]["reason"]


def test_the_msft_shape_stamped_before_its_slices_at_neither_price_is_matched():
    row = {**TER, "id": 47, "ticker": "MSFT", "entry_price": 505.06, "exit_price": 501.57000732421875,
           "exited_at": "2026-09-14T14:04:37.916590+00:00", "pnl": -6.98, "outcome": "LOSS"}
    fills = [{"ticker": "MSFT", "side": "sell", "qty": 1.0, "price": p, "order_id": "8405dedf", "filled_at": t}
             for p, t in ((501.45, "2026-09-14T14:04:38.114018Z"), (501.49, "2026-09-14T14:04:38.952841Z"))]
    [p] = plan_exit_corrections([row], fills)[0]
    assert (p["fill"], p["new_pnl"]) == (501.47, round((501.47 - 505.06) * 2, 2))


def test_two_orders_inside_the_window_are_ambiguous():
    other = [{**f, "order_id": "other"} for f in SLICES]
    plan, skipped = plan_exit_corrections([TER], SLICES + other)
    assert plan == [] and "2 sell orders" in skipped[0]["reason"]


def test_correct_exit_fill_is_guarded_on_the_old_price_and_recomputes_pnl():
    init_db()
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO live_trades (alpaca_order_id, ticker, sector, timestamp, entry_price, qty, exit_price, "
                           "exited_at, pnl, outcome) VALUES ('ord-ter', 'TER', 'Semiconductors', '2026-09-22T13:33:24+00:00', "
                           "377.95, 2.0, 395.94, '2026-09-23T13:31:03.302004+00:00', 35.98, 'WIN')")
        rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    assert correct_exit_fill(rid, 999.0, 393.62, "2026-09-23T13:32:13.745827Z") == 0   # stale plan: untouched
    assert correct_exit_fill(rid, 395.94, 393.62, "2026-09-23T13:32:13.745827Z") == 1
    conn = get_db()
    try:
        row = conn.execute("SELECT exit_price, exited_at, pnl, outcome FROM live_trades WHERE id = ?",
                           (rid,)).fetchone()
    finally:
        conn.close()
    assert tuple(row) == (393.62, "2026-09-23T13:32:13.745827Z", 31.34, "WIN")
    # The test DB is shared across files: leave no consumed TER exit behind for find_exit_fill's invariant 2.
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_trades WHERE id = ?", (rid,))
        conn.commit()
    finally:
        conn.close()
