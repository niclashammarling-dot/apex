"""
Pins the weekly report's prior-week references (order (b), "derive, don't
store", 2026-10-15; raw note 2026-10-03-apex-weekly-report-read-against-db.md):

- One closed-trade population per book (_closed_summary), read by the totals,
  the sector rows and the references. Demo and live both drop annotated
  non-samples; live also drops the unverified RECONCILIATION batch.
- _reference covers exactly the REFERENCE_WEEKS full weeks before `since`,
  never this week, never a week before REFERENCE_START.
- _ref_book pools lock and win rates with their n, and gives the median and
  range of weekly counts.
- The report shows this week next to the reference, in HTML and plain text.

All rows sit in 2033, a year no other test writes.
"""
from datetime import datetime, timedelta

import backend.config as config_module
import backend.weekly_report as wr
from backend.db import close_live_trade, close_trade, get_db, init_db, insert_live_trade, insert_trade

init_db()


def _gate_rows(table: str, at: str, stops: dict[str, int]):
    """Rows by stop_code (lock labels phase 2); gate_decision is the owner's lock code."""
    from backend.gate import lock_codes
    conn = get_db()
    try:
        for stop, n in stops.items():
            code = stop if stop == "TRADE_EXECUTED" else lock_codes.lock_code(lock_codes.parse_stop_code(stop)[0])
            for i in range(n):
                conn.execute(
                    f"INSERT INTO {table} (timestamp, ticker, sector, gate_decision, stop_code) VALUES (?, ?, ?, ?, ?)",
                    (at, f"R{i}", "Technology", code, stop),
                )
        conn.commit()
    finally:
        conn.close()


def _closed_demo(tag: str, sector: str, exited_at: str, pnl: float, annotate: bool = False) -> int:
    tid = insert_trade({
        "timestamp": exited_at, "ticker": f"D{tag}", "sector": sector, "action": "BUY",
        "price": 100.0, "shares": 1.0, "amount": 100.0, "signal_score": 0.7,
        "sentiment_score": None, "claude_confidence": None, "claude_reasoning": None,
        "outcome": "OPEN", "pnl": None,
    })
    close_trade(tid, 100.0 + pnl, pnl, "WIN" if pnl > 0 else "LOSS", "TP", exited_at)
    if annotate:
        conn = get_db()
        conn.execute("INSERT INTO trade_annotations (trade_table, trade_id, tag, note, created_at) "
                     "VALUES ('trades', ?, 'superseded_posterior', 'test', ?)", (tid, exited_at))
        conn.commit()
        conn.close()
    return tid


def _closed_live(tag: str, sector: str, exited_at: str, pnl: float, confidence: str = "confirmed"):
    tid = insert_live_trade({
        "timestamp": exited_at, "ticker": f"L{tag}", "sector": sector,
        "alpaca_order_id": f"ref-{tag}", "entry_price": 100.0, "qty": 1.0,
        "notional": 100.0, "tp_price": 110.0, "sl_price": 90.0,
    })
    close_live_trade(trade_id=tid, exit_price=100.0 + pnl, pnl=pnl,
                     outcome="WIN" if pnl > 0 else "LOSS", exit_reason="TP", exited_at=exited_at)
    if confidence != "confirmed":
        conn = get_db()
        conn.execute("UPDATE live_trades SET exit_confidence = ? WHERE id = ?", (confidence, tid))
        conn.commit()
        conn.close()


def _monday(iso: str, weeks_back: int = 0) -> str:
    return (datetime.fromisoformat(iso) - timedelta(weeks=weeks_back)).isoformat()


class TestClosedSummary:
    def test_demo_drops_annotated_live_drops_unverified(self):
        since, until = "2033-01-03T00:00:00+00:00", "2033-01-10T00:00:00+00:00"
        at = "2033-01-04T15:00:00+00:00"
        _closed_demo("a1", "Energy", at, 30.0)
        _closed_demo("a2", "Energy", at, -10.0)
        _closed_demo("a3", "Energy", at, 500.0, annotate=True)
        _closed_live("a1", "Energy", at, 20.0)
        _closed_live("a2", "Energy", at, 900.0, confidence="unverified")
        d = wr._closed_summary("demo", since, until)
        l = wr._closed_summary("live", since, until)
        assert (d["closed_total"], d["wins"], d["losses"], d["realized_pnl"]) == (2, 1, 1, 20.0)
        assert (l["closed_total"], l["wins"], l["realized_pnl"]) == (1, 1, 20.0)

    def test_demo_stats_reads_the_shared_population_and_values_open_at_market(self, monkeypatch):
        since, until = "2033-01-10T00:00:00+00:00", "2033-01-17T00:00:00+00:00"
        _closed_demo("b1", "Energy", "2033-01-11T15:00:00+00:00", 12.5)
        monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {"DOPENB": 110.0})
        before = wr._demo_stats(since, until)
        insert_trade({
            "timestamp": "2033-01-11T15:00:00+00:00", "ticker": "DOPENB", "sector": "Energy", "action": "BUY",
            "price": 100.0, "shares": 2.0, "amount": 200.0, "signal_score": 0.7, "sentiment_score": None,
            "claude_confidence": None, "claude_reasoning": None, "outcome": "OPEN", "pnl": None,
        })
        after = wr._demo_stats(since, until)
        closed = wr._closed_summary("demo", since, until)
        assert {k: after[k] for k in closed} == closed
        assert after["closed_total"] == 1 and after["realized_pnl"] == 12.5
        assert after["open_positions"] == before["open_positions"] + 1
        assert round(after["unrealized_pnl"] - before["unrealized_pnl"], 2) == 20.0   # 2 sh × (110 − 100)


