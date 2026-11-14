"""
Idempotent order submission (Saturday 2026-11-14 order item 7c; scope vault
raw/notes/2026-11/2026-11-08-apex-idempotent-order-submission-scope.md).

Every live bracket entry carries a deterministic client_order_id (apex-<book>-<ticker>-<cycle>-<side>), so the same
decision in the same cycle is one order at Alpaca. On a submit exception the order is read back by that id:
found -> submitted, the row written and the cycle's cash, count and tickers updated; not found -> broker_error as
before; the readback fails too -> broker_unknown, stored with its cause, fail closed (sized as if accepted) and cooled.
"""
from unittest.mock import MagicMock

import pytest

from backend.brokers import alpaca
from backend.gate import lock_codes
from tests.test_execution_rule_states import _run_live, _stored

READBACK = -1                                         # tests.test_gate_runners._live_patches: its last patch


def test_the_id_is_deterministic_per_decision_and_cycle():
    a = alpaca.client_order_id("live", "NVDA", "20261116T153012", "buy")
    assert a == alpaca.client_order_id("live", "NVDA", "20261116T153012", "buy") == "apex-live-NVDA-20261116T153012-buy"
    assert a != alpaca.client_order_id("live", "NVDA", "20261116T154512", "buy")
    assert len(alpaca.client_order_id("live", "X" * 200, "c", "buy")) == 128


def test_the_entry_is_submitted_with_its_client_order_id():
    _, mocks = _run_live()
    cid = mocks[14].call_args.kwargs["client_order_id"]
    assert cid.startswith("apex-live-") and cid.endswith("-buy")


def test_a_timeout_after_acceptance_books_the_order_found_by_readback():
    results, mocks = _run_live(broker_raises=TimeoutError("read timed out"),
                               readback={"id": "ord-found-1", "status": "accepted"})
    assert results[0]["outcome"] == "TRADE_EXECUTED"
    assert mocks[READBACK].call_args.args[0] == mocks[14].call_args.kwargs["client_order_id"]
    assert mocks[10].call_args[0][0]["alpaca_order_id"] == "ord-found-1"          # insert_live_trade


def test_a_timeout_before_acceptance_stays_broker_error():
    results, mocks = _run_live(broker_raises=TimeoutError("connect timed out"), readback=None)
    assert _stored(mocks)["outcome_reason"].startswith("broker_error: connect timed out")
    assert lock_codes.stop_of(results[0]["rule_states"]) == (6, "broker_error")
    mocks[10].assert_not_called()


def test_a_failed_readback_is_broker_unknown_with_its_cause_and_cools_the_ticker():
    results, mocks = _run_live(broker_raises=TimeoutError("read timed out"),
                               readback=ConnectionError("dns failure"))
    reason = _stored(mocks)["outcome_reason"]
    assert reason.startswith("broker_unknown") and "read timed out" in reason and "dns failure" in reason
    stop = lock_codes.stop_of(results[0]["rule_states"])
    assert stop == (6, "broker_unknown") and lock_codes.stop_code(*stop) in lock_codes.COOLOFF_STOPS
    mocks[10].assert_not_called()


def test_readback_reads_not_found_as_none_and_raises_on_other_failures(monkeypatch):
    class NotFound(Exception):
        status_code = 404
    client = MagicMock()
    client.get_order_by_client_id.side_effect = NotFound("order not found")
    monkeypatch.setattr(alpaca, "_client", lambda: client)
    assert alpaca.get_order_by_client_id("apex-live-X-c-buy") is None
    client.get_order_by_client_id.side_effect = ConnectionError("reset")
    with pytest.raises(ConnectionError):
        alpaca.get_order_by_client_id("apex-live-X-c-buy")


def _gate_row(ticker, ts, reason):
    from backend.db import get_db
    conn = get_db()
    try:
        conn.execute("INSERT INTO live_gate_history (ticker, sector, gate_decision, timestamp, outcome_reason) "
                     "VALUES (?, 'Tech', 'FILTERED_L6_REVIEW', ?, ?)", (ticker, ts, reason))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def _clean_gate_rows():
    yield
    from backend.db import get_db
    conn = get_db()
    try:
        conn.execute("DELETE FROM live_gate_history WHERE ticker LIKE 'BU%'")
        conn.commit()
    finally:
        conn.close()


def test_check89_warns_on_this_weeks_broker_unknown(tmp_path, monkeypatch, _clean_gate_rows):
    import shutil
    from datetime import datetime, timezone
    import audit.checks_data as cd
    import backend.db as db_module
    _gate_row("BU1", "2026-11-19T15:00:00+00:00", "broker_unknown: submit read timed out; readback dns failure")
    _gate_row("BU2", "2026-11-19T15:05:00+00:00", "broker_error: insufficient buying power")   # not counted
    (tmp_path / "data").mkdir()
    shutil.copy(db_module.DB_PATH, tmp_path / "data" / "apex.db")
    found = []
    monkeypatch.setattr(cd, "flag", lambda num, name, sev, where, msg: found.append((sev, msg)))
    cd._check89_broker_unknown(tmp_path / "data" / "apex.db", "2026-07-07", datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc))
    warn = [m for s, m in found if s == "WARNING"]
    assert len(warn) == 1 and "BU1" in warn[0] and "BU2" not in warn[0]
    assert any(s == "INFO" and m.startswith("1 broker_unknown") for s, m in found)


def test_the_ops_card_counts_broker_unknown(_clean_gate_rows):
    from backend.routers.signals_router import _order_integrity
    before = _order_integrity("2026-07-07")["broker_unknown"]
    _gate_row("BU3", "2026-11-19T15:00:00+00:00", "broker_unknown: submit x; readback y")
    assert _order_integrity("2026-07-07")["broker_unknown"] == before + 1


def test_the_bracket_request_carries_the_client_order_id(monkeypatch):
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="ord-1")
    monkeypatch.setattr(alpaca, "_client", lambda: client)
    assert alpaca.place_bracket_order("NVDA", 1000.0, 100.0, 0.06, 0.06, client_order_id="apex-live-NVDA-c-buy") == "ord-1"
    assert client.submit_order.call_args.args[0].client_order_id == "apex-live-NVDA-c-buy"


def test_the_entry_fills_endpoint_carries_the_order_block():
    from backend.routers.signals_router import get_entry_fills
    out = get_entry_fills(limit=1)
    assert set(out["orders"]) == {"broker_unknown", "quote_exits", "readback_found"}
