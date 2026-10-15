"""Parameter provenance registry (2026-10-14): schema, value pin, ratchet.

Niclas 2026-10-14: four kinds of origin, engine and commit recorded when a value is
computed or tested, one registry beside the configs ("Ta en separat fil och gör ett
gemensamt register"). Reads files only; nothing here imports the modules it inventories.

Ratchet: the applied parameters WITHOUT an entry are pinned by name in
audit/PARAMETERS_UNREGISTERED.txt. A key not in it → a new parameter arrived without
provenance, and the failure names it. A listed key no longer missing → delete its line so
the improvement locks in. The backlog is a list, not a blocker (same shape as
test_check_registry_controls). Pinned by name since 2026-10-15: the count-only pin printed
the alphabetical tail as "Newest unregistered" (five live_config.json keys) when the new
one was backend/weekly_report.py::REFERENCE_WEEKS (CI run #139). Review 2026-10-18.
"""
import json
from pathlib import Path

from audit import parameters as P

BASELINE_FILE = Path(P.REPO) / "audit" / "PARAMETERS_UNREGISTERED.txt"


def _baseline(path: Path = BASELINE_FILE) -> set[str]:
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def ratchet_problems(missing: list[str], baseline: set[str]) -> list[str]:
    added = sorted(set(missing) - baseline)
    gone = sorted(baseline - set(missing))
    out = []
    if added:
        out.append(f"{len(added)} new parameter(s) without provenance — add an entry to audit/PARAMETERS.json "
                   f"for each: {added}")
    if gone:
        out.append(f"{len(gone)} listed parameter(s) now registered or removed — delete their lines from "
                   f"audit/PARAMETERS_UNREGISTERED.txt so the improvement locks in: {gone}")
    return out


def test_population_control():
    # Known-present members of each source: a scan that loses them is broken, not clean.
    vals = P.applied()
    assert "backend/regime/regime_bayes.py::_REGIME_BULL_EXIT" in vals
    assert "backend/regime/regime_bayes.py::POSTERIOR_DECAY" in vals
    assert "data/live_config.json:stop_loss_pct" in vals
    assert "data/demo_config.json:stop_loss_pct" in vals


def test_registry_entries_are_valid_and_current():
    found = P.problems(P.load_registry(), P.applied())
    assert not found, "\n".join(found)


def test_unregistered_set_is_pinned_both_ways():
    found = ratchet_problems(P.unregistered(P.load_registry(), P.applied()), _baseline())
    assert not found, "\n".join(found)


def test_ratchet_names_the_added_and_the_gone_key():
    # Positive control: the message names the exact key, not a tail of the list.
    base = {"data/live_config.json:vix_threshold", "backend/a.py::OLD"}
    found = ratchet_problems(["data/live_config.json:vix_threshold", "backend/a.py::OLD", "backend/z.py::NEW"], base)
    assert len(found) == 1 and "['backend/z.py::NEW']" in found[0] and "vix_threshold" not in found[0]
    found = ratchet_problems(["data/live_config.json:vix_threshold"], base)
    assert len(found) == 1 and "['backend/a.py::OLD']" in found[0] and "delete their lines" in found[0]
    assert ratchet_problems(sorted(base), base) == []


# ── positive controls: each failure mode fires on a synthetic repo ────────────

def _repo(tmp_path, value="0.70"):
    (tmp_path / "backend").mkdir()
    (tmp_path / "audit").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "backend" / "m.py").write_text(f"THRESH = {value}\nGAP = 0.37 - 0.04\nname = 1\n")
    (tmp_path / "data" / "live_config.json").write_text(json.dumps({"stop_loss_pct": 0.06, "flag": True}))
    return tmp_path


def test_population_reads_literals_without_import(tmp_path):
    vals = P.applied(_repo(tmp_path))
    assert vals == {"backend/m.py::THRESH": 0.70, "backend/m.py::GAP": 0.37 - 0.04,
                    "data/live_config.json:stop_loss_pct": 0.06}


def test_stale_value_fires(tmp_path):
    vals = P.applied(_repo(tmp_path, value="0.65"))
    reg = {"backend/m.py::THRESH": {"value": 0.70, "kind": "structural", "tier": "money", "source": "x"}}
    assert any("stale" in p for p in P.problems(reg, vals))


def test_kind_fields_and_tier_fire(tmp_path):
    vals = P.applied(_repo(tmp_path))
    reg = {
        "backend/m.py::THRESH": {"value": 0.70, "kind": "measured", "tier": "money", "source": "sweep"},
        "backend/m.py::GAP": {"value": 0.37 - 0.04, "kind": "derived", "tier": "fast", "source": "x",
                              "from": "backend/m.py::NOPE"},
        "data/live_config.json:stop_loss_pct": {"value": 0.06, "kind": "guess", "tier": "money"},
    }
    found = "\n".join(P.problems(reg, vals))
    assert "needs ['engine', 'commit', 'date', 'sample']" in found
    assert "has no registry entry" in found
    assert "tier 'fast'" in found
    assert "kind 'guess'" in found


def test_unknown_key_fires(tmp_path):
    vals = P.applied(_repo(tmp_path))
    reg = {"backend/m.py::GONE": {"value": 1, "kind": "structural", "tier": "ops", "source": "x"}}
    assert any("not an applied parameter" in p for p in P.problems(reg, vals))
