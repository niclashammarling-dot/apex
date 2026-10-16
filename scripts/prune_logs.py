"""
Prune dated log files in logs/ older than KEEP_DAYS — the retention that actually runs.

Why (2026-10-16): backend/main.py sets loguru `retention="14 days"`, but loguru 0.7.3
applies retention only on an in-process rotation (`FileSink._terminate_file`,
`is_rotating`). Since the market-window launcher the backend no longer runs across
midnight, so nothing was pruned: 35 apex_*.log files 2026-09-02..10-15 on 10-16. The
other dated logs (market_window_, eod_window_, cycle_watch_) never had a retention.

14 days covers every reader with margin (2026-10-16 inventory): /api/ops/window reads
10 calendar days, CHECK 82 the trailing 4 sessions, CHECK 75 the last 3 apex_*.log
files, alert mails the same or next day. Evidence needed for longer belongs in a DB
row (outcome_reason, b3804fb), not in a log.

Only files named <prefix>_YYYY-MM-DD.log are touched; the date comes from the name,
never from mtime. Anything else (backend.log, frontend.log, loguru rename files) is
kept and listed as undated so it is visible, not silently skipped.

Run by scripts/market_window.sh at launch. CLI:
    python scripts/prune_logs.py [--log-dir DIR] [--keep-days N] [--dry-run]
Prints one summary line; exit 1 if the log dir is missing.
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 14
_DATED = re.compile(r"^[A-Za-z_]+_(\d{4}-\d{2}-\d{2})\.log$")


def prune(log_dir: Path, today: date, keep_days: int = KEEP_DAYS, dry_run: bool = False) -> dict:
    """Remove dated logs with date < today - keep_days. Returns removed/kept/undated names."""
    cutoff = today - timedelta(days=keep_days)
    removed, kept, undated = [], [], []
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        m = _DATED.match(path.name)
        if not m:
            undated.append(path.name)
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            undated.append(path.name)
            continue
        if d < cutoff:
            if not dry_run:
                path.unlink()
            removed.append(path.name)
        else:
            kept.append(path.name)
    return {"removed": removed, "kept": kept, "undated": undated, "cutoff": cutoff}


def summary(result: dict, dry_run: bool = False) -> str:
    verb = "would remove" if dry_run else "removed"
    oldest = min((n.rsplit("_", 1)[1][:10] for n in result["removed"]), default=None)
    return (f"log prune: {verb} {len(result['removed'])}"
            f"{f' (oldest {oldest})' if oldest else ''} before {result['cutoff']}, "
            f"kept {len(result['kept'])}, undated {len(result['undated'])}"
            f"{': ' + ', '.join(result['undated']) if result['undated'] else ''}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--log-dir", type=Path, default=Path(__file__).resolve().parent.parent / "logs")
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.log_dir.is_dir():
        print(f"log prune: log dir missing ({args.log_dir}) — nothing pruned")
        return 1
    result = prune(args.log_dir, date.today(), args.keep_days, args.dry_run)
    print(summary(result, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
