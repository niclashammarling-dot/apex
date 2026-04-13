# APEX Nightly Audit Instructions

You are the APEX Audit + Fixer system. Complete Phase 1 fully before starting Phase 2.

---

## Phase 1 — Audit

Check the codebase for bugs, inconsistencies, and violations of patterns learned from past incidents. Report findings only — do not fix anything in this phase.

### Codebase layout
- Gate pipeline: `backend/gate/gate_runner.py` (demo), `backend/gate/gate_runner_live.py` (live)
- Broker: `backend/brokers/alpaca.py`
- DB layer: `backend/db.py`
- Config: `backend/config.py`, `data/demo_config.json`, `data/live_config.json`
- Tests: `tests/`
- Frontend: `frontend/src/`

### Severity definitions
- **CRITICAL** — can cause incorrect trades, data loss, or silent wrong state right now without any user action required.
- **WARNING** — will cause incorrect behavior under a plausible condition (API down, edge-case input, config drift). Needs fixing soon.
- **INFO** — brittle pattern, missing logging, or style issue. No current risk but makes future bugs harder to catch.

### CHECK 1 — Result-dict sync hazard
Look for any place where a result dict is built early (e.g. `gate_decision` derived from `outcome` in `_gate_result()`) and then `outcome` is mutated later without updating the derived field before a DB write.
- At every DB insert (`insert_live_gate_result`, `update_signal_gate`, etc.), verify the stored `gate_decision` comes from the final `outcome`, not an earlier snapshot.
- Flag any pattern: dict built, then `result["outcome"] = Y` without matching `result["gate_decision"] = Y`, followed by a DB write that reads `result["gate_decision"]`.

### CHECK 2 — Exception-catch coverage in tests
For every test that uses `side_effect=Exception(...)` to mock a broker or external call:
- Does it assert only the return value (e.g. `result["outcome"]`)?
- Does it also assert what was passed to the DB insert mock (e.g. `insert_live_gate_result.call_args`)?
Flag tests that check the return value but NOT the persistence call args — the bug hides in the persistence path.

### CHECK 3 — Fractional qty in broker order placement
- In `backend/brokers/alpaca.py`, verify that `place_bracket_order` uses integer qty (floored), not fractional. Alpaca rejects fractional shares for bracket orders (error 42210000).
- Check any other order placement functions for the same issue.

### CHECK 4 — Config parity
Every config key must exist in all three places: `backend/config.py` (as constants), `data/demo_config.json`, and `data/live_config.json`.
- Extract all keys from each source and report any key present in one but missing from another.
- Only flag keys that are clearly runtime config (not hardcoded constants unrelated to trading config).

### CHECK 5 — Sector name strings
Sector names are load-bearing — they must exactly match the SECTORS list in `backend/config.py`.
- Find all hardcoded sector name strings in `.py`, `.ts`, and `.tsx` files.
- Flag any that don't match the canonical list (case-sensitive).

### CHECK 6 — Test DB isolation
Tests must never touch the production DB (`data/apex.db` or `backend/apex.db`).
- Verify `tests/conftest.py` redirects to a temp DB via `pytest_configure`.
- Search for any test file that directly references or opens either DB path.

### CHECK 7 — Demo/live gate runner parity
Demo and live runners should be structurally identical except for thresholds and Alpaca calls.
- Compare `gate_runner.py` and `gate_runner_live.py` for logic divergence that isn't threshold-related.
- Flag any lock, skip guard, context field, or candidate-filtering logic present in one but missing from the other.

### CHECK 8 — General code health
- Bare `except: pass` or `except Exception: pass` that swallows errors silently without logging.
- TODO / FIXME / HACK comments.
- Functions that return inconsistent types (e.g. sometimes a dict, sometimes None) where callers don't check.

### CHECK 9 — Config value drift against documented constraints
Flag any value outside these bounds:
- `lock1_threshold` demo: >= 0.65. Warn if < 0.65, CRITICAL if < 0.60.
- `lock1_threshold` live: >= 0.65.
- `vix_threshold`: >= 30.
- `take_profit_pct`: <= 0.08.
- `max_positions` demo: <= 6.
- Check git log: `git log --oneline -10 -- data/demo_config.json data/live_config.json`. Flag any config change in the last 7 days without a per-value justification in the commit message.

### CHECK 10 — Ticker signal data coverage (silent degradation canary)
- In `backend/db.py`, verify `get_ticker_daily_scores` uses `days=180` (not 90).
- Run SQLite query if `data/apex.db` is accessible:
  `SELECT ticker, COUNT(DISTINCT day) as day_count FROM (SELECT ticker, day FROM ticker_history WHERE day >= DATE('now', '-180 days') UNION SELECT ticker, DATE(timestamp) AS day FROM signals WHERE timestamp >= DATE('now', '-180 days')) GROUP BY ticker ORDER BY day_count ASC LIMIT 10;`
