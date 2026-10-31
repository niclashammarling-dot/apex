"""
US macro event calendar, 2015-2027, from the official sources (2026-10-17).

One source for Lock 1's blackout and both backtest engines. Before this, live
(l1_eligibility.py) and engine.py each kept their own copy, engine_fast had
none, and both copies had drifted: engine.py's CPI was wrong on 10 dates and its
first-Friday NFP rule on 15 (e.g. 2021-01-01, a holiday) between 2021 and
2026-10-16, and it had no dates before 2021.

- FOMC_DATES: scheduled meetings, decision (second) day. 2015-2020 from
  federalreserve.gov/monetarypolicy/fomchistorical<year>.htm, 2021-2027 from
  /fomccalendars.htm (2027 tentative until each preceding meeting). Unscheduled
  meetings and notation votes are left out (2020-03-02, 2020-03-15, 2025-08-22):
  no calendar could have known them in advance. The cancelled 2020-03-18 meeting
  is kept, because the schedule listed it.
- CPI_DATES / NFP_DATES: release dates of the Consumer Price Index and The
  Employment Situation. 2015 to 2026-10 from the BLS release archives
  (bls.gov/bls/news-release/cpi.htm, /empsit.htm: the date is in each archived
  release's file name), the rest of 2026 and 2027 from bls.gov/schedule/<year>.
  2025 has 11 of each: the October 2025 releases were cancelled in the shutdown.
Read 2026-10-17. CHECK 91 watches the horizon.
"""
from __future__ import annotations

from datetime import date

FOMC_DATES: frozenset[date] = frozenset({
    date(2015, 1, 28), date(2015, 3, 18), date(2015, 4, 29), date(2015, 6, 17),
    date(2015, 7, 29), date(2015, 9, 17), date(2015, 10, 28), date(2015, 12, 16),
    date(2016, 1, 27), date(2016, 3, 16), date(2016, 4, 27), date(2016, 6, 15),
    date(2016, 7, 27), date(2016, 9, 21), date(2016, 11, 2), date(2016, 12, 14),
    date(2017, 2, 1), date(2017, 3, 15), date(2017, 5, 3), date(2017, 6, 14),
    date(2017, 7, 26), date(2017, 9, 20), date(2017, 11, 1), date(2017, 12, 13),
    date(2018, 1, 31), date(2018, 3, 21), date(2018, 5, 2), date(2018, 6, 13),
    date(2018, 8, 1), date(2018, 9, 26), date(2018, 11, 8), date(2018, 12, 19),
    date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1), date(2019, 6, 19),
    date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    date(2020, 1, 29), date(2020, 3, 18), date(2020, 4, 29), date(2020, 6, 10),
    date(2020, 7, 29), date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
})

