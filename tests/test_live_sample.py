"""The canonical live sample (ruling 2026-10-20): one definition in db.py, read by the
dashboard stats, the weekly report and every analysis. Rows created here are deleted:
the test DB is shared."""
from backend import weekly_report as wr
from backend.db import LIVE_SAMPLE, get_db, init_db, live_sample_rows, live_trade_stats

init_db()

_ROWS = (  # (entered, outcome, pnl, exit_confidence)
    ("2031-04-01T15:00:00+00:00", "WIN", 10.0, "confirmed"),
    ("2031-04-01T15:00:00+00:00", "LOSS", -4.0, None),            # NULL counts as confirmed
    ("2031-04-01T15:00:00+00:00", "WIN", 99.0, "unverified"),     # never corroborated: out
    ("2031-04-01T15:00:00+00:00", "OPEN", None, "confirmed"),     # not closed: out
    ("2031-03-20T15:00:00+00:00", "WIN", 50.0, "confirmed"),      # before since: out
)


def _insert(conn):
    ids = []
    for ts, outcome, pnl, conf in _ROWS:
        cur = conn.execute("INSERT INTO live_trades (timestamp, ticker, sector, alpaca_order_id, entry_price, qty, "
                           "notional, tp_price, sl_price, outcome, pnl, exited_at, exit_confidence) VALUES "
                           "(?, 'SMPL', 'Technology', 'o', 10, 1, 10, 11, 9, ?, ?, '2031-04-02T15:00:00+00:00', ?)",
                           (ts, outcome, pnl, conf))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_stats_weekly_and_rows_read_the_same_sample():
    conn = get_db()
    ids = _insert(conn)
    try:
        rows = live_sample_rows(since="2031-03-25", until="2031-05-01")
        assert sorted(r["pnl"] for r in rows) == [-4.0, 10.0]
        st = live_trade_stats(since="2031-03-25")
        assert st["sample"] == LIVE_SAMPLE
        assert (st["overall"]["trades"], st["overall"]["realized"]) == (2, 6.0)
        wk = wr._closed_summary("live", "2031-04-02", "2031-04-03")   # exit window; the 03-20 entry exits here too
        assert (wk["closed_total"], wk["realized_pnl"]) == (3, 56.0)
    finally:
        conn.execute(f"DELETE FROM live_trades WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        conn.close()


def test_until_bounds_the_entry_window():
    conn = get_db()
    ids = _insert(conn)
    try:
        assert [r["pnl"] for r in live_sample_rows(since="2031-03-01", until="2031-03-25")] == [50.0]
    finally:
        conn.execute(f"DELETE FROM live_trades WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        conn.close()
