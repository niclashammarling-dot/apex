"""backend/macro_calendar.py — one official calendar for Lock 1 and both engines (2026-10-17)."""
from collections import Counter
from datetime import date

from backend.macro_calendar import CPI_DATES, FOMC_DATES, MACRO_DATES, NFP_DATES


def test_counts_per_year_and_no_weekends():
    for s, per in ((FOMC_DATES, 8), (CPI_DATES, 12), (NFP_DATES, 12)):
        c = Counter(d.year for d in s)
        assert set(c) == set(range(2015, 2028))
        assert all(n == per for y, n in c.items() if y != 2025 or s is FOMC_DATES)
    assert Counter(d.year for d in CPI_DATES)[2025] == 11 == Counter(d.year for d in NFP_DATES)[2025]
    assert all(d.weekday() < 5 for d in MACRO_DATES)


def test_spot_dates_from_the_sources():
    # BLS archive file names / schedules, Fed calendars (read 2026-10-17)
    assert {date(2022, 3, 10), date(2024, 1, 11), date(2025, 10, 24), date(2026, 9, 11)} <= CPI_DATES
    assert {date(2021, 1, 8), date(2026, 1, 9), date(2027, 1, 8)} <= NFP_DATES
    assert date(2021, 1, 1) not in NFP_DATES                      # first-Friday rule's holiday
    assert {date(2024, 11, 7), date(2020, 3, 18), date(2027, 12, 8)} <= FOMC_DATES
    assert not {date(2020, 3, 2), date(2020, 3, 15), date(2025, 8, 22)} & FOMC_DATES   # unscheduled
