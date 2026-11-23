"""m5_universe_probe: membership spans of names that left, and bar coverage inside a span (2026-11-23)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "m5_universe_probe", Path(__file__).resolve().parent.parent / "scripts" / "m5_universe_probe.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def test_spans_cover_only_names_that_left_and_start_at_the_window():
    rows = [("2015-06-01", {"A", "OLD"}), ("2016-01-04", {"A", "B", "OLD"}),
            ("2018-03-01", {"A", "B"}), ("2026-08-18", {"A", "C"})]
    assert p.spans(rows) == {"B": ("2016-01-04", "2018-03-01"), "OLD": ("2016-01-04", "2016-01-04")}


def test_coverage_counts_sessions_inside_the_span_only():
    sessions = ["2016-01-04", "2016-01-05", "2016-01-06", "2016-01-07"]
    assert p.coverage(["2016-01-04", "2016-01-06", "2016-01-07", "2017-01-03"], sessions,
                      ("2016-01-04", "2016-01-06")) == round(2 / 3, 4)
    assert p.coverage([], sessions, ("2015-01-01", "2015-12-31")) is None


def test_no_args_prints_usage_and_exits_2(capsys):
    assert p.main([]) == 2
    assert "Usage" in capsys.readouterr().out
