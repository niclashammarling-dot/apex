"""/api/live/stats carries the broker fees the fill reconcile reports (2026-11-01).

CHECK 89's reconcile (audit/fill_reconcile.py, 2026-10-31) totals Alpaca FEE activities
since live_account_since, but no surface showed them: the WALLET card's REALIZED holds
no fee. The stats response now carries them beside REALIZED, not netted, and a missing
file is a visible state rather than a zero. Reads a tmp file, never data/.
"""
import json

import backend.routers.live_router as lr


def test_fees_read_from_the_reconcile_file(tmp_path, monkeypatch):
    f = tmp_path / "fill_reconcile.json"
    f.write_text(json.dumps({"generated_at": "2026-10-31T14:09:39+00:00", "since": "2026-07-07",
                             "rows": 63, "fees_total": -1.56, "fee_days": 100}))
    monkeypatch.setattr(lr, "FILL_RECONCILE", f)
    assert lr.live_fees() == {"total": -1.56, "days": 100, "since": "2026-07-07",
                              "as_of": "2026-10-31T14:09:39+00:00", "missing": None}


def test_missing_file_is_a_visible_state_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "FILL_RECONCILE", tmp_path / "absent.json")
    fees = lr.live_fees()
    assert fees["total"] is None and "fill_reconcile.json" in fees["missing"]


def test_stats_response_carries_fees_and_keeps_realized_unnetted(tmp_path, monkeypatch):
    # Runs on the test DB (conftest). Parent control: before this change the response had no "fees" key.
    f = tmp_path / "fill_reconcile.json"
    f.write_text(json.dumps({"generated_at": "x", "since": "2026-07-07", "fees_total": -9.0, "fee_days": 3}))
    monkeypatch.setattr(lr, "FILL_RECONCILE", f)
    from backend.db import live_trade_stats
    out = lr.live_stats()
    assert out["fees"]["total"] == -9.0
    assert out["overall"]["realized"] == live_trade_stats()["overall"]["realized"]