class TestReference:
    SINCE = "2033-06-06T00:00:00+00:00"   # a Monday

    def test_covers_the_prior_weeks_only(self):
        # one TRADE_EXECUTED in each of: this week, prior weeks 1 and 8, and week 9 (outside)
        for k, n in ((0, 5), (1, 2), (wr.REFERENCE_WEEKS, 3), (wr.REFERENCE_WEEKS + 1, 7)):
            at = _monday(self.SINCE, k).replace("00:00:00", "15:00:00")
            _gate_rows("signals", at, {"TRADE_EXECUTED": n, "FILTERED_L1_ELIGIBILITY_REGIME": 2 * n})
        ref = wr._reference(self.SINCE)
        assert len(ref["weeks"]) == wr.REFERENCE_WEEKS
        assert ref["weeks"][0]["since"] == _monday(self.SINCE, 1)
        assert ref["weeks"][-1]["since"] == _monday(self.SINCE, wr.REFERENCE_WEEKS)
        rb = wr._ref_book(ref, "demo")
        assert sorted(w["funnel"]["demo"]["entered"] for w in ref["weeks"]) == [0] * 6 + [2, 3]
        assert rb["entered"] == {"median": 0, "min": 0, "max": 3}
        elig = rb["locks"][1]
        assert elig["label"] == "L1 · Eligibility"
        assert (elig["count"], rb["candidates"]) == (10, 15)   # 2·2 + 2·3 stopped of (2+4) + (3+6) candidates

    def test_never_reaches_before_the_lock_chain_start(self, monkeypatch):
        monkeypatch.setattr(wr, "REFERENCE_START", "2033-05-23T00:00:00+00:00")
        ref = wr._reference(self.SINCE)
        assert [w["since"][:10] for w in ref["weeks"]] == ["2033-05-30", "2033-05-23"]

    def test_no_weeks_gives_empty_book_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(wr, "REFERENCE_START", self.SINCE)
        ref = wr._reference(self.SINCE)
        rb = wr._ref_book(ref, "live")
        assert ref["weeks"] == [] and rb["n"] == 0 and rb["closed"] is None and rb["locks"] == []
        assert wr._reference_note(ref, rb, self.SINCE).startswith("No reference weeks")
        assert wr._spread_text(None) == "—" and wr._ratio_text(0, 0) == "0/0"

    def test_pooled_win_rate_and_sector_rows_use_the_shared_population(self):
        since = "2033-09-05T00:00:00+00:00"
        _closed_live("c1", "Utilities", "2033-08-30T15:00:00+00:00", 10.0)    # prior week 1
        _closed_live("c2", "Utilities", "2033-08-23T15:00:00+00:00", -4.0)    # prior week 2
        _closed_live("c3", "Utilities", "2033-08-23T16:00:00+00:00", 50.0, confidence="unverified")
        _closed_live("c4", "Utilities", "2033-09-06T15:00:00+00:00", 99.0)    # this week: not reference
        ref = wr._reference(since)
        rb = wr._ref_book(ref, "live")
        assert (rb["wins"], rb["n"]) == (1, 2)
        assert rb["pnl"] == {"median": 0.0, "min": -4.0, "max": 10.0}
        util = next(r for r in ref["sectors"]["live"] if r["sector"] == "Utilities")
        assert (util["n"], util["wins"], util["pnl"]) == (2, 1, 6.0)


class TestReportShowsReference:
    def test_html_and_plain_carry_the_reference(self, monkeypatch):
        since = "2033-11-07T00:00:00+00:00"
        _gate_rows("signals", "2033-11-08T15:00:00+00:00", {"FILTERED_L1_ELIGIBILITY_REGIME": 4, "TRADE_EXECUTED": 1})
        _gate_rows("signals", "2033-11-01T15:00:00+00:00", {"FILTERED_L1_ELIGIBILITY_REGIME": 6, "TRADE_EXECUTED": 2})
        _closed_live("r1", "Materials", "2033-11-08T15:00:00+00:00", 5.0)
        _closed_live("r2", "Materials", "2033-11-02T15:00:00+00:00", 7.0)
        monkeypatch.setattr(wr, "_week_start_iso", lambda: since)
        monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
        monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")
        _, html, plain = wr.build_report(recal_changes=None)
        for text in (html, plain):
            assert f"Prior {wr.REFERENCE_WEEKS} full weeks" in text
            assert "not a cause" in text
            assert "6/8 (75%)" in text           # prior: 6 stopped at L1 of 8 candidates
            assert "4/5 (80%)" in text           # this week
        assert "Materials 1/1 $+7" in plain      # prior-week sector row
        assert f"Prior {wr.REFERENCE_WEEKS} wk (demo / live)" in html
