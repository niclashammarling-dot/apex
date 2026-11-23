"""CRITICAL ledger and the bundle's ACT section (2026-11-21, minimal form; vault
raw/notes/2026-11/2026-11-20-apex-weekend-bundle-misses-criticals-that-cleared-before-friday.md).

Founding case: CHECK 81 was CRITICAL on 11-17 and 11-18, cleared before Friday's run, and the 11-20 bundle, which
read only Friday's run, did not carry it. Niclas: "criticals stannar i listan tills lördagens bundle agerar på dom".
"""
import json

import pytest

from audit import critical_ledger as cl

C81 = [81, "L6 REVIEW fail-closed (API unavailable)", "CRITICAL", "backend/gate/l6_review.py:_fail_closed",
       "6 L6 REVIEW fail-closed row(s) in the last 2 days — live: 11-17 SPG"]
C71 = [71, "entry-floor ceiling sweep", "CRITICAL", "data/regime_signal_trace.jsonl", "9/11 eligible sectors dark"]
WARN = [36, "leading pass rate", "WARNING", "x", "y"]


def test_a_tuesday_only_critical_is_still_open_on_friday(tmp_path):
    p = tmp_path / "ledger.json"
    cl.update([C81], "2026-11-17", p)
    cl.update([C81[:4] + ["7 rows"]], "2026-11-18", p)       # same check and place, a new count: one item
    cl.update([WARN], "2026-11-19", p)
    led = cl.update([C71], "2026-11-20", p)
    items = {i["key"]: i for i in cl.open_items(led)}
    assert set(items) == {"81|backend/gate/l6_review.py:_fail_closed|2026-11-17", "71|data/regime_signal_trace.jsonl|2026-11-20"}
    c81 = items["81|backend/gate/l6_review.py:_fail_closed|2026-11-17"]
    assert c81["nights"] == ["2026-11-17", "2026-11-18"] and c81["cleared"] == "2026-11-19" and c81["text"] == "7 rows"
    assert items["71|data/regime_signal_trace.jsonl|2026-11-20"]["cleared"] is None


def test_a_critical_that_returns_after_a_clean_run_is_a_new_episode(tmp_path):
    """Niclas 2026-11-21: a return must not land under the earlier episode or its decision."""
    p = tmp_path / "ledger.json"
    cl.update([C81], "2026-11-17", p)
    cl.update([], "2026-11-18", p)
    led = cl.update([C81], "2026-11-19", p)
    eps = {i["key"]: i for i in cl.open_items(led)}
    assert set(eps) == {"81|backend/gate/l6_review.py:_fail_closed|2026-11-17", "81|backend/gate/l6_review.py:_fail_closed|2026-11-19"}
    assert eps["81|backend/gate/l6_review.py:_fail_closed|2026-11-17"]["cleared"] == "2026-11-18" and eps["81|backend/gate/l6_review.py:_fail_closed|2026-11-19"]["cleared"] is None


def test_dispose_needs_an_action_and_a_reference(tmp_path):
    p = tmp_path / "ledger.json"
    cl.update([C81, C71], "2026-11-18", p)
    k81 = "81|backend/gate/l6_review.py:_fail_closed|2026-11-18"
    with pytest.raises(ValueError):
        cl.dispose(k81, "fixed", "  ", path=p)
    with pytest.raises(ValueError):
        cl.dispose(k81, "accepted", "R124", path=p)                       # R119: no accepted
    with pytest.raises(ValueError):
        cl.dispose("71|data/regime_signal_trace.jsonl|2026-11-18", "dated", "INDEX title", path=p)   # dated needs a date
    with pytest.raises(KeyError):
        cl.dispose("99|nowhere", "fixed", "abc1234", path=p)
    led = cl.dispose(k81, "fixed", "0efa6a9", today="2026-11-21", path=p)
    assert [i["key"] for i in cl.open_items(led)] == ["71|data/regime_signal_trace.jsonl|2026-11-18"]
    d = led["items"][k81]["disposition"]
    assert d["ref"] == "0efa6a9" and d["disposed_on"] == "2026-11-21"
    with pytest.raises(KeyError):
        cl.dispose(k81, "fixed", "again", path=p)                         # an episode is disposed once


