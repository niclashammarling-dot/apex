"""CHECK 91 — macro calendar horizon (2026-10-17)."""
from datetime import date


def _run91(tmp_path, monkeypatch, text, today):
    from audit import _audit_core as core
    from audit import checks_gate as cg
    repo = tmp_path / "repo"
    (repo / "backend/gate").mkdir(parents=True, exist_ok=True)
    (repo / "backend/gate/lock1_eligibility.py").write_text(text)
    monkeypatch.setattr(cg, "REPO", repo)
    monkeypatch.setattr(core, "REPO", repo)
    monkeypatch.setattr(core, "findings", [])
    monkeypatch.setattr(core, "skipped", [])
    monkeypatch.setattr(core, "triggered", set())
    monkeypatch.setattr(cg, "flag", core.flag)
    cg.check91(today=today)
    return [(f[2], f[4]) for f in core.findings if f[0] == 91]


SRC = "_CPI = {date(2026, 11, 10), date(2026, 12, 10)}\nd = date(year, month, 1)\n"


def test_warns_near_the_end_and_is_critical_after(tmp_path, monkeypatch):
    assert _run91(tmp_path, monkeypatch, SRC, date(2026, 9, 1)) == []                 # 100 d left
    out = _run91(tmp_path, monkeypatch, SRC, date(2026, 10, 17))
    assert len(out) == 1 and out[0][0] == "WARNING" and "2026-12-10 (54 d)" in out[0][1]
    out = _run91(tmp_path, monkeypatch, SRC, date(2026, 12, 11))
    assert len(out) == 1 and out[0][0] == "CRITICAL"


def test_real_calendar_is_clear_today():
    from audit import _audit_core as core
    from audit import checks_gate as cg
    before = len(core.findings)
    cg.check91(today=date(2026, 10, 17))
    assert not [f for f in core.findings[before:] if f[0] == 91]
