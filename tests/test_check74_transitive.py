"""
CHECK 74 sees a clock read that sits calls away or carries a timezone (2026-11-14).

Audit finding 7 moved the hold count's clock read into market_calendar._today_ny
(`datetime.now(NY)`), two calls below the `_trading_days_since(...) >= max_hold_days`
comparison. The empty-parens pattern and the one-level Tier B lost the three reviewed
files (test_check74_reviewed's positive control failed). This pins both halves on a
synthetic tree: the tz-argument read, and the read two calls below the comparison.
"""
import audit.checks_code as cc


def _tree(tmp_path, clock: str):
    (tmp_path / "backend").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "backend" / "cal.py").write_text(
        "from datetime import datetime\n"
        "def _today():\n"
        f"    return {clock}.date()\n"
        "def held_since(ts):\n"
        "    return count_between(ts, _today())\n")
    (tmp_path / "backend" / "book.py").write_text(
        "def _held(ts):\n"
        "    return held_since(ts)\n"
        "def check_stops(trade, max_hold):\n"
        "    if _held(trade['ts']) >= max_hold:\n"
        "        return 'TIME'\n")
    (tmp_path / "tests" / "test_book.py").write_text(
        "def test_time_stop():\n"
        "    assert check_stops({'ts': \"2026-01-02T15:00:00\"}, 25) == 'TIME'\n")


def _flagged(monkeypatch, tmp_path):
    found = []
    monkeypatch.setattr(cc, "REPO", tmp_path)
    monkeypatch.setattr(cc, "_C74_REVIEWED", {})
    monkeypatch.setattr(cc, "flag", lambda num, name, sev, where, msg: found.append(where))
    cc.check74()
    return found


def test_tz_clock_read_two_calls_below_the_comparison_is_flagged(monkeypatch, tmp_path):
    _tree(tmp_path, "datetime.now(NY)")
    assert _flagged(monkeypatch, tmp_path) == ["tests/test_book.py"]


def test_bare_clock_read_two_calls_below_is_flagged(monkeypatch, tmp_path):
    _tree(tmp_path, "datetime.now()")
    assert _flagged(monkeypatch, tmp_path) == ["tests/test_book.py"]


def test_no_clock_read_flags_nothing(monkeypatch, tmp_path):
    # Control: the same tree with a constant date is not a decaying shape.
    _tree(tmp_path, "datetime(2026, 1, 2)")
    assert _flagged(monkeypatch, tmp_path) == []