def test_a_decision_holds_while_its_episode_stands(tmp_path):
    p = tmp_path / "ledger.json"
    cl.update([C71], "2026-11-20", p)
    cl.dispose("71|data/regime_signal_trace.jsonl|2026-11-20", "dated", "R125 a", date="2026-12-05", today="2026-11-21", path=p)
    led = cl.update([C71], "2026-11-23", p)                              # still standing: no new item
    assert cl.open_items(led) == [] and led["items"]["71|data/regime_signal_trace.jsonl|2026-11-20"]["nights"] == ["2026-11-20", "2026-11-23"]


def test_a_disposed_episode_that_clears_and_returns_opens_a_new_one(tmp_path):
    p = tmp_path / "ledger.json"
    cl.update([C81], "2026-11-18", p)
    cl.dispose("81|backend/gate/l6_review.py:_fail_closed|2026-11-18", "fixed", "0efa6a9", today="2026-11-21", path=p)
    cl.update([], "2026-11-23", p)
    led = cl.update([C81], "2026-11-24", p)
    assert [i["key"] for i in cl.open_items(led)] == ["81|backend/gate/l6_review.py:_fail_closed|2026-11-24"]


def test_a_dated_decision_reopens_when_its_date_passes_while_standing(tmp_path):
    p = tmp_path / "ledger.json"
    cl.update([C71], "2026-11-20", p)
    cl.dispose("71|data/regime_signal_trace.jsonl|2026-11-20", "dated", "R125 a", date="2026-12-05", today="2026-11-21", path=p)
    cl.update([C71], "2026-12-05", p)
    assert cl.open_items(path=p) == []                                    # the action's own day: still dated
    led = cl.update([C71], "2026-12-07", p)
    it = led["items"]["71|data/regime_signal_trace.jsonl|2026-11-20"]
    assert it["disposition"] is None and it["history"][0]["reopened"] == "2026-12-07"


def test_night_of_reads_the_run_in_new_york_time():
    assert cl._night_of({"generated_at": "2026-11-21T03:34:44+00:00"}) == "2026-11-20"
    assert cl._night_of({}) is None


def test_the_bundle_opens_with_the_open_ledger_items(tmp_path):
    from audit import bundle
    p = tmp_path / "ledger.json"
    cl.update([C81], "2026-11-17", p)
    cl.update([], "2026-11-18", p)
    acts = bundle.act_items(p)
    assert len(acts) == 1 and acts[0]["section"] == "act" and acts[0]["severity"] == "CRITICAL"
    assert "open since 2026-11-17" in acts[0]["text"] and "cleared 2026-11-18, still open" in acts[0]["text"]
    b = {"generated_at": "2026-11-20T21:34:00+00:00", "host_commit": "abc", "excepted": [], "throughput": None,
         "items": [dict(acts[0], first_seen="2026-11-17"),
                   bundle._item("finding:36:WARNING:x", "findings", "WARNING", "x", "y", first_seen="2026-11-20",
                                scope="s")]}
    text = bundle.render_text(b)
    assert text.index("ACT: OPEN CRITICALS") < text.index("AUDIT FINDINGS")


def test_throughput_counts_action_items_and_config_apart():
    """3b (R101 e): config items leave by their window, not by an action. Fails on the parent, which counted them."""
    from audit import bundle
    prev = {"generated_at": "2026-11-13T21:00:00+00:00",
            "items": [{"key": "finding:1", "section": "findings"}, {"key": "config:a", "section": "config"},
                      {"key": "config:b", "section": "config"}]}
    kept = [{"key": "finding:1", "section": "findings", "first_seen": "2026-11-10"},
            {"key": "act:81|x", "section": "act", "first_seen": "2026-11-17"},
            {"key": "config:c", "section": "config", "first_seen": "2026-11-20"}]
    t = bundle.throughput(prev, kept, [], "2026-11-20")
    assert (t["closed"], t["new"], t["carried"], t["config"]) == (0, 1, 1, 1)
    assert "config changes 1 (counted apart)" in bundle._throughput_line(t)


