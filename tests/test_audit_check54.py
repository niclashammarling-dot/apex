"""CHECK 54 (rewritten 2026-10-26, lock labels phase 1) and CHECK 25's table-driven consumers."""
import dataclasses

from backend.gate import lock_codes

TABLE_DRIVEN = 'const r = await fetch("/api/gate/locks");\n'
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
    assert len(out) == 3


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


def test_a_mislabelled_stage_in_the_table_is_flagged(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, TABLE_DRIVEN)
    bad = tuple(dataclasses.replace(s, label="L1 · Score") if s.code == "FILTERED_L1" else s
                for s in lock_codes.STAGES)
    monkeypatch.setattr(lock_codes, "STAGES", bad)
    cg.check54()
    assert any('FILTERED_L1 is labelled "L1 · Score" but sits at lock 3 (SCORE)' in m for m in _found(core, 54))


def test_check25_skips_a_table_driven_consumer_and_compares_one_with_literals(tmp_path, monkeypatch):
    cg, core = _setup(tmp_path, monkeypatch, TABLE_DRIVEN, "const BADGE = {};\n")
    (cg.REPO / "backend/gate").mkdir(parents=True)
    (cg.REPO / "backend/gate/gate_runner.py").write_text("from backend.gate import lock_codes\n")
    cg.check25()
    out = _found(core, 25)
    assert any("not handled in ApexTerminal.jsx" in m for m in out)
    assert not any("App.jsx" in m and "not handled" in m for m in out)
