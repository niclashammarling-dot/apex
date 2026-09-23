"""
update_registry() — the liveness-cell write guard.

Why this file exists (2026-09-23). `update_registry()` rewrites
`last_triggered`/`last_clean` on every registry row. It runs only in the CI
path (the host calls `--emit-state`, which returns before the registry write),
so it had been dark since the 2026-06-27 report — and during those three
months the `last_clean` cell was used as a free-text notes field. Nine rows
came to hold design amendments there, CHECK 45's running to 822 characters.
The first clean CI run would have replaced each with a bare date.

It was confirmed by running the writer against the real `audit/CHECKS.md`,
which rewrote 162 lines and was restored from a copy taken beforehand. That
is the run this file replaces. Two properties follow, and both matter more
than the assertions:

  1. The guard is exercised on every suite run, instead of meeting production
     for the first time on the first dispatch after seven weeks dark.
  2. Nothing here points `update_registry` at the real registry. Every test
     patches REPO to a tmp fixture, so testing the writer cannot repeat the
     near-miss even for someone who has not read this docstring.
"""


HEADER = (
    "# APEX Audit Check Registry\n\n"
    "<!-- REGISTRY — column order and header format are parsed by the audit agent. Do not reorder. -->\n"
    "| # | Name | Added | Prompted by | Files covered | last_triggered | last_clean |\n"
    "|---|------|-------|-------------|---------------|----------------|------------|\n"
)

# A plain row: both liveness cells are dates. The writer owns these.
ROW_PLAIN = "| 1 | Result-dict sync hazard | 2026-03-25 | stale field persisted | backend/db.py | 2026-03-25 | 2026-06-27 |\n"
# An em-dash row: never triggered. Also writer-owned.
ROW_DASH = "| 2 | Never fired | 2026-09-01 | filed with no instance yet | backend/x.py | — | — |\n"
# A note-carrying row, the shape that nine real rows are in.
NOTE = ("— **Event/level split 2026-09-16 (second pass):** CRITICAL only for a missing session "
        "in the trailing 3; cumulative share WARNING. Same reason as 77.")
ROW_NOTE = f"| 78 | PCR history gap count | 2026-09-16 | gap count vs the exchange calendar | audit/checks_data.py | 2026-09-16 | {NOTE} |\n"
# A note in the OTHER liveness cell — the guard must key on both, not just the last.
ROW_NOTE_LT = "| 79 | Deferred-replacement markers | 2026-09-16 | markers past their gate | audit/checks_code.py | see the decision record | 2026-06-27 |\n"


def _run(tmp_path, monkeypatch, rows: str, triggered: set, skipped: list | None = None):
    from audit import _audit_core as core
    from audit import mechanical_checks as mc
    repo = tmp_path / "repo"
    (repo / "audit").mkdir(parents=True, exist_ok=True)
    path = repo / "audit/CHECKS.md"
    path.write_text(HEADER + rows)
    monkeypatch.setattr(mc, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "triggered", set(triggered))
    monkeypatch.setattr(core, "skipped", list(skipped or []))
    monkeypatch.setattr(mc, "triggered", core.triggered)
    monkeypatch.setattr(mc, "skipped", core.skipped)
    monkeypatch.setattr(mc, "flag", core.flag)
    mc.update_registry()
    return path.read_text(), core.findings


def test_note_cell_is_never_overwritten(tmp_path, monkeypatch):
    """The whole point: a clean run must not replace a note with today's date."""
    text, findings = _run(tmp_path, monkeypatch, ROW_NOTE, triggered=set())
    assert NOTE in text, "the design amendment was destroyed by a clean run"
    assert "| 78 | PCR history gap count | 2026-09-16 |" in text
    assert [f for f in findings if f[0] == 78 and f[2] == "CRITICAL"]


def test_guard_keys_on_both_liveness_cells(tmp_path, monkeypatch):
    """A note in last_triggered is guarded too — not only the last column."""
    text, findings = _run(tmp_path, monkeypatch, ROW_NOTE_LT, triggered=set())
    assert "see the decision record" in text
    assert "2026-06-27" in text, "the clean date was rewritten on a guarded row"
    assert [f for f in findings if f[0] == 79]


def test_plain_rows_still_update(tmp_path, monkeypatch):
    """The guard must not freeze the rows it does own."""
    from datetime import date
    today = date.today().isoformat()
    text, findings = _run(tmp_path, monkeypatch, ROW_PLAIN, triggered=set())
    assert f"| 2026-03-25 | {today} |" in text, "clean run should advance last_clean"
    assert not findings


def test_triggered_row_advances_last_triggered(tmp_path, monkeypatch):
    from datetime import date
    today = date.today().isoformat()
    text, _ = _run(tmp_path, monkeypatch, ROW_PLAIN, triggered={1})
    assert f"| {today} | 2026-06-27 |" in text


def test_em_dash_is_a_valid_liveness_value(tmp_path, monkeypatch):
    """— means 'never', and is writer-owned: a clean run fills last_clean."""
    from datetime import date
    text, findings = _run(tmp_path, monkeypatch, ROW_DASH, triggered=set())
    assert f"| — | {date.today().isoformat()} |" in text
    assert not findings


def test_guard_does_not_depend_on_row_order(tmp_path, monkeypatch):
    """
    flag() mutates the shared `triggered` set, which the loop reads to decide
    every later row's values. Flagging inside the loop would make a plain row's
    output depend on whether a guarded row preceded it. Same rows, both orders,
    identical plain-row result.
    """
    a, _ = _run(tmp_path, monkeypatch, ROW_NOTE + ROW_PLAIN, triggered=set())
    b, _ = _run(tmp_path, monkeypatch, ROW_PLAIN + ROW_NOTE, triggered=set())
    line_a = [l for l in a.splitlines() if l.startswith("| 1 |")][0]
    line_b = [l for l in b.splitlines() if l.startswith("| 1 |")][0]
    assert line_a == line_b


def test_skipped_check_neither_triggers_nor_clears(tmp_path, monkeypatch):
    """SKIPPED is a third state: a check that did not evaluate moves neither column."""
    text, _ = _run(tmp_path, monkeypatch, ROW_PLAIN, triggered=set(),
                   skipped=[(1, "Result-dict sync hazard", "backend/db.py", "db absent")])
    assert "| 2026-03-25 | 2026-06-27 |" in text


def test_writer_never_touches_the_real_registry(tmp_path, monkeypatch):
    """
    Guards the test file itself. If REPO is left unpatched, update_registry
    rewrites the repository's own audit/CHECKS.md — which is how the nine
    note rows were nearly lost. Assert the fixture path is what was written.
    """
    from audit import mechanical_checks as mc
    text, _ = _run(tmp_path, monkeypatch, ROW_PLAIN, triggered=set())
    assert mc.REPO == tmp_path / "repo"
    assert (tmp_path / "repo/audit/CHECKS.md").read_text() == text