def test_publish_writes_the_ledger_from_tonights_state(tmp_path, monkeypatch):
    from audit import publish_state as ps
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"generated_at": "2026-11-18T03:00:00+00:00", "findings": [C81, WARN]}))
    monkeypatch.setattr(ps, "STATE_FILE", state)
    monkeypatch.setattr(cl, "LEDGER", tmp_path / "ledger.json")
    ps.write_critical_ledger()
    led = cl.load(tmp_path / "ledger.json")
    assert list(led["items"]) == ["81|backend/gate/l6_review.py:_fail_closed|2026-11-17"]
    assert led["items"]["81|backend/gate/l6_review.py:_fail_closed|2026-11-17"]["nights"] == ["2026-11-17"]


def test_seed_replays_the_published_nights_oldest_first(tmp_path, monkeypatch):
    states = {"a1": {"generated_at": "2026-11-18T03:00:00+00:00", "findings": [C81]},
              "b2": {"generated_at": "2026-11-19T03:00:00+00:00", "findings": []},
              "c3": {"generated_at": "2026-11-21T02:34:00+00:00", "findings": [C71]},
              "d4": "not json"}
    calls = []

    def fake_run(cmd, capture_output=True, text=True, check=False):
        calls.append(cmd)
        out = "a1\nb2\nc3\nd4\n" if "log" in cmd else (
            lambda v: v if isinstance(v, str) else json.dumps(v))(states[cmd[-1].split(":")[0]])
        return type("P", (), {"stdout": out})()

    monkeypatch.setattr(cl.subprocess, "run", fake_run)
    p = tmp_path / "ledger.json"
    assert cl.seed("2026-11-16", path=p) == ["2026-11-17", "2026-11-18", "2026-11-20"]
    assert "--reverse" in calls[0] and "--since=2026-11-16" in calls[0]
    items = {i["key"]: i for i in cl.open_items(path=p)}
    assert items["81|backend/gate/l6_review.py:_fail_closed|2026-11-17"]["cleared"] == "2026-11-18"
    assert items["71|data/regime_signal_trace.jsonl|2026-11-20"]["nights"] == ["2026-11-20"]


def test_cli_dispose_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cl, "LEDGER", tmp_path / "ledger.json")
    cl.update([C81, C71], "2026-11-18")
    assert cl.main(["dispose", "81|backend/gate/l6_review.py:_fail_closed|2026-11-18", "--action", "fixed", "--ref", "0efa6a9"]) == 0
    out = capsys.readouterr().out
    assert "disposed 81|" in out and "OPEN  71|data/regime_signal_trace.jsonl|2026-11-18" in out and "OPEN  81|" not in out
    assert cl.main(["show"]) == 0


def test_publish_main_writes_the_ledger_after_routing_and_before_the_bundle(monkeypatch, tmp_path):
    """The order is the property: a bundle built before the ledger write would miss tonight's CRITICALs."""
    from audit import publish_state as ps
    order = []
    for name in ("write_fill_reconcile", "run_checks", "stamp_alert_channel", "route_criticals",
                 "write_critical_ledger", "publish_bundle", "check_watcher_ran", "check_ci_liveness"):
        monkeypatch.setattr(ps, name, (lambda n: lambda *a, **k: order.append(n))(name))
    monkeypatch.setattr(ps, "push_state", lambda: order.append("push_state") or "abcdef123456")
    monkeypatch.setattr(ps, "_liveness_due", lambda: True)
    monkeypatch.setattr(ps, "STATE_FILE", ps.REPO / "audit/state/latest.json")
    assert ps.main() == 0
    assert order.index("route_criticals") < order.index("write_critical_ledger") < order.index("publish_bundle")


def test_publish_line_counts_only_undisposed_episodes_as_open(tmp_path, monkeypatch, capsys):
    """2026-11-23: the line read "2 open" over two episodes disposed on 11-21. Open means no disposition."""
    from audit import publish_state as ps
    monkeypatch.setattr(cl, "LEDGER", tmp_path / "ledger.json")
    cl.update([C81, C71], "2026-11-18")
    assert cl.main(["dispose", "81|backend/gate/l6_review.py:_fail_closed|2026-11-18", "--action", "fixed", "--ref", "0efa6a9"]) == 0
    capsys.readouterr()
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"generated_at": "2026-11-19T03:00:00+00:00", "findings": []}))
    monkeypatch.setattr(ps, "STATE_FILE", state)
    ps.write_critical_ledger()
    out = capsys.readouterr().out
    assert "CRITICAL ledger: 1 open, 2 in the ledger after 2026-11-18" in out, out
