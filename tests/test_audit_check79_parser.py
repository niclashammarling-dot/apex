"""
CHECK 79's parser path (ratchet round 1, R046: the three free slots after the 11-05 regressions). The founding
blocks are fixtures: a dated block ages from its first date (the VOL_ACCUM marker's shape since 2026-11-14), and a
reworded block that no longer carries the vocabulary is not seen at all (the known limit, R046: the commit-time
parser run is not built). One gate is parsed in months, one block is past its gate, and one gate ages from its
table. Dates are relative to today, so no fixture drifts into another branch (CHECK 74).
"""
import sqlite3
from datetime import date, timedelta

from audit import _audit_core as core
from audit import checks_code as cc


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _repo(tmp_path, monkeypatch, files: dict[str, str], table_min: str | None = None):
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / "data").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(repo / "data/apex.db")
    conn.execute("CREATE TABLE sector_posterior_history (date TEXT)")
    if table_min:
        conn.execute("INSERT INTO sector_posterior_history VALUES (?)", (table_min,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(cc, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(cc, "flag", core.flag)
    return repo


def _run79():
    cc.check79()
    return [(f[2], f[4]) for f in core.findings if f[0] == 79]


def test_comment_and_docstring_lines_keep_their_line_numbers(tmp_path):
    f = tmp_path / "m.py"
    f.write_text('# first\nX = 1  # trailing\n"""doc\nsecond line\n"""\nY = "not a docstring"\n')
    assert cc._c79_comment_lines(f) == [(1, "# first"), (2, "# trailing"), (3, '"""doc'), (4, "second line"), (5, '"""')]
    broken = tmp_path / "broken.py"
    broken.write_text('# kept\n"""never closed\n')
    assert cc._c79_comment_lines(broken) == [(1, "# kept")]


def test_blocks_split_at_a_gap_in_line_numbers():
    assert cc._c79_blocks([(1, "a"), (2, "b"), (5, "c")]) == [[(1, "a"), (2, "b")], [(5, "c")]]
    assert cc._c79_blocks([]) == []


def test_weeks_of_reads_the_tables_first_date(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, {}, table_min=_ago(70))
    assert cc._c79_weeks_of("sector_posterior_history") == 10.0
    assert cc._c79_weeks_of("no_such_history") is None
    _repo(tmp_path / "empty", monkeypatch, {})
    assert cc._c79_weeks_of("sector_posterior_history") == 0.0
    monkeypatch.setattr(cc, "REPO", tmp_path / "nowhere")
    assert cc._c79_weeks_of("sector_posterior_history") is None


def test_a_dated_block_ages_from_its_first_date(tmp_path, monkeypatch):
    # The later, older date (a history reference) must not age the block: 3 weeks against a 2-month gate is inside.
    _repo(tmp_path, monkeypatch, {"backend/x.py": f"# Re-dated {_ago(21)} after the card read (history {_ago(400)});\n"
                                                  "# revisit after 2 months.\nT = 1.2\n"})
    assert _run79() == []


def test_a_marker_past_its_month_gate_fires(tmp_path, monkeypatch):
    # 3-6 months = 26 weeks; 186 days is 26.6 weeks, crossed within the last week: the event, CRITICAL.
    _repo(tmp_path, monkeypatch, {"backend/x.py": f"# dated {_ago(186)}: revisit after 3-6 months of live data\nT = 1\n",
                                  "backend/y.py": "# interim value, no gate stated\nU = 2\n"})
    [(sev, text)] = _run79()
    assert sev == "CRITICAL" and "backend/x.py:1 gated on dated" in text and ">=26w, now 27w" in text
    assert "backend/y.py:1" in text                                        # ungated markers listed when one fires


def test_a_marker_long_past_its_gate_is_a_standing_warning(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, {"backend/x.py": f"# dated {_ago(280)}: revisit after 3-6 months\nT = 1\n"})
    [(sev, text)] = _run79()
    assert sev == "WARNING" and "now 40w" in text


def test_a_table_gate_ages_from_the_table(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, {"backend/x.py": "# Interim: replace once sector_posterior_history has 4-8 weeks\nW = 0\n"},
          table_min=_ago(70))
    [(sev, text)] = _run79()
    assert "gated on sector_posterior_history >=8w, now 10w" in text


def test_a_reworded_block_is_not_seen(tmp_path, monkeypatch):
    # The known blindness: without the vocabulary the block is no marker, not even an ungated one.
    _repo(tmp_path, monkeypatch, {"backend/x.py": f"# dated {_ago(400)}: look at this value again later\nT = 1\n",
                                  "backend/z.py": f"# dated {_ago(280)}: revisit after 3-6 months\nV = 1\n"})
    [(sev, text)] = _run79()
    assert "backend/z.py" in text and "backend/x.py" not in text
