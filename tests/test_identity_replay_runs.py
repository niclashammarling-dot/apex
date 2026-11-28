"""The identity replay runs on the current tree (2026-11-28).

Why: scripts/identity_replay.py is the decision test of every lock-labels move (R068 a), used only at a merge. Between
uses nothing ran it: from 2026-11-21 (M4 entered the demo book, gate_runner.run started setting the loss cap) it failed on
master with a KeyError, found only when phase 4a needed it. It was also edited on 2026-11-28 (the cap for both
checkouts, --earnings-live) without a test. This runs it each suite, in its own process (it patches real modules), and
checks the flag it gained changes what it should.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "identity_replay.py"
# Production files the conftest redirects; the replay runs outside that redirect, so the test reads their mtimes.
GUARDED = [REPO / "data" / f for f in ("regime_result_cache.json", "regime_signal_trace.jsonl", "bayesian_multiplier_stats.json")]


def _replay(tmp_path: Path, *flags: str) -> dict:
    out = tmp_path / ("replay" + "".join(f.strip("-") for f in flags) + ".json")
    # No key, as in CI: the replay must reach its stubbed Claude anyway (CI 402 found it did not). load_dotenv never
    # overrides a set variable, so the empty value holds.
    env = {**os.environ, "ANTHROPIC_API_KEY": ""}
    r = subprocess.run([sys.executable, "-u", str(SCRIPT), "--root", str(REPO), *flags, str(out)],
                       capture_output=True, text=True, timeout=300, cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(out.read_text())


def test_the_replay_runs_and_its_earnings_flag_exercises_the_rule(tmp_path):
    before = {p: p.stat().st_mtime for p in GUARDED if p.exists()}
    plain = _replay(tmp_path)
    live = _replay(tmp_path, "--earnings-live")
    assert {p: p.stat().st_mtime for p in GUARDED if p.exists()} == before        # nothing written to production
    codes = lambda res: {r["ticker"]: r["row"].get("stop_code") for r in res["rows"]}
    p, e = codes(plain), codes(live)
    assert len(plain["rows"]) == len(live["rows"]) == 19
    assert all(p.values())                                                          # every candidate wrote a row
    assert p["AAE"] == "TRADE_EXECUTED"                                             # shadow mode: the blackout blocks nothing
    assert p["AAM"] == "FILTERED_L6_REVIEW_CLAUDE"                                  # the stubbed Claude decided, not a missing key
    assert e["AAE"] == "FILTERED_L2_RISK_EARNINGS"                                  # --earnings-live: it acts
    assert e["AAQ"] == "FILTERED_L2_RISK_EARNINGS_NEAR"                             # the near penalty decides L3
    assert live["producer"]["earnings_live"] is True and plain["producer"]["earnings_live"] is False
