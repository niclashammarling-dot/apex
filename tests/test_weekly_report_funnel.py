"""
Pins the weekly-report fixes from the 2026-10-03 read against the DB
(raw/notes/2026-10/2026-10-03-apex-weekly-report-read-against-db.md):

- The funnel is built from gate_decision per lock, with each stage's reached
  count. The old query counted open/cooloff skips as "L1 pass" and gated the
  eligibility row on lock1_pass=1, which has never occurred.
- Decisions the stages don't name are shown, never dropped.
- Per-sector closed P&L uses the same population as the totals.
- Threshold rows state measured facts; the cause labels ("selloff",
  "regime floor working correctly", "recalibrate — scores drifted up") are gone.

Each test uses its own week far from real data, so rows from other tests
can't land in it.
"""
import backend.config as config_module
import backend.weekly_report as wr
from backend.db import close_live_trade, get_db, init_db, insert_live_trade

init_db()


def _gate_rows(table: str, since: str, decisions: dict[str, int], sector: str = "Technology"):
    conn = get_db()
    try:
        for decision, n in decisions.items():
            for i in range(n):
                conn.execute(
                    f"INSERT INTO {table} (timestamp, ticker, sector, gate_decision) VALUES (?, ?, ?, ?)",
                    (since.replace("00:00:00", "15:00:00"), f"T{i}", sector, decision),
                )
        conn.commit()
    finally:
        conn.close()


def _closed_live(tag: str, sector: str, exited_at: str, pnl: float, confidence: str = "confirmed"):
    trade_id = insert_live_trade({
        "timestamp": exited_at, "ticker": f"F{tag}", "sector": sector,
        "alpaca_order_id": f"funnel-{tag}", "entry_price": 100.0, "qty": 1.0,
        "notional": 100.0, "tp_price": 110.0, "sl_price": 90.0,
    })
    close_live_trade(trade_id=trade_id, exit_price=100.0 + pnl, pnl=pnl,
                     outcome="WIN" if pnl > 0 else "LOSS", exit_reason="TP", exited_at=exited_at)
    if confidence != "confirmed":
        conn = get_db()
        conn.execute("UPDATE live_trades SET exit_confidence = ? WHERE id = ?", (confidence, trade_id))
        conn.commit()
        conn.close()


# Shape of the 09-28 demo week (measured 2026-10-03), plus one unknown decision.
WEEK = {
    "SKIPPED_OPEN": 426, "SKIPPED_COOLOFF": 373,
    "FILTERED_ELIGIBILITY": 180, "FILTERED_L1": 6, "FILTERED_L2": 14,
    "FILTERED_LEADING": 101, "FILTERED_L3": 5, "FILTERED_OVERFLOW_QUANT": 3,
    "TRADE_EXECUTED": 6,
}


class TestFunnel:
    def test_stages_reproduce_the_hand_count(self):
        since, until = "2031-03-03T00:00:00+00:00", "2031-03-10T00:00:00+00:00"
        _gate_rows("signals", since, WEEK)
        f = wr._funnel("demo", since, until)
        assert f["reached_gate"] == 315
        assert [(s["reached"], s["failed"]) for s in f["locks"]] == [(315, 180), (135, 6), (129, 14), (115, 101)]
        assert f["passed_leading"] == 14
        assert {a["outcome"]: a["n"] for a in f["after_leading"]}["Entered"] == 6
        assert f["balanced"] and not f["other"]

    def test_skips_never_count_as_passing_a_lock(self):
        since, until = "2031-03-17T00:00:00+00:00", "2031-03-24T00:00:00+00:00"
        _gate_rows("live_gate_history", since, {"SKIPPED_OPEN": 50, "SKIPPED_COOLOFF": 20, "SKIPPED_BLOCKLIST": 2})
        f = wr._funnel("live", since, until)
        assert f["reached_gate"] == 0
        assert all(s["reached"] == 0 for s in f["locks"])

    def test_unknown_decision_is_shown_not_dropped(self):
        since, until = "2031-03-31T00:00:00+00:00", "2031-04-07T00:00:00+00:00"
        _gate_rows("live_gate_history", since, {"FILTERED_L3": 1, "FILTERED_SOMETHING_NEW": 2})
        f = wr._funnel("live", since, until)
        assert f["other"] == {"FILTERED_SOMETHING_NEW": 2}
        assert f["balanced"]
        plain = wr._plain_funnel("Live", f)
        assert "FILTERED_SOMETHING_NEW 2" in plain


class TestSectorPnl:
    def test_sector_rows_sum_to_the_live_total_and_skip_unverified(self):
        since, until = "2031-04-14T00:00:00+00:00", "2031-04-21T00:00:00+00:00"
        at = "2031-04-15T15:00:00+00:00"
        _closed_live("s1", "Semiconductors", at, 40.0)
        _closed_live("s2", "Semiconductors", at, 10.0)
        _closed_live("h1", "Healthcare", at, -25.0)
        _closed_live("u1", "Healthcare", at, 99.0, confidence="unverified")
        sp = wr._sector_pnl(since, until)
        by = {r["sector"]: r for r in sp["live"]}
        assert (by["Semiconductors"]["n"], by["Semiconductors"]["wins"], by["Semiconductors"]["pnl"]) == (2, 2, 50.0)
        assert (by["Healthcare"]["n"], by["Healthcare"]["pnl"]) == (1, -25.0)
        assert round(sum(r["pnl"] for r in sp["live"]), 2) == wr._live_stats(since, until)["realized_pnl"]


class TestReportText:
    def test_no_cause_labels_and_funnel_has_denominators(self, monkeypatch):
        since = "2031-05-05T00:00:00+00:00"
        _gate_rows("signals", since, WEEK)
        monkeypatch.setattr(wr, "_week_start_iso", lambda: since)
        monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
        monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")
        _, html, plain = wr.build_report(recal_changes={})
        for text in (html, plain):
            assert "selloff" not in text
            assert "regime floor" not in text
            assert "drifted up" not in text
            assert "no recalibration needed" not in text
        assert "180 of 315 filtered" in html and "180 of 315 filtered" in plain
        assert "no threshold moved" in plain

    def test_calibration_fact_states_what_happened(self):
        status = {"sectors": [{"sector": "Healthcare", "flag": "high"}, {"sector": "Energy", "flag": "low"}]}
        assert wr._calibration_fact(status, None).startswith("Calibration not triggered")
        assert "no threshold moved" in wr._calibration_fact(status, {})
        assert "Healthcare 0.58 → 0.6" in wr._calibration_fact(status, {"Healthcare": (0.58, 0.6)})
