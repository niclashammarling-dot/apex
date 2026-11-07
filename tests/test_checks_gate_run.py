"""
checks_gate.run calls every check the module defines (2026-11-07; the function ratchet's change gate asked for a test
when CHECKs 96 and 97 were registered in it). A check that is defined but not called has no runner, the class the
bundle's NO RUNNER line names at registry level; this is the same fact at code level.
"""
import re
from pathlib import Path

from audit import checks_gate as cg


def test_run_calls_every_defined_check(monkeypatch):
    names = sorted(re.findall(r"^def (check\d+)\(", Path(cg.__file__).read_text(), re.M))
    called = []
    for n in names:
        monkeypatch.setattr(cg, n, lambda *a, _n=n, **k: called.append(_n))
    cg.run()
    assert sorted(called) == names, sorted(set(names) - set(called))
    assert {"check96", "check97"} <= set(called)
