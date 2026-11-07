"""Read-only probe: what did L6 REVIEW (Claude) reject on, and when did it stop? (Saturday 2026-11-07)

Vault: raw/notes/2026-11/2026-11-07-apex-l5-rejections-were-the-cap-and-check-90s-window-hid-the-stop.md.
Asked by the 11-06 bundle's CHECK 90 item (L5 approval 6/6 before 11-02, 14/14 since) and Niclas 2026-11-07:
how long is the window, and where did T7's 44 L5 rejections come from?

Definitions are CHECK 90's (audit/checks_gate.py check90): an approval is l6_review_pass = 1, a rejection is
stop_code L6 claude; L6 unavailable (fail-closed) is counted apart, never as Claude's judgement. The unit
is the ticker-day, as in T7 (raw/notes/2026-10/2026-10-20-apex-rejected-candidate-card.md: 44 L5-rejected
ticker-days 2026-07-07..10-09).

Primary reason: every rejection ticker-day's decision_reason read by hand on 2026-11-07 (a keyword class
on the first 200 characters was tried first and failed its control: MRNA 10-09 and LLY 10-15 mention the
sector cap only to say it does not bind). Classes, by the block Claude names first:
  hard  a limit at or over its value: open_positions >= max_positions (live has no hard position cap, the
        overflow threshold applies, so this is stricter than the system), or sector exposure >= the flat 30 %
        max_sector_exposure L5 was given until 10-19 (execution enforces risk_limits.sector_cap);
  near  headroom under a limit (sector near 30 %, the last slot);
  held  the ticker is likely already open (the runner removes held tickers before the chain);
  own   Claude's own reading of the signals or gate history, with every limit stated as within bounds.
A day with several rejections takes its first rejection's class (09-22 AAPL: own, then hard).

Known-present controls (the run exits 2 if any misses):
  - CHECK 90's 11-06 line is reproduced in rows: 6/6 approvals 2026-10-19..11-01, 14/14 from 11-02;
  - every rejection ticker-day has a hand label, and every label has a rejection.

Opens data/apex.db with mode=ro; writes only OUT.json.
Usage: .venv/bin/python -u scripts/l5_rejection_probe.py OUT.json
"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.gate.lock_codes import stop_code  # noqa: E402

DB = ROOT / "data" / "apex.db"
SINCE = "2026-07-07"                      # live_account_since, T7's start
T7_END = "2026-10-10"                     # T7 rows 07-07..10-09
CHANGE = "2026-10-19"                     # _C90_CHANGES first row: L5 sees risk_limits.sector_cap
WHY = "raw/notes/2026-11/2026-11-07-apex-l5-rejections-were-the-cap-and-check-90s-window-hid-the-stop.md"
LABELS = {  # 2026-MM-DD ticker -> class, read by hand 2026-11-07 (docstring)
    **{f'2026-{x}': 'hard' for x in (
        '07-07 GE', '07-07 MRNA', '07-10 JNJ', '07-13 JNJ', '07-20 TMO', '08-13 ORCL',
        '08-13 NVDA', '08-13 ANET', '08-14 NVDA', '08-17 PFE', '08-18 ADBE', '08-18 JNJ',
        '08-18 LLY', '08-18 PFE', '08-24 CVX', '08-25 CVX', '09-08 HAL', '09-08 AMD',
        '09-11 NOW', '09-23 AAPL', '09-23 NOW', '09-24 AAPL', '09-24 CRM', '09-24 ISRG',
        '09-25 ISRG', '09-28 ISRG', '09-29 ISRG', '09-29 AMD', '09-29 AMAT', '09-30 ISRG',
        '09-30 LLY', '10-01 TER', '10-01 MU', '10-01 ON', '10-09 MRNA',
    )},
    **{f'2026-{x}': 'near' for x in (
        '09-30 TER', '10-08 V', '10-15 OXY', '10-16 OXY',
    )},
    **{f'2026-{x}': 'held' for x in (
        '07-23 EOG',
    )},
    **{f'2026-{x}': 'own' for x in (
        '08-24 JNJ', '09-21 AAPL', '09-22 AAPL', '10-15 LLY', '10-16 LLY',
    )},
}


def main(out: str) -> int:
    claude, unavail = stop_code(6, "claude"), stop_code(6, "unavailable")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT timestamp, date(timestamp), ticker, l6_review_pass, stop_code, decision_reason FROM live_gate_history "
        "WHERE timestamp >= ? AND (l6_review_pass = 1 OR stop_code IN (?, ?)) ORDER BY timestamp",
        (SINCE, claude, unavail)).fetchall()
    last_row = conn.execute("SELECT max(timestamp) FROM live_gate_history").fetchone()[0]
    conn.close()

    def tdays(lo, hi, codes, approved):
        return {(d, t) for ts, d, t, ok, sc, _ in rows if lo <= d < hi and (sc in codes or (approved and ok == 1))}

    periods = {"before_change": (SINCE, CHANGE), "since_change": (CHANGE, "9999"), "t7_window": (SINCE, T7_END)}
    counts = {k: {"approved": len(tdays(lo, hi, (), True)), "rejected": len(tdays(lo, hi, (claude,), False)),
                  "fail_closed": len(tdays(lo, hi, (unavail,), False))} for k, (lo, hi) in periods.items()}

    classes = ("hard", "near", "held", "own")
    rejected = sorted({f"{d} {t}" for ts, d, t, ok, sc, _ in rows if sc == claude})

    def split(days):
        m = {"ticker_days": len(days), **{c: sorted(x for x in days if LABELS.get(x) == c) for c in classes}}
        m["counts"] = {c: len(m[c]) for c in classes}
        return m

    t7 = split([x for x in rejected if x < T7_END])
    pre = split([x for x in rejected if x < CHANGE])
    last_rej = max((ts for ts, d, t, ok, sc, _ in rows if sc == claude), default=None)

    def c90(lo, hi):   # CHECK 90's unit: rows, approvals over approvals + Claude's rejections
        part = [(ok, sc) for ts, d, t, ok, sc, _ in rows if lo <= d < hi and (ok == 1 or sc == claude)]
        return sum(1 for ok, _ in part if ok == 1), len(part)

    misses = []
    if c90(CHANGE, "2026-11-02") != (6, 6) or c90("2026-11-02", "2026-11-07") != (14, 14):
        misses.append(f"CHECK 90 line not reproduced: {c90(CHANGE, '2026-11-02')} / {c90('2026-11-02', '2026-11-07')}")
    if set(rejected) != set(LABELS):
        misses.append(f"labels vs rejections: unlabelled {sorted(set(rejected) - set(LABELS))}, "
                      f"no rejection {sorted(set(LABELS) - set(rejected))}")

    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    result = {
        "producer": {"repo": "apex", "commit": commit, "script": "scripts/l5_rejection_probe.py",
                     "run": f".venv/bin/python -u scripts/l5_rejection_probe.py {out}", "why": WHY,
                     "run_at": datetime.now(timezone.utc).isoformat(), "db": "data/apex.db (mode=ro)",
                     "unit": "ticker-day", "since": SINCE, "last_row": last_row,
                     "classes": "hand-read 2026-11-07, docstring"},
        "counts": counts, "last_claude_rejection": last_rej,
        "primary_reason_t7_window": t7, "primary_reason_before_change": pre,
        "controls": {"missed": misses},
    }
    Path(out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("counts", "last_claude_rejection", "primary_reason_t7_window",
                                             "primary_reason_before_change", "controls")}, indent=1))
    return 2 if misses else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
