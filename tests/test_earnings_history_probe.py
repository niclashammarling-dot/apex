"""earnings_history_probe.summarise: coverage counts and the gap that found BLK's 2009-2022 hole (2026-11-23)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "earnings_history_probe", Path(__file__).resolve().parent.parent / "scripts" / "earnings_history_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_quarterly_reports_counted_since_and_future_date_excluded():
    d = ["2014-10-20", "2015-01-27", "2015-04-27", "2015-07-21", "2027-01-28"]
    s = probe.summarise(d, today="2026-11-23")
    assert (s["n"], s["n_past"], s["n_since"]) == (5, 4, 3)
    assert s["first"] == "2014-10-20" and s["last"] == "2027-01-28"
    assert s["gaps"] == []


def test_a_multi_year_hole_is_reported_not_counted_as_coverage():
    d = ["2009-07-16", "2009-10-20", "2022-07-15", "2022-10-13"]
    s = probe.summarise(d, today="2026-11-23")
    assert s["n_since"] == 2
    assert s["gaps"] == [("2009-10-20", "2022-07-15")]


def test_a_late_q4_report_is_not_a_gap_but_a_missed_quarter_is():
    s = probe.summarise(["2015-10-06", "2016-02-11", "2025-12-18", "2026-06-23"], today="2026-11-23")
    assert s["gaps"] == [("2016-02-11", "2025-12-18"), ("2025-12-18", "2026-06-23")]


def test_no_args_prints_usage_and_exits_2(capsys):
    assert probe.main([]) == 2
    assert "Usage" in capsys.readouterr().out


def test_universe_is_the_engines_get_sectors_not_config_sectors():
    from backend.ticker_config import get_sectors
    want = {t for cfg in get_sectors().values() for t in cfg["tickers"]}
    assert set(probe.universe()) == want
    assert {"ADI", "ODFL", "WAB"} <= want  # three of the ten the config.SECTORS run missed
