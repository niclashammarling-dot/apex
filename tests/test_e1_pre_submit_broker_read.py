"""
E1, the pre-submit open-order read (Saturday 2026-11-28 order, R102 d; vault scope
raw/notes/2026-11/2026-11-27-apex-e1-pre-submit-broker-read-scope.md; riktning, Niclas 2026-11-27: only the open-order
read, fail closed, one function for both submit sites, CHECK 89 counts the two refusals apart, one mail per ticker).

A broker_unknown entry is cooled 4 hours; after that the gate could submit the ticker again with a new client order id
while the first order still stood unfilled at Alpaca. The serial phase reads broker positions, so a filled order is
already position_open; an accepted but unfilled order has no position, and only an open-order read sees it.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.brokers import alpaca
from backend.db import clear_alert_latch, get_db
from backend.gate import gate_runner_live, lock_codes
from backend.gate.lock_codes import PASS
from backend.gate.lock_codes import rule_key as K
from tests.test_execution_rule_states import _run_live, _stored

OPEN_ORDERS, MAIL, SUBMIT, INSERT_TRADE = -3, -2, 14, 10     # tests.test_gate_runners._live_patches indexes
PENDING_BUY = [{"id": "ord-pending-1", "ticker": "NVDA", "side": "buy", "type": "market", "status": "accepted",
                "client_order_id": "apex-live-NVDA-20261127T153012-buy"}]


@pytest.fixture(autouse=True)
def _no_latch():
    clear_alert_latch("broker_holds:NVDA")
    yield
    clear_alert_latch("broker_holds:NVDA")


def test_an_open_order_at_the_broker_refuses_the_entry_before_the_submit_and_cools_it():
    results, mocks = _run_live(open_orders=PENDING_BUY)
    mocks[SUBMIT].assert_not_called()
    mocks[INSERT_TRADE].assert_not_called()
    assert mocks[OPEN_ORDERS].call_args.args == ("NVDA",)
    row = _stored(mocks)
    assert row["outcome_reason"].startswith("broker_holds: 1 open order(s): ord-pending-1 buy market accepted")
    stop = lock_codes.stop_of(results[0]["rule_states"])
    assert stop == (1, "broker_holds") and lock_codes.stop_code(*stop) in lock_codes.COOLOFF_STOPS
    assert row["stop_code"] == "FILTERED_L1_ELIGIBILITY_BROKER_HOLDS"
    assert results[0]["l6_review_pass"] == 1                  # Claude approved; L1 refused the order at the submit


def test_a_failed_read_is_broker_read_failed_not_cooled_and_mails_nothing():
    from contextlib import ExitStack
    from tests.test_gate_runners import _live_patches, _signal
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _live_patches(candidates=[_signal()])]
        mocks[OPEN_ORDERS].side_effect = ConnectionError("reset by peer")
        results = gate_runner_live.run()
    mocks[SUBMIT].assert_not_called()
    mocks[MAIL].assert_not_called()
    row = mocks[9].call_args[0][0]
    assert row["outcome_reason"] == "broker_read_failed: reset by peer"
    stop = lock_codes.stop_of(results[0]["rule_states"])
    assert stop == (1, "broker_read_failed") and lock_codes.stop_code(*stop) not in lock_codes.COOLOFF_STOPS


def test_a_clean_read_submits_as_before_and_records_the_pass():
    results, mocks = _run_live()
    assert results[0]["outcome"] == "TRADE_EXECUTED" and mocks[SUBMIT].called
    assert mocks[OPEN_ORDERS].call_args.args == ("NVDA",)
    assert results[0]["rule_states"][K(1, "broker_holds")] == PASS
    assert K(1, "broker_read_failed") not in results[0]["rule_states"]      # a failure mode: recorded only as a fail
    mocks[MAIL].assert_not_called()


def test_one_mail_per_ticker_until_a_clean_read_releases_the_latch():
    _, first = _run_live(open_orders=PENDING_BUY)
    _, second = _run_live(open_orders=PENDING_BUY)
    assert first[MAIL].call_count == 1 and second[MAIL].call_count == 0          # two refused cycles, one mail
    assert first[MAIL].call_args.args[0] == "NVDA" and first[MAIL].call_args.args[1] == PENDING_BUY
    _run_live()                                                                  # a clean read releases the latch
    _, again = _run_live(open_orders=PENDING_BUY)
    assert again[MAIL].call_count == 1                                           # a recurrence mails again


def test_a_held_order_takes_the_slot_without_spending_cash_and_a_failed_read_takes_nothing():
    tickers = set()
    result = {"rule_states": {}}
    assert gate_runner_live._submit_failed(result, "broker_holds: 1 open order(s): x", "NVDA", 500.0, 2000.0, 3,
                                           tickers) == (2000.0, 4)
    assert tickers == {"NVDA"} and result["outcome_reason"].startswith("broker_holds")
    tickers = set()
    assert gate_runner_live._submit_failed({"rule_states": {}}, "broker_read_failed: x", "NVDA", 500.0, 2000.0, 3,
                                           tickers) == (2000.0, 3)
    assert tickers == set()


def test_the_read_asks_alpaca_for_open_orders_in_the_ticker_and_raises_on_failure(monkeypatch):
    client = MagicMock()
    order = MagicMock(id="o1", symbol="NVDA", client_order_id="cid-1")
    order.side.value, order.order_type.value, order.status.value = "buy", "market", "accepted"
    client.get_orders.return_value = [order]
    monkeypatch.setattr(alpaca, "_client", lambda: client)
    got = alpaca.get_open_orders("NVDA")
    req = client.get_orders.call_args.kwargs["filter"]
    assert req.status.value == "open" and req.symbols == ["NVDA"]
    assert got == [{"id": "o1", "ticker": "NVDA", "side": "buy", "type": "market", "status": "accepted",
                    "client_order_id": "cid-1"}]
    client.get_orders.side_effect = ConnectionError("reset")
    with pytest.raises(ConnectionError):
        alpaca.get_open_orders("NVDA")


def _gate_row(ticker, ts, reason):
    conn = get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, timestamp, outcome_reason) "
                     "VALUES (?, 'Tech', 'FILTERED_L1_ELIGIBILITY', ?, ?)", (ticker, ts, reason))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def _clean_rows():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_gate_history WHERE ticker LIKE 'E1%'")
        conn.commit()
    finally:
        conn.close()


def test_check89_warns_on_broker_holds_and_counts_a_failed_read_apart(tmp_path, monkeypatch, _clean_rows):
    import shutil
    import audit.checks_data as cd
    import backend.db as db_module
    _gate_row("E1H", "2026-11-30T15:00:00+00:00", "broker_holds: 1 open order(s): o1 buy market accepted")
    _gate_row("E1F", "2026-11-30T15:05:00+00:00", "broker_read_failed: reset by peer")
    (tmp_path / "data").mkdir()
    shutil.copy(db_module.DB_PATH, tmp_path / "data" / "apex.db")
    found = []
    monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))
    cd._check89_broker_reads(tmp_path / "data" / "apex.db", "2026-07-07", datetime(2026, 12, 1, 21, 0, tzinfo=timezone.utc))
    warn = [m for s, m in found if s == "WARNING"]
    assert len(warn) == 1 and "E1H" in warn[0] and "E1F" not in warn[0]
    info = [m for s, m in found if s == "INFO"]
    assert any(m.startswith("1 broker_holds refusal since") for m in info)
    assert any(m.startswith("1 broker_read_failed refusal since") and "1 this week" in m for m in info)


def test_check89_is_silent_when_neither_refusal_exists(tmp_path, monkeypatch, _clean_rows):
    import shutil
    import audit.checks_data as cd
    import backend.db as db_module
    (tmp_path / "data").mkdir()
    shutil.copy(db_module.DB_PATH, tmp_path / "data" / "apex.db")
    found = []
    monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))
    cd._check89_broker_reads(tmp_path / "data" / "apex.db", "2026-07-07", datetime(2026, 12, 1, 21, 0, tzinfo=timezone.utc))
    assert found == []


def test_the_ops_card_counts_both_refusals_apart(_clean_rows):
    from backend.routers.signals_router import _order_integrity
    before = _order_integrity("2026-07-07")
    _gate_row("E1H2", "2026-11-30T15:00:00+00:00", "broker_holds: 1 open order(s): o1")
    _gate_row("E1F2", "2026-11-30T15:05:00+00:00", "broker_read_failed: x")
    _gate_row("E1F3", "2026-11-30T15:06:00+00:00", "broker_read_failed: y")
    after = _order_integrity("2026-07-07")
    assert after["broker_holds"] == before["broker_holds"] + 1
    assert after["broker_read_failed"] == before["broker_read_failed"] + 2
