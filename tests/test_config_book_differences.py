"""The demo and live config files differ exactly as data/config_book_differences.json lists (2026-11-28).

Why (Niclas 2026-11-28, with R151): "när skillnaderna är avsiktliga är det värt att skriva ner vilka de är. Då går det
att skilja en avsiktlig skillnad från en som har glidit dit. Demos tak på 1 000 dollar i stället för 100 var ett sådant
fall: ingen visste att det stod där." Book parity (tests/test_book_parity.py) reads rules, not values. This reads the
values: a key that differs without a list entry fails, a listed key whose values changed fails, and a listed key that
no longer differs fails, so the list cannot go stale in either direction. A Settings save or a Promote that changes a
differing key updates the list in the same commit, with its reason.
Vault: raw/notes/2026-11/2026-11-28-apex-get-config-readers-money-path-scope.md § Not this row.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE, DEMO = ROOT / "data" / "live_config.json", ROOT / "data" / "demo_config.json"
LIST = ROOT / "data" / "config_book_differences.json"
ABSENT = "<absent>"


def mismatches(live: dict, demo: dict, listed: dict) -> list[str]:
    """Each way the two files' differences disagree with the list, as one line each; [] when they agree."""
    actual = {k: {"live": live.get(k, ABSENT), "demo": demo.get(k, ABSENT)}
              for k in sorted(set(live) | set(demo)) if live.get(k, ABSENT) != demo.get(k, ABSENT)}
    out = []
    for k, v in actual.items():
        if k not in listed:
            out.append(f"{k}: live {v['live']!r}, demo {v['demo']!r} differ and are not listed")
        elif {"live": listed[k]["live"], "demo": listed[k]["demo"]} != v:
            out.append(f"{k}: listed live {listed[k]['live']!r} / demo {listed[k]['demo']!r}, "
                       f"files now live {v['live']!r} / demo {v['demo']!r}")
    out += [f"{k}: listed as a difference, but the files agree" for k in listed if k not in actual]
    return out


def test_the_files_differ_exactly_as_listed():
    listed = json.loads(LIST.read_text())["differences"]
    found = mismatches(json.loads(LIVE.read_text()), json.loads(DEMO.read_text()), listed)
    assert found == [], ("data/live_config.json and data/demo_config.json disagree with "
                         "data/config_book_differences.json. If the change is intended, update the list in the same "
                         "commit with its reason; if not, restore the file:\n  " + "\n  ".join(found))


def test_every_entry_says_why_and_since_when():
    listed = json.loads(LIST.read_text())["differences"]
    assert all(e.get("why") and e.get("since") for e in listed.values()), listed


def test_the_comparison_sees_each_kind_of_drift():
    live, demo = {"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "c": 3}
    listed = {"b": {"live": 2, "demo": 5}}
    assert mismatches(live, demo, listed) == []
    assert mismatches(live, {**demo, "a": 9}, listed) == ["a: live 1, demo 9 differ and are not listed"]
    assert mismatches(live, {**demo, "b": 6}, listed) == ["b: listed live 2 / demo 5, files now live 2 / demo 6"]
    assert mismatches(live, {**demo, "b": 2}, listed) == ["b: listed as a difference, but the files agree"]
    assert mismatches({**live, "d": 0}, demo, listed) == ["d: live 0, demo '<absent>' differ and are not listed"]