- WARNING if median ticker < 15 days (CONFIRMED_MIN). WARNING if < 21 days (EXTENDED_MIN).
- Check `backend/sector_regime.py` `compute_ticker_signals()` calls `get_ticker_daily_scores(days=180)`.

### CHECK 11 — NaN/null config pipeline
- Frontend `frontend/src/App.jsx`: find any `parseFloat(val)` without empty-string guard. `parseFloat("")` = NaN; `NaN ?? ""` does not fall back. Correct: `val === "" ? null : parseFloat(val)`.
- Backend `backend/gate/lock1_eligibility.py`: find `cfg.get(key, default)` for numeric fields used in comparisons. Stored None bypasses the default. Check: `macro_event_blackout_days`, `macro_earnings_blackout_days`, `vix_threshold`, `gate_cooloff_hours`.

### Output format

Get today's date with `date +%Y-%m-%d`. Write findings to `audit/nightly-report-YYYY-MM-DD.md` (create `audit/` if needed).

Short summary only — no prose, no code blocks. Every finding is ONE row. Total length under 50 lines.

```
# APEX Nightly Audit — YYYY-MM-DD
N issues: X critical, Y warnings, Z info

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
| 1 Result-dict sync | ✓ or ⚠ | INFO | gate_runner.py:149 | one-line description |

## Retirement Candidates
None
```

Status: `✓` = clean, `⚠` = has findings. Sev: CRITICAL / WARNING / INFO / —. Multiple findings = multiple rows. File:line mandatory for every non-clean row.

### CHECK 19 — Breakout volume floor integrity
Verify that breakout signal classification enforces minimum volume backing.
- Confirm `BREAKOUT_MIN_VOLUME` is defined in `backend/sector_regime.py` and set to ≥ 0.40.
- Confirm `get_latest_ticker_volume_scores()` exists in `backend/db.py` and its query anchors to `DATE(timestamp) < DATE('now')` (EOD, not intraday).
- Confirm `compute_ticker_signals()` calls `get_latest_ticker_volume_scores()` and applies the floor before assigning `signal = "breakout"`.
- Flag any breakout classification path that bypasses the volume check as CRITICAL.

### CHECK 18 — Audit pipeline completeness
Every `.py` script in `audit/` must be explicitly called somewhere in these instructions.
- List all `.py` files in `audit/` (excluding `__pycache__`).
- Verify each one appears in a `python3 audit/...` command in this file.
- Flag any script present in `audit/` but not referenced here as WARNING — it is dead code.

### Update the registry

After writing the report, run this Python script. Set `triggered` to check numbers that had at least one finding.

```bash
python3 - <<'PYEOF'
from datetime import date, timedelta
import subprocess
today = date.today().isoformat()
CHECKS_PATH = "audit/CHECKS.md"
RETIREMENT_DAYS = 90
triggered = []  # replace with actual triggered check numbers
with open(CHECKS_PATH) as f:
    lines = f.read().splitlines()
new_lines = []
retirement_candidates = []
for line in lines:
    parts = [p.strip() for p in line.split('|')]
    if len(parts) >= 9 and parts[1].isdigit():
        num = int(parts[1])
        name, added, prompted, files = parts[2], parts[3], parts[4], parts[5]
        lt = today if num in triggered else parts[6]
        lc = parts[7] if num in triggered else today
        try:
            days_since = (date.today() - date.fromisoformat(lt)).days
            if days_since >= RETIREMENT_DAYS:
                since = (date.today() - timedelta(days=RETIREMENT_DAYS)).isoformat()
                if any(subprocess.run(['git','log','--oneline',f'--since={since}','--',f],
                       capture_output=True, text=True).stdout.strip() for f in files.split(',')):
                    retirement_candidates.append((num, name, days_since))
        except Exception:
            pass
        new_lines.append(f'| {num} | {name} | {added} | {prompted} | {files} | {lt} | {lc} |')
    else:
        new_lines.append(line)
with open(CHECKS_PATH, 'w') as f:
    f.write('\n'.join(new_lines) + '\n')
for num, name, days in retirement_candidates:
    print(f'RETIRE CHECK {num} ({name}) - {days} days since last triggered')
PYEOF
```

Run the LLM finding verifier — strips template artifacts and tags unverified findings in place:
```
python3 audit/verify_llm_findings.py
```

Commit the report and registry to master:
```
git add audit/nightly-report-YYYY-MM-DD.md audit/CHECKS.md
git commit -m "audit: nightly report YYYY-MM-DD"
git push
```

---

## Phase 2 — Fixer Agent

After committing the audit report, run:
```
python3 audit/fixer.py
```

This reads today's report, applies Pattern A fixes (bare except with no logging) to INFO findings, and opens a PR if anything was fixed. It will not push to master. Let it run to completion without intervention.
