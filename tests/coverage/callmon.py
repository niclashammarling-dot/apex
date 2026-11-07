"""
Opt-in call recorder for the function ratchet (tests/coverage/ratchet.py): every repo code object the suite starts,
once each, keyed (file, qualname). Loaded by tests/conftest.py only when APEX_CALLMON_OUT is set, so a plain pytest
is unchanged. Moved into apex 2026-11-07 from the vault's measurement copy (Vault/data/apex-inventory/callmon.py,
11-05), which keyed on co_name: two methods with one name in one file merged. co_qualname keeps them apart
(Class.method, outer.<locals>.inner), the key the baseline and the change gate use.
sys.monitoring (3.12+): PY_START fires once per code object and the callback disables itself, so the cost is one
call per function, not per call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[2]) + os.sep
M = sys.monitoring
seen: set[tuple[str, str]] = set()
# The outermost session owns the output. A test that starts pytest in a subprocess passes APEX_CALLMON_OUT on to it;
# found 2026-11-07, such a nested session wrote its 93 code objects over the outer run's file mid-suite.
OWNER = os.environ.setdefault("APEX_CALLMON_OWNER", str(os.getpid()))


def _free_tool_id() -> int:
    for tool in (5, 4, 3):
        if M.get_tool(tool) is None:
            return tool
    raise RuntimeError("no free sys.monitoring tool id for the call recorder")


TOOL = _free_tool_id()


def _start(code, offset):
    f = code.co_filename
    if f.startswith(REPO):
        seen.add((os.path.relpath(f, REPO), code.co_qualname))
    return M.DISABLE


M.use_tool_id(TOOL, "apex_callmon")
M.register_callback(TOOL, M.events.PY_START, _start)
M.set_events(TOOL, M.events.PY_START)


def pytest_sessionfinish(session, exitstatus):
    M.set_events(TOOL, 0)
    if OWNER == str(os.getpid()):
        Path(os.environ["APEX_CALLMON_OUT"]).write_text(json.dumps(sorted(seen)))
