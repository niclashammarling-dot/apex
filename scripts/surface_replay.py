"""Surface replay for the phase 3 column rename: what the dashboard and Claude read, before against after.

Vault: raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md § Verification, "Surfaces" (acceptance
criterion 2026-10-26: "det måste synas rätt överallt"). Each checkout calls its own readers behind the surfaces on
its own database: the parent on the pre-merge snapshot, the branch on that snapshot after the rename. The outputs
are compared after the column map; any other difference is a finding.
  - both gate-history responses' rows (get_*_gate_history + lock_codes.decorate_rows) for the week 11-02..11-06;
  - both funnels (get_*_gate_funnel_counts) over all rows and over the week;
  - Claude's gate history for the tickers the week evaluated (get_ticker_gate_fails, L6's context);
  - the weekly report funnel (weekly_report._funnel) for both books, the week 11-02..11-07.
Settings and Promote read config keys, not columns (phase 3 part a keys, already on the branch); the locks
table (as_api) changes by design and is not compared.

Usage: .venv/bin/python -u scripts/surface_replay.py --root CHECKOUT --db DB OUT.json
       .venv/bin/python -u scripts/surface_replay.py --compare PARENT.json BRANCH.json [SUMMARY.json]
The run outputs are large (every row of the week); SUMMARY.json keeps both producer blocks, each surface's size and
the differing surfaces, for archiving.
"""
import json
import subprocess
import sys
from pathlib import Path

COLUMN_MAP = {
    "lock1_pass": "l3_score_pass", "lock2_pass": "l4_sentiment_pass", "lock_leading_pass": "l5_positioning_pass",
    "lock3_pass": "l6_review_pass", "lock_leading_checks": "l5_positioning_checks",
    "l2_summary": "l4_sentiment_summary", "lock3_sentiment_score": "l4_sentiment_score",
    "lock3_conviction": "l4_sentiment_conviction", "macro_reason": "l1_eligibility_reason",
    "lock3_reasoning": "decision_reason",
}
SINCE, UNTIL = "2026-11-02", "2026-11-07"


def _map(x):
    if isinstance(x, dict):
        return {COLUMN_MAP.get(k, k): _map(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_map(v) for v in x]
    return x


def run(root: Path, dbfile: Path, out: Path) -> None:
    sys.path.insert(0, str(root))
    import backend.db as db
    db.DB_PATH = dbfile
    from backend import weekly_report as wr
    from backend.gate import lock_codes
    res = {}
    for book, hist, funnel in (("live", db.get_live_gate_history, db.get_live_gate_funnel_counts),
                               ("demo", db.get_demo_gate_history, db.get_demo_gate_funnel_counts)):
        rows = hist(since=SINCE, limit=None)
        res[f"{book}_history"] = lock_codes.decorate_rows([dict(r) for r in rows], book)
        res[f"{book}_funnel_all"] = funnel()
        res[f"{book}_funnel_week"] = funnel(since=SINCE)
        res[f"{book}_weekly_report_funnel"] = wr._funnel(book, SINCE, UNTIL)
    tickers = sorted({r["ticker"] for r in res["live_history"]})
    res["claude_gate_history"] = {t: db.get_ticker_gate_fails(t) for t in tickers}
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    producer = {"repo": "apex", "commit": commit, "script": "scripts/surface_replay.py",
                "run": f".venv/bin/python -u {Path(__file__).resolve()} --root {root} --db {dbfile} {out}",
                "why": "raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md § Verification (Surfaces)",
                "db": f"{dbfile} (read through the checkout's readers)"}
    out.write_text(json.dumps({"producer": producer, **res}, indent=1, default=str))
    print({k: len(v) if hasattr(v, "__len__") else v for k, v in res.items()})


def compare(parent: Path, branch: Path, summary: Path | None = None) -> int:
    p, b = json.loads(parent.read_text()), json.loads(branch.read_text())
    diffs = []
    for k in sorted(set(p) | set(b)):
        if k == "producer":
            continue
        if _map(p.get(k)) != b.get(k):
            diffs.append(k)
            pa, ba = _map(p.get(k)), b.get(k)
            if isinstance(pa, list) and isinstance(ba, list):
                for i, (x, y) in enumerate(zip(pa, ba)):
                    if x != y:
                        keys = sorted(kk for kk in set(x) | set(y) if x.get(kk) != y.get(kk))
                        print(f"DIFF {k}[{i}] keys {keys}")
                        break
            else:
                print(f"DIFF {k}")
    print(f"{len([k for k in p if k != 'producer'])} surfaces, {len(diffs)} differing: {diffs}")
    if summary:
        me = Path(__file__).resolve()
        summary.write_text(json.dumps({
            "producer": {**b["producer"], "run": f".venv/bin/python -u {me} --compare {parent} {branch} {summary}"},
            "parent_producer": p["producer"], "branch_producer": b["producer"],
            "sizes": {k: len(v) for k, v in b.items() if k != "producer" and hasattr(v, "__len__")},
            "differing": diffs}, indent=1))
    return 1 if diffs else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "--compare":
        sys.exit(compare(Path(a[1]), Path(a[2]), Path(a[3]) if len(a) > 3 else None))
    run(Path(a[a.index("--root") + 1]).resolve(), Path(a[a.index("--db") + 1]).resolve(), Path(a[-1]))
