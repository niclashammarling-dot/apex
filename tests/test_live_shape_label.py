"""Off-shape label on sweep/optimizer output, and distinct-outcome detection (2026-09-29)."""
import json

import pytest

import backend.backtest.live_shape as ls

LIVE = {"take_profit_pct": 0.06, "stop_loss_pct": 0.06, "profit_lock_trigger_pct": 0.04,
        "profit_lock_trail_pct": 0.01, "max_positions": 8, "lock1_threshold": 0.65}


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    import backend.live_config as lc
    monkeypatch.setattr(lc, "get_live_config", lambda: dict(LIVE))


# ── label ────────────────────────────────────────────────────────────────────

def test_result_without_hash_is_off_shape_and_names_live_values():
    label = ls.shape_label({}, "optimizer")
    assert label.startswith("OFF-SHAPE")
    assert "0.04/0.01" in label and "live runs 8" in label and "legacy trailing stop" in label


def test_sweep_label_names_the_demo_baseline():
    assert "demo config" in ls.shape_label({}, "sweep")


def test_label_clears_when_run_at_the_live_shape():
    assert ls.shape_label({"live_shape_hash": ls.live_shape_hash()}, "sweep") is None


def test_label_returns_as_stale_when_live_config_changes_under_the_result(monkeypatch):
    h = ls.live_shape_hash()
    import backend.live_config as lc
    monkeypatch.setattr(lc, "get_live_config", lambda: {**LIVE, "stop_loss_pct": 0.05})
    assert ls.shape_label({"live_shape_hash": h}, "sweep").startswith("STALE")


def test_hash_ignores_fields_outside_the_shape(monkeypatch):
    h = ls.live_shape_hash()
    import backend.live_config as lc
    monkeypatch.setattr(lc, "get_live_config", lambda: {**LIVE, "lock1_threshold": 0.70})
    assert ls.live_shape_hash() == h


# ── distinct outcomes ────────────────────────────────────────────────────────

def _t(ticker, pnl):
    return {"ticker": ticker, "entry_date": "2026-09-01", "exit_date": "2026-09-05",
            "exit_reason": "TP", "pnl": pnl}


def test_same_trades_same_signature():
    assert ls.outcome_signature([_t("A", 10)]) == ls.outcome_signature([_t("A", 10.001)])
    assert ls.outcome_signature([_t("A", 10)]) != ls.outcome_signature([_t("A", 11)])


def test_inert_axis_is_named_and_live_axis_is_not():
    rows = []
    for tp in (0.06, 0.08):
        for vix in (None, 30, 35):          # vix never binds: outcome depends on tp only
            rows.append({"tp": tp, "vix": vix, "outcome": f"o{tp}"})
    assert ls.inert_axes(rows, ("tp", "vix")) == ["vix"]


def test_singleton_groups_are_not_evidence_of_inertness():
    # every tp=0.08 combo was dropped below MIN_TRADES except one per vix value's partner
    rows = [{"tp": 0.06, "vix": None, "outcome": "a"},
            {"tp": 0.08, "vix": 30, "outcome": "b"}]
    assert ls.inert_axes(rows, ("tp", "vix")) == []


# ── the outputs carry it ─────────────────────────────────────────────────────

def test_sweep_mail_leads_with_label_and_prints_every_varied_axis(monkeypatch):
    import backend.alerts as alerts
    import backend.demo_config as dc
    from backend.backtest import weekend_sweep as ws
    sent = []
    monkeypatch.setattr(alerts, "_cfg", lambda: {"slack_url": None, "email_to": "x", "smtp_user": "u", "smtp_pass": "p"})
    monkeypatch.setattr(alerts, "_send_email", lambda cfg, subj, body: sent.append(body))
    monkeypatch.setattr(dc, "get_demo_config", lambda: {"lock1_threshold": 0.6, "take_profit_pct": 0.06,
                                                        "stop_loss_pct": 0.07, "max_hold_days": 30})
    top = [{"lock1_threshold": 0.75, "take_profit_pct": 0.06, "stop_loss_pct": 0.04, "time_stop_days": 20,
            "vix_threshold": v, "use_leading_rs": True, "max_positions": 6, "sharpe": 1.8,
            "total_return_pct": 0.035, "win_rate": 0.43, "total_trades": 53, "spy_return_pct": 0.044}
           for v in (None, 30, 35)]
    payload = {"valid_combos": 3240, "distinct_outcomes": 1080, "inert_axes": ["vix_threshold"]}
    ws._notify_sweep(top, "2026-06-27", "2026-09-25", payload)
    body = sent[0].replace("<br>", "\n")
    assert body.startswith("OFF-SHAPE")
    assert "1080 of 3240" in body and "never changed a result: vix_threshold" in body
    assert "vix=off" in body and "vix=30" in body and "vix=35" in body   # the three rows now differ


def test_optimizer_mail_leads_with_label(monkeypatch, tmp_path):
    import backend.alerts as alerts
    from backend.backtest import optimizer as o
    results = tmp_path / "optimizer_results.json"
    results.write_text(json.dumps({"best_score": 0.88}))
    monkeypatch.setattr(o, "RESULTS_PATH", results)
    sent = []
    monkeypatch.setattr(alerts, "_cfg", lambda: {"slack_url": None, "email_to": "x", "smtp_user": "u", "smtp_pass": "p"})
    monkeypatch.setattr(alerts, "_send_email", lambda cfg, subj, body: sent.append((subj, body)))
    bp = {"lock1_threshold": 0.76, "take_profit_pct": 0.07, "stop_loss_pct": 0.05, "trailing_stop_pct": 0.05,
          "time_stop_days": 15, "max_positions": 2, "vix_threshold": None}
    o._notify(bp, 0.8866, {"sharpe": 2.9, "total_return_pct": 0.145, "spy_return_pct": 0.13}, 8, 200)
    subj, body = sent[0]
    assert subj == "[APEX] Weekly Optimizer Complete"
    assert body.startswith("OFF-SHAPE") and "legacy trailing stop" in body


def test_weekly_report_section_carries_label(tmp_path):
    from backend.weekly_report import _shape_html
    p = tmp_path / "sweep_results.json"
    p.write_text(json.dumps({"valid_combos": 3240, "distinct_outcomes": 1080, "inert_axes": ["vix_threshold"]}))
    html = _shape_html(p, "sweep")
    assert "OFF-SHAPE" in html and "1080 of 3240" in html