CPI_DATES: frozenset[date] = frozenset({
    date(2015, 1, 16), date(2015, 2, 26), date(2015, 3, 24), date(2015, 4, 17),
    date(2015, 5, 22), date(2015, 6, 18), date(2015, 7, 17), date(2015, 8, 19),
    date(2015, 9, 16), date(2015, 10, 15), date(2015, 11, 17), date(2015, 12, 15),
    date(2016, 1, 20), date(2016, 2, 19), date(2016, 3, 16), date(2016, 4, 14),
    date(2016, 5, 17), date(2016, 6, 16), date(2016, 7, 15), date(2016, 8, 16),
    date(2016, 9, 16), date(2016, 10, 18), date(2016, 11, 17), date(2016, 12, 15),
    date(2017, 1, 18), date(2017, 2, 15), date(2017, 3, 15), date(2017, 4, 14),
    date(2017, 5, 12), date(2017, 6, 14), date(2017, 7, 14), date(2017, 8, 11),
    date(2017, 9, 14), date(2017, 10, 13), date(2017, 11, 15), date(2017, 12, 13),
    date(2018, 1, 12), date(2018, 2, 14), date(2018, 3, 13), date(2018, 4, 11),
    date(2018, 5, 10), date(2018, 6, 12), date(2018, 7, 12), date(2018, 8, 10),
    date(2018, 9, 13), date(2018, 10, 11), date(2018, 11, 14), date(2018, 12, 12),
    date(2019, 1, 11), date(2019, 2, 13), date(2019, 3, 12), date(2019, 4, 10),
    date(2019, 5, 10), date(2019, 6, 12), date(2019, 7, 11), date(2019, 8, 13),
    date(2019, 9, 12), date(2019, 10, 10), date(2019, 11, 13), date(2019, 12, 11),
    date(2020, 1, 14), date(2020, 2, 13), date(2020, 3, 11), date(2020, 4, 10),
    date(2020, 5, 12), date(2020, 6, 10), date(2020, 7, 14), date(2020, 8, 12),
    date(2020, 9, 11), date(2020, 10, 13), date(2020, 11, 12), date(2020, 12, 10),
    date(2021, 1, 13), date(2021, 2, 10), date(2021, 3, 10), date(2021, 4, 13),
    date(2021, 5, 12), date(2021, 6, 10), date(2021, 7, 13), date(2021, 8, 11),
    date(2021, 9, 14), date(2021, 10, 13), date(2021, 11, 10), date(2021, 12, 10),
    date(2022, 1, 12), date(2022, 2, 10), date(2022, 3, 10), date(2022, 4, 12),
    date(2022, 5, 11), date(2022, 6, 10), date(2022, 7, 13), date(2022, 8, 10),
    date(2022, 9, 13), date(2022, 10, 13), date(2022, 11, 10), date(2022, 12, 13),
    date(2023, 1, 12), date(2023, 2, 14), date(2023, 3, 14), date(2023, 4, 12),
    date(2023, 5, 10), date(2023, 6, 13), date(2023, 7, 12), date(2023, 8, 10),
    date(2023, 9, 13), date(2023, 10, 12), date(2023, 11, 14), date(2023, 12, 12),
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12), date(2024, 4, 10),
    date(2024, 5, 15), date(2024, 6, 12), date(2024, 7, 11), date(2024, 8, 14),
    date(2024, 9, 11), date(2024, 10, 10), date(2024, 11, 13), date(2024, 12, 11),
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12), date(2025, 4, 10),
    date(2025, 5, 13), date(2025, 6, 11), date(2025, 7, 15), date(2025, 8, 12),
    date(2025, 9, 11), date(2025, 10, 24), date(2025, 12, 18),
    date(2026, 1, 13), date(2026, 2, 13), date(2026, 3, 11), date(2026, 4, 10),
    date(2026, 5, 12), date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12),
    date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 10), date(2026, 12, 10),
    date(2027, 1, 13), date(2027, 2, 11), date(2027, 3, 10), date(2027, 4, 13),
    date(2027, 5, 12), date(2027, 6, 10), date(2027, 7, 14), date(2027, 8, 11),
    date(2027, 9, 14), date(2027, 10, 14), date(2027, 11, 10), date(2027, 12, 10),
})

