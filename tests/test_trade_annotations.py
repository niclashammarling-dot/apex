"""
trade_annotations is read by the outcome statistics (2026-10-10). Built 2026-09-16
to mark demo AMD (trades.id 61, entered on the contaminated posterior) with
"Filter on this tag in any outcome analysis" — but nothing read it, so the trade
counted in every win rate. Rows created here are deleted: the test DB is shared.
"""
from backend.db import get_db, init_db, live_trade_stats, not_annotated

init_db()


def _annotate(conn, table, tid):
    conn.execute("INSERT INTO trade_annotations (trade_table, trade_id, tag, note, created_at) "
                 "VALUES (?, ?, 'superseded_posterior', 'test', '2031-03-07')", (table, tid))


def test_predicate_names_the_table_and_tag():
    p = not_annotated("live_trades", "t")
    assert "t.id NOT IN" in p and "'live_trades'" in p and "'superseded_posterior'" in p


def test_annotated_live_trade_leaves_outcome_stats_not_the_table():
    conn = get_db()
    ids = []
    try:
        for outcome, pnl in (("WIN", 10.0), ("LOSS", -5.0)):
            cur = conn.execute("INSERT INTO live_trades (timestamp, ticker, sector, alpaca_order_id, entry_price, qty, "
                               "notional, tp_price, sl_price, outcome, pnl, exited_at) VALUES "
                               "('2031-03-07T15:00:00+00:00', 'ANNX', 'Technology', 'o', 10, 1, 10, 11, 9, ?, ?, "
                               "'2031-03-08T15:00:00+00:00')", (outcome, pnl))
            ids.append(cur.lastrowid)
        conn.commit()
        before = live_trade_stats(since="2031-03-01")["overall"]
        _annotate(conn, "live_trades", ids[0])
        conn.commit()
        after = live_trade_stats(since="2031-03-01")["overall"]
        assert before["trades"] - after["trades"] == 1
        assert round(before["realized"] - after["realized"], 2) == 10.0
    finally:
        conn.execute("DELETE FROM trade_annotations WHERE trade_table = 'live_trades' AND trade_id IN (?, ?)", ids)
        conn.execute("DELETE FROM live_trades WHERE id IN (?, ?)", ids)
        conn.commit()
        conn.close()
