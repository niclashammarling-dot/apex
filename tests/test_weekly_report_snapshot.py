"""
Pins the weekly report's stored half (order (b2) of the 2026-10-03 read, "store
only what cannot be recomputed", built 2026-10-19):

- build_report fills `capture` with the facts the DB cannot give back later:
  optimizer result with its noise floor, sweep, thresholds as they stood, the
  calibration changes, the commentary, the serving commit.
- send_weekly_report stores one weekly_report_snapshots row per ISO week before
  the send, and a second send keeps the first row.
- A failed store does not stop the mail.

The send tests point the sent marker at tmp_path and give alerts no channel, so
nothing reaches data/ or a mailbox. All weeks sit in 2034, a year no other test writes.
"""
import backend.alerts as alerts
import backend.config as config_module
import backend.db as db
import backend.weekly_report as wr
from backend.db import get_weekly_snapshot, init_db

init_db()

SINCE = "2034-03-06T00:00:00+00:00"   # a Monday


def _quiet(monkeypatch, tmp_path, week):
    monkeypatch.setattr(wr, "_week_start_iso", lambda: SINCE)
    monkeypatch.setattr(wr, "_this_week_label", lambda: week)
    monkeypatch.setattr(wr, "_SENT_MARKER", tmp_path / "weekly_report_sent.txt")
    monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
    monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")


class TestCapture:
    def test_capture_holds_what_the_db_cannot_recompute(self, monkeypatch, tmp_path):
        _quiet(monkeypatch, tmp_path, "2034-W10")
        cap: dict = {}
        wr.build_report(recal_changes={"Energy": (0.6, 0.62)}, capture=cap)
        assert cap["week"] == "2034-W10" and cap["since"] == SINCE
        assert set(cap["optimizer"]) == {"state", "detail", "result"}
        if cap["optimizer"]["state"] == "ok":
            assert "noise_floor" in cap["optimizer"]["result"] and "noise_floor_valid" in cap["optimizer"]["result"]
        assert "flat" in cap["thresholds"] and all("effective" in s for s in cap["thresholds"]["sectors"])
        assert cap["recal_changes"] == {"Energy": (0.6, 0.62)}
        assert "commentary" in cap and cap["commentary"] is None      # no key: commentary skipped, stored as None
        assert set(cap["code"]) == {"commit", "dirty", "dirty_files"}
        assert cap["reference_weeks"] == wr.REFERENCE_WEEKS

    def test_no_capture_argument_still_builds(self, monkeypatch, tmp_path):
        _quiet(monkeypatch, tmp_path, "2034-W10")
        subject, html, plain = wr.build_report(recal_changes=None)
        assert subject.startswith("[APEX] Weekly Report")


class TestStore:
    def test_send_stores_one_row_and_keeps_the_first(self, monkeypatch, tmp_path):
        _quiet(monkeypatch, tmp_path, "2034-W11")
        monkeypatch.setattr(alerts, "_cfg", lambda: {"email_to": "", "smtp_user": "", "smtp_pass": "", "slack_url": ""})
        assert get_weekly_snapshot("2034-W11") is None
        wr.send_weekly_report()
        first = get_weekly_snapshot("2034-W11")
        assert first is not None and first["payload"]["week"] == "2034-W11"
        assert "optimizer" in first["payload"] and "thresholds" in first["payload"]
        assert (tmp_path / "weekly_report_sent.txt").read_text() == "2034-W11"

        monkeypatch.setattr(wr, "_week_start_iso", lambda: "2034-03-13T00:00:00+00:00")
        wr.send_weekly_report()
        again = get_weekly_snapshot("2034-W11")
        assert again["written_at"] == first["written_at"] and again["payload"]["since"] == SINCE

    def test_failed_store_still_sends(self, monkeypatch, tmp_path):
        _quiet(monkeypatch, tmp_path, "2034-W12")
        monkeypatch.setattr(alerts, "_cfg", lambda: {"email_to": "x@example.invalid", "smtp_user": "u",
                                                     "smtp_pass": "p", "slack_url": ""})
        sent = []
        monkeypatch.setattr(alerts, "_send_email", lambda cfg, subject, plain, html_body=None: sent.append(subject) or True)

        def boom(week, payload):
            raise RuntimeError("disk full")
        monkeypatch.setattr(db, "insert_weekly_snapshot", boom)
        wr.send_weekly_report()
        assert len(sent) == 1
        assert get_weekly_snapshot("2034-W12") is None
