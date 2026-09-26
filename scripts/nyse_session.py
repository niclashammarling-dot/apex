"""
Exit 0 if the date is an NYSE session, 1 if not, 2 if the calendar lookup
failed. Date: argv[1] (YYYY-MM-DD) or today in America/New_York.

Used by scripts/market_window.sh and scripts/eod_window.sh before starting
the backend (2026-09-26). Imports nothing from backend/.
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


def main() -> int:
    d = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else \
        datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    try:
        import pandas_market_calendars as mcal
        sched = mcal.get_calendar("NYSE").schedule(start_date=d, end_date=d)
    except Exception as e:
        print(f"calendar lookup failed: {e}", file=sys.stderr)
        return 2
    return 0 if len(sched) else 1


if __name__ == "__main__":
    sys.exit(main())
