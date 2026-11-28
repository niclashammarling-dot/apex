"""The engines' earnings source (2026-11-28, APEX Saturday order item 1; PRODUCTION_GAP rows 8-9).

Niclas's three conditions of 2026-11-23 and PRODUCTION_GAP row 8's: an explicit row limit (the `.earnings_dates`
property stops at 2021-01); a hole is marked, never read as "no report"; the battery unit key hashes the fetched
set; a source that returns nothing for every ticker fails instead of filtering nothing."""
import ast
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.backtest import earnings_source as es_mod
from backend.backtest.earnings_source import GAP_DAYS, EarningsSet, load

D = date.fromisoformat


def _quarterly(first: str, n: int, step: int = 91) -> tuple[date, ...]:
    return tuple(D(first) + timedelta(days=step * i) for i in range(n))


# ── the set ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_near_is_the_engines_old_filter_on_covered_days():
    es = EarningsSet({"AAA": _quarterly("2020-01-30", 12)})
    old = lambda day, n: any(day <= ed <= day + timedelta(days=n) for ed in es.dates["AAA"])
    for day in (D("2020-01-27"), D("2020-01-30"), D("2020-01-31"), D("2020-04-28"), D("2020-07-01")):
        for n in (0, 2, 3):
            assert es.near("AAA", day, n) == old(day, n), (day, n)


def test_a_hole_is_unknown_not_no_report():
    es = EarningsSet({"AAA": (D("2015-02-01"), D("2015-05-01"), D("2016-07-20"), D("2016-10-20"))},
                     failed={"BBB": "no rows"})
    assert es.in_hole("AAA", D("2014-12-31"))                 # before the first report the source returned
    assert es.in_hole("AAA", D("2015-09-01"))                 # inside a gap over GAP_DAYS (MRVL's shape)
    assert es.in_hole("AAA", D("2016-11-01"))                 # after the last report
    assert es.in_hole("BBB", D("2015-09-01"))                 # the source returned nothing
    assert es.in_hole("ZZZ", D("2015-09-01"))                 # never fetched
    assert not es.in_hole("AAA", D("2015-03-15"))             # between two quarterly reports
    assert not es.in_hole("AAA", D("2015-05-01"))             # on a report


def test_a_routine_q4_gap_is_not_a_hole():
    # COST and PEP: Q4 lands 120-135 days after Q3 (the probe's reason for 150, not 120)
    es = EarningsSet({"COST": (D("2020-10-01"), D("2021-02-12"), D("2021-05-20"))})
    assert (D("2021-02-12") - D("2020-10-01")).days < GAP_DAYS
    assert not es.in_hole("COST", D("2020-12-15"))


def test_holes_are_clipped_to_the_window():
    es = EarningsSet({"AAA": (D("2015-02-01"), D("2015-05-01"), D("2016-07-20"), D("2026-12-01"))})
    got = es.holes("AAA", D("2015-01-01"), D("2016-12-31"))
    assert got[0] == (D("2015-01-01"), D("2015-01-31"))
    assert got[1] == (D("2015-05-02"), D("2016-07-19"))
    assert (D("2016-07-21"), D("2016-12-31")) in got           # 2016-07-20 to 2026-12-01 is one long gap
    assert EarningsSet({}).holes("X", D("2020-01-01"), D("2020-02-01")) == [(D("2020-01-01"), D("2020-02-01"))]


def test_digest_moves_with_the_set_and_holds_without_a_change():
    a = EarningsSet({"AAA": _quarterly("2020-01-30", 4)})
    assert a.digest() == EarningsSet({"AAA": _quarterly("2020-01-30", 4)}).digest()
    assert a.digest() != EarningsSet({"AAA": _quarterly("2020-01-30", 5)}).digest()
    assert a.digest() != EarningsSet({"AAA": _quarterly("2020-01-30", 4)}, failed={"BBB": "x"}).digest()


# ── the fetch ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_fetch_takes_an_explicit_limit_not_the_property():
    tk = MagicMock()
    tk.get_earnings_dates.return_value = pd.DataFrame(
        {"EPS Estimate": [1.0, 1.1]}, index=pd.DatetimeIndex(["2015-02-01 16:00", "2015-05-01 16:00"], tz="America/New_York"))
    with patch("yfinance.Ticker", return_value=tk):
        got = es_mod._yf_fetch("AAA")
    tk.get_earnings_dates.assert_called_once_with(limit=es_mod.EARNINGS_LIMIT)
    assert got == [D("2015-02-01"), D("2015-05-01")]
    assert es_mod.EARNINGS_LIMIT >= 48                        # the probe's median count of reports since 2015