NFP_DATES: frozenset[date] = frozenset({
    date(2015, 1, 9), date(2015, 2, 6), date(2015, 3, 6), date(2015, 4, 3),
    date(2015, 5, 8), date(2015, 6, 5), date(2015, 7, 2), date(2015, 8, 7),
    date(2015, 9, 4), date(2015, 10, 2), date(2015, 11, 6), date(2015, 12, 4),
    date(2016, 1, 8), date(2016, 2, 5), date(2016, 3, 4), date(2016, 4, 1),
    date(2016, 5, 6), date(2016, 6, 3), date(2016, 7, 8), date(2016, 8, 5),
    date(2016, 9, 2), date(2016, 10, 7), date(2016, 11, 4), date(2016, 12, 2),
    date(2017, 1, 6), date(2017, 2, 3), date(2017, 3, 10), date(2017, 4, 7),
    date(2017, 5, 5), date(2017, 6, 2), date(2017, 7, 7), date(2017, 8, 4),
    date(2017, 9, 1), date(2017, 10, 6), date(2017, 11, 3), date(2017, 12, 8),
    date(2018, 1, 5), date(2018, 2, 2), date(2018, 3, 9), date(2018, 4, 6),
    date(2018, 5, 4), date(2018, 6, 1), date(2018, 7, 6), date(2018, 8, 3),
    date(2018, 9, 7), date(2018, 10, 5), date(2018, 11, 2), date(2018, 12, 7),
    date(2019, 1, 4), date(2019, 2, 1), date(2019, 3, 8), date(2019, 4, 5),
    date(2019, 5, 3), date(2019, 6, 7), date(2019, 7, 5), date(2019, 8, 2),
    date(2019, 9, 6), date(2019, 10, 4), date(2019, 11, 1), date(2019, 12, 6),
    date(2020, 1, 10), date(2020, 2, 7), date(2020, 3, 6), date(2020, 4, 3),
    date(2020, 5, 8), date(2020, 6, 5), date(2020, 7, 2), date(2020, 8, 7),
    date(2020, 9, 4), date(2020, 10, 2), date(2020, 11, 6), date(2020, 12, 4),
    date(2021, 1, 8), date(2021, 2, 5), date(2021, 3, 5), date(2021, 4, 2),
    date(2021, 5, 7), date(2021, 6, 4), date(2021, 7, 2), date(2021, 8, 6),
    date(2021, 9, 3), date(2021, 10, 8), date(2021, 11, 5), date(2021, 12, 3),
    date(2022, 1, 7), date(2022, 2, 4), date(2022, 3, 4), date(2022, 4, 1),
    date(2022, 5, 6), date(2022, 6, 3), date(2022, 7, 8), date(2022, 8, 5),
    date(2022, 9, 2), date(2022, 10, 7), date(2022, 11, 4), date(2022, 12, 2),
    date(2023, 1, 6), date(2023, 2, 3), date(2023, 3, 10), date(2023, 4, 7),
    date(2023, 5, 5), date(2023, 6, 2), date(2023, 7, 7), date(2023, 8, 4),
    date(2023, 9, 1), date(2023, 10, 6), date(2023, 11, 3), date(2023, 12, 8),
    date(2024, 1, 5), date(2024, 2, 2), date(2024, 3, 8), date(2024, 4, 5),
    date(2024, 5, 3), date(2024, 6, 7), date(2024, 7, 5), date(2024, 8, 2),
    date(2024, 9, 6), date(2024, 10, 4), date(2024, 11, 1), date(2024, 12, 6),
    date(2025, 1, 10), date(2025, 2, 7), date(2025, 3, 7), date(2025, 4, 4),
    date(2025, 5, 2), date(2025, 6, 6), date(2025, 7, 3), date(2025, 8, 1),
    date(2025, 9, 5), date(2025, 11, 20), date(2025, 12, 16),
    date(2026, 1, 9), date(2026, 2, 11), date(2026, 3, 6), date(2026, 4, 3),
    date(2026, 5, 8), date(2026, 6, 5), date(2026, 7, 2), date(2026, 8, 7),
    date(2026, 9, 4), date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4),
    date(2027, 1, 8), date(2027, 2, 5), date(2027, 3, 5), date(2027, 4, 2),
    date(2027, 5, 7), date(2027, 6, 4), date(2027, 7, 2), date(2027, 8, 6),
    date(2027, 9, 3), date(2027, 10, 8), date(2027, 11, 5), date(2027, 12, 3),
})

MACRO_DATES: frozenset[date] = FOMC_DATES | CPI_DATES | NFP_DATES


def macro_status(today: date, fomc_pre_days: int = 2, cpi_nfp_pre_days: int = 1) -> tuple[str, str | None]:
    """("hard_block", desc), ("pre_event", desc) or ("clear", None) — Lock 1 B2's rule.

    FOMC: hard block from fomc_pre_days calendar days before the decision day through it.
    CPI / NFP: hard block on the release day; pre_event up to cpi_nfp_pre_days before.
    Post-event is clear (the print is known). A hard block wins over a pre_event on the
    same day. Live's loop over a set returned the first match, so the result could
    depend on iteration order when both applied (found 2026-10-17).
    """
    pre = None
    for d in sorted(MACRO_DATES):
        ahead = (d - today).days
        if ahead < 0:
            continue
        if ahead > max(fomc_pre_days, cpi_nfp_pre_days):
            break
        if d in FOMC_DATES and ahead <= fomc_pre_days:
            return "hard_block", f"FOMC on {d.isoformat()} (in {ahead}d)"
        if d in CPI_DATES or d in NFP_DATES:
            name = "CPI" if d in CPI_DATES else "NFP"
            if ahead == 0:
                return "hard_block", f"{name} on {d.isoformat()} (today)"
            if ahead <= cpi_nfp_pre_days and pre is None:
                pre = ("pre_event", f"{name} on {d.isoformat()} (in {ahead}d)")
    return pre or ("clear", None)
