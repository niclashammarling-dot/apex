"""CHECK 54 (rewritten 2026-10-26, lock labels phase 1) and CHECK 25's table-driven consumers."""
import dataclasses

from backend.gate import lock_codes

TABLE_DRIVEN = 'const cols = D.gateLocks?.columns ?? [];  // lock_codes table, from the gate-history response\n'
OWN_LABELS = '  { stage: "FILTERED_L1",           label: "L1 · Score",        count: l1Fail },\n'
BADGE_MAP = '  FILTERED_L2:             { label: "L3 FAIL",   cls: "t-gate-filt" },\n'


def _setup(tmp_path, monkeypatch, app: str, terminal: str):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "frontend/src/components").mkdir(parents=True)
    (repo / "frontend/src/App.jsx").write_text(app)
    (repo / "frontend/src/components/ApexTerminal.jsx").write_text(terminal)
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(cg, "flag", core.flag)
    return cg, core


def _found(core, n):
    return [f[4] for f in core.findings if f[0] == n]


def _found_in(core, n):
    """(file, message) pairs: C names the file in the finding's location, not in its text."""
    return [(f[3], f[4]) for f in core.findings if f[0] == n]


def test_frontend_reading_the_table_is_clean(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, TABLE_DRIVEN)
    cg.check54()
    assert _found(core, 54) == []


def test_own_label_rows_are_flagged(tmp_path, monkeypatch):
    # The 06-11 drift: App.jsx's own stage/label rows and the Terminal's own badge map.
    cg, core = _setup(tmp_path, monkeypatch, OWN_LABELS, BADGE_MAP)
    cg.check54()
    out = _found(core, 54)
    assert any("App.jsx" not in m and "FILTERED_L2" in m for m in out)
    assert any("FILTERED_L1" in m for m in out)
    assert any("lock number(s) written in the frontend (L1 ·)" in m for m in out)   # C sees the literal too
    assert any(f.endswith("ApexTerminal.jsx") and "(L3)" in m for f, m in _found_in(core, 54))  # badge "L3 FAIL"
    assert len(out) == 4


# C, added after the phase 1 review (R5): master's Terminal detail panel and Settings labels.
DETAIL_PANEL = '<span style={{ color: "var(--t-text-3)", marginRight: 8 }}>L5:</span>\n'
SETTINGS_LABEL = '  { key: "lock2_sentiment_min",  label: "Lock 2 Sentiment Min",  step: 0.01 },\n'
COMMENT_ONLY = '// The lock prefix of a Settings/Promote label comes from the backend table.\n'


def test_lock_numbers_written_in_the_frontend_are_flagged(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, SETTINGS_LABEL, DETAIL_PANEL)
    cg.check54()
    out = _found(core, 54)
    assert any("(L5)" in m for m in out) and any("(Lock 2)" in m for m in out) and len(out) == 2


def test_table_labels_and_plain_comments_are_not_lock_numbers(tmp_path, monkeypatch):
    panel = '<span>{D.gateLocks?.detail_labels?.l2_summary}:</span>\n<span>{r.reason_label ?? "Reason"}:</span>\n'
    cg, core = _setup(tmp_path, monkeypatch, COMMENT_ONLY, panel)
    cg.check54()
    assert _found(core, 54) == []


def test_a_rule_or_module_outside_the_chain_is_flagged(tmp_path, monkeypatch):
    # A: the table agrees with the approved chain (rewritten for lock labels phase 2: the stored code is the
    # owner's lock code, so every rule must be owned by a lock of the chain and every module lock map to one).
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, TABLE_DRIVEN)
    stray = dataclasses.replace(lock_codes.RULES[0], owner=9, name="stray")
    monkeypatch.setattr(lock_codes, "RULES", lock_codes.RULES + (stray,))
    monkeypatch.setattr(lock_codes, "MODULE_TO_CHAIN", {k: v for k, v in lock_codes.MODULE_TO_CHAIN.items() if k != 3})
    cg.check54()
    out = _found(core, 54)
    assert any("rule stray is owned by lock 9" in m for m in out)
    assert any("module lock 3 maps to no chain lock" in m for m in out)


def test_check25_skips_a_table_driven_consumer_and_compares_one_with_literals(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, "const BADGE = {};\n")
    (cg.REPO / "backend/gate").mkdir(parents=True)
    (cg.REPO / "backend/gate/gate_runner.py").write_text("from backend.gate import lock_codes  # codes_for\n")
    cg.check25()
    out = _found(core, 25)
    assert any("not handled in ApexTerminal.jsx" in m for m in out)
    assert not any("App.jsx" in m and "not handled" in m for m in out)


# C widened 2026-10-26 (Niclas: "det måste synas rätt överallt … dashboard, funnel och gate activity"): any
# frontend file, a lock number before a word, and a lock count. Master had all three outside the two files.
SECTOR_ROTATION = 'title={normalized ? "Show raw scores" : "Normalize by L1 threshold — makes sectors comparable"}\n'
FUNNEL_TITLE = '<div className="t-card-title">GATE FUNNEL · 5-LOCK</div>\n'
NOT_LOCKS = ('const fillPath = path + ` L${w} ${h} L0 ${h} Z`;\n'
             '// the old "L1 threshold" wording, kept in a comment\n'
             '{/* L3 score: rendered from the table */}\n')


def test_any_frontend_file_with_a_lock_number_before_a_word_or_a_lock_count_is_flagged(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, FUNNEL_TITLE)
    (cg.REPO / "frontend/src/components/SectorRotation.jsx").write_text(SECTOR_ROTATION)
    cg.check54()
    out = _found_in(core, 54)
    assert any(f.endswith("SectorRotation.jsx") and "(L1)" in m for f, m in out)
    assert any(f.endswith("ApexTerminal.jsx") and "(5-LOCK)" in m for f, m in out)
    assert len(out) == 2


def test_svg_paths_and_comments_are_not_lock_numbers(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, NOT_LOCKS)
    cg.check54()
    assert _found(core, 54) == []