def test_an_empty_source_fails_instead_of_filtering_nothing():
    # PRODUCTION_GAP row 8: "row 9's test must fail on an empty set"; the founding case is a missing lxml
    def no_lxml(_t):
        raise ImportError("Missing optional dependency 'lxml'")
    with pytest.raises(RuntimeError, match="silent no-op"):
        load(["AAA", "BBB"], fetch=no_lxml)
    with pytest.raises(RuntimeError, match="silent no-op"):
        load(["AAA"], fetch=lambda _t: [])


def test_one_ticker_failing_is_a_marked_hole_and_a_warning():
    from loguru import logger
    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        def fetch(t):
            if t == "BBB":
                raise ValueError("boom")
            return list(_quarterly("2020-01-30", 4))
        es = load(["AAA", "BBB"], fetch=fetch)
    finally:
        logger.remove(sink)
    assert set(es.dates) == {"AAA"} and "BBB" in es.failed
    assert es.in_hole("BBB", D("2020-03-01"))
    assert any("1 of 2 tickers returned nothing" in s and "BBB" in s for s in seen)


def test_lxml_is_a_runtime_requirement():
    # the conftest interpreter guard (R143 a) reads requirements.txt, so an interpreter without lxml is refused
    lines = [ln.split("#", 1)[0].strip() for ln in Path("requirements.txt").read_text().splitlines()]
    assert any(ln.startswith("lxml") for ln in lines)


def test_no_engine_reads_the_earnings_dates_property():
    # both engine copies read the 25-row property until 2026-11-28; they now call earnings_source.load
    for path in ("backend/backtest/engine.py", "backend/backtest/engine_fast.py"):
        tree = ast.parse(Path(path).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "earnings_dates" not in attrs and "get_earnings_dates" not in attrs, path
        assert "load_earnings" in Path(path).read_text(), path


# ── the engine ──────────────────────────────────────────────────────────────────────────────────────────────────

def _engine_run(pre, es, **kw):
    from backend.backtest import engine_fast
    from tests.test_engine_flag_off_parity import END, RUN_KW, START
    with patch.object(engine_fast, "load_earnings", return_value=es):
        return engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **{**RUN_KW, **kw})


def test_engine_filters_a_covered_report_and_passes_a_hole_with_a_count():
    from backend.backtest import engine_fast
    from tests.test_engine_flag_off_parity import END, RUN_KW, START, synthetic_precomputed
    pre = synthetic_precomputed()
    base = engine_fast.run(START.isoformat(), END.isoformat(), precomputed=pre, **RUN_KW)
    first = base["trade_log"][0]
    tk, day = first["ticker"], D(first["entry_date"])
    covered = {t: _quarterly("2024-06-01", 12) for t in pre["ticker_sector"]}
    covered[tk] = tuple(sorted(set(covered[tk]) | {day + timedelta(days=1)}))

    res = _engine_run(pre, EarningsSet(covered), earnings_filter_days=3)
    assert (tk, first["entry_date"]) not in {(t["ticker"], t["entry_date"]) for t in res["trade_log"]}
    assert res["earnings_coverage"]["hole_ticker_days"] == 0

    led: list = []
    holes = _engine_run(pre, EarningsSet({}, failed={t: "no rows" for t in pre["ticker_sector"]}),
                        earnings_filter_days=3, candidate_ledger=led)
    assert holes["trade_log"] == base["trade_log"]           # nothing filtered on an unknown day ...
    assert holes["earnings_coverage"]["hole_ticker_days"] > 0  # ... and every such day counted
    assert sorted(holes["earnings_coverage"]["failed"]) == sorted(pre["ticker_sector"])
    assert led and all(r["earnings_near"] is None for r in led)   # the ledger says unknown, not False


def test_engine_without_the_filter_fetches_nothing():
    from tests.test_engine_flag_off_parity import synthetic_precomputed
    with patch.object(es_mod, "_yf_fetch", side_effect=AssertionError("fetched")):
        from backend.backtest import engine_fast
        from tests.test_engine_flag_off_parity import END, RUN_KW, START
        res = engine_fast.run(START.isoformat(), END.isoformat(), precomputed=synthetic_precomputed(), **RUN_KW)
    assert "earnings_coverage" not in res


# ── the battery unit key ────────────────────────────────────────────────────────────────────────────────────────

def test_unit_key_hashes_the_earnings_set_only_when_the_filter_is_set():
    import backend.backtest.battery_2026_10 as bt
    with patch.object(es_mod, "load", side_effect=AssertionError("fetched")):
        plain = bt.unit("IS", bt.R2).key()                    # no fetch, so every stored key is unchanged
    with patch.object(es_mod, "load", return_value=EarningsSet({"AAA": _quarterly("2020-01-30", 4)})):
        k1 = bt.unit("IS", bt.R2, earnings_filter_days=2).key()
    with patch.object(es_mod, "load", return_value=EarningsSet({"AAA": _quarterly("2020-01-30", 5)})):
        k2 = bt.unit("IS", bt.R2, earnings_filter_days=2).key()
    assert len({plain, k1, k2}) == 3                          # a changed set misses the stored result
