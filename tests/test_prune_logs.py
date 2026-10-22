"""
scripts/prune_logs.py — the log retention that actually runs (2026-10-16).

loguru's retention="14 days" in backend/main.py prunes only on an in-process
midnight rotation, which the market-window launcher never reaches: 35 apex_*.log
files 09-02..10-15 were on disk on 10-16. All tests use tmp_path; nothing here
touches the repo's logs/.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import prune_logs  # noqa: E402

TODAY = date(2026, 10, 16)   # with keep_days=14: cutoff 2026-10-02 (the mechanics tests)
K14 = 14


def _touch(d: Path, *names: str) -> None:
    for n in names:
        (d / n).write_text("x")


def test_removes_only_dated_logs_older_than_cutoff(tmp_path):
    _touch(tmp_path,
           "apex_2026-09-02.log", "apex_2026-10-01.log",          # older than cutoff
           "apex_2026-10-02.log", "apex_2026-10-15.log",          # boundary and recent
           "market_window_2026-09-30.log", "eod_window_2026-10-10.log",
           "cycle_watch_2026-09-01.log",
           "backend.log", "frontend.log", "cycle_watch_test.log")
    r = prune_logs.prune(tmp_path, TODAY, K14)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert sorted(r["removed"]) == ["apex_2026-09-02.log", "apex_2026-10-01.log",
                                    "cycle_watch_2026-09-01.log", "market_window_2026-09-30.log"]
    assert "apex_2026-10-02.log" in left, "the cutoff day itself is kept"
    assert {"apex_2026-10-15.log", "eod_window_2026-10-10.log"} <= set(left)
    assert sorted(r["undated"]) == ["backend.log", "cycle_watch_test.log", "frontend.log"]
    assert {"backend.log", "frontend.log", "cycle_watch_test.log"} <= set(left)


def test_dry_run_removes_nothing(tmp_path):
    _touch(tmp_path, "apex_2026-09-02.log")
    r = prune_logs.prune(tmp_path, TODAY, K14, dry_run=True)
    assert r["removed"] == ["apex_2026-09-02.log"]
    assert (tmp_path / "apex_2026-09-02.log").exists()


def test_date_from_name_not_mtime_and_bad_date_is_undated(tmp_path):
    _touch(tmp_path, "apex_2026-13-40.log", "apex_2026-10-14.2026-10-14_12-00-00_000000.log")
    r = prune_logs.prune(tmp_path, TODAY, K14)
    assert r["removed"] == []
    assert len(r["undated"]) == 2


def test_summary_line_names_counts_and_oldest(tmp_path):
    _touch(tmp_path, "apex_2026-09-02.log", "apex_2026-09-20.log", "apex_2026-10-15.log", "backend.log")
    line = prune_logs.summary(prune_logs.prune(tmp_path, TODAY, K14))
    assert line == ("log prune: removed 2 (oldest 2026-09-02) before 2026-10-02, "
                    "kept 1, undated 1: backend.log")


def test_missing_dir_is_a_finding_not_silence(tmp_path, capsys):
    rc = prune_logs.main(["--log-dir", str(tmp_path / "nope")])
    assert rc == 1
    assert "log dir missing" in capsys.readouterr().out


def test_cli_respects_keep_days(tmp_path, capsys):
    _touch(tmp_path, f"apex_{date.today().isoformat()}.log", "apex_2000-01-01.log")
    assert prune_logs.main(["--log-dir", str(tmp_path), "--keep-days", "30"]) == 0
    assert not (tmp_path / "apex_2000-01-01.log").exists()
    assert (tmp_path / f"apex_{date.today().isoformat()}.log").exists()
    assert "removed 1" in capsys.readouterr().out


@pytest.mark.parametrize("script", ["market_window.sh"])
def test_launcher_runs_the_prune_on_its_log_dir(script):
    text = (REPO / "scripts" / script).read_text()
    assert 'scripts/prune_logs.py --log-dir "$LOG_DIR"' in text, \
        f"{script} must run the prune; without it nothing enforces retention"


def test_default_keeps_90_days(tmp_path):
    """Ruling 2026-10-22: 90 days. On 10-16 the cutoff is 07-18: that day is kept, 07-17 goes;
    a 10-01 log, which the old 14-day default removed, is kept (parent control)."""
    _touch(tmp_path, "apex_2026-07-17.log", "apex_2026-07-18.log", "apex_2026-10-01.log")
    r = prune_logs.prune(tmp_path, TODAY)
    assert r["removed"] == ["apex_2026-07-17.log"]
    assert sorted(r["kept"]) == ["apex_2026-07-18.log", "apex_2026-10-01.log"]


def test_loguru_retention_matches_the_prune():
    """The inert loguru setting must not state a different window than the prune that runs."""
    assert f'retention="{prune_logs.KEEP_DAYS} days"' in (REPO / "backend" / "main.py").read_text()
