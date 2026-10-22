"""
Prune dated log files in logs/ older than KEEP_DAYS — the retention that actually runs.

Why (2026-10-16): backend/main.py sets loguru `retention="14 days"`, but loguru 0.7.3
applies retention only on an in-process rotation (`FileSink._terminate_file`,
`is_rotating`). Since the market-window launcher the backend no longer runs across
midnight, so nothing was pruned: 35 apex_*.log files 2026-09-02..10-15 on 10-16. The
other dated logs (market_window_, eod_window_, cycle_watch_) never had a retention.

Keep 90 days (Niclas ruling 2026-10-22, was 14). 14 days covered every scheduled reader
(2026-10-16 inventory: /api/ops/window 10 calendar days, CHECK 82 the trailing 4 sessions,
CHECK 75 the last 3 apex_*.log files, alert mails the same or next day), but the readers
that lost evidence this month were investigations, not schedules: three findings died or
limped on logs already pruned (the vol slope read had nothing before 09-02, the 08-04
incident could not be decided, the 09-26 cap-rejection baseline could not attribute 13
TRADE_REJECTED rows). Ruling: "Förläng till 90 dagar, och fortsätt flytta det som ska bära
beslut till databasen; det ena ersätter inte det andra." Evidence a decision rests on still
belongs in a DB row (outcome_reason, b3804fb); the longer window is for the question nobody
scheduled. Size: logs/ held 21 MB for about 15 days on 10-22, so about 130 MB at 90 days.
Recovery path: set KEEP_DAYS back; nothing reads the number but this script.

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

KEEP_DAYS = 90   # Niclas ruling 2026-10-22 (was 14 since 2026-10-16); see docstring
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
