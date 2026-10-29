"""
Pins the weekly-report fixes from the 2026-10-03 read against the DB
(raw/notes/2026-10/2026-10-03-apex-weekly-report-read-against-db.md):

- The funnel counts each row once, under the lock and rule that stopped it
  (stop_code; lock labels phase 2, 2026-10-29). The old query counted open/cooloff
  skips as "L1 pass" and gated the eligibility row on lock1_pass=1, which has
  never occurred; the skips are L1's rules now.
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
from backend.gate import lock_codes

init_db()


def _code(stop: str) -> str:
    try:
        return lock_codes.lock_code(lock_codes.parse_stop_code(stop)[0])
    except KeyError:
        return stop                                  # TRADE_EXECUTED, FILTERED_UNKNOWN, or a code no rule has


def _gate_rows(table: str, since: str, stops: dict[str, int], sector: str = "Technology"):
    conn = get_db()
    try:
        for stop, n in stops.items():
            for i in range(n):
                conn.execute(
                    f"INSERT INTO {table} (timestamp, ticker, sector, gate_decision, stop_code) VALUES (?, ?, ?, ?, ?)",
                    (since.replace("00:00:00", "15:00:00"), f"T{i}", sector, _code(stop), stop),
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


# Shape of the 09-28 demo week (measured 2026-10-03), by the rule that stopped each row after phase 2's
# migration: the skips are L1 position_open / cooloff, every score row that week a macro pre-event bite.
WEEK = {
    "FILTERED_L1_ELIGIBILITY_POSITION_OPEN": 426, "FILTERED_L1_ELIGIBILITY_COOLOFF": 373,
    "FILTERED_L1_ELIGIBILITY_REGIME": 180, "FILTERED_L1_ELIGIBILITY_MACRO_PRE_EVENT": 6,
    "FILTERED_L4_SENTIMENT_GROK": 14, "FILTERED_L5_POSITIONING_LEADING": 101, "FILTERED_L6_REVIEW_CLAUDE": 5,
    "FILTERED_L2_RISK_OVERFLOW": 3, "TRADE_EXECUTED": 6,
}


class TestFunnel:
    def test_each_row_is_counted_once_under_its_lock_and_rule(self):
        since, until = "2031-03-03T00:00:00+00:00", "2031-03-10T00:00:00+00:00"
        _gate_rows("signals", since, WEEK)
        f = wr._funnel("demo", since, until)
        assert f["total"] == 1114
        assert [(s["label"], s["count"]) for s in f["locks"]] == [
            ("L0 · Portfolio", 0), ("L1 · Eligibility", 985), ("L2 · Risk", 3), ("L3 · Score", 0),
            ("L4 · Sentiment", 14), ("L5 · Positioning", 101), ("L6 · Review", 5)]
        assert {r["rule"]: r["count"] for r in f["locks"][1]["rules"]} == {
            "position_open": 426, "cooloff": 373, "regime": 180, "macro_pre_event": 6}
        assert f["entered"] == 6 and f["unknown"] == 0
        assert f["balanced"] and not f["other"]

    def test_skips_are_l1_rules_and_never_pass_a_lock(self):
        since, until = "2031-03-17T00:00:00+00:00", "2031-03-24T00:00:00+00:00"
        _gate_rows("live_gate_history", since, {"FILTERED_L1_ELIGIBILITY_POSITION_OPEN": 50,
                                                "FILTERED_L1_ELIGIBILITY_EXIT_COOLOFF": 20,
                                                "FILTERED_L1_ELIGIBILITY_BLOCKLIST": 2})
        f = wr._funnel("live", since, until)
        assert [s["count"] for s in f["locks"]] == [0, 72, 0, 0, 0, 0, 0] and f["entered"] == 0

    def test_unknown_and_unregistered_codes_are_shown_not_dropped(self):
        since, until = "2031-03-31T00:00:00+00:00", "2031-04-07T00:00:00+00:00"
        _gate_rows("live_gate_history", since, {"FILTERED_L6_REVIEW_CLAUDE": 1, "FILTERED_UNKNOWN": 1,
                                                "FILTERED_SOMETHING_NEW": 2})
        f = wr._funnel("live", since, until)
        assert f["other"] == {"FILTERED_SOMETHING_NEW": 2} and f["unknown"] == 1
        assert f["balanced"]
        plain = wr._plain_funnel("Live", f)
        assert "FILTERED_SOMETHING_NEW 2" in plain and "unknown 1" in plain


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
        assert "985 (position_open 426, cooloff 373, regime 180, macro_pre_event 6)" in html
        assert "985 stopped (position_open 426, cooloff 373, regime 180, macro_pre_event 6)" in plain
        assert "no threshold moved" in plain

    def test_calibration_fact_states_what_happened(self):
        status = {"sectors": [{"sector": "Healthcare", "flag": "high"}, {"sector": "Energy", "flag": "low"}]}
        assert wr._calibration_fact(status, None).startswith("Calibration not triggered")
        assert "no threshold moved" in wr._calibration_fact(status, {})
        assert "Healthcare 0.58 → 0.6" in wr._calibration_fact(status, {"Healthcare": (0.58, 0.6)})
